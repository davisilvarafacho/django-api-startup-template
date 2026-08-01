"""Testes do registry de lookup (não tocam o banco)."""

from django.core.exceptions import ImproperlyConfigured

import pytest

from apps.api.core.lookup import LookupRegistry, LookupSerializer


class Fake:
    __name__ = "Fake"

    class _meta:
        model_name = "fornecedor"

    def __str__(self):
        return "Acme"


class Outro:
    __name__ = "Outro"

    class _meta:
        model_name = "fornecedor"


def test_registra_com_chave_do_model_name():
    reg = LookupRegistry()
    reg.register(Fake, search=["nome"])
    config = reg.get("fornecedor")
    assert config is not None
    assert config.search_fields == ["nome"]
    assert config.serializer_class is LookupSerializer


def test_chave_explicita():
    reg = LookupRegistry()
    reg.register(Fake, key="fornecedor_estoque", search=["nome"])
    assert reg.get("fornecedor_estoque") is not None
    assert reg.get("fornecedor") is None


def test_colisao_de_chave_levanta():
    reg = LookupRegistry()
    reg.register(Fake, search=[])
    with pytest.raises(ImproperlyConfigured):
        reg.register(Outro, search=[])


def test_serializer_default_id_label():
    class Obj:
        id = 7

        def __str__(self):
            return "Acme Ltda"

    assert dict(LookupSerializer(Obj()).data) == {"id": 7, "label": "Acme Ltda"}
