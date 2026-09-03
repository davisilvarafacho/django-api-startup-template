from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import Mock

from django.db import close_old_connections, connection, connections
from django.utils import timezone

import pytest
from django_checkouts.enums import Gateway, InvoiceReason, InvoiceStatus, ResourceKind, RetryDisposition, SetupStatus, SubscriptionStatus
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError, RetryAdvice

from apps.assinaturas.models import StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subapps.faturamento.checkouts import criar_referencia_checkout
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    CheckpointReconciliacao,
    EventoCobranca,
    FaturaAssinatura,
    FinalidadeCheckout,
    ReaberturaEventoCobranca,
    SolicitacaoReconciliacaoCobranca,
    StatusCheckout,
    StatusEventoCobranca,
)
from apps.assinaturas.subapps.faturamento.processing import (
    ClaimEvento,
    _aplicar_fatura,
    _validar_identidade,
    _validar_itens_assinatura,
    calcular_backoff,
    claim_evento,
    finalizar_evento,
    processar_evento,
    reabrir_evento_operacional,
    reabrir_evento_reconciliado,
    reconciliar_duravel,
    reconciliar_janela,
    registrar_falha,
    solicitar_reconciliacao_operacional,
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
    recurso = remoto[0] if kind == ResourceKind.INVOICE else remoto
    if not hasattr(recurso, "gateway"):
        recurso.gateway = Gateway.STRIPE
    claim = ClaimEvento(1, 1, "stripe", tipo, "cs_1", "sub_1", "in_1", 1, 1, 1, None, None, "stripe")
    with pytest.raises(ValueError, match="remote_"):
        _validar_identidade(claim, kind, remoto)


@pytest.mark.parametrize(
    ("gateway", "checkout_referencia"),
    [
        ("outro", 1),
        ("stripe", 2),
    ],
)
def test_invoice_valida_gateway_e_referencia_da_assinatura_relacionada(gateway, checkout_referencia):
    claim = ClaimEvento(1, 7, "stripe", "invoice.paid", "", "sub_1", "in_1", 1, 1, 1, None, 2, "stripe")
    referencia = criar_referencia_checkout(1, 7)
    invoice = SimpleNamespace(
        gateway="stripe",
        variant="stripe",
        external_id="in_1",
        subscription_id="sub_1",
        reference_id=referencia,
    )
    subscription = SimpleNamespace(
        gateway=gateway,
        variant="stripe",
        external_id="sub_1",
        reference_id=criar_referencia_checkout(checkout_referencia, 7),
    )
    with pytest.raises(ValueError, match="remote_invoice_subscription_mismatch"):
        _validar_identidade(claim, ResourceKind.INVOICE, (invoice, subscription))


def test_invoice_rejeita_referencias_assinadas_para_outro_tenant():
    claim = ClaimEvento(1, 7, "stripe", "invoice.paid", "", "sub_1", "in_1", 1, 1, 1, None, 2, "stripe")
    referencia_alheia = criar_referencia_checkout(99, 8)
    invoice = SimpleNamespace(
        gateway=Gateway.STRIPE,
        variant="stripe",
        external_id="in_1",
        subscription_id="sub_1",
        reference_id=referencia_alheia,
    )
    subscription = SimpleNamespace(
        gateway=Gateway.STRIPE,
        variant="stripe",
        external_id="sub_1",
        reference_id=referencia_alheia,
    )

    with pytest.raises(ValueError, match="remote_invoice_reference_tenant_mismatch"):
        _validar_identidade(claim, ResourceKind.INVOICE, (invoice, subscription))


def test_itens_enterprise_sem_versao_aceitam_mapeamento_inequivoco():
    assinatura = SimpleNamespace(
        versao_plano=None,
        periodicidade=10,
        moeda="BRL",
        valor_base_centavos=1_000,
        valor_seat_centavos=250,
        seats_contratados=4,
        seats_inclusos=2,
    )
    remoto = SimpleNamespace(
        variant="stripe",
        items=(
            SimpleNamespace(price_id=None, unit_amount=1_000, currency="BRL", quantity=1),
            SimpleNamespace(price_id=None, unit_amount=250, currency="BRL", quantity=2),
        ),
    )

    _validar_itens_assinatura(assinatura, remoto)


def test_itens_enterprise_sem_versao_rejeitam_componentes_ambiguos():
    assinatura = SimpleNamespace(
        versao_plano=None,
        periodicidade=10,
        moeda="BRL",
        valor_base_centavos=500,
        valor_seat_centavos=500,
        seats_contratados=2,
        seats_inclusos=1,
    )
    remoto = SimpleNamespace(
        variant="stripe",
        items=(SimpleNamespace(price_id=None, unit_amount=500, currency="BRL", quantity=1),),
    )

    with pytest.raises(ValueError, match="remote_subscription_price_mismatch"):
        _validar_itens_assinatura(assinatura, remoto)


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

    primeiro = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
    segundo = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)

    assert primeiro is not None
    assert segundo is None
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()


