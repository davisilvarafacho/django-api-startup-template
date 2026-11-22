"""Testes da tarefa `process_billing_event`: aplicação do evento ao espelho, erros e retentativas."""

from django.utils import timezone

import pytest
from celery.exceptions import Retry
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import CancellationTiming, Gateway
from django_checkouts.enums import SubscriptionStatus as RemoteStatus
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.gateways.commands import CancelRemoteSubscription, RetrieveSubscription
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import Subscriptions
from apps.billing.tasks import MISSING_ORGANIZATION_MESSAGE, MISSING_REFERENCE_MESSAGE, process_billing_event
from apps.organizacoes.models import Organizacao
from tests.support.billing import create_organization, patch_billing_gateway, remote_subscription

pytestmark = pytest.mark.django_db


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
    organization = create_organization()
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
    organization = create_organization()
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
    organization = create_organization()
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
    organization = create_organization()
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
    organization = create_organization()
    other = create_organization("outra")
    Subscriptions.ensure_free(organization.pk)
    with organization_scope(organization.pk):
        Subscription.all_objects.filter(organizacao_id=organization.pk).update(external_id="sub_antiga", gateway_variant="stripe")
    before = _mirror(organization)

    def retrieve(command):
        if command.external_id == "sub_nova":
            return _remote(organization, external_id="sub_nova")
        return _remote(other, external_id="sub_antiga")

    # `sub_nova` é uma segunda assinatura da organização: é cancelada antes da sincronização falhar.
    _patch_gateway(
        monkeypatch,
        {
            RetrieveSubscription: retrieve,
            CancelRemoteSubscription: _remote(organization, external_id="sub_nova"),
        },
    )
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


def test_process_unexpected_error_counts_attempt_and_propagates(monkeypatch):
    organization = create_organization("inesperado")
    _patch_gateway(monkeypatch, {RetrieveSubscription: _remote(organization)})

    def explode(*args, **kwargs):
        raise RuntimeError("falha de infraestrutura")

    monkeypatch.setattr("apps.billing.tasks.Subscriptions.link_external", explode)
    event = _registered_event()

    with pytest.raises(RuntimeError, match="falha de infraestrutura"):
        process_billing_event(event.pk)

    record = _record(event)
    assert record.attempts == 1
    assert record.last_error == "falha inesperada (RuntimeError): falha de infraestrutura"
    assert record.processed_at is None

    with pytest.raises(RuntimeError):
        process_billing_event(event.pk)

    assert _record(event).attempts == 2


