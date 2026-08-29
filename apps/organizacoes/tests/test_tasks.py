"""Processamento periódico dos encerramentos vencidos."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

import pytest
from celery.schedules import crontab

from apps.organizacoes import tasks
from apps.organizacoes.models import Organizacao
from apps.organizacoes.organizations import Organizacoes

from .test_closure import AssinaturasEncerramentoTeste

pytestmark = pytest.mark.django_db


def test_task_efetiva_no_maximo_o_lote_configurado(monkeypatch, settings):
    agora = timezone.now()
    organizacoes = [
        Organizacao.objects.create(
            nome=f"Organização {indice}",
            slug=f"task-{indice}",
            encerramento_solicitado_em=agora - timedelta(days=2),
            encerramento_agendado_para=agora - timedelta(days=1),
        )
        for indice in range(2)
    ]
    settings.ORGANIZATION_CLOSURE_BATCH_SIZE = 1
    monkeypatch.setattr(tasks, "_carregar_assinaturas", lambda: AssinaturasEncerramentoTeste)

    tasks.efetivar_encerramentos_vencidos.run()

    assert Organizacao.all_objects.filter(pk__in=[item.pk for item in organizacoes], is_deleted=True).count() == 1
    tasks.efetivar_encerramentos_vencidos.run()
    assert Organizacao.all_objects.filter(pk__in=[item.pk for item in organizacoes], is_deleted=True).count() == 2


def test_task_periodica_usa_lote_configuravel():
    entry = settings.CELERY_BEAT_SCHEDULE["close-expired-organizations"]

    assert tasks.efetivar_encerramentos_vencidos.name == "organizacoes.efetivar_encerramentos_vencidos"
    assert tasks.efetivar_encerramentos_vencidos.ignore_result is True
    assert tasks.efetivar_encerramentos_vencidos.autoretry_for
    assert entry["task"] == tasks.efetivar_encerramentos_vencidos.name
    assert isinstance(entry["schedule"], crontab)
    assert settings.ORGANIZATION_CLOSURE_BATCH_SIZE == 100


def test_lote_de_encerramento_precisa_ser_positivo():
    with pytest.raises(ValueError, match="positivo"):
        Organizacoes.efetivar_encerramentos_vencidos(
            assinaturas=AssinaturasEncerramentoTeste,
            batch_size=0,
        )
