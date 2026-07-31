from django.contrib.auth import authenticate
from django.utils import timezone

from rest_framework import serializers

import serpy

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.base.serializers import BaseModelSerpySerializer
from apps.api.core.errors import APIError


class AuthTokenSerializer(BaseModelSerpySerializer):
    id = serpy.StrField(attr="digest")
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
    """Valida credenciais de login aceitando o identificador de e-mail atual."""

    email = serializers.EmailField(required=False)
    username = serializers.CharField(required=False, write_only=True)
    password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        identifier = attrs.get("email") or attrs.get("username")
        if not identifier:
            raise APIError(
                AuthErrorCode.INVALID_CREDENTIALS,
                status_code=401,
                message="E-mail ou senha inválidos.",
            )

        user = authenticate(
            request=self.context.get("request"),
            username=identifier,
            password=attrs["password"],
        )
        if user is None:
            raise APIError(
                AuthErrorCode.INVALID_CREDENTIALS,
                status_code=401,
                message="E-mail ou senha inválidos.",
            )

        attrs["user"] = user
        return attrs


class LoginResponseSerializer(serializers.Serializer):
    """Serializa a sessão recém-emitida sem reutilizar o segredo do token."""

    token = serializers.CharField()
    expiry = serializers.DateTimeField(allow_null=True)
    session = serializers.DictField()
    device = serializers.DictField()


class ReauthenticateSerializer(serializers.Serializer):
    """Confirma a senha da identidade já autenticada na sessão atual."""

    password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        user = self.context["user"]
        if not user.check_password(attrs["password"]):
            raise APIError(
                AuthErrorCode.INVALID_CREDENTIALS,
                status_code=401,
                message="E-mail ou senha inválidos.",
            )

        return attrs
