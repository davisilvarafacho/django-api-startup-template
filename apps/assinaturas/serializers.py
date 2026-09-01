from rest_framework import serializers

from apps.assinaturas.models import Periodicidade, TipoAlteracaoAssinatura
from apps.organizacoes.models import Papel


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
    revisao = serializers.IntegerField()
    cancelamento_agendado_para = serializers.DateTimeField(allow_null=True)


class AceitarPropostaRequestSerializer(serializers.Serializer):
    revisao_esperada = serializers.IntegerField(min_value=1, max_value=32_767)


class PreparacaoCheckoutPropostaSerializer(serializers.Serializer):
    proposta_id = serializers.IntegerField()
    organizacao_id = serializers.IntegerField()
    revisao = serializers.IntegerField()
    moeda = serializers.CharField()
    total_centavos = serializers.IntegerField()


class AceitarPropostaResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.IntegerField()
    revisao = serializers.IntegerField()
    modo_ativacao = serializers.IntegerField()
    preparacao_checkout = PreparacaoCheckoutPropostaSerializer(allow_null=True)
