"""Testes da ativação do django-axes no fluxo de login."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from axes.models import AccessAttempt
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"


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


def tentar_login(email, senha, ip="203.0.113.10"):
    client = APIClient()
    return client.post(
        URL_LOGIN,
        {"username": email, "password": senha},
        format="json",
        HTTP_X_FORWARDED_FOR=ip,
    )


def test_login_valido_continua_funcionando_com_o_axes_ligado():
    usuario = UsuarioFactory()

    resposta = tentar_login(usuario.email, SENHA)

    assert resposta.status_code == status.HTTP_200_OK


def test_falhas_abaixo_do_limite_retornam_400(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        resposta = tentar_login(usuario.email, "senha-errada")
        assert resposta.status_code == status.HTTP_400_BAD_REQUEST


def test_falha_no_limite_bloqueia_com_429(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar_login(usuario.email, "senha-errada")

    resposta = tentar_login(usuario.email, "senha-errada")

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_bloqueio_registra_a_tentativa_no_banco(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT):
        tentar_login(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)

    assert tentativa.ip_address == "203.0.113.10"
    assert tentativa.failures_since_start == settings.AXES_FAILURE_LIMIT
