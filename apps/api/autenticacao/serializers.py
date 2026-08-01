from django.contrib.auth import authenticate, get_user_model

from rest_framework import serializers

from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Organizacao, Vinculo

from .errors import AuthErrorCode
from .scope_delegation import validate_scope_delegation


class LoginSerializer(serializers.Serializer):
    """Valida credenciais e os metadados opcionais de dispositivo enviados no login."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    device_name = serializers.CharField(required=False, allow_blank=True, default="")
    app_version = serializers.CharField(required=False, allow_blank=True, default="")
    fcm_token = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )

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
    """Confirma a senha da sessão atual.

    O segundo fator não é verificado aqui: com MFA ativo a view responde 202 e o
    step-up se completa pelo par `/auth/reauthenticate/challenge/{start,verify}/`,
    que é desafio-resposta e não aceita um código solto no corpo do POST.
    """

    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user

        if not user.check_password(attrs["password"]):
            raise APIError(AuthErrorCode.INVALID_CREDENTIALS, status_code=401)

        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """Pedido de redefinição. Aceita qualquer e-mail bem formado, de propósito.

    Não valida a existência da conta: a resposta precisa ser idêntica para
    e-mail cadastrado e não cadastrado, senão o endpoint vira um oráculo que
    diz quem tem conta aqui.
    """

    email = serializers.EmailField()


class PasswordConfirmationMixin:
    """Exige que as duas senhas digitadas sejam iguais."""

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirmation"]:
            raise serializers.ValidationError({"new_password_confirmation": "As senhas não conferem."})
        return attrs


class PasswordResetConfirmSerializer(PasswordConfirmationMixin, serializers.Serializer):
    """Consome o token do e-mail e define a nova senha."""

    token = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirmation = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordChangeSerializer(PasswordConfirmationMixin, serializers.Serializer):
    """Troca de senha por quem já está autenticado e reautenticado.

    A senha atual não é pedida aqui: `@require_recent_auth` já garante que a
    identidade foi confirmada há pouco, e pedir de novo só treinaria o usuário
    a digitar a senha em mais um formulário.
    """

    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirmation = serializers.CharField(write_only=True, trim_whitespace=False)


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


class APIKeySerializer(serializers.Serializer):
    """Saída de uma API key. Nunca inclui `digest`, `token_key` ou o plain token."""

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    responsavel = serializers.PrimaryKeyRelatedField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    scopes = serializers.ListField(child=serializers.CharField(), read_only=True)
    expiry = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    revoked_at = serializers.DateTimeField(read_only=True, allow_null=True)
    suspended_at = serializers.DateTimeField(read_only=True, allow_null=True)
    suspension_reason = serializers.CharField(read_only=True)
    status = serializers.SerializerMethodField()

    def get_status(self, obj):
        if obj.revoked_at:
            return "revoked"
        if obj.suspended_at:
            return "suspended"
        return "active"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        plain_token = getattr(instance, "token", None)
        if plain_token is not None:
            data["token"] = plain_token
        return data


class APIKeyWriteSerializer(APIKeySerializer):
    """Create/PATCH: valida vínculo do responsável e delega scopes com autoridade do ator."""

    name = serializers.CharField()
    responsavel = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.none())
    scopes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    expiry = serializers.DateTimeField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsavel"].queryset = get_user_model().objects.all()

    def validate_scopes(self, scopes):
        request = self.context["request"]
        return list(validate_scope_delegation(request.user, scopes))

    def validate_responsavel(self, responsavel):
        organizacao_id = self.context["request"].organizacao_id
        vinculo_ativo = Vinculo.objects.filter(organizacao_id=organizacao_id, usuario=responsavel, is_active=True).exists()
        if not vinculo_ativo:
            raise APIError(
                OrganizationErrorCode.MEMBERSHIP_REQUIRED,
                status_code=422,
                field="responsavel",
            )
        return responsavel

    def create(self, validated_data):
        from .services import create_api_key

        request = self.context["request"]
        issued = create_api_key(
            responsavel=validated_data["responsavel"],
            created_by=request.user,
            name=validated_data["name"],
            scopes=validated_data.get("scopes", []),
            # A permission resolve a organização pelo cache, que carrega só o id;
            # `issue_token` precisa da instância para validar o vínculo do responsável.
            organization=Organizacao.objects.get(pk=request.organizacao_id),
            expiry=validated_data.get("expiry"),
        )
        instance = issued.instance
        instance.token = issued.plain_token
        return instance

    def update(self, instance, validated_data):
        from .services import update_api_key

        return update_api_key(
            instance,
            actor=self.context["request"].user,
            name=validated_data.get("name"),
            responsavel=validated_data.get("responsavel"),
            scopes=validated_data.get("scopes"),
        )
