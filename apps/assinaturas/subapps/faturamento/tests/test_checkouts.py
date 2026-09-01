from __future__ import annotations

from datetime import UTC, datetime

from django.db import connection
from django.urls import resolve

import pytest
from django_checkouts.capabilities import (
    CheckoutCapabilities,
    GatewayCapabilities,
    InvoiceCapabilities,
    ReconciliationCapabilities,
    SubscriptionCapabilities,
    WebhookCapabilities,
)
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import BillingCycle, CancellationTiming, ChangeTiming, CheckoutMode, CheckoutStatus, PaymentMethod, ProrationBehavior
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.gateways.commands import CreateCheckout
from django_checkouts.testing import FakeCheckoutGateway
from django_checkouts.types import Checkout

from apps.assinaturas.models import Periodicidade, StatusAssinatura
from apps.assinaturas.subapps.faturamento.checkouts import (
    CheckoutsCobranca,
    ConflitoCheckout,
    CriacaoCheckout,
    FalhaCheckoutIncerta,
    decodificar_referencia_checkout,
)
from apps.assinaturas.subapps.faturamento.models import CheckoutCobranca, ComponentePreco, FinalidadeCheckout, ReferenciaPrecoGateway, StatusCheckout
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

pytestmark = pytest.mark.django_db(transaction=True)


def _capabilities(*, catalog: bool = True, inline: bool = False) -> GatewayCapabilities:
    return GatewayCapabilities(
        checkouts=CheckoutCapabilities(
            modes=frozenset({CheckoutMode.SUBSCRIPTION}),
            payment_methods_by_mode={CheckoutMode.SUBSCRIPTION: frozenset({PaymentMethod.CARD})},
            billing_cycles=frozenset({BillingCycle.MONTHLY, BillingCycle.YEARLY}),
            supports_catalog_prices=catalog,
            supports_inline_prices=inline,
            supports_expiration=False,
            supports_customer_prefill=False,
        ),
        subscriptions=SubscriptionCapabilities(
            retrieve=True,
            change_quantity=True,
            replace_price=True,
            add_remove_items=True,
            timings=frozenset({ChangeTiming.IMMEDIATELY}),
            proration_behaviors=frozenset({ProrationBehavior.NONE}),
            cancellation_timings=frozenset({CancellationTiming.IMMEDIATELY}),
            resume_scheduled_cancellation=True,
            atomic_multi_change=True,
        ),
        invoices=InvoiceCapabilities(retrieve=True),
        webhooks=WebhookCapabilities(signed=True),
        reconciliation=ReconciliationCapabilities(events=True, maximum_page_size=100),
    )


def _checkout_result(command):
    assert connection.in_atomic_block is False
    request = command.request
    return Checkout(
        external_id="cs_test_1",
        gateway="fake",
        variant="stripe",
        status=CheckoutStatus.PENDING,
        mode=CheckoutMode.SUBSCRIPTION,
        url="https://checkout.example/cs_test_1",
        amount_total=1500,
        currency="BRL",
        customer=None,
        reference_id=request.reference_id,
        subscription_id=None,
        expires_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw={"secret": "must-not-persist"},
    )


def _cenario(*, status=StatusAssinatura.PENDENTE):
    organizacao = Organizacao.objects.create(nome="Checkout", slug=f"checkout-{Organizacao.objects.count()}")
    versao = _criar_versao(codigo=f"checkout-{organizacao.pk}")
    assinatura = _criar_assinatura(
        organizacao,
        versao,
        status=status,
        status_financeiro=20 if status == StatusAssinatura.PENDENTE else 30,
    )
    preco = versao.precos.get(periodicidade=Periodicidade.MENSAL, moeda="BRL")
    return organizacao, assinatura, preco


def test_referencia_assinada_rejeita_adulteracao_e_tenant_divergente():
    organizacao, assinatura, _ = _cenario()
    fake = CheckoutClient(FakeCheckoutGateway(results={CreateCheckout: _checkout_result}, capabilities=_capabilities(), variant="stripe"))
    ReferenciaPrecoGateway.objects.create(
        preco_plano=assinatura.versao_plano.precos.get(), variante="stripe", componente=10, identificador_externo="price_base"
    )
    ReferenciaPrecoGateway.objects.create(
        preco_plano=assinatura.versao_plano.precos.get(), variante="stripe", componente=20, identificador_externo="price_seat"
    )

    resultado = CheckoutsCobranca.criar(
        CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia="checkout-1"),
        client=fake,
    )

    assert decodificar_referencia_checkout(resultado.referencia, organizacao_id=organizacao.pk) == resultado.checkout.pk
    with pytest.raises(ConflitoCheckout):
        decodificar_referencia_checkout(resultado.referencia + "x", organizacao_id=organizacao.pk)
    with pytest.raises(ConflitoCheckout):
        decodificar_referencia_checkout(resultado.referencia, organizacao_id=organizacao.pk + 1)


