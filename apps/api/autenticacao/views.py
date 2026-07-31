from datetime import timedelta

from django.contrib.auth.signals import user_logged_in
from django.db.models import Q
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

import posthog
from knox.models import get_token_model
from knox.views import LoginView as KnoxLoginView
from posthog import capture, identify_context, new_context

from apps.api.core.errors import APIError
from apps.api.core.route_markers import no_tenancy

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
from .models import MFAFactor, MFAFactorType, TokenMetaData, TokenType, TrustedDevice
from .permissions import RecentAuthenticationPermission
from .recent_auth import require_recent_auth
from .serializers import AuthTokenSerializer as CustomAuthTokenSerializer
from .serializers import LoginResponseSerializer, LoginSerializer, ReauthenticateSerializer
from .services import issue_token
from .utils import build_token_metadata

# class LoginView(KnoxLoginView):
#     permission_classes = (AllowAny,)
#
#     def post(self, request, format=None):
#         serializer = AuthTokenSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         user = serializer.validated_data["user"]
#         login(request, user)
#         return super().post(request, format=None)


class LoginView(KnoxLoginView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        metadata_input = build_token_metadata(request, request.data)
        trusted_device = (
            consume_trusted_device(user, request.data.get("trusted_device_token", "")) if request.data.get("trusted_device_token") else None
        )
        if active_factors(user).exists() and trusted_device is None:
            issued = issue_token(
                responsavel=user,
                token_type=TokenType.PRE_AUTH,
                expiry=timezone.now() + PRE_AUTH_LIFETIME,
                metadata_input=metadata_input,
            )
            return Response({"pre_auth_token": issued.plain_token, "methods": available_methods(user)}, status=status.HTTP_202_ACCEPTED)

        token_limit_per_user = self.get_token_limit_per_user()
        if token_limit_per_user is not None:
            now = timezone.now()
            token = user.auth_token_set.filter(type=TokenType.TOKEN).filter(Q(expiry__isnull=True) | Q(expiry__gt=now))
            if token.count() >= token_limit_per_user:
                raise APIError(
                    AuthErrorCode.TOO_MANY_ATTEMPTS,
                    status_code=status.HTTP_403_FORBIDDEN,
                    message="Limite de sessões ativas atingido.",
                )

        metadata_input["reauthenticated_at"] = timezone.now()
        issued = issue_token(
            responsavel=user,
            token_type=TokenType.TOKEN,
            expiry=self.get_token_ttl(),
            metadata_input=metadata_input,
        )
        instance = issued.instance
        plain_token = issued.plain_token
        user_logged_in.send(sender=user.__class__, request=request, user=user)
        metadata = instance.metadata

        # verifica se há comportamento suspeito
        self.check_suspicious_activity(user, metadata)

        # PostHog: identifica o usuário e captura o evento de login
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

        response_serializer = LoginResponseSerializer(
            {
                "token": plain_token,
                "expiry": instance.expiry,
                "session": {"id": instance.digest, "type": instance.type},
                "device": {
                    "type": metadata.device_type,
                    "name": metadata.device_name or f"{metadata.device_brand} {metadata.device_model}".strip() or "Dispositivo desconhecido",
                    "location": metadata.get_location_string(),
                },
            }
        )
        response = response_serializer.data
        if trusted_device:
            response["trusted_device_token"] = trusted_device.plain_token
        return Response(response)

    def check_suspicious_activity(self, user, new_metadata):
        """Verifica atividades suspeitas comparando com tokens anteriores"""

        # pega tokens recentes do usuário (últimos 7 dias)

        recent_tokens = (
            TokenMetaData.objects.filter(token__responsavel=user, first_used__gte=timezone.now() - timedelta(days=7))
            .exclude(token=new_metadata.token)
            .select_related("token")
        )

        if not recent_tokens.exists():
            return  # primeiro login, sem com o que comparar

        # verifica mudança drástica de localização
        for old_token in recent_tokens:
            if old_token.country_code and new_metadata.country_code:
                if old_token.country_code != new_metadata.country_code:
                    new_metadata.mark_as_suspicious(f"Login de país diferente: {old_token.country} → {new_metadata.country}")
                    new_metadata.risk_score = 50
                    new_metadata.save()

                    # PostHog: captura evento de login suspeito
                    with new_context():
                        identify_context(str(user.pk))
                        capture(
                            "suspicious_login_detected",
                            properties={
                                "previous_country_code": old_token.country_code,
                                "new_country_code": new_metadata.country_code,
                                "risk_score": new_metadata.risk_score,
                                "device_type": new_metadata.device_type,
                            },
                        )

                    # aqui você pode enviar notificação ao usuário
                    # send_security_alert(user, new_metadata)
                    break


@no_tenancy
class ReauthenticateView(APIView):
    """Atualiza a confirmação recente de senha da sessão atual."""

    permission_classes = (IsAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "auth_reauthenticate"

    def post(self, request):
        auth_token = getattr(request, "auth", None)
        if auth_token is None or auth_token.type != TokenType.TOKEN:
            raise APIError(
                AuthErrorCode.REAUTHENTICATION_REQUIRED,
                status_code=status.HTTP_403_FORBIDDEN,
                message="Uma sessão comum é necessária para reautenticar.",
            )

        serializer = ReauthenticateSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)

        if active_factors(request.user).exists():
            return Response({"methods": available_methods(request.user)}, status=status.HTTP_202_ACCEPTED)

        auth_token.metadata.reauthenticated_at = timezone.now()
        auth_token.metadata.save(update_fields=["reauthenticated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class AuthTokenViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CustomAuthTokenSerializer

    def get_queryset(self):
        return get_token_model().objects.filter(responsavel=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["get"])
    def current(self, request):
        if not hasattr(request, "auth") or not request.auth:
            return Response({"detail": "Token não encontrado"}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(request.auth)
        return Response(serializer.data)

    @action(detail=True, methods=["delete"])
    def revoke(self, request, pk=None):
        try:
            token = self.get_queryset().get(digest=pk)

            # Não permite revogar o token atual
            if hasattr(request, "auth") and request.auth.digest == token.digest:
                return Response(
                    {"detail": "Você não pode revogar o token atual. Use o endpoint de logout."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token.delete()

            # PostHog: captura revogação de token individual
            with new_context():
                identify_context(str(request.user.pk))
                capture("token_revoked")

            return Response({"detail": "Token revogado com sucesso"}, status=status.HTTP_204_NO_CONTENT)
        except get_token_model().DoesNotExist:
            return Response({"detail": "Token não encontrado"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["delete"])
    def revoke_all_except_current(self, request):
        current_digest = request.auth.digest if hasattr(request, "auth") else None

        deleted_count = self.get_queryset().exclude(digest=current_digest).delete()[0]

        # PostHog: captura revogação de todos os tokens
        with new_context():
            identify_context(str(request.user.pk))
            capture(
                "all_tokens_revoked",
                properties={
                    "revoked_count": deleted_count,
                },
            )

        return Response({"detail": f"{deleted_count} token(s) revogado(s) com sucesso"})
