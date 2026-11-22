"""Testes da tarefa `reconcile_billing`: eventos faltantes, pendentes parados, limite de tentativas e limpeza."""

import logging
from datetime import timedelta

from django.utils import timezone

import pytest
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import Gateway
from django_checkouts.gateways.commands import ListEvents, RetrieveSubscription
from django_checkouts.types.events import EventPage, WebhookEvent

from apps.billing.context import organization_scope
from apps.billing.events import record_event
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.billing.tasks import process_billing_event, reconcile_billing
from apps.organizacoes.models import Organizacao
from tests.support.billing import fake_gateway, remote_subscription

pytestmark = pytest.mark.django_db


def _webhook_event(event_id: str) -> WebhookEvent:
    """Evento recebido do gateway, sem recurso de assinatura."""
    return WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id=event_id,
        event_type="customer.subscription.updated",
        type=None,
        occurred_at=timezone.now(),
        resource_kind=None,
        resource_id=None,
        resource=None,
        livemode=None,
        raw={},
    )


def _page(items, *, next_cursor=None) -> EventPage:
    """Página de eventos da janela de reconciliação."""
    now = timezone.now()
    return EventPage(
        items=tuple(items),
        next_cursor=next_cursor,
        occurred_since=now - timedelta(hours=72),
        occurred_before=now,
    )


def _pending_event(event_id: str, *, created_ago: timedelta, attempts: int = 0, processed: bool = False) -> BillingEvent:
    """Registro da caixa de entrada com a idade e o estado informados."""
    event = BillingEvent.all_objects.create(
        gateway_variant="stripe",
        event_id=event_id,
        event_type="customer.subscription.updated",
        resource_kind="",
        resource_id="",
        occurred_at=timezone.now(),
        attempts=attempts,
        processed_at=timezone.now() if processed else None,
    )
    BillingEvent.all_objects.filter(pk=event.pk).update(created_at=timezone.now() - created_ago)
    event.refresh_from_db()
    return event


def _patch_reconcile_gateway(monkeypatch, results: dict):
    """Gateway fake usado pela reconciliação; devolve o fake para inspecionar os comandos."""
    gateway = fake_gateway(results)
    monkeypatch.setattr("apps.billing.tasks.get_client", lambda: CheckoutClient(gateway))
    return gateway


def _enqueued_spy(monkeypatch) -> list[int]:
    """Troca o enfileiramento de `process_billing_event` por uma lista que guarda os ids recebidos."""
    enqueued: list[int] = []
    monkeypatch.setattr("apps.billing.tasks.process_billing_event.delay", enqueued.append)
    return enqueued


def test_reconcile_records_missing_events_across_pages(monkeypatch):
    record_event(_webhook_event("evt_existing"), variant="stripe")
    first_page = _page([_webhook_event("evt_1")], next_cursor="cursor-2")
    second_page = _page([_webhook_event("evt_existing"), _webhook_event("evt_2")])
    gateway = _patch_reconcile_gateway(
        monkeypatch,
        {ListEvents: lambda command: first_page if command.cursor is None else second_page},
    )
    enqueued = _enqueued_spy(monkeypatch)

    reconcile_billing()

    assert [command.cursor for command in gateway.commands] == [None, "cursor-2"]
    assert set(BillingEvent.all_objects.values_list("event_id", flat=True)) == {"evt_existing", "evt_1", "evt_2"}
    new_pks = BillingEvent.all_objects.filter(event_id__in=["evt_1", "evt_2"]).values_list("pk", flat=True)
    assert sorted(enqueued) == sorted(new_pks)


def test_reconcile_requeues_stale_pending_events(monkeypatch):
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([])})
    enqueued = _enqueued_spy(monkeypatch)
    stale = _pending_event("evt_stale", created_ago=timedelta(minutes=30), attempts=2)
    _pending_event("evt_recent", created_ago=timedelta(minutes=1))
    _pending_event("evt_applied", created_ago=timedelta(minutes=30), processed=True)

    reconcile_billing()

    assert enqueued == [stale.pk]


def test_reconcile_skips_events_at_max_attempts_and_logs(monkeypatch, settings, caplog):
    settings.BILLING_EVENT_MAX_ATTEMPTS = 3
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([])})
    enqueued = _enqueued_spy(monkeypatch)
    retryable = _pending_event("evt_retryable", created_ago=timedelta(hours=1), attempts=2)
    _pending_event("evt_exhausted_1", created_ago=timedelta(hours=1), attempts=3)
    _pending_event("evt_exhausted_2", created_ago=timedelta(hours=1), attempts=5)

    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        reconcile_billing()

    assert enqueued == [retryable.pk]
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].args == (2, 3)


