"""Testes da política de bloqueio configurada para o django-axes."""
from rest_framework import status

import pytest
from axes.models import AccessAttempt

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"
IP_A = "203.0.113.10"
IP_B = "198.51.100.20"


def test_bloqueio_nao_alcanca_o_mesmo_usuario_em_outro_ip(esgotar_tentativas, tentar_login):
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email, ip=IP_A)

    resposta = tentar_login(usuario.email, SENHA, ip=IP_B)

    assert resposta.status_code == status.HTTP_200_OK


def test_bloqueio_nao_alcanca_outro_usuario_no_mesmo_ip(esgotar_tentativas, tentar_login):
    vitima = UsuarioFactory()
    outro = UsuarioFactory()
    esgotar_tentativas(vitima.email, ip=IP_A)

    resposta = tentar_login(outro.email, SENHA, ip=IP_A)

    assert resposta.status_code == status.HTTP_200_OK


def test_login_bem_sucedido_zera_o_contador(settings, tentar_login):
    usuario = UsuarioFactory()
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar_login(usuario.email, "senha-errada")

    tentar_login(usuario.email, SENHA)

    assert not AccessAttempt.objects.filter(username=usuario.email).exists()


def test_tentativa_durante_o_bloqueio_nao_estende_o_cooloff(esgotar_tentativas, tentar_login):
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email)
    momento_do_bloqueio = AccessAttempt.objects.get(username=usuario.email).attempt_time

    tentar_login(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)
    assert tentativa.attempt_time == momento_do_bloqueio
