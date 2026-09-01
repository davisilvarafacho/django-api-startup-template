from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.db import connection
from django.utils import timezone

import pytest

from apps.assinaturas.subapps.faturamento.models import EventoCobranca, StatusEventoCobranca
from apps.assinaturas.subapps.faturamento.processing import calcular_backoff, claim_evento, reconciliar_janela
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


def test_backoff_exponencial_tem_cap_e_jitter_deterministico():
    assert calcular_backoff(1, jitter=0) == timedelta(seconds=30)
    assert calcular_backoff(4, jitter=0) == timedelta(minutes=4)
    assert calcular_backoff(8, jitter=0) == timedelta(hours=1)
    assert calcular_backoff(8, jitter=0.5) == timedelta(hours=1)


def test_reconciliacao_pagina_e_reusa_ingestao_fora_de_transacao():
    inicio = datetime(2026, 8, 1, tzinfo=UTC)
    fim = inicio + timedelta(hours=1)
    evento_a = SimpleNamespace(variant="stripe")
    evento_b = SimpleNamespace(variant="stripe")
    paginas = [
        SimpleNamespace(items=(evento_a,), next_cursor="cursor-2", occurred_since=inicio, occurred_before=fim),
        SimpleNamespace(items=(evento_b,), next_cursor=None, occurred_since=inicio, occurred_before=fim),
    ]
    chamadas = []

    class Events:
        def list(self, **kwargs):
            assert connection.in_atomic_block is False
            chamadas.append(kwargs)
            return paginas.pop(0)

    recebidos = []
    total = reconciliar_janela(
        variante="stripe",
        inicio=inicio,
        fim=fim,
        client=SimpleNamespace(events=Events()),
        receber=lambda variante, evento, *, client: recebidos.append((variante, evento, client)),
    )

    assert total == 2
    assert [c["cursor"] for c in chamadas] == [None, "cursor-2"]
    assert [item[1] for item in recebidos] == [evento_a, evento_b]


@pytest.mark.django_db(transaction=True)
def test_worker_concorrente_nao_reivindica_lease_vigente():
    organizacao = Organizacao.objects.create(nome="Worker", slug="worker-lease")
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt_worker_lease",
            tipo="invoice.paid",
            identificador_fatura="in_worker_lease",
            status=StatusEventoCobranca.ROTEADO,
            hash_payload="a" * 64,
            ocorrido_em=agora,
        )

    primeiro = claim_evento(evento.pk, organizacao.pk, agora=agora)
    segundo = claim_evento(evento.pk, organizacao.pk, agora=agora)

    assert primeiro is not None
    assert segundo is None
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
        assert evento.tentativas_processamento == 1