def test_reconcile_purges_processed_older_than_90_days(monkeypatch):
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([])})
    _enqueued_spy(monkeypatch)
    old = _pending_event("evt_old", created_ago=timedelta(days=100), processed=True)
    BillingEvent.all_objects.filter(pk=old.pk).update(processed_at=timezone.now() - timedelta(days=91))
    recent = _pending_event("evt_recent", created_ago=timedelta(days=100), processed=True)
    BillingEvent.all_objects.filter(pk=recent.pk).update(processed_at=timezone.now() - timedelta(days=89))
    pending = _pending_event("evt_pending", created_ago=timedelta(days=100))

    reconcile_billing()

    # `all_objects` inclui linhas com soft delete: a ausência prova o DELETE real.
    assert BillingEvent.all_objects.filter(pk=old.pk).exists() is False
    assert set(BillingEvent.all_objects.values_list("pk", flat=True)) >= {recent.pk, pending.pk}


def test_reconcile_stops_requeuing_after_repeated_unexpected_failures(monkeypatch, settings):
    settings.BILLING_EVENT_MAX_ATTEMPTS = 2
    organization = Organizacao.objects.create(nome="Falhas inesperadas", slug="falhas-inesperadas")
    remote = remote_subscription(external_id="sub_1", reference_id=str(organization.pk))
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([]), RetrieveSubscription: remote})
    enqueued = _enqueued_spy(monkeypatch)

    def explode(*args, **kwargs):
        raise RuntimeError("falha de infraestrutura")

    monkeypatch.setattr("apps.billing.tasks.Subscriptions.link_external", explode)
    event = BillingEvent.all_objects.create(
        gateway_variant="stripe",
        event_id="evt_unexpected",
        event_type="customer.subscription.updated",
        resource_kind="subscription",
        resource_id="sub_1",
        subscription_external_id="sub_1",
        occurred_at=timezone.now(),
    )
    BillingEvent.all_objects.filter(pk=event.pk).update(created_at=timezone.now() - timedelta(hours=1))

    with pytest.raises(RuntimeError):
        process_billing_event(event.pk)
    reconcile_billing()  # 1 tentativa, abaixo do limite de 2: reenfileira
    with pytest.raises(RuntimeError):
        process_billing_event(event.pk)  # a reenfileirada falha de novo
    reconcile_billing()  # no limite: não reenfileira mais

    assert enqueued == [event.pk]
    assert BillingEvent.all_objects.get(pk=event.pk).attempts == 2


def _organization_with_subscription(slug: str, *, linked: bool, closed: bool) -> Organizacao:
    """Organização com assinatura paga, ligada ou não ao gateway, aberta ou encerrada."""
    organization = Organizacao.objects.create(nome=slug, slug=slug)
    plan = Plan.objects.create(name=f"Pro {slug}", slug=f"pro-{slug}", rank=2, member_limit=10, gateway_price_id=f"price_{slug}")
    with organization_scope(organization.pk):
        Subscription.all_objects.create(
            organizacao=organization,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            external_id=f"sub_{slug}" if linked else None,
            gateway_variant="stripe" if linked else None,
        )
    if closed:
        Organizacao.all_objects.filter(pk=organization.pk).update(
            is_active=False,
            is_deleted=True,
            encerramento_solicitado_em=timezone.now(),
        )
    return organization


def _cancel_spy(monkeypatch) -> list[int]:
    """Troca o enfileiramento do cancelamento de encerradas por uma lista com os ids recebidos."""
    enqueued: list[int] = []
    monkeypatch.setattr("apps.billing.tasks.cancel_for_closed_organization.delay", enqueued.append)
    return enqueued


def test_reconcile_requeues_cancellation_only_for_closed_organizations_with_link(monkeypatch):
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([])})
    _enqueued_spy(monkeypatch)
    cancellations = _cancel_spy(monkeypatch)
    closed = _organization_with_subscription("encerrada-vinculada", linked=True, closed=True)
    _organization_with_subscription("encerrada-sem-vinculo", linked=False, closed=True)
    _organization_with_subscription("ativa-vinculada", linked=True, closed=False)

    reconcile_billing()

    assert cancellations == [closed.pk]


def test_reconcile_closed_organization_sweep_failure_does_not_abort_requeue_or_purge(monkeypatch, caplog):
    _patch_reconcile_gateway(monkeypatch, {ListEvents: _page([])})
    enqueued = _enqueued_spy(monkeypatch)
    stale = _pending_event("evt_stale_varredura", created_ago=timedelta(minutes=30), attempts=2)
    old = _pending_event("evt_antigo_varredura", created_ago=timedelta(days=100), processed=True)
    BillingEvent.all_objects.filter(pk=old.pk).update(processed_at=timezone.now() - timedelta(days=91))
    _organization_with_subscription("encerrada-falha", linked=True, closed=True)

    def broker_down(organization_id):
        raise RuntimeError("broker indisponível")

    monkeypatch.setattr("apps.billing.tasks.cancel_for_closed_organization.delay", broker_down)

    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        reconcile_billing()

    assert enqueued == [stale.pk]
    assert BillingEvent.all_objects.filter(pk=old.pk).exists() is False
    assert any(record.levelno == logging.ERROR for record in caplog.records)
