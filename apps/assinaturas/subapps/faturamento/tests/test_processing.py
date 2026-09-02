from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.db import connection
from django.utils import timezone

import pytest
from django_checkouts.enums import ResourceKind, RetryDisposition
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError, RetryAdvice

from apps.assinaturas.models import StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    CheckpointReconciliacao,
    EventoCobranca,
    FinalidadeCheckout,
    ReaberturaEventoCobranca,
    StatusCheckout,
    StatusEventoCobranca,
)
from apps.assinaturas.subapps.faturamento.processing import (
    ClaimEvento,
    _validar_identidade,
    calcular_backoff,
    claim_evento,
    reabrir_evento_operacional,
    reconciliar_duravel,
    reconciliar_janela,
    registrar_falha,
)
from apps.assinaturas.subscriptions import Assinaturas
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario


def _evento_mapeado(*, sufixo: str, tentativas_ciclo: int = 0):
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome=f"Retry {sufixo}", slug=f"retry-{sufixo}")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo=f"retry-{sufixo}"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo=f"sub_{sufixo}",
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento=f"evt_{sufixo}",
            tipo="subscription.updated",
            identificador_assinatura=f"sub_{sufixo}",
            status=StatusEventoCobranca.ROTEADO,
            tentativas_automaticas_ciclo=tentativas_ciclo,
            hash_payload="d" * 64,
            ocorrido_em=agora,
        )
    return organizacao, evento, agora


def test_backoff_exponencial_tem_cap_e_jitter_deterministico():
    assert calcular_backoff(1, jitter=0) == timedelta(seconds=30)
    assert calcular_backoff(4, jitter=0) == timedelta(minutes=4)
    assert calcular_backoff(8, jitter=0) == timedelta(hours=1)
    assert calcular_backoff(8, jitter=0.5) == timedelta(hours=1)


@pytest.mark.parametrize(
    ("tipo", "kind", "remoto"),
    [
        ("checkout.paid", ResourceKind.SETUP, SimpleNamespace(variant="stripe", external_id="cs_1")),
        ("setup.updated", ResourceKind.SETUP, SimpleNamespace(variant="outra", external_id="cs_1")),
        ("subscription.updated", ResourceKind.SUBSCRIPTION, SimpleNamespace(variant="stripe", external_id="sub_errada")),
        (
            "invoice.paid",
            ResourceKind.INVOICE,
            (SimpleNamespace(variant="stripe", external_id="in_1", subscription_id="sub_errada"), None),
        ),
    ],
)
def test_matriz_remota_rejeita_kind_variante_ids_e_invoice_subscription(tipo, kind, remoto):
    claim = ClaimEvento(1, 1, "stripe", tipo, "cs_1", "sub_1", "in_1", 1, 1, 1, None, None)
    with pytest.raises(ValueError, match="remote_"):
        _validar_identidade(claim, kind, remoto)


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
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="worker-lease"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_worker_lease",
    )
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt_worker_lease",
            tipo="invoice.paid",
            identificador_fatura="in_worker_lease",
            identificador_assinatura="sub_worker_lease",
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


@pytest.mark.django_db
def test_checkpoint_reconciliacao_e_reabertura_preservam_historico():
    inicio = timezone.now() - timedelta(hours=1)
    checkpoint = CheckpointReconciliacao.objects.create(
        variante="stripe",
        janela_inicio=inicio,
        janela_fim=inicio + timedelta(minutes=20),
    )
    assert checkpoint.cursor == ""
    assert checkpoint.ultimo_limite_concluido is None
    campos = {field.name for field in ReaberturaEventoCobranca._meta.fields}
    assert {"evento", "motivo", "ator", "chave_idempotencia", "tentativas_anteriores"} <= campos


@pytest.mark.parametrize("familia", ["checkout", "setup", "subscription", "invoice"])
@pytest.mark.django_db(transaction=True)
def test_claim_evento_atrasado_fica_no_contrato_historico(familia):
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome=f"Histórico {familia}", slug=f"historico-{familia}")
    versao = _criar_versao(codigo=f"historico-{familia}")
    status_antigo_inicial = {
        "checkout": StatusAssinatura.PENDENTE,
        "setup": StatusAssinatura.ATIVA,
    }.get(familia, StatusAssinatura.ENCERRADA)
    antigo = _criar_assinatura(
        organizacao,
        versao,
        status=status_antigo_inicial,
        status_financeiro=StatusFinanceiro.PENDENTE if status_antigo_inicial == StatusAssinatura.PENDENTE else StatusFinanceiro.REGULAR,
        encerrada_em=agora - timedelta(days=1) if status_antigo_inicial == StatusAssinatura.ENCERRADA else None,
        motivo_encerramento="substituido" if status_antigo_inicial == StatusAssinatura.ENCERRADA else None,
        chave_idempotencia=f"antigo-{familia}",
    )
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=antigo,
        variante="stripe",
        identificador_externo=f"sub_old_{familia}",
    )
    checkout = None
    if familia in {"checkout", "setup"}:
        with organizacao_atual_privilegiada(organizacao.pk):
            checkout = CheckoutCobranca.objects.create(
                organizacao=organizacao,
                assinatura=antigo,
                finalidade=FinalidadeCheckout.FORMA_PAGAMENTO if familia == "setup" else FinalidadeCheckout.CONTRATACAO,
                status=StatusCheckout.ABERTO,
                chave_idempotencia=f"checkout-{familia}",
                operacao_chave=f"historico-{familia}",
                snapshot_hash="b" * 64,
                variante="stripe",
                identificador_externo=f"cs_old_{familia}",
                valor_esperado_centavos=0 if familia == "setup" else 1000,
                moeda_esperada="BRL",
            )
        if familia in {"checkout", "setup"}:
            Assinaturas.encerrar(organizacao, encerrada_em=agora - timedelta(days=1))
    atual = _criar_assinatura(organizacao, versao, chave_idempotencia=f"atual-{familia}")
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento=f"evt_old_{familia}",
            tipo=f"{familia}.paid" if familia == "checkout" else f"{familia}.updated",
            identificador_checkout=f"cs_old_{familia}" if checkout else "",
            identificador_assinatura=f"sub_old_{familia}",
            identificador_fatura="in_old" if familia == "invoice" else "",
            status=StatusEventoCobranca.ROTEADO,
            hash_payload="c" * 64,
            ocorrido_em=agora,
        )

    claim = claim_evento(evento.pk, organizacao.pk, agora=agora)

    assert claim is not None
    assert claim.assinatura_pk == antigo.pk
    assert claim.assinatura_pk != atual.pk
    assert claim.checkout_pk == (checkout.pk if checkout else None)


