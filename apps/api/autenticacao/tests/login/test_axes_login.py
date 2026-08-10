"""Testes da ativação do django-axes no fluxo de login."""

from rest_framework import status

import pytest
from axes.models import AccessAttempt

from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"


def test_login_valido_continua_funcionando_com_o_axes_ligado(tentar_login):
    usuario = criar_usuario()

    resposta = tentar_login(usuario.email, SENHA)

    assert resposta.status_code == status.HTTP_200_OK


def test_falhas_abaixo_do_limite_retornam_401(settings, tentar_login):
    usuario = criar_usuario()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        resposta = tentar_login(usuario.email, "senha-errada")
        assert resposta.status_code == status.HTTP_401_UNAUTHORIZED


def test_falha_no_limite_bloqueia_com_429(settings, tentar_login):
    usuario = criar_usuario()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar_login(usuario.email, "senha-errada")

    resposta = tentar_login(usuario.email, "senha-errada")

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_bloqueio_registra_a_tentativa_no_banco(settings, tentar_login):
    usuario = criar_usuario()

    for _ in range(settings.AXES_FAILURE_LIMIT):
        tentar_login(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)

    assert tentativa.ip_address == "203.0.113.10"
    assert tentativa.failures_since_start == settings.AXES_FAILURE_LIMIT
