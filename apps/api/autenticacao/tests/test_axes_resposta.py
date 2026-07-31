"""Testes da resposta de bloqueio devolvida ao cliente."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"


@pytest.fixture(autouse=True)
def ambiente_axes(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def bloquear(email, ip="203.0.113.10"):
    """Erra a senha até o bloqueio disparar e devolve a última resposta."""
    from django.conf import settings

    client = APIClient()
    resposta = None
    for _ in range(settings.AXES_FAILURE_LIMIT):
        resposta = client.post(
            URL_LOGIN,
            {"username": email, "password": "senha-errada"},
            format="json",
            HTTP_X_FORWARDED_FOR=ip,
        )
    return resposta


def test_bloqueio_responde_json_no_formato_do_projeto():
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resposta["Content-Type"] == "application/json"
    assert resposta.json() == {"mensagem": "Muitas tentativas de login."}


def test_bloqueio_nao_revela_o_prazo_no_corpo():
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    assert "minuto" not in resposta.json()["mensagem"]


def test_bloqueio_informa_o_prazo_no_header_retry_after(settings):
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    segundos = int(resposta["Retry-After"])
    assert 0 < segundos <= settings.AXES_COOLOFF_TIME.total_seconds()
