from django.contrib.auth import authenticate

from rest_framework import serializers

from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .recent_auth import user_has_mfa_enabled, verify_mfa_code


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


class SessionSerializer(serializers.Serializer):
    """Sessão de login. Nunca inclui `digest`, `token_key` ou o plain token."""

    uuid = serializers.UUIDField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    expiry = serializers.DateTimeField(read_only=True, allow_null=True)
    device_name = serializers.CharField(source="metadata.device_name", required=False, allow_blank=True)
    device_type = serializers.CharField(source="metadata.device_type", read_only=True)
    location = serializers.SerializerMethodField()
    last_used = serializers.DateTimeField(source="metadata.last_used", read_only=True)
    risk_score = serializers.IntegerField(source="metadata.risk_score", read_only=True)
    is_suspicious = serializers.BooleanField(source="metadata.is_suspicious", read_only=True)
    is_current = serializers.SerializerMethodField()

    def get_location(self, obj):
        return obj.metadata.get_location_string()

    def get_is_current(self, obj):
        request = self.context.get("request")
        auth_token = getattr(request, "auth", None) if request else None
        return bool(auth_token and auth_token.digest == obj.digest)

    def update(self, instance, validated_data):
        device_name = validated_data.get("metadata", {}).get("device_name")
        if device_name is not None:
            instance.metadata.device_name = device_name
            instance.metadata.save(update_fields=["device_name"])
        return instance
