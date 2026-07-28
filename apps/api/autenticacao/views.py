from django.contrib.auth.signals import user_logged_in
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

import posthog
from knox.models import get_token_model
from knox.settings import knox_settings
from posthog import capture, identify_context, new_context

from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .models import TokenType
from .risk import evaluate_login_risk
from .serializers import AuthTokenSerializer as CustomAuthTokenSerializer
from .serializers import LoginResponseSerializer, LoginSerializer
from .services import issue_token
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


class AuthTokenViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CustomAuthTokenSerializer

    def get_queryset(self):
        return AuthToken.objects.filter(responsavel=self.request.user).order_by("-created_at")

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
                capture('token_revoked')

            return Response({"detail": "Token revogado com sucesso"}, status=status.HTTP_204_NO_CONTENT)
        except AuthToken.DoesNotExist:
            return Response({"detail": "Token não encontrado"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["delete"])
    def revoke_all_except_current(self, request):
        current_digest = request.auth.digest if hasattr(request, "auth") else None

        deleted_count = self.get_queryset().exclude(digest=current_digest).delete()[0]

        # PostHog: captura revogação de todos os tokens
        with new_context():
            identify_context(str(request.user.pk))
            capture('all_tokens_revoked', properties={
                'revoked_count': deleted_count,
            })

        return Response({"detail": f"{deleted_count} token(s) revogado(s) com sucesso"})
