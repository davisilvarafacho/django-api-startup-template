"""Regressões HTTP do gerenciamento de tokens swappable."""

from django.contrib.auth.models import Permission

from rest_framework import status

import pytest

from apps.api.autenticacao.models import AuthToken
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Organizacao, Papel, Vinculo

pytestmark = pytest.mark.django_db


def _client_com_token(api_client, usuario):
    _, plain_token = AuthToken.objects.create(user=usuario)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    return api_client


def _conceder_acesso_a_sessao(usuario):
    permission_names = ("view_authtoken", "delete_authtoken")
    permissions = Permission.objects.filter(content_type__app_label="autenticacao", codename__in=permission_names)
    usuario.user_permissions.add(*permissions)

    organizacao = Organizacao.objects.create(nome="Organização de teste", slug="organizacao-de-teste")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    return {META_HEADER_ORGANIZACAO: organizacao.slug}


def test_login_emite_token_do_modelo_configurado(api_client, usuario, monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.views.posthog.tag", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.api.autenticacao.views.capture", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.api.autenticacao.views.identify_context", lambda *args, **kwargs: None)

    response = api_client.post(
        "/auth/login/",
        {"username": usuario.email, "password": "Senha123!"},
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert AuthToken.objects.get(responsavel=usuario).digest


def test_lista_apenas_tokens_do_usuario_autenticado(api_client, usuario):
    outro_usuario = type(usuario).objects.create_user(
        email="outra-pessoa@example.com",
        password="Senha123!",
        first_name="Outra",
        last_name="Pessoa",
    )
    token_do_usuario, _ = AuthToken.objects.create(user=usuario)
    token_de_outro_usuario, _ = AuthToken.objects.create(user=outro_usuario)
    headers = _conceder_acesso_a_sessao(usuario)

    response = _client_com_token(api_client, usuario).get("/auth/tokens/", **headers)

    assert response.status_code == status.HTTP_200_OK, response.content
    digests = {item["digest"] for item in response.data["resultados"]}
    assert token_do_usuario.digest in digests
    assert token_de_outro_usuario.digest not in digests


def test_revoga_token_do_usuario_autenticado(api_client, usuario):
    token_a_revogar, _ = AuthToken.objects.create(user=usuario)
    headers = _conceder_acesso_a_sessao(usuario)

    response = _client_com_token(api_client, usuario).delete(f"/auth/tokens/{token_a_revogar.digest}/revoke/", **headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
    assert not AuthToken.objects.filter(digest=token_a_revogar.digest).exists()
