from rest_framework import serializers


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
