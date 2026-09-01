"""Processamento periodico das transicoes comerciais locais."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import pytest
from celery.schedules import crontab

from apps.assinaturas import tasks
from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura
from apps.assinaturas.subscriptions import Assinaturas, ConflitoRevisaoAssinatura
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Papel, Vinculo
from tests.support.usuarios import criar_usuario

from .test_subscription_access_transitions import _assinatura_ativa, _trial

pytestmark = pytest.mark.django_db


def test_task_encerra_somente_trials_vencidos_e_e_idempotente(settings):
    organizacao, assinatura = _trial(slug="task-trial-vencido")
    _, vigente = _trial(slug="task-trial-vigente")
    vigente.trial_termina_em = timezone.now() + timedelta(days=2)
    with organizacao_atual_privilegiada(vigente.organizacao_id):
        vigente.save(update_fields=["trial_termina_em", "last_modified_at"])
    settings.SUBSCRIPTION_TASK_BATCH_SIZE = 1

    primeira = tasks.encerrar_trials_vencidos.run()
    repetida = tasks.encerrar_trials_vencidos.run()

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert primeira == 1
    assert repetida == 0
    assert assinatura.status == StatusAssinatura.ATIVA
    assert assinatura.versao_plano.plano.codigo == "gratuito"


def test_task_reconcilia_abertura_e_limpeza_da_carencia_sem_reiniciar_prazo(settings):
    organizacao, assinatura = _assinatura_ativa(slug="task-carencia-seats", seats=1)
    vinculos = [
        Vinculo.objects.create(
            organizacao=organizacao,
            usuario=criar_usuario(email=f"task-seat-{indice}@example.com"),
            papel=Papel.MEMBRO,
        )
        for indice in range(2)
    ]
    settings.SUBSCRIPTION_TASK_BATCH_SIZE = 1

    primeira = tasks.reconciliar_carencias_seats.run()
    repetida = tasks.reconciliar_carencias_seats.run()
    vinculos[0].delete()
    regularizada = tasks.reconciliar_carencias_seats.run()

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = AssinaturaOrganizacao.all_objects.get(pk=assinatura.pk)
    assert primeira == 1
    assert repetida == 0
    assert regularizada == 1
    assert assinatura.carencia_excesso_seats_iniciada_em is None
    assert assinatura.carencia_excesso_seats_termina_em is None
    assert assinatura.revisao == 3


def test_task_ignora_trial_que_evento_concorrente_ja_encerrou(settings, monkeypatch):
    _trial(slug="task-trial-evento-concorrente")
    settings.SUBSCRIPTION_TASK_BATCH_SIZE = 1

    def evento_venceu(*args, **kwargs):
        raise ConflitoRevisaoAssinatura("O evento encerrou o trial primeiro.")

    monkeypatch.setattr(Assinaturas, "encerrar_trial", evento_venceu)

    assert tasks.encerrar_trials_vencidos.run() == 0


def test_tasks_periodicas_usam_lote_configuravel():
    trials = settings.CELERY_BEAT_SCHEDULE["finish-expired-subscription-trials"]
    carencias = settings.CELERY_BEAT_SCHEDULE["reconcile-subscription-seat-graces"]

    assert tasks.encerrar_trials_vencidos.name == "assinaturas.encerrar_trials_vencidos"
    assert tasks.reconciliar_carencias_seats.name == "assinaturas.reconciliar_carencias_seats"
    assert tasks.encerrar_trials_vencidos.autoretry_for
    assert tasks.reconciliar_carencias_seats.autoretry_for
    assert trials["task"] == tasks.encerrar_trials_vencidos.name
    assert carencias["task"] == tasks.reconciliar_carencias_seats.name
    assert isinstance(trials["schedule"], crontab)
    assert isinstance(carencias["schedule"], crontab)
    assert settings.SUBSCRIPTION_TASK_BATCH_SIZE == 100


@pytest.mark.parametrize("task", [tasks.encerrar_trials_vencidos, tasks.reconciliar_carencias_seats])
def test_lote_das_tasks_precisa_ser_positivo(task, settings):
    settings.SUBSCRIPTION_TASK_BATCH_SIZE = 0

    with pytest.raises(ValueError, match="positivo"):
        task.run()
