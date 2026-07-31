"""Contratos HTTP para emissão de sessão e reautenticação."""

from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError

from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token

pytestmark = pytest.mark.django_db


def _disable_analytics(monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.views.posthog.tag", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.api.autenticacao.views.capture", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.api.autenticacao.views.identify_context", lambda *args, **kwargs: None)


def _session_client(api_client, usuario):
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        expiry=timedelta(hours=1),
        metadata_input={},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")
    return api_client, issued.instance


def test_login_emite_sessao_pelo_service(api_client, usuario, monkeypatch):
    _disable_analytics(monkeypatch)
    issued_arguments = []
    original_issue_token = issue_token

    def tracked_issue_token(**kwargs):
        issued_arguments.append(kwargs)
        return original_issue_token(**kwargs)

    monkeypatch.setattr("apps.api.autenticacao.views.issue_token", tracked_issue_token)

    response = api_client.post(
        "/auth/login/",
        {"email": usuario.email, "password": "Senha123!"},
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    token = AuthToken.objects.get(responsavel=usuario)
    assert token.type == TokenType.TOKEN
    assert token.metadata.reauthenticated_at is not None
    assert response.data["token"]
    assert response.data["expiry"]
    assert response.data["session"]["id"] == token.digest
    assert issued_arguments[0]["token_type"] == TokenType.TOKEN
    assert issued_arguments[0]["responsavel"] == usuario


def test_login_nao_deixa_token_parcial_se_metadata_falha(api_client, usuario, monkeypatch):
    monkeypatch.setattr(TokenMetaData.objects, "create", lambda **kwargs: (_ for _ in ()).throw(IntegrityError("boom")))

    with pytest.raises(IntegrityError, match="boom"):
        api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!"})

    assert not AuthToken.objects.filter(responsavel=usuario).exists()


def test_login_mantem_alias_username_para_clientes_existentes(api_client, usuario, monkeypatch):
    _disable_analytics(monkeypatch)

    response = api_client.post(
        "/auth/login/",
        {"username": usuario.email, "password": "Senha123!"},
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == status.HTTP_200_OK, response.content


def test_login_invalido_retorna_erro_tipado(api_client, usuario):
    response = api_client.post(
        "/auth/login/",
        {"email": usuario.email, "password": "senha-incorreta"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data == {
        "code": "auth.invalid_credentials",
        "message": "E-mail ou senha inválidos.",
    }


def test_login_respeita_throttle_nomeado(api_client, usuario, monkeypatch):
    _disable_analytics(monkeypatch)
    cache.clear()
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {
            **ScopedRateThrottle.THROTTLE_RATES,
            "auth_login": "1/min",
        },
    )

    payload = {"email": usuario.email, "password": "Senha123!"}
    assert api_client.post("/auth/login/", payload, REMOTE_ADDR="127.0.0.1").status_code == status.HTTP_200_OK
    assert api_client.post("/auth/login/", payload, REMOTE_ADDR="127.0.0.1").status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_login_limita_apenas_sessoes_e_retorna_envelope_tipado(api_client, usuario, monkeypatch):
    _disable_analytics(monkeypatch)
    monkeypatch.setattr("apps.api.autenticacao.views.LoginView.get_token_limit_per_user", lambda self: 1)
    issue_token(
        responsavel=usuario,
        token_type=TokenType.API_KEY,
        expiry=timedelta(hours=1),
        metadata_input={},
    )

    payload = {"email": usuario.email, "password": "Senha123!"}
    first_response = api_client.post("/auth/login/", payload, REMOTE_ADDR="127.0.0.1")
    second_response = api_client.post("/auth/login/", payload, REMOTE_ADDR="127.0.0.1")

    assert first_response.status_code == status.HTTP_200_OK
    assert second_response.status_code == status.HTTP_403_FORBIDDEN
    assert second_response.data == {
        "code": "auth.too_many_attempts",
        "message": "Limite de sessões ativas atingido.",
    }


def test_reauthenticate_atualiza_sessao_atual(api_client, usuario):
    client, token = _session_client(api_client, usuario)
    TokenMetaData.objects.filter(token=token).update(reauthenticated_at=None)

    response = client.post("/auth/reauthenticate/", {"password": "Senha123!"})

    assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
    token.metadata.refresh_from_db()
    assert token.metadata.reauthenticated_at is not None


def test_reauthenticate_recusa_api_key(api_client, usuario):
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.API_KEY,
        expiry=timedelta(hours=1),
        metadata_input={},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    response = api_client.post("/auth/reauthenticate/", {"password": "Senha123!"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["code"] == "auth.reauthentication_required"


def test_reauthenticate_invalida_nao_atualiza_marcador(api_client, usuario):
    client, token = _session_client(api_client, usuario)
    TokenMetaData.objects.filter(token=token).update(reauthenticated_at=None)

    response = client.post("/auth/reauthenticate/", {"password": "senha-incorreta"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    token.metadata.refresh_from_db()
    assert token.metadata.reauthenticated_at is None


def test_reauthenticate_respeita_throttle_nomeado(api_client, usuario, monkeypatch):
    cache.clear()
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {
            **ScopedRateThrottle.THROTTLE_RATES,
            "auth_reauthenticate": "1/min",
        },
    )
    client, _ = _session_client(api_client, usuario)

    assert client.post("/auth/reauthenticate/", {"password": "Senha123!"}).status_code == status.HTTP_204_NO_CONTENT
    assert client.post("/auth/reauthenticate/", {"password": "Senha123!"}).status_code == status.HTTP_429_TOO_MANY_REQUESTS
