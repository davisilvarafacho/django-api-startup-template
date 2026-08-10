"""Serializers do metadata genérico."""

from rest_framework import serializers


class MetadataAlteracaoSerializer(serializers.Serializer):
    """Valida o corpo de `PATCH /<recurso>/<id>/metadata/`.

    Cuida apenas do formato; os limites de tamanho e quantidade são aplicados
    por `apps.api.metadata.handlers.mesclar_dados`.
    """

    dados = serializers.DictField(allow_empty=True)

    def validate_dados(self, value):
        """Exige valores de texto ou nulos.

        `DictField(child=CharField())` não serve: o `CharField` do DRF converte
        silenciosamente `3` em `"3"`, e o contrato é de texto declarado pelo
        cliente, não de coerção.

        Args:
            value: Documento enviado na requisição.

        Returns:
            O mesmo documento, quando válido.

        Raises:
            rest_framework.serializers.ValidationError: Quando algum valor não é
                texto nem nulo.
        """
        for chave, valor in value.items():
            if valor is not None and not isinstance(valor, str):
                raise serializers.ValidationError(f"O valor da chave '{chave}' deve ser texto ou nulo.")

        return value
