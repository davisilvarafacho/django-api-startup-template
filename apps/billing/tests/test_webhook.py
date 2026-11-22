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


def _event(*, event_id="evt_1", event_type=EventType.CHECKOUT_PAID, resource=None, resource_kind=ResourceKind.CHECKOUT, resource_id="cs_1"):
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


def _verifier(monkeypatch, result):
    """Troca o cliente da view por um gateway que devolve `result` (ou levanta, se for exceção)."""

    def respond(command):
        if isinstance(result, Exception):
            raise result
        return result

    gateway = fake_gateway({VerifyWebhook: respond})
    monkeypatch.setattr("apps.billing.views.get_client", lambda: CheckoutClient(gateway))
    return gateway


class _FakeEnqueuer:
    """Registra os ids enfileirados em vez de executar a tarefa."""

    def __init__(self):
        self.enqueued = []

    def delay(self, event_pk):
        self.enqueued.append(event_pk)


@pytest.fixture
def enqueuer(monkeypatch):
    fake = _FakeEnqueuer()
    monkeypatch.setattr("apps.billing.views.process_billing_event", fake)
    return fake


def test_webhook_invalid_signature_returns_400(client, monkeypatch, enqueuer):
    _verifier(monkeypatch, WebhookVerificationError("assinatura inválida"))

    response = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "billing.webhook_invalid"
    assert BillingEvent.all_objects.count() == 0
    assert enqueuer.enqueued == []


def test_webhook_unknown_variant_returns_404(client, monkeypatch, enqueuer):
    gateway = _verifier(monkeypatch, _event())

    response = client.post("/cobranca/webhooks/outra/", data=PAYLOAD, content_type="application/json")

    assert response.status_code == 404
    assert gateway.commands == []
    assert BillingEvent.all_objects.count() == 0
    assert enqueuer.enqueued == []


def test_webhook_records_and_enqueues_once(client, monkeypatch, enqueuer, django_capture_on_commit_callbacks):
    _verifier(monkeypatch, _event(resource=replace(fake_checkout(), subscription_id="sub_1")))

    with django_capture_on_commit_callbacks() as first_callbacks:
        first = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")
    # O enfileiramento é adiado até o commit: nada foi enviado ainda.
    assert enqueuer.enqueued == []
    for callback in first_callbacks:
        callback()

    with django_capture_on_commit_callbacks() as second_callbacks:
        second = client.post(WEBHOOK_URL, data=PAYLOAD, content_type="application/json")
    for callback in second_callbacks:
        callback()

    assert first.status_code == 200
    assert second.status_code == 200
    record = BillingEvent.all_objects.get(gateway_variant="stripe", event_id="evt_1")
    assert BillingEvent.all_objects.filter(event_id="evt_1").count() == 1
    assert enqueuer.enqueued == [record.pk]
    assert record.subscription_external_id == "sub_1"
    assert record.resource_kind == ResourceKind.CHECKOUT


def test_record_event_extracts_subscription_id_from_resource():
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
    cases = [
        (_event(resource=replace(fake_checkout(), subscription_id="sub_checkout")), "sub_checkout"),
        (_event(resource=remote_subscription(external_id="sub_direta"), resource_kind=ResourceKind.SUBSCRIPTION), "sub_direta"),
        (_event(resource=invoice, resource_kind=ResourceKind.INVOICE, event_type=EventType.INVOICE_PAID), "sub_fatura"),
        (_event(resource=None, resource_kind=None, resource_id=None), None),
    ]

    for index, (event, expected) in enumerate(cases):
        record, created = record_event(replace(event, event_id=f"evt_{index}"), variant="stripe")
        assert created is True
        assert record.subscription_external_id == expected
