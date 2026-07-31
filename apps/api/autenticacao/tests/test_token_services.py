"""Emissão e revogação centralizadas de tokens."""

from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token, revoke_tokens


@pytest.fixture
def metadata_input():
    return {
        "device_name": "Notebook",
        "device_type": "desktop",
        "ip_address": "203.0.113.10",
        "user_agent": "pytest",
    }


def test_metadata_nao_duplica_tipo_ou_scopes_do_token():
    assert not hasattr(TokenMetaData, "type")
    assert not hasattr(TokenMetaData, "scopes")


@pytest.mark.django_db
def test_issue_token_cria_token_e_metadata_atomicamente(usuario, metadata_input):
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        expiry=timedelta(hours=1),
        metadata_input=metadata_input,
    )

    assert issued.instance.responsavel == usuario
    assert issued.instance.type == TokenType.TOKEN
    assert issued.instance.metadata.device_name == "Notebook"
    assert issued.plain_token.startswith(issued.instance.token_key)


@pytest.mark.django_db
def test_issue_token_faz_rollback_quando_metadata_falha(usuario, metadata_input):
    with patch.object(TokenMetaData.objects, "create", side_effect=IntegrityError("boom")):
        with pytest.raises(IntegrityError):
            issue_token(
                responsavel=usuario,
                token_type=TokenType.TOKEN,
                expiry=None,
                metadata_input=metadata_input,
            )

    assert not AuthToken.objects.filter(responsavel=usuario).exists()


@pytest.mark.django_db
def test_revoke_tokens_respeita_tipos_e_digest_excluido(usuario):
    kept, _ = AuthToken.objects.create(user=usuario, type=TokenType.TOKEN)
    removed, _ = AuthToken.objects.create(user=usuario, type=TokenType.TOKEN)
    reset, _ = AuthToken.objects.create(user=usuario, type=TokenType.RESET_PASSWORD, expiry=timedelta(minutes=5))

    assert revoke_tokens(usuario, types=[TokenType.TOKEN], exclude_digest=kept.digest) == 1
    assert AuthToken.objects.filter(digest=kept.digest).exists()
    assert not AuthToken.objects.filter(digest=removed.digest).exists()
    assert AuthToken.objects.filter(digest=reset.digest).exists()
