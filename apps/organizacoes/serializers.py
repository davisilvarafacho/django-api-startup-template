from rest_framework import serializers

from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Time, Vinculo
from apps.organizacoes.organizations import Organizacoes
from apps.organizacoes.teams import Times
from apps.workspaces.serializers import WorkspaceCompactSerializer


def _validar_papel_ator(request) -> bool:
    """API keys usam scopes; sessões humanas também revalidam papel pessoal."""
    return getattr(getattr(request, "auth", None), "type", None) != TokenType.API_KEY


class UsuarioResumoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    nome = serializers.CharField(source="get_full_name")


class OrganizacaoSerializer(serializers.ModelSerializer):
    papel = serializers.SerializerMethodField()
    current_workspace = serializers.SerializerMethodField()

    class Meta:
        model = Organizacao
        fields = ["id", "nome", "slug", "papel", "current_workspace"]
        read_only_fields = ["id", "papel", "current_workspace"]

    def get_papel(self, obj):
        if self.context.get("include_personal_role") is False:
            return None

        vinculos_por_organizacao = self.context.get("vinculos_por_organizacao", {})
        vinculo = vinculos_por_organizacao.get(obj.id)
        if vinculo:
            return vinculo.papel

        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None

        vinculo = Vinculo.objects.filter(organizacao=obj, usuario=request.user, is_active=True).first()
        return vinculo.papel if vinculo else None

    def get_current_workspace(self, obj):
        if self.context.get("include_personal_role") is False:
            return None

        vinculos_por_organizacao = self.context.get("vinculos_por_organizacao", {})
        vinculo = vinculos_por_organizacao.get(obj.id)
        if vinculo is None:
            request = self.context.get("request")
            if not request or not request.user.is_authenticated:
                return None
            vinculo = (
                Vinculo.objects.select_related("current_workspace")
                .filter(
                    organizacao=obj,
                    usuario=request.user,
                    is_active=True,
                )
                .first()
            )

        workspace = vinculo.current_workspace if vinculo is not None else None
        return WorkspaceCompactSerializer(workspace).data if workspace is not None else None


class OrganizacaoEmailFaturamentoSerializer(serializers.ModelSerializer):
    email_faturamento = serializers.EmailField(allow_null=True, required=True)

    class Meta:
        model = Organizacao
        fields = ["id", "email_faturamento"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if "email_faturamento" not in attrs:
            raise serializers.ValidationError(
                {"email_faturamento": "Este campo é obrigatório."},
                code="required",
            )
        return attrs

    def update(self, instance, validated_data):
        return Organizacoes.atualizar_email_faturamento(
            instance,
            email_faturamento=validated_data["email_faturamento"],
            ator=self.context["request"].user,
        )


class EncerramentoAgendadoResponseSerializer(serializers.Serializer):
    scheduled_for = serializers.DateTimeField()


class TimeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Time
        fields = ["id", "nome"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        organizacao_id = validated_data.pop("organizacao_id")
        organizacao = Organizacao.objects.get(pk=organizacao_id)
        request = self.context["request"]
        return Times.criar(
            organizacao=organizacao,
            dados=validated_data,
            ator=request.user,
            validar_papel_ator=_validar_papel_ator(request),
        )

    def update(self, instance, validated_data):
        request = self.context["request"]
        return Times.atualizar(
            instance,
            dados=validated_data,
            ator=request.user,
            validar_papel_ator=_validar_papel_ator(request),
        )


class VinculoSerializer(serializers.ModelSerializer):
    usuario = UsuarioResumoSerializer(read_only=True)
    times = serializers.PrimaryKeyRelatedField(many=True, queryset=Time.objects.none(), required=False)
    times_detalhe = TimeSerializer(source="times", many=True, read_only=True)

    class Meta:
        model = Vinculo
        fields = ["id", "usuario", "papel", "times", "times_detalhe", "is_active"]
        read_only_fields = ["id", "usuario", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)
        if tenant is not None:
            self.fields["times"].queryset = Time.objects.filter(organizacao_id=tenant.organization_id, is_active=True)

    def validate_papel(self, papel):
        request = self.context["request"]
        if not _validar_papel_ator(request):
            return papel
        if papel > request.tenant.role:
            raise APIError(
                OrganizationErrorCode.ROLE_INSUFFICIENT,
                status_code=422,
                field="papel",
                message="Você não pode conceder um papel acima do seu.",
            )
        return papel

    def update(self, instance, validated_data):
        request = self.context["request"]
        return Vinculos.atualizar_vinculo(
            instance,
            dados=validated_data,
            ator=request.user,
            validar_papel_ator=_validar_papel_ator(request),
        )


class ConviteSerializer(serializers.ModelSerializer):
    convidado_por = UsuarioResumoSerializer(read_only=True)
    pendente = serializers.BooleanField(read_only=True)
    expirado = serializers.BooleanField(read_only=True)

    class Meta:
        model = Convite
        fields = [
            "id",
            "email",
            "papel",
            "expira_em",
            "convidado_por",
            "aceito_em",
            "pendente",
            "expirado",
        ]
        read_only_fields = ["id", "convidado_por", "aceito_em", "pendente", "expirado"]

    def update(self, instance, validated_data):
        request = self.context["request"]
        return Vinculos.atualizar_convite(
            instance,
            dados=validated_data,
            ator=request.user,
            validar_papel_ator=_validar_papel_ator(request),
        )


class ConviteCreateSerializer(ConviteSerializer):
    token = serializers.CharField(read_only=True)

    class Meta(ConviteSerializer.Meta):
        fields = [*ConviteSerializer.Meta.fields, "token"]

    def validate_papel(self, papel):
        request = self.context["request"]
        if not _validar_papel_ator(request):
            return papel
        if papel > request.tenant.role:
            raise APIError(
                OrganizationErrorCode.ROLE_INSUFFICIENT,
                status_code=422,
                field="papel",
                message="Você não pode convidar alguém para um papel acima do seu.",
            )
        return papel

    def create(self, validated_data):
        organizacao_id = validated_data.pop("organizacao_id")
        organizacao = Organizacao.objects.get(pk=organizacao_id)
        request = self.context["request"]
        return Vinculos.criar_convite(
            organizacao=organizacao,
            ator=request.user,
            validar_papel_ator=_validar_papel_ator(request),
            **validated_data,
        )


class AceitarConviteSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, token):
        try:
            convite = Convite.objects.select_related("organizacao").get(token=token)
        except Convite.DoesNotExist as exc:
            raise APIError(OrganizationErrorCode.INVITATION_INVALID, status_code=422, field="token") from exc

        if not convite.pendente:
            raise APIError(OrganizationErrorCode.INVITATION_EXPIRED, status_code=422, field="token")

        usuario = self.context["request"].user
        if convite.email.lower() != usuario.email.lower():
            raise APIError(OrganizationErrorCode.INVITATION_EMAIL_MISMATCH, status_code=422, field="token")

        request_organization = getattr(self.context["request"], "organizacao", None)
        if request_organization is not None and convite.organizacao_id != request_organization.id:
            raise APIError(
                OrganizationErrorCode.TENANT_MISMATCH,
                status_code=409,
                field="token",
            )

        return convite

    def save(self, **kwargs):
        convite = self.validated_data["token"]
        usuario = self.context["request"].user
        return Vinculos.aceitar_convite(convite, usuario)


class AceitarConviteResponseSerializer(serializers.Serializer):
    organizacao = OrganizacaoSerializer()
    vinculo = VinculoSerializer()
