from datetime import timedelta

from django.contrib.auth.signals import user_logged_in
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

import posthog
from knox.models import get_token_model
from knox.views import LoginView as KnoxLoginView
from posthog import capture, identify_context, new_context

from .models import TokenMetaData
from .serializers import AuthTokenSerializer as CustomAuthTokenSerializer
from .utils import get_client_ip, get_geolocation_data, parse_user_agent

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

    def post(self, request):
        # valida credenciais
        serializer = AuthTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        # cria o token Knox
        token_limit_per_user = self.get_token_limit_per_user()
        if token_limit_per_user is not None:
            now = timezone.now()
            token = user.auth_token_set.filter(expiry__gt=now)
            if token.count() >= token_limit_per_user:
                return Response({"error": "Maximum amount of tokens allowed per user exceeded."}, status=status.HTTP_403_FORBIDDEN)

        instance, plain_token = self.create_token(user)
        user_logged_in.send(sender=user.__class__, request=request, user=user)
        response = self.get_post_response(request, plain_token, instance)

        # extrai informações do dispositivo
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_info = parse_user_agent(user_agent)

        # extrai informações de localização
        ip_address = get_client_ip(request)
        geo_data = get_geolocation_data(ip_address)

        # dados enviados pelo cliente (opcionais)
        device_name = request.data.get("device_name", "")
        app_version = request.data.get("app_version", "")
        fcm_token = request.data.get("fcm_token", "")

        # Cria os metadados
        metadata = TokenMetaData.objects.create(
            token=instance,
            # Dispositivo
            device_name=device_name,
            device_type=device_info.get("device_type", "unknown"),
            device_brand=device_info.get("device_brand", ""),
            device_model=device_info.get("device_model", ""),
            os_name=device_info.get("os_name", ""),
            os_version=device_info.get("os_version", ""),
            browser_name=device_info.get("browser_name", ""),
            browser_version=device_info.get("browser_version", ""),
            user_agent=user_agent,
            # localização
            ip_address=ip_address,
            country=geo_data.get("country", ""),
            country_code=geo_data.get("country_code", ""),
            region=geo_data.get("region", ""),
            city=geo_data.get("city", ""),
            latitude=geo_data.get("latitude"),
            longitude=geo_data.get("longitude"),
            timezone=geo_data.get("timezone", ""),
            isp=geo_data.get("isp", ""),
            # Outros
            app_version=app_version,
            fcm_token=fcm_token,
        )

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

        # Adiciona informações do dispositivo na resposta
        response.data["device"] = {
            "type": metadata.device_type,
            "name": metadata.device_name or f"{metadata.device_brand} {metadata.device_model}".strip() or "Dispositivo desconhecido",
            "location": metadata.get_location_string(),
        }

        return response

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

    def create_token(self, user):
        token_prefix = self.get_token_prefix()
        return get_token_model().objects.create(responsavel=user, expiry=self.get_token_ttl(), prefix=token_prefix)


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
