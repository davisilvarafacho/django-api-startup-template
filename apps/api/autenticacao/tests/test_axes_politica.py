"""Testes da política de bloqueio configurada para o django-axes."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from axes.models import AccessAttempt
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"
IP_A = "203.0.113.10"
IP_B = "198.51.100.20"


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


def tentar(email, senha, ip=IP_A):
    client = APIClient()
    return client.post(
        URL_LOGIN,
        {"username": email, "password": senha},
        format="json",
        HTTP_X_FORWARDED_FOR=ip,
    )


def esgotar_tentativas(email, ip=IP_A):
    from django.conf import settings

    for _ in range(settings.AXES_FAILURE_LIMIT):
        tentar(email, "senha-errada", ip=ip)


def test_bloqueio_nao_alcanca_o_mesmo_usuario_em_outro_ip():
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email, ip=IP_A)

    resposta = tentar(usuario.email, SENHA, ip=IP_B)

    assert resposta.status_code == status.HTTP_200_OK


def test_bloqueio_nao_alcanca_outro_usuario_no_mesmo_ip():
    vitima = UsuarioFactory()
    outro = UsuarioFactory()
    esgotar_tentativas(vitima.email, ip=IP_A)

    resposta = tentar(outro.email, SENHA, ip=IP_A)

    assert resposta.status_code == status.HTTP_200_OK


def test_login_bem_sucedido_zera_o_contador(settings):
    usuario = UsuarioFactory()
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar(usuario.email, "senha-errada")

    tentar(usuario.email, SENHA)

    assert not AccessAttempt.objects.filter(username=usuario.email).exists()


def test_tentativa_durante_o_bloqueio_nao_estende_o_cooloff():
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email)
    momento_do_bloqueio = AccessAttempt.objects.get(username=usuario.email).attempt_time

    tentar(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)
    assert tentativa.attempt_time == momento_do_bloqueio
