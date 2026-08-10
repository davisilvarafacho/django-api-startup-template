"""Testes do expurgo periódico dos logs de acesso do django-axes."""

from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.utils import timezone

import pytest
from axes.models import AccessLog
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from apps.api.core.tasks import limpar_logs_de_acesso_antigos

migracao_agendamento = import_module("apps.api.core.migrations.0001_schedule_access_log_cleanup")
agendar = migracao_agendamento.agendar
desagendar = migracao_agendamento.desagendar

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
    AccessLog.objects.filter(pk=log.pk).update(attempt_time=timezone.now() - timedelta(days=dias_atras))
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
    # A suíte roda com `--nomigrations`, então a linha semeada pela data migration
    # não existe: aplicamos a mesma função aqui. `agendar` é idempotente, então o
    # teste vale igual quando as migrations rodam de verdade.
    agendar(django_apps, None)

    tarefa = PeriodicTask.objects.get(task="apps.api.core.tasks.limpar_logs_de_acesso_antigos")

    assert tarefa.enabled
    assert tarefa.crontab.hour == "3"


def test_agendamento_por_nome_unico_remove_duplicatas_legadas():
    PeriodicTask.objects.filter(task="apps.api.core.tasks.limpar_logs_de_acesso_antigos").delete()
    agenda_legada = CrontabSchedule.objects.create(
        minute="30",
        hour="6",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.create(
        name="Expurgo legado 1",
        task="apps.api.core.tasks.limpar_logs_de_acesso_antigos",
        crontab=agenda_legada,
    )
    PeriodicTask.objects.create(
        name="Expurgo legado 2",
        task="apps.api.core.tasks.limpar_logs_de_acesso_antigos",
        crontab=agenda_legada,
    )

    agendar(django_apps, None)
    agendar(django_apps, None)

    tarefas = PeriodicTask.objects.filter(task="apps.api.core.tasks.limpar_logs_de_acesso_antigos")
    assert tarefas.count() == 1
    tarefa = tarefas.get()
    assert tarefa.name == "Expurgar logs de acesso antigos"
    assert tarefa.enabled
    assert tarefa.crontab.hour == "3"


def test_rollback_preserva_schedules_exclusivos_e_compartilhados():
    exclusivo = CrontabSchedule.objects.create(
        minute="15",
        hour="4",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    compartilhado = CrontabSchedule.objects.create(
        minute="45",
        hour="5",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.create(name="Tarefa alvo exclusiva", task="apps.api.core.tasks.limpar_logs_de_acesso_antigos", crontab=exclusivo)
    PeriodicTask.objects.create(name="Tarefa alvo compartilhada", task="apps.api.core.tasks.limpar_logs_de_acesso_antigos", crontab=compartilhado)
    PeriodicTask.objects.create(name="Outra tarefa", task="outra.tarefa", crontab=compartilhado)

    desagendar(django_apps, None)

    assert not PeriodicTask.objects.filter(task="apps.api.core.tasks.limpar_logs_de_acesso_antigos").exists()
    assert CrontabSchedule.objects.filter(pk=exclusivo.pk).exists()
    assert CrontabSchedule.objects.filter(pk=compartilhado.pk).exists()

    agenda, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.create(
        name="Expurgar logs de acesso antigos",
        task="apps.api.core.tasks.limpar_logs_de_acesso_antigos",
        crontab=agenda,
        enabled=True,
    )


def test_rollback_preserva_schedule_preexistente_reutilizado():
    PeriodicTask.objects.filter(task="apps.api.core.tasks.limpar_logs_de_acesso_antigos").delete()
    agenda, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )

    agendar(django_apps, None)
    desagendar(django_apps, None)

    assert CrontabSchedule.objects.filter(pk=agenda.pk).exists()
