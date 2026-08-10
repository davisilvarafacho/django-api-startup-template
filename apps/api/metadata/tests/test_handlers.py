"""Regras de merge do metadata genérico (não tocam o banco)."""

from django.test import override_settings

from rest_framework.serializers import ValidationError

import pytest

from apps.api.metadata.handlers import mesclar_dados


def test_mescla_preservando_chaves_ausentes_do_payload():
    resultado = mesclar_dados({"erp_id": "X-1", "nota": "urgente"}, {"nota": "normal"})

    assert resultado == {"erp_id": "X-1", "nota": "normal"}


def test_valor_nulo_remove_a_chave():
    resultado = mesclar_dados({"erp_id": "X-1", "nota": "urgente"}, {"nota": None})

    assert resultado == {"erp_id": "X-1"}


def test_remover_chave_inexistente_nao_falha():
    resultado = mesclar_dados({"erp_id": "X-1"}, {"ausente": None})

    assert resultado == {"erp_id": "X-1"}


def test_nao_altera_o_dicionario_recebido():
    atuais = {"erp_id": "X-1"}

    mesclar_dados(atuais, {"nota": "urgente"})

    assert atuais == {"erp_id": "X-1"}


@override_settings(METADATA_MAX_KEYS=2)
def test_rejeita_quando_excede_o_total_de_chaves():
    with pytest.raises(ValidationError, match="2 chaves"):
        mesclar_dados({"a": "1", "b": "2"}, {"c": "3"})


@override_settings(METADATA_MAX_KEYS=2)
def test_remocao_nao_conta_para_o_limite_de_chaves():
    resultado = mesclar_dados({"a": "1", "b": "2"}, {"b": None, "c": "3"})

    assert resultado == {"a": "1", "c": "3"}


@override_settings(METADATA_MAX_KEY_LENGTH=5)
def test_rejeita_chave_longa_demais():
    with pytest.raises(ValidationError, match="5 caracteres"):
        mesclar_dados({}, {"chave-enorme": "1"})


@override_settings(METADATA_MAX_VALUE_LENGTH=5)
def test_rejeita_valor_longo_demais():
    with pytest.raises(ValidationError, match="5 caracteres"):
        mesclar_dados({}, {"nota": "valor-enorme"})


def test_rejeita_chave_vazia():
    with pytest.raises(ValidationError, match="vazia"):
        mesclar_dados({}, {"": "1"})
