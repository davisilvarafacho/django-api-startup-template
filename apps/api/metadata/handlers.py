"""Service layer do metadata genérico.

`aplicar_metadata` é o único ponto do sistema que cria linha de `Metadata`;
`mesclar_dados` concentra as regras, sem tocar no banco.
"""

from django.conf import settings

from rest_framework import serializers


def mesclar_dados(atuais, alteracoes):
    """Funde as alterações no documento atual, aplicando os limites.

    Args:
        atuais: Documento já persistido, no formato chave/valor de texto.
        alteracoes: Chaves a gravar; valor `None` remove a chave.

    Returns:
        O documento resultante, sem alterar o dicionário recebido.

    Raises:
        rest_framework.serializers.ValidationError: Quando uma chave é vazia ou
            algum limite de `settings` é violado.
    """
    for chave, valor in alteracoes.items():
        if not chave:
            raise serializers.ValidationError("A chave de metadata não pode ser vazia.")

        if len(chave) > settings.METADATA_MAX_KEY_LENGTH:
            raise serializers.ValidationError(f"A chave de metadata não pode ter mais de {settings.METADATA_MAX_KEY_LENGTH} caracteres.")

        if valor is not None and len(valor) > settings.METADATA_MAX_VALUE_LENGTH:
            raise serializers.ValidationError(f"O valor da chave '{chave}' não pode ter mais de {settings.METADATA_MAX_VALUE_LENGTH} caracteres.")

    resultado = dict(atuais)
    for chave, valor in alteracoes.items():
        if valor is None:
            resultado.pop(chave, None)
        else:
            resultado[chave] = valor

    if len(resultado) > settings.METADATA_MAX_KEYS:
        raise serializers.ValidationError(f"O objeto não pode ter mais de {settings.METADATA_MAX_KEYS} chaves de metadata.")

    return resultado
