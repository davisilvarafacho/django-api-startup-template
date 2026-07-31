"""Testes da resposta de bloqueio devolvida ao cliente."""
from datetime import timedelta

from django.test import RequestFactory
from django.utils import timezone

from rest_framework import status

import pytest
from axes.models import AccessAttempt

from apps.api.autenticacao.handlers import segundos_ate_o_desbloqueio
from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"


def test_resolve_cooloff_callable_com_a_request(settings, ambiente_axes):
    request = RequestFactory().post(URL_LOGIN)

    def cooloff(request_recebida):
        assert request_recebida is request
        return timedelta(seconds=123)

    settings.AXES_COOLOFF_TIME = cooloff

    assert segundos_ate_o_desbloqueio(request, None) == 123


def test_prazo_usa_o_combo_que_atingiu_o_limite(settings, ambiente_axes):
    agora = timezone.now()
    settings.AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"], ["user_agent"]]
    request = RequestFactory().post(
        URL_LOGIN,
        {"username": "usuario@example.com"},
        REMOTE_ADDR="203.0.113.10",
        HTTP_USER_AGENT="agente-atual",
    )
    tentativa_bloqueada = AccessAttempt.objects.create(
        username="usuario@example.com",
        ip_address="203.0.113.10",
        user_agent="agente-antigo",
        failures_since_start=settings.AXES_FAILURE_LIMIT,
        attempt_time=agora - timedelta(seconds=120),
        http_accept="",
        path_info=URL_LOGIN,
        get_data="",
        post_data="",
    )
    tentativa_recente = AccessAttempt.objects.create(
        username="outro@example.com",
        ip_address="198.51.100.20",
        user_agent="agente-atual",
        failures_since_start=1,
        attempt_time=agora - timedelta(seconds=10),
        http_accept="",
        path_info=URL_LOGIN,
        get_data="",
        post_data="",
    )
    AccessAttempt.objects.filter(pk=tentativa_bloqueada.pk).update(attempt_time=agora - timedelta(seconds=120))
    AccessAttempt.objects.filter(pk=tentativa_recente.pk).update(attempt_time=agora - timedelta(seconds=10))

    segundos = segundos_ate_o_desbloqueio(request, {"username": "usuario@example.com"})

    assert 1600 <= segundos <= 1690


def test_bloqueio_responde_json_no_formato_do_projeto(bloquear):
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resposta["Content-Type"] == "application/json"
    assert resposta.json() == {"mensagem": "Muitas tentativas de login."}


def test_bloqueio_nao_revela_o_prazo_no_corpo(settings, bloquear):
    settings.AXES_COOLOFF_TIME = timedelta(minutes=30)
    resposta_30_minutos = bloquear(UsuarioFactory().email)

    settings.AXES_COOLOFF_TIME = timedelta(minutes=45)
    resposta_45_minutos = bloquear(UsuarioFactory().email)

    assert resposta_30_minutos["Retry-After"] != resposta_45_minutos["Retry-After"]
    assert resposta_30_minutos.json() == resposta_45_minutos.json() == {"mensagem": "Muitas tentativas de login."}


def test_bloqueio_informa_o_prazo_no_header_retry_after(settings, bloquear):
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    segundos = int(resposta["Retry-After"])
    assert 0 < segundos <= settings.AXES_COOLOFF_TIME.total_seconds()
