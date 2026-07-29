from django.db import transaction

from rest_framework import serializers

from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo


class UsuarioResumoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    nome = serializers.CharField(source="get_full_name")


class OrganizacaoSerializer(serializers.ModelSerializer):
    papel = serializers.SerializerMethodField()

    class Meta:
        model = Organizacao
        fields = ["id", "nome", "slug", "papel"]
        read_only_fields = ["id", "papel"]

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

        vinculo = Vinculo.objects.filter(organizacao=obj, usuario=request.user, ativo=True).first()
        return vinculo.papel if vinculo else None

    def create(self, validated_data):
        usuario = self.context["request"].user
        with transaction.atomic():
            organizacao = Organizacao.objects.create(**validated_data)
            Vinculo.objects.create(
                organizacao=organizacao,
                usuario=usuario,
                papel=Papel.PROPRIETARIO,
            )
        return organizacao


class TimeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Time
        fields = ["id", "nome"]
        read_only_fields = ["id"]


class VinculoSerializer(serializers.ModelSerializer):
    usuario = UsuarioResumoSerializer(read_only=True)
    times = serializers.PrimaryKeyRelatedField(many=True, queryset=Time.objects.none(), required=False)
    times_detalhe = TimeSerializer(source="times", many=True, read_only=True)

    class Meta:
        model = Vinculo
        fields = ["id", "usuario", "papel", "times", "times_detalhe", "ativo"]
        read_only_fields = ["id", "usuario", "ativo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        organizacao = getattr(request, "organizacao", None)
        if organizacao is not None:
            self.fields["times"].queryset = Time.objects.filter(organizacao=organizacao, ativo=True)

    def validate_papel(self, papel):
        request = self.context["request"]
        if papel > request.vinculo.papel:
            raise APIError(
                OrganizationErrorCode.ROLE_INSUFFICIENT,
                status_code=422,
                field="papel",
                message="Você não pode conceder um papel acima do seu.",
            )
        return papel


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


class ConviteCreateSerializer(ConviteSerializer):
    token = serializers.CharField(read_only=True)

    class Meta(ConviteSerializer.Meta):
        fields = [*ConviteSerializer.Meta.fields, "token"]

    def validate_papel(self, papel):
        request = self.context["request"]
        if papel > request.vinculo.papel:
            raise APIError(
                OrganizationErrorCode.ROLE_INSUFFICIENT,
                status_code=422,
                field="papel",
                message="Você não pode convidar alguém para um papel acima do seu.",
            )
        return papel


class AceitarConviteSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, token):
        try:
            convite = Convite.objects.select_related("organizacao").get(token=token)
        except Convite.DoesNotExist as exc:
            raise APIError(
                OrganizationErrorCode.INVITATION_INVALID, status_code=422, field="token"
            ) from exc

        if not convite.pendente:
            raise APIError(OrganizationErrorCode.INVITATION_EXPIRED, status_code=422, field="token")

        usuario = self.context["request"].user
        if convite.email.lower() != usuario.email.lower():
            raise APIError(
                OrganizationErrorCode.INVITATION_EMAIL_MISMATCH, status_code=422, field="token"
            )

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
        return convite.aceitar(usuario)
