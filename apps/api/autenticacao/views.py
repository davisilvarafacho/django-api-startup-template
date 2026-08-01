from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.utils import timezone

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

import posthog
from knox.models import get_token_model
from knox.settings import knox_settings
from posthog import capture, identify_context, new_context

from apps.api.core.errors import APIError
from apps.api.core.route_markers import no_tenancy
from apps.organizacoes.permissions import TenantPermission

from .errors import AuthErrorCode
from .mfa import (
    PRE_AUTH_LIFETIME,
    active_factors,
    available_methods,
    confirm_enrollment,
    consume_trusted_device,
    regenerate_recovery_codes,
    remove_factor,
    reset_user_mfa,
    start_enrollment,
    start_login_challenge,
    start_reauthentication,
    verify_login_challenge,
    verify_reauthentication,
)
from .models import MFAFactor, MFAFactorType, TokenType, TrustedDevice
from .permissions import APIKeyPermissions, TokenScopePermission
from .recent_auth import RecentAuthenticationPermission, require_recent_auth
from .risk import evaluate_login_risk
from .schema import (
    document_api_key_create,
    document_api_key_resume,
    document_api_key_rotate,
    document_api_key_suspend,
    document_login,
    document_reauthenticate,
)
from .serializers import (
    APIKeySerializer,
    APIKeyWriteSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    ReauthenticateSerializer,
    SessionSerializer,
)
from .services import (
    issue_token,
    resume_api_key,
    revoke_all_sessions,
    revoke_api_key,
    revoke_session,
    rotate_api_key,
    suspend_api_key,
)
from .utils import build_token_metadata

# Nunca importar `knox.models.AuthToken` diretamente: o modelo ativo é o
# swappable definido em `settings.KNOX_TOKEN_MODEL`.
AuthToken = get_token_model()


class LoginView(APIView):
    """Valida credenciais, emite a sessão e avalia risco. Nada mais mora aqui:

    parsing de dispositivo/geolocalização está em `utils.build_token_metadata`,
    emissão atômica em `services.issue_token`, e comparação de risco em
    `risk.evaluate_login_risk`.
    """

    permission_classes = (AllowAny,)
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "auth_login"

    @document_login
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request._request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        metadata_input = build_token_metadata(request, serializer.validated_data)

        # Gate de MFA: com fator ativo, o login não emite sessão — devolve um
        # token de pré-autenticação de vida curta, que só o fluxo de challenge
        # troca por sessão real. Um dispositivo confiável válido pula o desafio.
        trusted_device_token = request.data.get("trusted_device_token", "")
        trusted_device = consume_trusted_device(user, trusted_device_token) if trusted_device_token else None
        if trusted_device is None and active_factors(user).exists():
            pre_auth = issue_token(
                responsavel=user,
                token_type=TokenType.PRE_AUTH,
                created_by=user,
                expiry=PRE_AUTH_LIFETIME,
                metadata_input=metadata_input,
            )
            return Response(
                {"pre_auth_token": pre_auth.plain_token, "methods": available_methods(user)},
                status=status.HTTP_202_ACCEPTED,
            )

        self._checar_limite_de_sessoes(user)

        issued = issue_token(
            responsavel=user,
            token_type=TokenType.TOKEN,
            created_by=user,
            expiry=knox_settings.TOKEN_TTL,
            metadata_input=metadata_input,
        )
        metadata = issued.instance.metadata

        risco = evaluate_login_risk(metadata)
        if risco.is_suspicious:
            metadata.mark_as_suspicious(risco.reason)
            metadata.risk_score = risco.risk_score
            metadata.save(update_fields=["risk_score"])

        user_logged_in.send(sender=user.__class__, request=request, user=user)

        self._capturar_eventos_posthog(user, metadata, risco)

        response_data = {
            "token": issued.plain_token,
            "expiry": issued.instance.expiry,
            "session": {
                "uuid": issued.instance.uuid,
                "device": {
                    "type": metadata.device_type,
                    "name": (metadata.device_name or f"{metadata.device_brand} {metadata.device_model}".strip() or "Dispositivo desconhecido"),
                    "location": metadata.get_location_string(),
                },
            },
        }
        payload = LoginResponseSerializer(response_data).data
        if trusted_device is not None:
            # Rotação: o dispositivo confiável consumido no login vira um novo
            # token, entregue uma única vez, junto da sessão.
            payload["trusted_device_token"] = trusted_device.plain_token
        return Response(payload, status=status.HTTP_200_OK)

    def _checar_limite_de_sessoes(self, user):
        limite = knox_settings.TOKEN_LIMIT_PER_USER
        if limite is None:
            return

        sessoes_ativas = AuthToken.objects.filter(responsavel=user, type=TokenType.TOKEN, expiry__gt=timezone.now()).count()

        if sessoes_ativas >= limite:
            raise APIError(AuthErrorCode.TOKEN_LIMIT_EXCEEDED, status_code=403)

    def _capturar_eventos_posthog(self, user, metadata, risco):
        # Nunca dentro da transação de emissão, e nunca com segredo: só campos scrubbed.
        with new_context():
            identify_context(str(user.pk))
            posthog.tag("is_staff", user.is_staff)
            posthog.tag("date_joined", user.date_joined.isoformat())
            capture(
                "user_logged_in",
                properties={
                    "device_type": metadata.device_type,
                    "os_name": metadata.os_name,
                    "country_code": metadata.country_code,
                    "app_version": metadata.app_version,
                    "is_suspicious": metadata.is_suspicious,
                    "risk_score": metadata.risk_score,
                },
            )

            if risco.is_suspicious:
                capture(
                    "suspicious_login_detected",
                    properties={
                        "risk_score": risco.risk_score,
                        "device_type": metadata.device_type,
                        "country_code": metadata.country_code,
                    },
                )


