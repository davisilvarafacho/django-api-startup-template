from rest_framework import serializers

from apps.assinaturas.subapps.faturamento.models import FinalidadeCheckout


class CriarCheckoutRequestSerializer(serializers.Serializer):
    finalidade = serializers.ChoiceField(choices=(FinalidadeCheckout.CONTRATACAO, FinalidadeCheckout.ALTERACAO, FinalidadeCheckout.PROPOSTA))
    chave_idempotencia = serializers.CharField(min_length=1, max_length=120, trim_whitespace=True)
    alteracao_id = serializers.IntegerField(min_value=1, required=False)
    proposta_id = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs):
        finalidade = FinalidadeCheckout(attrs["finalidade"])
        exige_alteracao = finalidade == FinalidadeCheckout.ALTERACAO
        exige_proposta = finalidade == FinalidadeCheckout.PROPOSTA
        if exige_alteracao != ("alteracao_id" in attrs) or exige_proposta != ("proposta_id" in attrs):
            raise serializers.ValidationError("As referências devem corresponder à finalidade do checkout.")
        attrs["finalidade"] = finalidade
        return attrs


class CriarFormaPagamentoCheckoutRequestSerializer(serializers.Serializer):
    chave_idempotencia = serializers.CharField(min_length=1, max_length=120, trim_whitespace=True)


class CheckoutResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    finalidade = serializers.IntegerField()
    status = serializers.IntegerField()
    url = serializers.URLField(allow_blank=True)
    valor_esperado_centavos = serializers.IntegerField()
    moeda_esperada = serializers.CharField()
    created_at = serializers.DateTimeField()


class FaturaResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.IntegerField()
    motivo = serializers.CharField()
    total_centavos = serializers.IntegerField(allow_null=True)
    moeda = serializers.CharField()
    vencimento_em = serializers.DateTimeField(allow_null=True)
    paga_em = serializers.DateTimeField(allow_null=True)
    tentativas = serializers.IntegerField()
    url_hospedada = serializers.URLField(allow_blank=True)
    created_at = serializers.DateTimeField()
