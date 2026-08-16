"""Fixtures compartilhadas dos testes de autenticação."""

from django.core.cache import cache

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.views import LoginView
from internal_frameworks.context import ContextVariable


@pytest.fixture(autouse=True)
def _isolar_throttle():
    """Zera o contador de throttle entre testes.

    O DRF guarda o histórico de requisições no cache `default`, que sobrevive ao
    rollback do banco. Sem limpar, um teste que faz login herda as tentativas dos
    anteriores e leva 429 assim que a suíte cresce.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ambiente_axes(settings):
    """Liga o axes e limpa o contexto de request do teste."""
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            # O teste isola o bloqueio do Axes; o throttle de API é coberto
            # pela sua própria suíte e não deve interceptar este cenário.
            "anon": "10000/min",
            "auth_login": "10000/min",
        },
    }
    throttle_classes = LoginView.throttle_classes
    LoginView.throttle_classes = []
    ContextVariable.clear_context()
    yield
    LoginView.throttle_classes = throttle_classes
    ContextVariable.clear_context()


@pytest.fixture
def tentar_login(ambiente_axes):
    """Envia uma tentativa de login pelo endpoint real."""

    def _tentar(email, senha, ip="203.0.113.10"):
        return APIClient().post(
            "/auth/login/",
            {"email": email, "password": senha},
            format="json",
            HTTP_X_FORWARDED_FOR=ip,
        )

    return _tentar


@pytest.fixture
def esgotar_tentativas(tentar_login, settings):
    """Repete falhas até atingir o limite configurado."""

    def _esgotar(email, ip="203.0.113.10"):
        for _ in range(settings.AXES_FAILURE_LIMIT):
            tentar_login(email, "senha-errada", ip=ip)

    return _esgotar


@pytest.fixture
def bloquear(tentar_login, settings):
    """Erra a senha até o bloqueio disparar e devolve a última resposta."""

    def _bloquear(email, ip="203.0.113.10"):
        resposta = None
        for _ in range(settings.AXES_FAILURE_LIMIT):
            resposta = tentar_login(email, "senha-errada", ip=ip)
        return resposta

    return _bloquear
