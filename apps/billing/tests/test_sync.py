"""Testes do serviço de assinatura: espelho local, sincronização com o gateway e limite de membros."""

import logging
from datetime import timedelta

from django.db import connection
from django.utils import timezone

import pytest
from django_checkouts.enums import CancellationTiming, Gateway
from django_checkouts.enums import SubscriptionStatus as RemoteStatus
from django_checkouts.exceptions import CheckoutError, GatewayPermanentError
from django_checkouts.gateways.commands import CancelRemoteSubscription, RetrieveSubscription
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.gateway import ReferenceMismatch
from apps.billing.members import MemberLimits
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import Subscriptions
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.billing import patch_billing_gateway, remote_subscription
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _organization(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _plan(slug, *, price=None, rank=1, limit=5):
    return Plan.objects.create(name=slug, slug=slug, rank=rank, member_limit=limit, gateway_price_id=price)


def _subscription(organization, plan, **fields):
    with organization_scope(organization.pk):
        return Subscription.all_objects.create(organizacao=organization, plan=plan, **{"status": SubscriptionStatus.ACTIVE, **fields})


def _linked_subscription(organization, plan, **fields):
    """Assinatura local já ligada à assinatura remota `sub_1`."""
    return _subscription(organization, plan, external_id="sub_1", gateway_variant="stripe", **fields)


def _state(organization):
    with organization_scope(organization.pk):
        return Subscription.all_objects.get(organizacao_id=organization.pk)


def _memberships(organization, quantity):
    return [Vinculo.objects.create(organizacao=organization, usuario=criar_usuario(), papel=Papel.MEMBRO) for _ in range(quantity)]


def _remote_with_price(organization, price_id):
    item = SubscriptionItem(external_id="si_2", price_id=price_id, quantity=1, unit_amount=None, currency=None, billing_cycle=None, raw={})
    return remote_subscription(reference_id=str(organization.pk), items=(item,))


def test_ensure_free_is_idempotent():
    organization = _organization()

    first = Subscriptions.ensure_free(organization.pk)
    second = Subscriptions.ensure_free(organization.pk)

    assert first.pk == second.pk
    assert first.plan == Plan.get_free()
    assert first.status == SubscriptionStatus.ACTIVE
    assert first.external_id is None
    with organization_scope(organization.pk):
        assert Subscription.all_objects.filter(organizacao_id=organization.pk).count() == 1


def test_sync_free_without_external_id_only_applies_limit(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    limit = Plan.get_free().member_limit
    _memberships(organization, limit + 2)
    gateway = patch_billing_gateway(monkeypatch, {})

    Subscriptions.sync(organization.pk)

    assert gateway.commands == []
    assert MemberLimits.active_count(organization.pk) == limit


def test_sync_maps_price_to_plan_and_status(monkeypatch):
    organization = _organization()
    basic = _plan("basico", price="price_basico", rank=1)
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, basic)
    end = timezone.now() + timedelta(days=30)
    patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: remote_subscription(
                reference_id=str(organization.pk),
                status=RemoteStatus.PAST_DUE,
                customer_id="cus_1",
                current_period_end=end,
            ),
        },
    )

    Subscriptions.sync(organization.pk)

    state = _state(organization)
    assert state.plan_id == pro.pk
    assert state.status == SubscriptionStatus.PAST_DUE
    assert state.has_access is True
    assert state.customer_id == "cus_1"
    assert state.current_period_end == end
    assert state.synced_at is not None


def test_sync_clears_pending_plan_when_applied(monkeypatch):
    organization = _organization()
    basic = _plan("basico", price="price_basico", rank=1)
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, basic, pending_plan=pro)
    patch_billing_gateway(monkeypatch, {RetrieveSubscription: remote_subscription(reference_id=str(organization.pk))})

    Subscriptions.sync(organization.pk)

    state = _state(organization)
    assert state.plan_id == pro.pk
    assert state.pending_plan_id is None


def test_sync_terminal_resets_to_free(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro, cancel_at=timezone.now() + timedelta(days=5))
    patch_billing_gateway(
        monkeypatch,
        {RetrieveSubscription: remote_subscription(reference_id=str(organization.pk), status=RemoteStatus.CANCELED)},
    )

    Subscriptions.sync(organization.pk)

    state = _state(organization)
    assert state.plan == Plan.get_free()
    assert state.status == SubscriptionStatus.ACTIVE
    assert state.external_id is None
    assert state.cancel_at is None


