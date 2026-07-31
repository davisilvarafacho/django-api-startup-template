"""Fixtures compartilhadas dos testes de autenticação."""
from rest_framework.test import APIClient
from threadlocals.threadlocals import set_current_user, set_thread_variable

import pytest


@pytest.fixture
def ambiente_axes(settings):
    """Liga o axes e limpa o contexto de request do teste."""
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def tentar_login(ambiente_axes):
    """Envia uma tentativa de login pelo endpoint real."""
    def _tentar(email, senha, ip="203.0.113.10"):
        return APIClient().post(
            "/auth/login/",
            {"username": email, "password": senha},
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
