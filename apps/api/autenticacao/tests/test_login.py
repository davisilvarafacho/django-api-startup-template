"""Contrato HTTP de `POST /auth/login/`."""

from unittest.mock import patch

from django.contrib.auth.signals import user_logged_in

from rest_framework.test import APIClient
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.views import LoginView
from apps.usuarios.factories import UsuarioFactory
from threadlocals.threadlocals import set_current_user, set_thread_variable

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    """Limpa o estado que `ThreadLocalMiddleware` deixa entre requests.

    `ThreadLocalMiddleware` nunca limpa `request` sozinho: sem isso, o
    usuário desta request vazaria como `get_current_user()` para o próximo
    teste que rodar na mesma thread.
    """
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def usuario():
    return UsuarioFactory(email="login@example.com", password="senha-forte-123")


@pytest.fixture
def client():
    return APIClient()


def test_login_com_credenciais_validas_emite_token_de_sessao(client, usuario):
    response = client.post(
        "/auth/login/",
        {"email": usuario.email, "password": "senha-forte-123", "device_name": "Notebook"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["token"]
    assert response.data["session"]["device"]["name"] == "Notebook"

    token = AuthToken.objects.get(responsavel=usuario)
    assert token.type == TokenType.TOKEN
    assert token.metadata.device_name == "Notebook"


def test_login_tem_throttle_especifico(settings):
    assert LoginView.throttle_scope == "auth_login"
    assert LoginView.throttle_classes == [AnonRateThrottle, ScopedRateThrottle]
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth_login"] == "10/min"


def test_login_com_senha_invalida_e_recusado(client, usuario):
    response = client.post(
        "/auth/login/",
        {"email": usuario.email, "password": "senha-errada"},
        format="json",
    )

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.invalid_credentials"


def test_login_com_usuario_inexistente_e_recusado(client):
    response = client.post(
        "/auth/login/",
        {"email": "ninguem@example.com", "password": "qualquer"},
        format="json",
    )

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.invalid_credentials"


def test_login_respeita_limite_de_sessoes_ativas(client, usuario, monkeypatch):
    # `settings.REST_KNOX` reconstrói o objeto `knox_settings` via signal, mas
    # `views.py` já importou a referência antiga; ajustar o atributo direto
    # no objeto em uso evita depender dessa recarga.
    monkeypatch.setattr("apps.api.autenticacao.views.knox_settings.TOKEN_LIMIT_PER_USER", 1)

    primeira = client.post("/auth/login/", {"email": usuario.email, "password": "senha-forte-123"}, format="json")
    assert primeira.status_code == 200

    segunda = client.post("/auth/login/", {"email": usuario.email, "password": "senha-forte-123"}, format="json")

    assert segunda.status_code == 403
    assert segunda.data["errors"][0]["code"] == "auth.token_limit_exceeded"


def test_login_dispara_signal_user_logged_in(client, usuario):
    recebido = []

    def _receiver(sender, request, user, **kwargs):
        recebido.append(user)

    user_logged_in.connect(_receiver)
    try:
        client.post("/auth/login/", {"email": usuario.email, "password": "senha-forte-123"}, format="json")
    finally:
        user_logged_in.disconnect(_receiver)

    assert recebido == [usuario]


def test_login_com_mudanca_de_pais_marca_risco(client, usuario):
    anterior_token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    TokenMetaData.objects.create(token=anterior_token, country_code="BR")

    with patch(
        "apps.api.autenticacao.utils.get_geolocation_data",
        return_value={"country": "France", "country_code": "FR"},
    ):
        response = client.post("/auth/login/", {"email": usuario.email, "password": "senha-forte-123"}, format="json")

    assert response.status_code == 200
    novo_token = AuthToken.objects.exclude(digest=anterior_token.digest).get(responsavel=usuario)
    assert novo_token.metadata.is_suspicious is True
    assert novo_token.metadata.risk_score == 50


def test_login_nao_vaza_segredo_em_eventos_de_analytics(client, usuario):
    with patch("apps.api.autenticacao.views.capture") as capture_mock:
        response = client.post("/auth/login/", {"email": usuario.email, "password": "senha-forte-123"}, format="json")

    token_plano = response.data["token"]

    for chamada in capture_mock.call_args_list:
        propriedades = chamada.kwargs.get("properties") or {}
        assert token_plano not in str(propriedades)
        for valor in propriedades.values():
            assert valor != token_plano