def test_sync_unpaid_cancels_remote_and_resets(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: remote_subscription(reference_id=str(organization.pk), status=RemoteStatus.UNPAID),
            CancelRemoteSubscription: remote_subscription(reference_id=str(organization.pk), status=RemoteStatus.CANCELED),
        },
    )

    Subscriptions.sync(organization.pk)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 1
    assert cancellations[0].external_id == "sub_1"
    assert cancellations[0].timing == CancellationTiming.IMMEDIATELY
    assert cancellations[0].idempotency_key == "billing:abandon:sub_1"
    state = _state(organization)
    assert state.plan == Plan.get_free()
    assert state.external_id is None


def test_sync_unknown_price_keeps_plan_and_logs(monkeypatch, caplog):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    patch_billing_gateway(monkeypatch, {RetrieveSubscription: _remote_with_price(organization, "price_desconhecido")})

    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        Subscriptions.sync(organization.pk)

    assert _state(organization).plan_id == pro.pk
    assert any(record.levelno == logging.ERROR and "price_desconhecido" in record.getMessage() for record in caplog.records)


def test_sync_rejects_reference_mismatch(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    patch_billing_gateway(monkeypatch, {RetrieveSubscription: remote_subscription(reference_id="999", status=RemoteStatus.CANCELED)})

    with pytest.raises(ReferenceMismatch):
        Subscriptions.sync(organization.pk)

    state = _state(organization)
    assert state.plan_id == pro.pk
    assert state.external_id == "sub_1"
    assert state.status == SubscriptionStatus.ACTIVE


def test_sync_rejects_reference_mismatch_before_unpaid_cancel(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: remote_subscription(reference_id="999", status=RemoteStatus.UNPAID),
            CancelRemoteSubscription: remote_subscription(reference_id="999", status=RemoteStatus.CANCELED),
        },
    )

    with pytest.raises(ReferenceMismatch):
        Subscriptions.sync(organization.pk)

    assert not any(isinstance(command, CancelRemoteSubscription) for command in gateway.commands)
    state = _state(organization)
    assert state.plan_id == pro.pk
    assert state.external_id == "sub_1"
    assert state.status == SubscriptionStatus.ACTIVE
    assert state.synced_at is None


def test_sync_reads_remote_inside_lock(monkeypatch):
    organization = _organization()
    _linked_subscription(organization, _plan("pro", price="price_pro", rank=2))
    executed_sql = []
    calls = []

    def record_sql(execute, sql, params, many, context):
        executed_sql.append(sql)
        return execute(sql, params, many, context)

    def fake_retrieve(_command):
        calls.append(_command)
        assert connection.in_atomic_block
        assert any("FOR UPDATE" in sql and "billing_subscription" in sql for sql in executed_sql)
        return remote_subscription(reference_id=str(organization.pk))

    patch_billing_gateway(monkeypatch, {RetrieveSubscription: fake_retrieve})

    with connection.execute_wrapper(record_sql):
        Subscriptions.sync(organization.pk)

    assert len(calls) == 1


def test_sync_loss_of_access_suspends_members(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2, limit=5)
    _linked_subscription(organization, pro)
    _memberships(organization, 5)
    patch_billing_gateway(
        monkeypatch,
        {RetrieveSubscription: remote_subscription(reference_id=str(organization.pk), status=RemoteStatus.CANCELED)},
    )

    Subscriptions.sync(organization.pk)

    assert MemberLimits.active_count(organization.pk) == Plan.get_free().member_limit


def test_link_external_only_fills_empty_external_id(monkeypatch, caplog):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    patch_billing_gateway(monkeypatch, {CancelRemoteSubscription: remote_subscription(external_id="sub_2")})

    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        Subscriptions.link_external(organization.pk, external_id="sub_2", gateway_variant="stripe")

    state = _state(organization)
    assert state.external_id == "sub_1"
    assert state.gateway_variant == "stripe"
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_link_external_duplicate_cancels_newcomer_immediately(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    gateway = patch_billing_gateway(monkeypatch, {CancelRemoteSubscription: remote_subscription(external_id="sub_2")})

    Subscriptions.link_external(organization.pk, external_id="sub_2", gateway_variant="stripe")

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 1
    assert cancellations[0].external_id == "sub_2"
    assert cancellations[0].timing == CancellationTiming.IMMEDIATELY
    assert cancellations[0].idempotency_key == "billing:duplicate:sub_2"
    assert _state(organization).external_id == "sub_1"


def test_link_external_duplicate_cancel_failure_raises_and_keeps_first(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")

    def refused(command):
        raise GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe")

    patch_billing_gateway(monkeypatch, {CancelRemoteSubscription: refused})

    with pytest.raises(CheckoutError):
        Subscriptions.link_external(organization.pk, external_id="sub_2", gateway_variant="stripe")

    assert _state(organization).external_id == "sub_1"