@pytest.mark.django_db(transaction=True)
def test_reconciliacao_crash_na_ingestao_nao_avanca_cursor_e_restart_repete_pagina():
    agora = timezone.now()
    evento = SimpleNamespace(variant="stripe")
    pagina = SimpleNamespace(items=(evento,), next_cursor="cursor-2")
    chamadas = []

    class Events:
        def list(self, **kwargs):
            chamadas.append(kwargs)
            return pagina

    client = SimpleNamespace(events=Events())
    with pytest.raises(RuntimeError, match="crash"):
        reconciliar_duravel(
            variante="stripe",
            client=client,
            agora=agora,
            receber=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
        )
    checkpoint = CheckpointReconciliacao.objects.get(variante="stripe")
    assert checkpoint.cursor == ""
    checkpoint.lease_ate = agora - timedelta(seconds=1)
    checkpoint.save(update_fields=["lease_ate", "last_modified_at"])

    recebidos = []
    reconciliar_duravel(
        variante="stripe",
        client=client,
        agora=agora,
        receber=lambda variante, item, *, client: recebidos.append(item),
    )

    assert recebidos == [evento]
    assert chamadas[0]["cursor"] is None
    assert chamadas[1]["cursor"] is None
    assert CheckpointReconciliacao.objects.get(variante="stripe").cursor == "cursor-2"


@pytest.mark.parametrize(
    ("disposition", "status_esperado", "agenda_reconciliacao"),
    [
        (RetryDisposition.NEVER, StatusEventoCobranca.FALHOU, False),
        (RetryDisposition.RETRY, StatusEventoCobranca.ROTEADO, False),
        (RetryDisposition.RETRY_SAME_KEY, StatusEventoCobranca.ROTEADO, False),
        (RetryDisposition.RECONCILE_FIRST, StatusEventoCobranca.FALHOU, True),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_retry_dispositions_sao_finitas_e_reconcile_first_agenda(monkeypatch, disposition, status_esperado, agenda_reconciliacao):
    organizacao, evento, agora = _evento_mapeado(sufixo=disposition.value)
    claim = claim_evento(evento.pk, organizacao.pk, agora=agora)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))
    erro = GatewayPermanentError(
        "erro",
        gateway="stripe",
        variant="stripe",
        retry_advice=RetryAdvice(disposition=disposition, retry_after=timedelta(minutes=7)),
    )

    registrar_falha(claim, erro, agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == status_esperado
    assert (evento.proxima_tentativa_em == agora + timedelta(minutes=7)) is (status_esperado == StatusEventoCobranca.ROTEADO)
    assert envio.called is agenda_reconciliacao


@pytest.mark.django_db(transaction=True)
def test_cap_oito_e_reabertura_operacional_preservam_total_e_auditoria(monkeypatch):
    organizacao, evento, agora = _evento_mapeado(sufixo="cap8", tentativas_ciclo=7)
    claim = claim_evento(evento.pk, organizacao.pk, agora=agora)
    registrar_falha(claim, GatewayTemporaryError("temp", gateway="stripe", variant="stripe"), agora=agora)
    ator = Usuario.objects.create_user(email="operador@example.com", password=None)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == StatusEventoCobranca.FALHOU
    assert evento.tentativas_processamento == 1
    reaberto = reabrir_evento_operacional(evento=evento, ator=ator, motivo="Incidente resolvido", chave_idempotencia="ops-cap8")
    repetido = reabrir_evento_operacional(evento=reaberto, ator=ator, motivo="Incidente resolvido", chave_idempotencia="ops-cap8")
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
        auditoria = ReaberturaEventoCobranca.objects.get(evento=evento)
    assert repetido.pk == evento.pk
    assert evento.status == StatusEventoCobranca.ROTEADO
    assert evento.tentativas_processamento == 1
    assert evento.tentativas_automaticas_ciclo == 0
    assert auditoria.tentativas_anteriores == 1
    assert auditoria.ator == ator
