"""Service layer do metadata genérico.

`aplicar_metadata` é o único ponto do sistema que cria linha de `Metadata`;
`mesclar_dados` concentra as regras, sem tocar no banco.
"""

from django.conf import settings

from rest_framework import serializers

from apps.api.metadata.models import Metadata


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


def aplicar_metadata(objeto, alteracoes):
    """Aplica alterações ao documento de metadata de um objeto e persiste.

    É o único ponto do sistema que cria linha de `Metadata`. A validação roda
    antes de qualquer escrita, para que um payload inválido não deixe registro
    vazio para trás. A organização não é passada explicitamente: `TenantMixin`
    a preenche a partir do contexto RLS ativo.

    Args:
        objeto: Instância de qualquer model que herde de `BaseTenantless`.
        alteracoes: Chaves a gravar; valor `None` remove a chave.

    Returns:
        O registro de `Metadata` persistido.

    Raises:
        rest_framework.serializers.ValidationError: Propagada de `mesclar_dados`.
    """
    content_type = objeto.get_content_type()
    registro = Metadata.objects.filter(content_type=content_type, object_id=objeto.pk).first()
    dados = mesclar_dados(registro.dados if registro is not None else {}, alteracoes)

    if registro is None:
        return Metadata.objects.create(content_type=content_type, object_id=objeto.pk, dados=dados)

    registro.dados = dados
    registro.save(update_fields=["dados"])
    return registro