def test_checkout_catalogo_constroi_base_e_seats_sem_transacao_durante_io_e_sem_raw():
    organizacao, assinatura, preco = _cenario()
    ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=ComponentePreco.BASE, identificador_externo="price_base")
    ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=ComponentePreco.SEAT, identificador_externo="price_seat")
    gateway = FakeCheckoutGateway(results={CreateCheckout: _checkout_result}, capabilities=_capabilities(), variant="stripe")

    resultado = CheckoutsCobranca.criar(
        CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia="checkout-2"),
        client=CheckoutClient(gateway),
    )

    comando = gateway.commands[0]
    assert [(item.price.external_id, item.quantity) for item in comando.request.items] == [("price_base", 1), ("price_seat", 2)]
    assert resultado.checkout.status == StatusCheckout.ABERTO
    assert resultado.checkout.valor_esperado_centavos == 1500
    assert resultado.checkout.moeda_esperada == "BRL"
    assert resultado.checkout.identificador_externo == "cs_test_1"
    assert "secret" not in repr(resultado.checkout.__dict__)


def test_mesma_chave_e_snapshot_retorna_existente_sem_novo_io():
    organizacao, assinatura, preco = _cenario()
    for componente, external_id in ((ComponentePreco.BASE, "price_base"), (ComponentePreco.SEAT, "price_seat")):
        ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=componente, identificador_externo=external_id)
    gateway = FakeCheckoutGateway(results={CreateCheckout: _checkout_result}, capabilities=_capabilities(), variant="stripe")
    comando = CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia="same")

    primeiro = CheckoutsCobranca.criar(comando, client=CheckoutClient(gateway))
    segundo = CheckoutsCobranca.criar(comando, client=CheckoutClient(gateway))

    assert segundo.checkout.pk == primeiro.checkout.pk
    assert len(gateway.commands) == 1


def test_falta_de_referencia_e_capability_nao_emite_comando():
    organizacao, assinatura, _ = _cenario()
    gateway = FakeCheckoutGateway(
        results={CreateCheckout: _checkout_result}, capabilities=_capabilities(catalog=False, inline=False), variant="stripe"
    )

    with pytest.raises(ConflitoCheckout):
        CheckoutsCobranca.criar(
            CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia="missing"),
            client=CheckoutClient(gateway),
        )

    assert gateway.commands == []


@pytest.mark.parametrize(
    ("path", "view_name"),
    [
        ("/assinatura/checkouts/", "criar-checkout-assinatura"),
        ("/faturamento/checkouts/", "listar-checkouts-faturamento"),
        ("/faturamento/forma-pagamento/checkouts/", "criar-checkout-forma-pagamento"),
    ],
)
def test_rotas_financeiras_publicam_os_tres_endpoints(path, view_name):
    assert resolve(path).url_name == view_name


@pytest.mark.parametrize(
    ("error_type", "expected_status", "expected_exception"),
    [
        (GatewayPermanentError, StatusCheckout.FALHOU, ConflitoCheckout),
        (GatewayTemporaryError, StatusCheckout.AGUARDANDO_GATEWAY, FalhaCheckoutIncerta),
    ],
)
def test_falha_conhecida_termina_e_falha_incerta_preserva_para_conciliacao(error_type, expected_status, expected_exception):
    organizacao, assinatura, preco = _cenario()
    for componente, external_id in ((ComponentePreco.BASE, "price_base"), (ComponentePreco.SEAT, "price_seat")):
        ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=componente, identificador_externo=external_id)

    def falhar(_command):
        assert connection.in_atomic_block is False
        raise error_type("falha", gateway="fake", variant="stripe")

    gateway = FakeCheckoutGateway(results={CreateCheckout: falhar}, capabilities=_capabilities(), variant="stripe")
    with pytest.raises(expected_exception):
        CheckoutsCobranca.criar(
            CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia=f"failure-{error_type.__name__}"),
            client=CheckoutClient(gateway),
        )
    with organizacao_atual_privilegiada(organizacao.pk):
        checkout = CheckoutCobranca.objects.get(chave_idempotencia=f"failure-{error_type.__name__}")
    assert checkout.status == expected_status
    assert len(gateway.commands) == 1


def test_confirmacao_recusa_tentativa_que_ficou_stale_durante_io():
    organizacao, assinatura, preco = _cenario()
    for componente, external_id in ((ComponentePreco.BASE, "price_base"), (ComponentePreco.SEAT, "price_seat")):
        ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=componente, identificador_externo=external_id)

    def tornar_stale(command):
        assert connection.in_atomic_block is False
        with organizacao_atual_privilegiada(organizacao.pk):
            checkout = CheckoutCobranca.objects.get(chave_idempotencia="stale")
            checkout.status = StatusCheckout.CANCELADO
            checkout.save(update_fields=["status", "last_modified_at"])
        return _checkout_result(command)

    gateway = FakeCheckoutGateway(results={CreateCheckout: tornar_stale}, capabilities=_capabilities(), variant="stripe")
    with pytest.raises(ConflitoCheckout, match="deixou de ser vigente"):
        CheckoutsCobranca.criar(
            CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.CONTRATACAO, chave_idempotencia="stale"),
            client=CheckoutClient(gateway),
        )


def test_forma_pagamento_sem_capability_setup_recusa_antes_do_gateway():
    _, assinatura, _ = _cenario(status=StatusAssinatura.ATIVA)
    gateway = FakeCheckoutGateway(results={CreateCheckout: _checkout_result}, capabilities=_capabilities(), variant="stripe")
    with pytest.raises(ConflitoCheckout, match="capability de setup"):
        CheckoutsCobranca.criar(
            CriacaoCheckout(assinatura=assinatura, finalidade=FinalidadeCheckout.FORMA_PAGAMENTO, chave_idempotencia="payment-method"),
            client=CheckoutClient(gateway),
        )
    assert gateway.commands == []