@pytest.mark.django_db(transaction=True)
def test_dois_workers_com_conexoes_reais_produzem_um_unico_claim_sem_deadlock():
    organizacao, evento, agora = _evento_mapeado(sufixo="worker-real")
    barreira = Barrier(2)

    def reivindicar():
        close_old_connections()
        try:
            barreira.wait(timeout=10)
            return claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = [futuro.result(timeout=10) for futuro in (pool.submit(reivindicar), pool.submit(reivindicar))]

    assert sum(resultado is not None for resultado in resultados) == 1
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
        assert evento.tentativas_processamento == 1
        assert evento.status == StatusEventoCobranca.PROCESSANDO
        assert evento.tentativas_processamento == 1


@pytest.mark.django_db(transaction=True)
def test_dois_workers_completos_fazem_uma_unica_recuperacao_remota():
    organizacao, evento, _ = _evento_mapeado(sufixo="worker-completo")
    barreira = Barrier(2)
    lock = Lock()
    recuperacoes = 0
    remoto = SimpleNamespace(
        gateway=Gateway.STRIPE,
        variant="stripe",
        external_id="sub_worker-completo",
        status=SubscriptionStatus.CANCELED,
    )

    class Subscriptions:
        def retrieve(self, external_id):
            nonlocal recuperacoes
            assert external_id == remoto.external_id
            with lock:
                recuperacoes += 1
            return remoto

    def executar_worker():
        close_old_connections()
        try:
            barreira.wait(timeout=10)
            client = SimpleNamespace(variant="stripe", gateway=Gateway.STRIPE, subscriptions=Subscriptions())
            return processar_evento(evento.pk, organizacao.pk, "stripe", client=client)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = [futuro.result(timeout=15) for futuro in (pool.submit(executar_worker), pool.submit(executar_worker))]

    assert sorted(resultados) == [False, True]
    assert recuperacoes == 1
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == StatusEventoCobranca.PROCESSADO


@pytest.mark.parametrize("status", [StatusEventoCobranca.FALHOU, StatusEventoCobranca.PROCESSADO, StatusEventoCobranca.IGNORADO])
@pytest.mark.django_db(transaction=True)
def test_claim_mensagem_residual_terminal_e_noop_sem_consumir_tentativa(status):
    organizacao, evento, agora = _evento_mapeado(sufixo=f"terminal-{status}")
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.status = status
        evento.save(update_fields=["status", "last_modified_at"])

    assert claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora) is None

    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.tentativas_processamento == 0
    assert evento.tentativas_automaticas_ciclo == 0


