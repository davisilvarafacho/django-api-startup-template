"""Efeito da exclusão de conta sobre credenciais já emitidas."""

from django.db import models

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.authentications import TypedTokenAuthentication
from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.services import revoke_all_user_credentials
from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Vinculo
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_revoke_all_user_credentials_revoga_todos_os_tipos():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org", slug="org-revogacao")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
    tokens = [
        AuthToken.objects.create(responsavel=usuario, type=token_type)[0]
        for token_type in (TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD)
    ]
    api_key, _ = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    tokens.append(api_key)

    alterados = revoke_all_user_credentials(usuario)

    assert alterados == len(tokens)
    assert not AuthToken.objects.filter(pk__in=[token.pk for token in tokens], revoked_at__isnull=True).exists()
    assert AuthToken.objects.filter(pk__in=[token.pk for token in tokens]).count() == len(tokens)
    # Idempotente: só alcança o que ainda estava utilizável.
    assert revoke_all_user_credentials(usuario) == 0


def test_api_key_emitida_antes_da_exclusao_nao_autentica():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org", slug="org-conta-excluida")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
    token, plain_token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    TokenMetaData.objects.create(token=token)
    usuario.delete()

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    response = client.get("/times/", HTTP_X_ORGANIZATION=organizacao.slug)

    assert response.status_code == 401


def test_sessao_de_conta_legada_excluida_e_ativa_nao_autentica():
    usuario = criar_usuario()
    token, plain_token = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    TokenMetaData.objects.create(token=token)
    # Linha excluída antes desta correção: ainda ativa e com a credencial viva.
    models.QuerySet.update(Usuario.all_objects.filter(pk=usuario.pk), is_deleted=True)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    response = client.get("/times/", HTTP_X_ORGANIZATION="qualquer")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == AuthErrorCode.RESPONSIBLE_INACTIVE.value


def test_autenticacao_tipada_recusa_responsavel_excluido():
    usuario = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    models.QuerySet.update(Usuario.all_objects.filter(pk=usuario.pk), is_deleted=True)
    token = AuthToken.objects.select_related("responsavel").get(pk=token.pk)

    with pytest.raises(APIError) as excinfo:
        TypedTokenAuthentication().validate_user(token)

    assert excinfo.value.code == AuthErrorCode.RESPONSIBLE_INACTIVE.value
    assert excinfo.value.status_code == 401