def test_process_duplicate_subscription_cancels_newcomer_and_marks_processed(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    remotes = {"sub_1": _remote(organization, external_id="sub_1"), "sub_2": _remote(organization, external_id="sub_2")}
    gateway = _patch_gateway(
        monkeypatch,
        {
            RetrieveSubscription: lambda command: remotes[command.external_id],
            CancelRemoteSubscription: remote_subscription(external_id="sub_2", reference_id=str(organization.pk)),
        },
    )
    event = _registered_event(event_id="evt_dup", subscription_external_id="sub_2")

    process_billing_event(event.pk)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert [command.external_id for command in cancellations] == ["sub_2"]
    assert cancellations[0].idempotency_key == "billing:duplicate:sub_2"
    assert _record(event).processed_at is not None
    assert _mirror(organization).external_id == "sub_1"


def test_process_duplicate_subscription_stays_pending_when_cancel_fails(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    remotes = {"sub_1": _remote(organization, external_id="sub_1"), "sub_2": _remote(organization, external_id="sub_2")}
    _patch_gateway(
        monkeypatch,
        {
            RetrieveSubscription: lambda command: remotes[command.external_id],
            CancelRemoteSubscription: _raising(GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe")),
        },
    )
    event = _registered_event(event_id="evt_dup_falha", subscription_external_id="sub_2")

    process_billing_event(event.pk)

    record = _record(event)
    assert record.processed_at is None
    assert record.attempts == 1
    assert "recusado" in record.last_error
    assert _mirror(organization).external_id == "sub_1"


def _close(organization):
    """Marca a organização como encerrada, do jeito que o encerramento deixa a linha."""
    Organizacao.all_objects.filter(pk=organization.pk).update(
        is_active=False,
        is_deleted=True,
        encerramento_solicitado_em=timezone.now(),
    )


def test_late_checkout_for_closed_organization_cancels_with_close_key_without_paid_plan(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    _pro_plan()
    _close(organization)
    active = _remote(organization, external_id="sub_1", price_id="price_pro")
    canceled = remote_subscription(external_id="sub_1", reference_id=str(organization.pk), status=RemoteStatus.CANCELED)
    retrievals = []

    def retrieve(command):
        # Primeira leitura: a assinatura ainda dá acesso. Depois do cancelamento, o gateway a devolve cancelada.
        retrievals.append(command)
        return active if len(retrievals) == 1 else canceled

    gateway = _patch_gateway(monkeypatch, {RetrieveSubscription: retrieve, CancelRemoteSubscription: canceled})
    event = _registered_event(event_id="evt_tardio")

    process_billing_event(event.pk)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 1
    assert cancellations[0].external_id == "sub_1"
    assert cancellations[0].timing == CancellationTiming.IMMEDIATELY
    assert cancellations[0].idempotency_key == f"billing:close:{organization.pk}"
    assert _record(event).processed_at is not None
    mirror = _mirror(organization)
    assert mirror.plan.is_free is True
    assert mirror.external_id is None


def test_late_event_for_closed_organization_without_access_only_marks_processed(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    _close(organization)
    gateway = _patch_gateway(
        monkeypatch,
        {RetrieveSubscription: remote_subscription(external_id="sub_1", reference_id=str(organization.pk), status=RemoteStatus.CANCELED)},
    )
    event = _registered_event(event_id="evt_ja_cancelada")

    process_billing_event(event.pk)

    assert [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)] == []
    assert _record(event).processed_at is not None


def test_late_checkout_for_closed_organization_stays_pending_when_cancel_fails(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    _close(organization)
    _patch_gateway(
        monkeypatch,
        {
            RetrieveSubscription: _remote(organization, external_id="sub_1"),
            CancelRemoteSubscription: _raising(GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe")),
        },
    )
    event = _registered_event(event_id="evt_tardio_falha")

    process_billing_event(event.pk)

    record = _record(event)
    assert record.processed_at is None
    assert "recusado" in record.last_error


def test_late_checkout_for_closed_organization_with_other_link_cancels_only_newcomer(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    _close(organization)
    remotes = {"sub_1": _remote(organization, external_id="sub_1"), "sub_2": _remote(organization, external_id="sub_2")}
    gateway = _patch_gateway(
        monkeypatch,
        {
            RetrieveSubscription: lambda command: remotes[command.external_id],
            CancelRemoteSubscription: remote_subscription(external_id="sub_2", reference_id=str(organization.pk), status=RemoteStatus.CANCELED),
        },
    )
    event = _registered_event(event_id="evt_tardio_duplicado", subscription_external_id="sub_2")

    process_billing_event(event.pk)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert [(command.external_id, command.idempotency_key) for command in cancellations] == [("sub_2", "billing:duplicate:sub_2")]
    assert _record(event).processed_at is not None
    assert _mirror(organization).external_id == "sub_1"


def test_cancellation_event_for_closed_organization_resets_linked_mirror(monkeypatch):
    organization = create_organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    _close(organization)
    canceled = remote_subscription(external_id="sub_1", reference_id=str(organization.pk), status=RemoteStatus.CANCELED)
    gateway = _patch_gateway(monkeypatch, {RetrieveSubscription: canceled})
    event = _registered_event(event_id="evt_cancelamento_encerrada")

    process_billing_event(event.pk)

    assert [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)] == []
    assert _record(event).processed_at is not None
    mirror = _mirror(organization)
    assert mirror.external_id is None
    assert mirror.plan.is_free is True