@pytest.mark.django_db(transaction=True)
def test_claim_variante_incorreta_e_noop_antes_de_mutar():
    organizacao, evento, agora = _evento_mapeado(sufixo="variant-noop")

    assert claim_evento(evento.pk, organizacao.pk, variante="outra", agora=agora) is None

    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == StatusEventoCobranca.ROTEADO
    assert evento.tentativas_processamento == 0


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

    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)

    assert claim is not None
    assert claim.assinatura_pk == antigo.pk
    assert claim.assinatura_pk != atual.pk
    assert claim.checkout_pk == (checkout.pk if checkout else None)


@pytest.mark.parametrize("familia", ["checkout", "setup", "subscription", "invoice"])
@pytest.mark.django_db(transaction=True)
def test_finalize_atrasado_atualiza_apenas_contrato_historico(familia):
    """Retrieve/finalize de A nunca escolhe ou altera a assinatura B corrente."""
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome=f"Finalize {familia}", slug=f"finalize-{familia}")
    versao = _criar_versao(codigo=f"finalize-{familia}")
    status_antigo = {"checkout": StatusAssinatura.PENDENTE, "setup": StatusAssinatura.ATIVA}.get(familia, StatusAssinatura.ENCERRADA)
    antigo = _criar_assinatura(
        organizacao,
        versao,
        status=status_antigo,
        status_financeiro=StatusFinanceiro.PENDENTE if status_antigo == StatusAssinatura.PENDENTE else StatusFinanceiro.REGULAR,
        encerrada_em=agora - timedelta(days=1) if status_antigo == StatusAssinatura.ENCERRADA else None,
        motivo_encerramento="substituido" if status_antigo == StatusAssinatura.ENCERRADA else None,
        chave_idempotencia=f"finalize-old-{familia}",
    )
    mapping = AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=antigo,
        variante="stripe",
        identificador_externo=f"sub_finalize_{familia}",
    )
    checkout = None
    if familia in {"checkout", "setup"}:
        with organizacao_atual_privilegiada(organizacao.pk):
            checkout = CheckoutCobranca.objects.create(
                organizacao=organizacao,
                assinatura=antigo,
                finalidade=FinalidadeCheckout.FORMA_PAGAMENTO if familia == "setup" else FinalidadeCheckout.CONTRATACAO,
                status=StatusCheckout.ABERTO,
                chave_idempotencia=f"finalize-checkout-{familia}",
                operacao_chave=f"finalize-{familia}",
                snapshot_hash="e" * 64,
                variante="stripe",
                identificador_externo=f"cs_finalize_{familia}",
                valor_esperado_centavos=0 if familia == "setup" else 1000,
                moeda_esperada="BRL",
            )
        Assinaturas.encerrar(organizacao, encerrada_em=agora - timedelta(days=1))
    atual = _criar_assinatura(organizacao, versao, chave_idempotencia=f"finalize-current-{familia}")
    campos_financeiros = (
        "status",
        "status_financeiro",
        "revisao",
        "seats_contratados",
        "periodo_atual_iniciado_em",
        "periodo_atual_termina_em",
        "encerrada_em",
    )
    antes = tuple(getattr(atual, campo) for campo in campos_financeiros)
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento=f"evt_finalize_{familia}",
            tipo=f"{familia}.paid" if familia in {"checkout", "invoice"} else f"{familia}.updated",
            identificador_checkout=checkout.identificador_externo if checkout else "",
            identificador_assinatura=mapping.identificador_externo,
            identificador_fatura=f"in_finalize_{familia}" if familia == "invoice" else "",
            status=StatusEventoCobranca.ROTEADO,
            hash_payload="f" * 64,
            ocorrido_em=agora,
        )
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
    assert claim is not None
    referencia = criar_referencia_checkout(checkout.pk, organizacao.pk) if checkout else None
    if familia == "checkout":
        kind = ResourceKind.CHECKOUT
        remoto = SimpleNamespace(
            gateway=Gateway.STRIPE,
            variant="stripe",
            external_id=checkout.identificador_externo,
            reference_id=referencia,
            amount_total=1000,
            currency="BRL",
            mode="subscription",
            status="paid",
            subscription_id="sub_remote_old",
        )
    elif familia == "setup":
        kind = ResourceKind.SETUP
        remoto = SimpleNamespace(
            gateway=Gateway.STRIPE,
            variant="stripe",
            external_id=checkout.identificador_externo,
            reference_id=referencia,
            status=SetupStatus.COMPLETE,
        )
    elif familia == "subscription":
        kind = ResourceKind.SUBSCRIPTION
        remoto = SimpleNamespace(
            gateway=Gateway.STRIPE,
            variant="stripe",
            external_id=mapping.identificador_externo,
            status=SubscriptionStatus.CANCELED,
        )
    else:
        kind = ResourceKind.INVOICE
        invoice = SimpleNamespace(
            gateway=Gateway.STRIPE,
            variant="stripe",
            external_id="in_finalize_invoice",
            subscription_id=mapping.identificador_externo,
            status=InvoiceStatus.PAID,
            reason=InvoiceReason.RENEWAL,
            currency="BRL",
            lines=(),
            amount_due=1000,
            amount_paid=1000,
            amount_remaining=0,
            subtotal=900,
            discount_total=0,
            tax_total=100,
            total=1000,
            due_at=agora,
            paid_at=agora,
            next_payment_attempt_at=None,
            attempt_count=1,
            hosted_url=None,
        )
        remoto = (invoice, None)

    assert finalizar_evento(claim, kind, remoto, agora=agora) is True
    with organizacao_atual_privilegiada(organizacao.pk):
        atual.refresh_from_db()
        mapping.refresh_from_db()
        evento.refresh_from_db()
        assert tuple(getattr(atual, campo) for campo in campos_financeiros) == antes
        assert evento.status == StatusEventoCobranca.PROCESSADO
        assert mapping.assinatura_id == antigo.pk


