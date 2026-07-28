from django.contrib.auth import authenticate
from django.utils import timezone

from rest_framework import serializers

import serpy

from apps.api.base.serializers import BaseModelSerpySerializer
from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .recent_auth import user_has_mfa_enabled, verify_mfa_code


class AuthTokenSerializer(BaseModelSerpySerializer):
    digest = serpy.StrField()
    token_key = serpy.StrField()
    created = serpy.Field()
    expiry = serpy.Field()
    # is_current = serpy.MethodField()
    days_until_expiry = serpy.MethodField()
    device_info = serpy.MethodField()

    def get_is_current(self, obj):
        """Verifica se é o token da requisição atual."""
        request = self.context.get("request")
        if not request or not hasattr(request, "auth"):
            return False
        return request.auth.digest == obj.digest

    def get_days_until_expiry(self, obj):
        """Calcula dias restantes até expirar."""
        if not obj.expiry:
            return None
        delta = obj.expiry - timezone.now()
        return max(delta.days, 0)

    def get_device_info(self, obj):
        """Você pode estender para guardar info do dispositivo."""
        # Por padrão, Knox não guarda isso
        # Veja implementação alternativa abaixo
        return


class LoginSerializer(serializers.Serializer):
    """Valida credenciais e os metadados opcionais de dispositivo enviados no login."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    device_name = serializers.CharField(required=False, allow_blank=True, default="")
    app_version = serializers.CharField(required=False, allow_blank=True, default="")
    fcm_token = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        user = authenticate(username=attrs["email"], password=attrs["password"])

        if user is None:
            raise APIError(AuthErrorCode.INVALID_CREDENTIALS, status_code=401)

        if not user.is_active:
            raise APIError(AuthErrorCode.USER_INACTIVE, status_code=401)

        attrs["user"] = user
        return attrs


class SessionDeviceSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField()
    location = serializers.CharField()


class LoginSessionSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    device = SessionDeviceSerializer()


class LoginResponseSerializer(serializers.Serializer):
    """Formato de saída do login: `token` só aparece aqui, nunca em listagens."""

    token = serializers.CharField()
    expiry = serializers.DateTimeField(allow_null=True)
    session = LoginSessionSerializer()


class ReauthenticateSerializer(serializers.Serializer):
    """Confirma a identidade da sessão atual (senha e, se aplicável, MFA)."""

    password = serializers.CharField(write_only=True, trim_whitespace=False)
    mfa_code = serializers.CharField(required=False, allow_blank=True, write_only=True, default="")

    def validate(self, attrs):
        user = self.context["request"].user

        if not user.check_password(attrs["password"]):
            raise APIError(AuthErrorCode.INVALID_CREDENTIALS, status_code=401)

        if user_has_mfa_enabled(user):
            verify_mfa_code(user, attrs.get("mfa_code", ""))

        return attrs
