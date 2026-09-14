from rest_framework import serializers

from drf_spectacular.utils import extend_schema_field

from apps.assinaturas.access_policies import MotivoRestricao, StatusAcesso
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, TipoAlteracaoAssinatura, VersaoPlano
from apps.organizacoes.models import Papel


class SituacaoAcessoResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=StatusAcesso.choices)
    motivos = serializers.ListField(child=serializers.ChoiceField(choices=MotivoRestricao.choices))
    regularizar_ate = serializers.DateTimeField(allow_null=True)


class PrecoPlanoCatalogoSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrecoPlano
        fields = ["id", "periodicidade", "moeda", "valor_base_centavos", "valor_seat_centavos"]


class VersaoPlanoCatalogoSerializer(serializers.ModelSerializer):
    precos = PrecoPlanoCatalogoSerializer(source="precos_contrataveis", many=True)

    class Meta:
        model = VersaoPlano
        fields = [
            "id",
            "numero",
            "seats_inclusos",
            "limite_seats_trial",
            "duracao_trial_dias",
            "carencia_pagamento_dias",
            "carencia_excesso_seats_dias",
            "expansao_automatica_seats",
            "recursos",
            "precos",
        ]


class PlanoCatalogoSerializer(serializers.ModelSerializer):
    versao = serializers.SerializerMethodField()

    class Meta:
        model = Plano
        fields = ["id", "codigo", "nome", "descricao", "versao"]

    @extend_schema_field(VersaoPlanoCatalogoSerializer)
    def get_versao(self, plano):
        return VersaoPlanoCatalogoSerializer(plano.versoes_contrataveis[0]).data


class AssinaturaResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.IntegerField()
    status_financeiro = serializers.IntegerField()
    revisao = serializers.IntegerField()
    versao_plano_id = serializers.IntegerField(allow_null=True)
    periodicidade = serializers.IntegerField()
    moeda = serializers.CharField()
    seats_inclusos = serializers.IntegerField()
    seats_contratados = serializers.IntegerField()
    total_centavos = serializers.IntegerField()
    trial_termina_em = serializers.DateTimeField(allow_null=True)
    periodo_atual_termina_em = serializers.DateTimeField(allow_null=True)
    cancelamento_agendado_para = serializers.DateTimeField(allow_null=True)
    situacao_acesso = serializers.SerializerMethodField()

    @extend_schema_field(SituacaoAcessoResponseSerializer)
    def get_situacao_acesso(self, _assinatura):
        return SituacaoAcessoResponseSerializer(self.context["situacao_acesso"]).data


class RecursosAssinaturaResponseSerializer(serializers.Serializer):
    quantidade_projetos = serializers.IntegerField(min_value=0)
    papeis_isentos_seat = serializers.ListField(
        child=serializers.ChoiceField(choices=Papel.choices),
    )


class UtilizacaoSeatsResponseSerializer(serializers.Serializer):
    contratados = serializers.IntegerField(min_value=0)
    consumidos = serializers.IntegerField(min_value=0)
    reservados = serializers.IntegerField(min_value=0)
    comprometidos = serializers.IntegerField(min_value=0)
    disponiveis = serializers.IntegerField(min_value=0)
    excesso_real = serializers.IntegerField(min_value=0)
    excesso_comprometido = serializers.IntegerField(min_value=0)


TIPOS_ALTERACAO_PUBLICOS = tuple(
    (valor, rotulo) for valor, rotulo in TipoAlteracaoAssinatura.choices if valor != TipoAlteracaoAssinatura.FALLBACK_TRIAL
)


class SolicitarAlteracaoRequestSerializer(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=TIPOS_ALTERACAO_PUBLICOS)
    versao_plano_id = serializers.IntegerField(min_value=1, required=False)
    periodicidade = serializers.ChoiceField(choices=Periodicidade.choices, required=False)
    seats_contratados = serializers.IntegerField(min_value=0, max_value=32_767, required=False)
    revisao_esperada = serializers.IntegerField(min_value=1, max_value=32_767)
    chave_idempotencia = serializers.CharField(min_length=1, max_length=120, trim_whitespace=True)

    def validate(self, attrs):
        tipo = TipoAlteracaoAssinatura(attrs["tipo"])
        if tipo in (TipoAlteracaoAssinatura.AUMENTO_SEATS, TipoAlteracaoAssinatura.REDUCAO_SEATS):
            if "seats_contratados" not in attrs:
                raise serializers.ValidationError({"seats_contratados": "Este campo é obrigatório para alteração de seats."})
        elif tipo in (TipoAlteracaoAssinatura.UPGRADE_PLANO, TipoAlteracaoAssinatura.DOWNGRADE_PLANO):
            if "versao_plano_id" not in attrs:
                raise serializers.ValidationError({"versao_plano_id": "Este campo é obrigatório para mudança de plano."})
        elif tipo == TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE and "periodicidade" not in attrs:
            raise serializers.ValidationError({"periodicidade": "Este campo é obrigatório para mudança de periodicidade."})
        attrs["tipo"] = tipo
        if "periodicidade" in attrs:
            attrs["periodicidade"] = Periodicidade(attrs["periodicidade"])
        return attrs


class AlteracaoAssinaturaResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    tipo = serializers.IntegerField()
    status = serializers.IntegerField()
    momento_aplicacao = serializers.IntegerField()
    revisao_esperada = serializers.IntegerField()
    chave_idempotencia = serializers.CharField()
    aplicar_em = serializers.DateTimeField(allow_null=True)


class CancelamentoAssinaturaRequestSerializer(serializers.Serializer):
    revisao_esperada = serializers.IntegerField(min_value=1, max_value=32_767)


class CancelamentoAssinaturaResponseSerializer(serializers.Serializer):
    status = serializers.IntegerField()
    revisao = serializers.IntegerField()
    cancelamento_agendado_para = serializers.DateTimeField(allow_null=True)
    encerrada_em = serializers.DateTimeField(allow_null=True)
    motivo_encerramento = serializers.CharField(allow_null=True)


class AceitarPropostaRequestSerializer(serializers.Serializer):
    revisao_esperada = serializers.IntegerField(min_value=1, max_value=32_767)
    chave_idempotencia = serializers.CharField(min_length=1, max_length=120, required=False)


class PreparacaoCheckoutPropostaSerializer(serializers.Serializer):
    proposta_id = serializers.IntegerField()
    organizacao_id = serializers.IntegerField()
    revisao = serializers.IntegerField()
    moeda = serializers.CharField()
    total_centavos = serializers.IntegerField()
    checkout_id = serializers.IntegerField(required=False)
    checkout_url = serializers.URLField(required=False)


class AceitarPropostaResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.IntegerField()
    revisao = serializers.IntegerField()
    modo_ativacao = serializers.IntegerField()
    preparacao_checkout = PreparacaoCheckoutPropostaSerializer(allow_null=True)