@pytest.mark.django_db(transaction=True)
def test_fatura_preserva_fatos_indisponiveis_distintos_de_zero_explicito():
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome="Fatos opcionais", slug="fatos-opcionais")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="fatos-opcionais"))

    def invoice(external_id, valor):
        return SimpleNamespace(
            gateway=Gateway.STRIPE,
            variant="stripe",
            external_id=external_id,
            subscription_id="sub_fatos",
            status=InvoiceStatus.DRAFT,
            reason=InvoiceReason.RENEWAL,
            currency="BRL",
            lines=(),
            amount_due=0,
            amount_paid=0,
            amount_remaining=0,
            subtotal=valor,
            discount_total=valor,
            tax_total=valor,
            total=valor,
            due_at=None,
            paid_at=None,
            next_payment_attempt_at=None,
            attempt_count=0,
            hosted_url=None,
            reference_id=None,
        )

    with organizacao_atual_privilegiada(organizacao.pk):
        _aplicar_fatura(assinatura, invoice("in_indisponivel", None), agora=agora)
        _aplicar_fatura(assinatura, invoice("in_zero", 0), agora=agora)
        indisponivel = FaturaAssinatura.objects.get(identificador_externo="in_indisponivel")
        zero = FaturaAssinatura.objects.get(identificador_externo="in_zero")

    campos = ("subtotal_centavos", "desconto_centavos", "imposto_centavos", "total_centavos")
    assert tuple(getattr(indisponivel, campo) for campo in campos) == (None, None, None, None)
    assert tuple(getattr(zero, campo) for campo in campos) == (0, 0, 0, 0)


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
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
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
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
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


