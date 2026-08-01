"""Atomicidade da emissão de token + metadata."""

from unittest.mock import patch

from django.db import IntegrityError

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token
from apps.usuarios.factories import UsuarioFactory


@pytest.fixture
def usuario():
    return UsuarioFactory()


@pytest.fixture
def metadata_input():
    return {
        "device_name": "Notebook",
        "device_type": "desktop",
        "ip_address": "203.0.113.10",
        "user_agent": "pytest",
    }


def test_metadata_nao_possui_type_nem_scopes():
    assert not hasattr(TokenMetaData, "type")
    assert not hasattr(TokenMetaData, "scopes")


@pytest.mark.django_db
def test_issue_token_cria_token_e_metadata_na_mesma_transacao(usuario, metadata_input):
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        created_by=usuario,
        expiry=None,
        metadata_input=metadata_input,
    )

    assert issued.plain_token
    assert issued.instance.metadata.device_name == "Notebook"
    assert issued.instance.responsavel == usuario
    assert issued.instance.created_by == usuario
    assert issued.instance.type == TokenType.TOKEN


@pytest.mark.django_db
def test_issue_token_de_api_key_grava_organizacao_e_scopes(usuario, metadata_input):
    from apps.organizacoes.models import Organizacao, Vinculo

    organizacao = Organizacao.objects.create(nome="Org", slug="org-issue-token")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.API_KEY,
        created_by=usuario,
        expiry=None,
        metadata_input=metadata_input,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )

    assert issued.instance.organization == organizacao
    assert issued.instance.scopes == ["teams:read"]
    assert issued.instance.name == "Integração"


@pytest.mark.django_db
def test_issue_token_e_atomico_quando_metadata_falha(usuario, metadata_input):
    with patch.object(TokenMetaData.objects, "create", side_effect=IntegrityError("boom")):
        with pytest.raises(IntegrityError):
            issue_token(
                responsavel=usuario,
                token_type=TokenType.TOKEN,
                created_by=usuario,
                expiry=None,
                metadata_input=metadata_input,
            )

    assert not AuthToken.objects.filter(responsavel=usuario).exists()
