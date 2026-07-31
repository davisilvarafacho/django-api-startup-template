"""Testes do expurgo periódico dos logs de acesso do django-axes."""
from datetime import timedelta

from django.utils import timezone

import pytest
from axes.models import AccessLog
from django_celery_beat.models import PeriodicTask

from apps.api.core.tasks import limpar_logs_de_acesso_antigos

pytestmark = pytest.mark.django_db


def criar_log(dias_atras):
    log = AccessLog.objects.create(
        username="alguem@exemplo.com",
        ip_address="203.0.113.10",
        user_agent="pytest",
        http_accept="application/json",
        path_info="/auth/login/",
    )
    # `attempt_time` é `auto_now_add`, então precisa ser reescrito na marra.
    AccessLog.objects.filter(pk=log.pk).update(
        attempt_time=timezone.now() - timedelta(days=dias_atras)
    )
    return log


def test_remove_apenas_os_logs_mais_velhos_que_o_prazo():
    antigo = criar_log(dias_atras=120)
    recente = criar_log(dias_atras=10)

    limpar_logs_de_acesso_antigos(dias=90)

    assert not AccessLog.objects.filter(pk=antigo.pk).exists()
    assert AccessLog.objects.filter(pk=recente.pk).exists()


def test_devolve_a_quantidade_removida():
    criar_log(dias_atras=120)
    criar_log(dias_atras=200)

    assert limpar_logs_de_acesso_antigos(dias=90) == 2


def test_a_tarefa_periodica_fica_agendada():
    tarefa = PeriodicTask.objects.get(
        task="apps.api.core.tasks.limpar_logs_de_acesso_antigos"
    )

    assert tarefa.enabled
    assert tarefa.crontab.hour == "3"