class ReauthenticateView(APIView):
    """Confirma a identidade da sessão atual (step-up auth).

    Só tokens de sessão passam por aqui: `session_only` faz o
    `TokenScopePermission` recusar API keys antes mesmo da senha ser checada.
    """

    permission_classes = [IsAuthenticated, TokenScopePermission]
    session_only = True
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "auth_reauthenticate"

    @document_reauthenticate
    def post(self, request):
        serializer = ReauthenticateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        # Com MFA ativo a senha sozinha não reautentica: o step-up só se completa
        # pelo par `/auth/reauthenticate/challenge/{start,verify}/`, que é quem
        # grava `reauthenticated_at`.
        if active_factors(request.user).exists():
            return Response({"methods": available_methods(request.user)}, status=status.HTTP_202_ACCEPTED)

        metadata = request.auth.metadata
        metadata.reauthenticated_at = timezone.now()
        metadata.save(update_fields=["reauthenticated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionScopedViewMixin:
    """Endpoints administrativos de credenciais recusam API keys de saída."""

    permission_classes = [IsAuthenticated, TokenScopePermission]
    session_only = True


class SessionViewSet(
    SessionScopedViewMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Gerencia as sessões (`TokenType.TOKEN`) do usuário autenticado.

    Nunca lista/edita API keys ou tokens de reset; `PATCH` só aceita
    `device_name`; `DELETE` revoga logicamente (nunca apaga).
    """

    serializer_class = SessionSerializer
    lookup_field = "uuid"
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]

    def get_queryset(self):
        return (
            AuthToken.objects.filter(responsavel=self.request.user, type=TokenType.TOKEN, revoked_at__isnull=True)
            .select_related("metadata")
            .order_by("-created_at")
        )

    @action(detail=False, methods=["get"])
    def current(self, request):
        serializer = self.get_serializer(request.auth)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        revoke_session(instance, actor=request.user)

        with new_context():
            identify_context(str(request.user.pk))
            capture("session_revoked")

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="revoke_all_except_current")
    def revoke_all_except_current(self, request):
        revoked_count = revoke_all_sessions(request.user, actor=request.user, exclude_uuid=request.auth.uuid)

        with new_context():
            identify_context(str(request.user.pk))
            capture("all_sessions_revoked_except_current", properties={"revoked_count": revoked_count})

        return Response({"revoked_count": revoked_count})


class LogoutView(APIView):
    """Revoga logicamente só a sessão atual; nunca API keys/reset."""

    permission_classes = [IsAuthenticated, TokenScopePermission]
    session_only = True

    def post(self, request):
        auth_token = request.auth
        if getattr(auth_token, "type", None) == TokenType.TOKEN:
            revoke_session(auth_token, actor=request.user)

        user_logged_out.send(sender=request.user.__class__, request=request, user=request.user)

        with new_context():
            identify_context(str(request.user.pk))
            capture("user_logged_out")

        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    """Revoga logicamente todas as sessões do usuário, incluindo a atual."""

    permission_classes = [IsAuthenticated, TokenScopePermission]
    session_only = True

    def post(self, request):
        revoked_count = revoke_all_sessions(request.user, actor=request.user)

        user_logged_out.send(sender=request.user.__class__, request=request, user=request.user)

        with new_context():
            identify_context(str(request.user.pk))
            capture("all_sessions_revoked", properties={"revoked_count": revoked_count})

        return Response(status=status.HTTP_204_NO_CONTENT)


class APIKeyViewSet(viewsets.ModelViewSet):
    """CRUD e ciclo de vida de API keys da organização do header.

    Uma API key nunca administra outras credenciais: `session_only` faz o
    `TokenScopePermission` recusá-la de saída, antes de `APIKeyPermissions`
    (as permissions humanas explícitas `view_apikey`/`add_apikey`/...).
    """

    lookup_field = "uuid"
    permission_classes = [
        IsAuthenticated,
        TenantPermission,
        TokenScopePermission,
        APIKeyPermissions,
        RecentAuthenticationPermission,
    ]
    session_only = True
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return (
            AuthToken.objects.filter(type=TokenType.API_KEY, organization_id=self.request.organizacao_id)
            .select_related("metadata", "responsavel")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return APIKeyWriteSerializer
        return APIKeySerializer

    def perform_destroy(self, instance):
        revoke_api_key(instance, actor=self.request.user)

    @document_api_key_create
    @require_recent_auth()
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @require_recent_auth()
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @document_api_key_rotate
    @require_recent_auth()
    @action(detail=True, methods=["post"])
    def rotate(self, request, uuid=None):
        instance = self.get_object()
        issued = rotate_api_key(instance, actor=request.user)
        # Plain token só aparece na criação/rotação; não é um campo persistido.
        issued.instance.token = issued.plain_token

        data = self.get_serializer(issued.instance).data
        return Response(data, status=status.HTTP_201_CREATED)

    @document_api_key_suspend
    @action(detail=True, methods=["post"])
    def suspend(self, request, uuid=None):
        instance = self.get_object()
        suspend_api_key(instance, actor=request.user, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(instance).data)

    @document_api_key_resume
    @action(detail=True, methods=["post"])
    def resume(self, request, uuid=None):
        instance = self.get_object()
        resume_api_key(instance, actor=request.user)
        return Response(self.get_serializer(instance).data)


@no_tenancy
class ReauthenticateChallengeStartView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        try:
            challenge = start_reauthentication(request.auth, request.data.get("type", ""))
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Método MFA indisponível."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"challenge_id": challenge.pk}, status=status.HTTP_201_CREATED)


@no_tenancy
class ReauthenticateChallengeVerifyView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        try:
            verify_reauthentication(request.auth, request.data.get("code", ""), request.data.get("type", ""))
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Código MFA inválido ou expirado."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


@no_tenancy
class MFAFactorSetupView(APIView):
    """Inicia o enrollment de um fator MFA para a sessão reautenticada."""

    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request, factor_type):
        try:
            factor_type = MFAFactorType(factor_type)
        except ValueError:
            return Response({"detail": "Tipo de fator inválido."}, status=status.HTTP_400_BAD_REQUEST)
        result = start_enrollment(request.user, factor_type)
        data = {"type": factor_type, "uri": result.uri}
        if result.plain_secret:
            data["secret"] = result.plain_secret
        return Response(data, status=status.HTTP_201_CREATED)


@no_tenancy
class MFAFactorConfirmView(APIView):
    """Confirma um fator MFA e revela recovery codes somente no primeiro fator."""

    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request, factor_type):
        code = request.data.get("code", "")
        try:
            result = confirm_enrollment(request.user, MFAFactorType(factor_type), code)
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Código ou fator inválido."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"type": factor_type, "recovery_codes": result.recovery_codes})


@no_tenancy
class MFAFactorDeleteView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def delete(self, request, factor_type):
        try:
            remove_factor(request.user, MFAFactorType(factor_type))
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Fator inválido."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


@no_tenancy
class MFARecoveryCodesView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request):
        if not active_factors(request.user).exists():
            return Response({"detail": "Nenhum fator MFA ativo."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"recovery_codes": regenerate_recovery_codes(request.user)})


@no_tenancy
class MFAChallengeStartView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if getattr(request.auth, "type", None) != TokenType.PRE_AUTH:
            return Response({"detail": "Pré-autenticação obrigatória."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            challenge = start_login_challenge(request.auth, request.data.get("type", ""))
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Método MFA indisponível."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"challenge_id": challenge.pk}, status=status.HTTP_201_CREATED)


@no_tenancy
class MFAChallengeVerifyView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if getattr(request.auth, "type", None) != TokenType.PRE_AUTH:
            return Response({"detail": "Pré-autenticação obrigatória."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            result = verify_login_challenge(
                request.auth,
                request.data.get("code", ""),
                request.data.get("type", ""),
                trust_device=bool(request.data.get("trust_device")),
                metadata=build_token_metadata(request, request.data),
            )
        except (ValueError, MFAFactor.DoesNotExist):
            return Response({"detail": "Código MFA inválido ou expirado."}, status=status.HTTP_400_BAD_REQUEST)
        data = {"token": result.token, "expiry": result.instance.expiry, "session": {"id": result.instance.digest, "type": result.instance.type}}
        if result.trusted_device_token:
            data["trusted_device_token"] = result.trusted_device_token
        return Response(data)


@no_tenancy
class TrustedDeviceListView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def get(self, request):
        devices = TrustedDevice.objects.filter(user=request.user, revoked_at__isnull=True, expires_at__gt=timezone.now())
        return Response(
            [
                {"id": item.pk, "name": item.name, "created_at": item.created_at, "last_used_at": item.last_used_at, "expires_at": item.expires_at}
                for item in devices
            ]
        )


@no_tenancy
class TrustedDeviceDetailView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def delete(self, request, pk):
        updated = TrustedDevice.objects.filter(pk=pk, user=request.user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT if updated else status.HTTP_404_NOT_FOUND)


@no_tenancy
class MFAAdminResetView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request):
        if not request.user.has_perm("usuarios.can_reset_mfa_usuario"):
            return Response(status=status.HTTP_403_FORBIDDEN)
        from apps.usuarios.models import Usuario

        try:
            target = Usuario.objects.get(pk=request.data.get("user_id"))
            reset_user_mfa(target=target, actor=request.user, reason=request.data.get("reason", ""))
        except Usuario.DoesNotExist:
            return Response({"detail": "Usuário não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)
