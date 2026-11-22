"""Testes da tarefa `process_billing_event`: aplicação do evento ao espelho, erros e retentativas."""

from django.utils import timezone

import pytest
from celery.exceptions import Retry
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import Gateway
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.gateways.commands import RetrieveSubscription
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import Subscriptions
from apps.billing.tasks import MISSING_ORGANIZATION_MESSAGE, MISSING_REFERENCE_MESSAGE, process_billing_event
from apps.organizacoes.models import Organizacao
from tests.support.billing import patch_billing_gateway, remote_subscription

pytestmark = pytest.mark.django_db


def _organization(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _pro_plan():
    return Plan.objects.create(name="Pro", slug="pro", rank=2, member_limit=10, gateway_price_id="price_pro")


def _registered_event(*, event_id="evt_1", subscription_external_id="sub_1", resource_kind="subscription"):
    return BillingEvent.all_objects.create(
        gateway_variant="stripe",
        event_id=event_id,
        event_type="customer.subscription.updated",
        resource_kind=resource_kind,
        resource_id=subscription_external_id or "",
        subscription_external_id=subscription_external_id,
        occurred_at=timezone.now(),
    )


def _remote(organization, external_id="sub_1", price_id="price_pro"):
    """Assinatura remota da organização, no preço informado."""
    item = SubscriptionItem(external_id="si_1", price_id=price_id, quantity=1, unit_amount=None, currency=None, billing_cycle=None, raw={})
    return remote_subscription(external_id=external_id, reference_id=str(organization.pk), items=(item,))


def _patch_gateway(monkeypatch, results):
    """Gateway fake usado pela tarefa e pelo serviço de assinatura: o mesmo cliente nos dois."""
    gateway = patch_billing_gateway(monkeypatch, results)
    monkeypatch.setattr("apps.billing.tasks.get_client", lambda: CheckoutClient(gateway))
    return gateway


def _raising(error):
    """Resposta do gateway fake que levanta `error` no lugar de devolver um resultado."""

    def respond(command):
        raise error

    return respond


def _mirror(organization):
    with organization_scope(organization.pk):
        return Subscription.all_objects.get(organizacao_id=organization.pk)


def _record(event):
    return BillingEvent.all_objects.get(pk=event.pk)


def test_process_event_without_subscription_marks_processed(monkeypatch):
    event = _registered_event(subscription_external_id=None, resource_kind="checkout")
    gateway = _patch_gateway(monkeypatch, {})

    process_billing_event(event.pk)

    assert _record(event).processed_at is not None
    assert gateway.commands == []


def test_process_already_processed_event_does_nothing(monkeypatch):
    event = _registered_event()
    BillingEvent.all_objects.filter(pk=event.pk).update(processed_at=timezone.now())
    gateway = _patch_gateway(monkeypatch, {})

    process_billing_event(event.pk)

    assert gateway.commands == []


def test_process_checkout_paid_links_and_syncs(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    plan = _pro_plan()
    _patch_gateway(monkeypatch, {RetrieveSubscription: lambda command: _remote(organization)})
    event = _registered_event(resource_kind="checkout")

    process_billing_event(event.pk)

    mirror = _mirror(organization)
    assert mirror.external_id == "sub_1"
    assert mirror.gateway_variant == "stripe"
    assert mirror.plan == plan
    assert mirror.status == SubscriptionStatus.ACTIVE
    assert _record(event).processed_at is not None
    assert _record(event).attempts == 0


def test_process_subscription_event_before_checkout_links_and_syncs(monkeypatch):
    # O evento de assinatura chega antes do checkout.paid: a organização vem do `reference_id`.
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    plan = _pro_plan()
    gateway = _patch_gateway(monkeypatch, {RetrieveSubscription: lambda command: _remote(organization)})
    event = _registered_event(resource_kind="subscription")

    process_billing_event(event.pk)

    mirror = _mirror(organization)
    assert mirror.external_id == "sub_1"
    assert mirror.plan == plan
    assert _record(event).processed_at is not None
    assert all(command.external_id == "sub_1" for command in gateway.commands)


def test_process_reference_to_organization_without_subscription_links_and_syncs(monkeypatch):
    # A organização existe, mas ainda não tem linha de assinatura local: a tarefa cria a gratuita antes de vincular.
    organization = _organization()
    plan = _pro_plan()
    _patch_gateway(monkeypatch, {RetrieveSubscription: lambda command: _remote(organization)})
    event = _registered_event()

    process_billing_event(event.pk)

    mirror = _mirror(organization)
    assert mirror.external_id == "sub_1"
    assert mirror.plan == plan
    assert _record(event).processed_at is not None
    assert _record(event).attempts == 0


def test_process_missing_reference_records_error(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    remote = remote_subscription(external_id="sub_1", reference_id=None)
    _patch_gateway(monkeypatch, {RetrieveSubscription: remote})
    event = _registered_event()

    process_billing_event(event.pk)

    record = _record(event)
    assert record.last_error == MISSING_REFERENCE_MESSAGE == "assinatura do gateway sem referência à organização"
    assert record.attempts == 1
    assert record.processed_at is None
    assert _mirror(organization).external_id is None


@pytest.mark.parametrize("reference_id", ["²", "abc"])
def test_process_malformed_reference_records_error(monkeypatch, reference_id):
    # '²' passa em `isdigit` e quebra `int()`: tem de virar erro registrado, não exceção.
    _patch_gateway(monkeypatch, {RetrieveSubscription: remote_subscription(external_id="sub_1", reference_id=reference_id)})
    event = _registered_event()

    process_billing_event(event.pk)

    record = _record(event)
    assert record.last_error == MISSING_REFERENCE_MESSAGE
    assert record.attempts == 1
    assert record.processed_at is None


@pytest.mark.parametrize("reference_id", ["999999", "99999999999999999999"])
def test_process_reference_to_nonexistent_organization_records_error(monkeypatch, reference_id):
    _patch_gateway(monkeypatch, {RetrieveSubscription: remote_subscription(external_id="sub_1", reference_id=reference_id)})
    event = _registered_event()

    # Sem nova tentativa: a chamada volta normalmente, sem `Retry` nem exceção.
    process_billing_event(event.pk)

    record = _record(event)
    assert record.last_error == MISSING_ORGANIZATION_MESSAGE
    assert record.attempts == 1
    assert record.processed_at is None


def test_process_temporary_error_retries(monkeypatch):
    _patch_gateway(monkeypatch, {RetrieveSubscription: _raising(GatewayTemporaryError("indisponível", gateway=Gateway.STRIPE, variant="stripe"))})
    event = _registered_event()

    # Chamada direta da tarefa: a retentativa levanta `Retry` em vez de reenfileirar.
    with pytest.raises(Retry):
        process_billing_event(event.pk)

    record = _record(event)
    assert record.attempts == 1
    assert "indisponível" in record.last_error
    assert record.processed_at is None


def test_process_permanent_gateway_error_records_without_retry(monkeypatch):
    _patch_gateway(monkeypatch, {RetrieveSubscription: _raising(GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe"))})
    event = _registered_event()

    process_billing_event(event.pk)

    record = _record(event)
    assert record.attempts == 1
    assert "recusado" in record.last_error
    assert record.processed_at is None


def test_process_reference_mismatch_records_error(monkeypatch):
    # O espelho local já aponta para `sub_antiga`, que pertence a outra organização.
    organization = _organization()
    other = _organization("outra")
    Subscriptions.ensure_free(organization.pk)
    with organization_scope(organization.pk):
        Subscription.all_objects.filter(organizacao_id=organization.pk).update(external_id="sub_antiga", gateway_variant="stripe")
    before = _mirror(organization)

    def retrieve(command):
        if command.external_id == "sub_nova":
            return _remote(organization, external_id="sub_nova")
        return _remote(other, external_id="sub_antiga")

    _patch_gateway(monkeypatch, {RetrieveSubscription: retrieve})
    event = _registered_event(subscription_external_id="sub_nova")

    process_billing_event(event.pk)

    record = _record(event)
    assert record.attempts == 1
    assert "não pertence" in record.last_error
    assert record.processed_at is None
    after = _mirror(organization)
    assert after.external_id == "sub_antiga"
    assert after.status == before.status
    assert after.plan_id == before.plan_id