@pytest.mark.django_db(transaction=True)
def test_reconciliacao_operacional_persiste_parametros_ator_resultado_e_idempotencia(monkeypatch):
    organizacao, evento, agora = _evento_mapeado(sufixo="audit-reconcile")
    ator = Usuario.objects.create_user(email="reconcile@example.com", password=None)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))

    primeira = solicitar_reconciliacao_operacional(
        evento=evento,
        ator=ator,
        motivo="Incidente no gateway",
        chave_idempotencia="admin-reconcile:1",
        agora=agora,
    )
    segunda = solicitar_reconciliacao_operacional(
        evento=evento,
        ator=ator,
        motivo="Incidente no gateway",
        chave_idempotencia="admin-reconcile:1",
        agora=agora,
    )

    assert primeira.pk == segunda.pk
    with organizacao_atual_privilegiada(organizacao.pk):
        auditoria = SolicitacaoReconciliacaoCobranca.objects.get(pk=primeira.pk)
    assert auditoria.ator == ator
    assert auditoria.motivo == "Incidente no gateway"
    assert auditoria.janela_inicio == agora - timedelta(minutes=20)
    assert auditoria.janela_fim == agora
    assert auditoria.parametros == {"evento_id": evento.pk, "janela_segundos": 1200}
    assert auditoria.resultado == "agendada"
    envio.assert_called_once_with(
        "faturamento.executar_reconciliacao_operacional",
        args=(primeira.pk, organizacao.pk),
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_first_reabre_automaticamente_uma_vez_e_preserva_auditoria(monkeypatch):
    organizacao, evento, agora = _evento_mapeado(sufixo="reconcile-first")
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))
    erro = GatewayPermanentError(
        "incerto",
        gateway="stripe",
        variant="stripe",
        retry_advice=RetryAdvice(disposition=RetryDisposition.RECONCILE_FIRST),
    )
    registrar_falha(claim, erro, agora=agora)
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == StatusEventoCobranca.FALHOU
    assert evento.aguarda_reconciliacao is True
    envio.reset_mock()

    primeiro = reabrir_evento_reconciliado(evento_id=evento.pk, organizacao_id=organizacao.pk)
    segundo = reabrir_evento_reconciliado(evento_id=evento.pk, organizacao_id=organizacao.pk)

    assert primeiro is not None
    assert segundo is not None
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
        auditorias = list(ReaberturaEventoCobranca.objects.filter(evento=evento))
    assert evento.status == StatusEventoCobranca.ROTEADO
    assert evento.aguarda_reconciliacao is False
    assert evento.tentativas_automaticas_ciclo == 1
    assert len(auditorias) == 1
    assert auditorias[0].automatica is True
    assert auditorias[0].ator is None
    envio.assert_called_once_with(
        "faturamento.processar_evento_cobranca",
        args=(evento.pk, "stripe", organizacao.pk),
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_first_enfileira_ciclo_direcionado_uma_unica_vez(monkeypatch):
    organizacao, evento, agora = _evento_mapeado(sufixo="reconcile-directed")
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))
    erro = GatewayPermanentError(
        "incerto",
        gateway="stripe",
        variant="stripe",
        retry_advice=RetryAdvice(disposition=RetryDisposition.RECONCILE_FIRST),
    )

    registrar_falha(claim, erro, agora=agora)
    registrar_falha(claim, erro, agora=agora)

    envio.assert_called_once_with(
        "faturamento.reconciliar_evento_cobranca",
        args=(evento.pk, "stripe", organizacao.pk),
    )


@pytest.mark.django_db(transaction=True)
def test_reconcile_first_na_oitava_tentativa_fica_terminal_sem_novo_ciclo(monkeypatch):
    organizacao, evento, agora = _evento_mapeado(sufixo="reconcile-cap8", tentativas_ciclo=7)
    claim = claim_evento(evento.pk, organizacao.pk, variante="stripe", agora=agora)
    envio = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.processing.current_app", SimpleNamespace(send_task=envio))
    erro = GatewayPermanentError(
        "incerto",
        gateway="stripe",
        variant="stripe",
        retry_advice=RetryAdvice(disposition=RetryDisposition.RECONCILE_FIRST),
    )

    registrar_falha(claim, erro, agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        evento.refresh_from_db()
    assert evento.status == StatusEventoCobranca.FALHOU
    assert evento.tentativas_automaticas_ciclo == 8
    assert evento.aguarda_reconciliacao is False
    envio.assert_not_called()
