"""Testes do webhook de cobrança: verificação de assinatura, registro idempotente e enfileiramento após o commit."""

from dataclasses import replace

from django.utils import timezone

import pytest
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import EventType, Gateway, InvoiceReason, InvoiceStatus, ResourceKind
from django_checkouts.exceptions import WebhookVerificationError
from django_checkouts.gateways.commands import VerifyWebhook
from django_checkouts.types.events import WebhookEvent
from django_checkouts.types.invoices import Invoice

from apps.billing.events import record_event
from apps.billing.models import BillingEvent
from tests.support.billing import fake_checkout, fake_gateway, remote_subscription

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/cobranca/webhooks/stripe/"
PAYLOAD = b'{"id": "evt_1"}'


def _evento(*, event_id="evt_1", event_type=EventType.CHECKOUT_PAID, resource=None, resource_kind=ResourceKind.CHECKOUT, resource_id="cs_1"):
    """Evento já verificado, como o gateway o entregaria à biblioteca."""
    return WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id=event_id,
        event_type=event_type.value,
        type=event_type,
        occurred_at=timezone.now(),
        resource_kind=resource_kind,
        resource_id=resource_id,
        resource=resource,
        livemode=False,
        raw={},
    )


def _verificador(monkeypatch, resultado):
    """Troca o cliente da view por um gateway que devolve `resultado` (ou levanta, se for exceção)."""

    def responder(comando):
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    gateway = fake_gateway({VerifyWebhook: responder})
    monkeypatch.setattr("apps.billing.views.get_client", lambda: CheckoutClient(gateway))
    return gateway


class _EnfileiradorFalso:
    """Registra os ids enfileirados em vez de executar a tarefa."""

    def __init__(self):
        self.enfileirados = []

    def delay(self, event_pk):
        self.enfileirados.append(event_pk)


@pytest.fixture
def enfileirador(monkeypatch):
    falso = _EnfileiradorFalso()
    monkeypatch.setattr("apps.billing.views.process_billing_event", falso)
    return falso


def test_webhook_invalid_signature_returns_400(client, monkeypatch, enfileirador):
    _verificador(monkeypatch, WebhookVerificationError("assinatura inválida"))

    response = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "billing.webhook_invalid"
    assert BillingEvent.all_objects.count() == 0
    assert enfileirador.enfileirados == []


def test_webhook_unknown_variant_returns_404(client, monkeypatch, enfileirador):
    gateway = _verificador(monkeypatch, _evento())

    response = client.post("/cobranca/webhooks/outra/", data=PAYLOAD, content_type="application/json")

    assert response.status_code == 404
    assert gateway.commands == []
    assert BillingEvent.all_objects.count() == 0
    assert enfileirador.enfileirados == []


def test_webhook_records_and_enqueues_once(client, monkeypatch, enfileirador, django_capture_on_commit_callbacks):
    _verificador(monkeypatch, _evento(resource=replace(fake_checkout(), subscription_id="sub_1")))

    with django_capture_on_commit_callbacks() as primeiros:
        primeira = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")
    # O enfileiramento é adiado até o commit: nada foi enviado ainda.
    assert enfileirador.enfileirados == []
    for callback in primeiros:
        callback()

    with django_capture_on_commit_callbacks() as repetidos:
        segunda = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")
    for callback in repetidos:
        callback()

    assert primeira.status_code == 200
    assert segunda.status_code == 200
    registro = BillingEvent.all_objects.get(gateway_variant="stripe", event_id="evt_1")
    assert BillingEvent.all_objects.filter(event_id="evt_1").count() == 1
    assert enfileirador.enfileirados == [registro.pk]
    assert registro.subscription_external_id == "sub_1"
    assert registro.resource_kind == ResourceKind.CHECKOUT


def test_record_event_extrai_id_da_assinatura_do_recurso():
    invoice = Invoice(
        external_id="in_1",
        gateway=Gateway.STRIPE,
        variant="stripe",
        status=InvoiceStatus.PAID,
        reason=InvoiceReason.INITIAL_SUBSCRIPTION,
        subscription_id="sub_fatura",
        customer_id=None,
        amount_due=0,
        amount_paid=0,
        amount_remaining=0,
        currency="BRL",
        lines=(),
        due_at=None,
        paid_at=None,
        next_payment_attempt_at=None,
        attempt_count=0,
        hosted_url=None,
        reference_id=None,
        raw={},
    )
    casos = [
        (_evento(resource=replace(fake_checkout(), subscription_id="sub_checkout")), "sub_checkout"),
        (_evento(resource=remote_subscription(external_id="sub_direta"), resource_kind=ResourceKind.SUBSCRIPTION), "sub_direta"),
        (_evento(resource=invoice, resource_kind=ResourceKind.INVOICE, event_type=EventType.INVOICE_PAID), "sub_fatura"),
        (_evento(resource=None, resource_kind=None, resource_id=None), None),
    ]

    for indice, (evento, esperado) in enumerate(casos):
        registro, criado = record_event(replace(evento, event_id=f"evt_{indice}"), variant="stripe")
        assert criado is True
        assert registro.subscription_external_id == esperado
