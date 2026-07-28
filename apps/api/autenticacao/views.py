from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.utils import timezone

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import posthog
from knox.models import get_token_model
from knox.settings import knox_settings
from posthog import capture, identify_context, new_context

from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .models import TokenType
from .permissions import TokenScopePermission
from .risk import evaluate_login_risk
from .serializers import LoginResponseSerializer, LoginSerializer, ReauthenticateSerializer, SessionSerializer
from .services import issue_token, revoke_all_sessions, revoke_session
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

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        self._checar_limite_de_sessoes(user)

        metadata_input = build_token_metadata(request, serializer.validated_data)
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
                    "name": (
                        metadata.device_name
                        or f"{metadata.device_brand} {metadata.device_model}".strip()
                        or "Dispositivo desconhecido"
                    ),
                    "location": metadata.get_location_string(),
                },
            },
        }
        return Response(LoginResponseSerializer(response_data).data, status=status.HTTP_200_OK)

    def _checar_limite_de_sessoes(self, user):
        limite = knox_settings.TOKEN_LIMIT_PER_USER
        if limite is None:
            return

        sessoes_ativas = AuthToken.objects.filter(
            responsavel=user, type=TokenType.TOKEN, expiry__gt=timezone.now()
        ).count()

        if sessoes_ativas >= limite:
            raise APIError(AuthErrorCode.TOKEN_LIMIT_EXCEEDED, status_code=403)

    def _capturar_eventos_posthog(self, user, metadata, risco):
        # Nunca dentro da transação de emissão, e nunca com segredo: só campos scrubbed.
        with new_context():
            identify_context(str(user.pk))
            posthog.tag('is_staff', user.is_staff)
            posthog.tag('date_joined', user.date_joined.isoformat())
            capture('user_logged_in', properties={
                'device_type': metadata.device_type,
                'os_name': metadata.os_name,
                'country_code': metadata.country_code,
                'app_version': metadata.app_version,
                'is_suspicious': metadata.is_suspicious,
                'risk_score': metadata.risk_score,
            })

            if risco.is_suspicious:
                capture('suspicious_login_detected', properties={
                    'risk_score': risco.risk_score,
                    'device_type': metadata.device_type,
                    'country_code': metadata.country_code,
                })


class ReauthenticateView(APIView):
    """Confirma a identidade da sessão atual (step-up auth).

    Só tokens de sessão passam por aqui: `session_only` faz o
    `TokenScopePermission` recusar API keys antes mesmo da senha ser checada.
    """

    permission_classes = [IsAuthenticated, TokenScopePermission]
    session_only = True

    def post(self, request):
        serializer = ReauthenticateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

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
            capture('session_revoked')

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="revoke_all_except_current")
    def revoke_all_except_current(self, request):
        revoked_count = revoke_all_sessions(request.user, actor=request.user, exclude_uuid=request.auth.uuid)

        with new_context():
            identify_context(str(request.user.pk))
            capture('all_sessions_revoked_except_current', properties={'revoked_count': revoked_count})

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
            capture('user_logged_out')

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
            capture('all_sessions_revoked', properties={'revoked_count': revoked_count})

        return Response(status=status.HTTP_204_NO_CONTENT)
