"""Testes das operações de assinatura: assinar, trocar de plano, cancelar e desfazer o cancelamento."""

from datetime import timedelta

from django.utils import timezone

import pytest
from django_checkouts.enums import CancellationTiming, ChangeTiming, CheckoutMode, ProrationBehavior
from django_checkouts.gateways.commands import (
    CancelRemoteSubscription,
    ChangeRemoteSubscription,
    CreateCheckout,
    ResumeSubscription,
    RetrieveSubscription,
)
from django_checkouts.types import CatalogPrice
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.api.core.errors import APIError
from apps.billing.context import organization_scope
from apps.billing.errors import BillingErrorCode
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import Subscriptions
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.billing import fake_checkout, patch_billing_gateway, remote_subscription
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _organization(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _plan(slug, *, price=None, rank=1, limit=5, is_active=True):
    return Plan.objects.create(name=slug, slug=slug, rank=rank, member_limit=limit, gateway_price_id=price, is_active=is_active)


def _linked_subscription(organization, plan):
    """Assinatura paga local, ligada à assinatura remota `sub_1`."""
    with organization_scope(organization.pk):
        return Subscription.all_objects.create(
            organizacao=organization,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            external_id="sub_1",
            gateway_variant="stripe",
        )


def _state(organization):
    with organization_scope(organization.pk):
        return Subscription.all_objects.select_related("plan", "pending_plan").get(organizacao_id=organization.pk)


def _memberships(organization, quantity):
    return [Vinculo.objects.create(organizacao=organization, usuario=criar_usuario(), papel=Papel.MEMBRO) for _ in range(quantity)]


def _remote_with_price(organization, price_id, **overrides):
    item = SubscriptionItem(
        external_id="si_2",
        price_id=price_id,
        quantity=1,
        unit_amount=None,
        currency=None,
        billing_cycle=None,
        raw={},
    )
    return remote_subscription(reference_id=str(organization.pk), items=(item,), **overrides)


def test_subscribe_requires_free(monkeypatch):
    organization = _organization()
    _linked_subscription(organization, _plan("basic", price="price_basico"))
    pro = _plan("pro", price="price_pro", rank=2)
    patch_billing_gateway(monkeypatch, {})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.subscribe(organization.pk, plan=pro, idempotency_key="idem-1", customer_email=None)

    assert excinfo.value.code == BillingErrorCode.ALREADY_SUBSCRIBED
    assert excinfo.value.status_code == 409


def test_subscribe_rejects_free_or_inactive_plan(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    inactive = _plan("antigo", price="price_antigo", is_active=False)
    patch_billing_gateway(monkeypatch, {})

    for plan in (Plan.get_free(), inactive):
        with pytest.raises(APIError) as excinfo:
            Subscriptions.subscribe(organization.pk, plan=plan, idempotency_key="idem-1", customer_email=None)
        assert excinfo.value.code == BillingErrorCode.PLAN_UNAVAILABLE
        assert excinfo.value.status_code == 422


def test_subscribe_sends_reference_id_and_catalog_price(monkeypatch, settings):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    pro = _plan("pro", price="price_pro", rank=2)
    gateway = patch_billing_gateway(monkeypatch, {CreateCheckout: fake_checkout()})

    url = Subscriptions.subscribe(
        organization.pk,
        plan=pro,
        idempotency_key="idem-1",
        customer_email="financeiro@example.com",
    )

    assert url == "https://checkout.example/sessao"
    command = gateway.commands[0]
    assert isinstance(command, CreateCheckout)
    assert command.idempotency_key == "idem-1"
    request = command.request
    assert request.mode == CheckoutMode.SUBSCRIPTION
    assert request.reference_id == str(organization.pk)
    assert request.items[0].price == CatalogPrice(external_id="price_pro")
    assert request.customer.email == "financeiro@example.com"
    assert request.success_url == settings.FRONTEND_BILLING_CHECKOUT_SUCCESS_URL
    assert request.cancel_url == settings.FRONTEND_BILLING_CHECKOUT_CANCEL_URL


def test_upgrade_is_immediate_with_invoice_proration(monkeypatch):
    organization = _organization()
    basic = _plan("basic", price="price_basico", rank=1)
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, basic)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: _remote_with_price(organization, "price_pro"),
            ChangeRemoteSubscription: _remote_with_price(organization, "price_pro"),
        },
    )

    subscription = Subscriptions.change_plan(organization.pk, plan=pro, idempotency_key="idem-2")

    change = next(command for command in gateway.commands if isinstance(command, ChangeRemoteSubscription))
    assert change.external_id == "sub_1"
    assert change.request.timing == ChangeTiming.IMMEDIATELY
    assert change.request.proration == ProrationBehavior.INVOICE_IMMEDIATELY
    assert change.request.changes[0].item_id == "si_2"
    assert change.request.changes[0].price == CatalogPrice(external_id="price_pro")
    assert subscription.plan_id == pro.pk
    assert _state(organization).pending_plan is None


def test_downgrade_is_next_cycle_and_sets_pending_plan(monkeypatch):
    organization = _organization()
    basic = _plan("basic", price="price_basico", rank=1, limit=3)
    pro = _plan("pro", price="price_pro", rank=2, limit=10)
    _linked_subscription(organization, pro)
    _memberships(organization, 2)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: _remote_with_price(organization, "price_pro"),
            ChangeRemoteSubscription: _remote_with_price(organization, "price_pro"),
        },
    )

    Subscriptions.change_plan(organization.pk, plan=basic, idempotency_key="idem-3")

    change = next(command for command in gateway.commands if isinstance(command, ChangeRemoteSubscription))
    assert change.request.timing == ChangeTiming.NEXT_CYCLE
    assert change.request.proration == ProrationBehavior.NONE
    state = _state(organization)
    assert state.plan_id == pro.pk
    assert state.pending_plan_id == basic.pk


def test_downgrade_blocked_above_target_limit(monkeypatch):
    organization = _organization()
    basic = _plan("basic", price="price_basico", rank=1, limit=3)
    pro = _plan("pro", price="price_pro", rank=2, limit=10)
    _linked_subscription(organization, pro)
    _memberships(organization, 4)
    gateway = patch_billing_gateway(monkeypatch, {RetrieveSubscription: _remote_with_price(organization, "price_pro")})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.change_plan(organization.pk, plan=basic, idempotency_key="idem-4")

    assert excinfo.value.code == BillingErrorCode.DOWNGRADE_BLOCKED
    assert excinfo.value.status_code == 409
    assert excinfo.value.context == {"excess": 1}
    assert not any(isinstance(command, ChangeRemoteSubscription) for command in gateway.commands)


def test_same_rank_counts_as_upgrade(monkeypatch):
    organization = _organization()
    monthly = _plan("monthly", price="price_mensal", rank=2)
    annual = _plan("annual", price="price_anual", rank=2)
    _linked_subscription(organization, monthly)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            RetrieveSubscription: _remote_with_price(organization, "price_anual"),
            ChangeRemoteSubscription: _remote_with_price(organization, "price_anual"),
        },
    )

    Subscriptions.change_plan(organization.pk, plan=annual, idempotency_key="idem-5")

    change = next(command for command in gateway.commands if isinstance(command, ChangeRemoteSubscription))
    assert change.request.timing == ChangeTiming.IMMEDIATELY
    assert change.request.proration == ProrationBehavior.INVOICE_IMMEDIATELY


def test_change_to_free_cancels(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            CancelRemoteSubscription: _remote_with_price(organization, "price_pro", cancel_at=timezone.now() + timedelta(days=5)),
            RetrieveSubscription: _remote_with_price(organization, "price_pro", cancel_at=timezone.now() + timedelta(days=5)),
        },
    )

    Subscriptions.change_plan(organization.pk, plan=Plan.get_free(), idempotency_key="idem-6")

    cancel = next(command for command in gateway.commands if isinstance(command, CancelRemoteSubscription))
    assert cancel.timing == CancellationTiming.PERIOD_END
    assert not any(isinstance(command, ChangeRemoteSubscription) for command in gateway.commands)


def test_change_requires_paid_subscription(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    pro = _plan("pro", price="price_pro", rank=2)
    patch_billing_gateway(monkeypatch, {})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.change_plan(organization.pk, plan=pro, idempotency_key="idem-7")

    assert excinfo.value.code == BillingErrorCode.NOT_SUBSCRIBED
    assert excinfo.value.status_code == 409


def test_change_to_same_plan_is_rejected(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    patch_billing_gateway(monkeypatch, {})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.change_plan(organization.pk, plan=pro, idempotency_key="idem-8")

    assert excinfo.value.code == BillingErrorCode.SAME_PLAN
    assert excinfo.value.status_code == 422


def test_cancel_blocked_above_free_limit(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    limit = Plan.get_free().member_limit
    _memberships(organization, limit + 1)
    gateway = patch_billing_gateway(monkeypatch, {RetrieveSubscription: _remote_with_price(organization, "price_pro")})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.cancel(organization.pk, idempotency_key="idem-9")

    assert excinfo.value.code == BillingErrorCode.DOWNGRADE_BLOCKED
    assert excinfo.value.context == {"excess": 1}
    assert not any(isinstance(command, CancelRemoteSubscription) for command in gateway.commands)


def test_cancel_at_period_end_then_resume(monkeypatch):
    organization = _organization()
    pro = _plan("pro", price="price_pro", rank=2)
    _linked_subscription(organization, pro)
    end = timezone.now() + timedelta(days=5)

    gateway = patch_billing_gateway(
        monkeypatch,
        {
            CancelRemoteSubscription: _remote_with_price(organization, "price_pro", cancel_at=end),
            RetrieveSubscription: _remote_with_price(organization, "price_pro", cancel_at=end),
        },
    )
    cancelled = Subscriptions.cancel(organization.pk, idempotency_key="idem-10")

    cancel = next(command for command in gateway.commands if isinstance(command, CancelRemoteSubscription))
    assert cancel.timing == CancellationTiming.PERIOD_END
    assert cancel.idempotency_key == "idem-10"
    assert cancelled.cancel_at == end
    assert _state(organization).is_cancel_scheduled is True

    gateway = patch_billing_gateway(
        monkeypatch,
        {
            ResumeSubscription: _remote_with_price(organization, "price_pro"),
            RetrieveSubscription: _remote_with_price(organization, "price_pro"),
        },
    )
    resumed = Subscriptions.resume(organization.pk, idempotency_key="idem-11")

    assert isinstance(gateway.commands[0], ResumeSubscription)
    assert gateway.commands[0].idempotency_key == "idem-11"
    assert resumed.cancel_at is None
    state = _state(organization)
    assert state.cancel_at is None
    assert state.status == SubscriptionStatus.ACTIVE


def test_second_checkout_refused_once_first_subscription_is_linked(monkeypatch):
    organization = _organization()
    Subscriptions.ensure_free(organization.pk)
    pro = _plan("pro", price="price_pro", rank=2)
    patch_billing_gateway(monkeypatch, {CreateCheckout: fake_checkout()})
    Subscriptions.subscribe(organization.pk, plan=pro, idempotency_key="idem-1", customer_email=None)
    # O webhook do primeiro checkout chegou e ligou o espelho à assinatura remota.
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")

    with pytest.raises(APIError) as excinfo:
        Subscriptions.subscribe(organization.pk, plan=pro, idempotency_key="idem-2", customer_email=None)

    assert excinfo.value.code == BillingErrorCode.ALREADY_SUBSCRIBED
    assert excinfo.value.status_code == 409


def test_checkout_refused_when_linked_subscription_mirrors_free_plan(monkeypatch):
    organization = _organization()
    _linked_subscription(organization, Plan.get_free())
    pro = _plan("pro", price="price_pro", rank=2)
    patch_billing_gateway(monkeypatch, {})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.subscribe(organization.pk, plan=pro, idempotency_key="idem-1", customer_email=None)

    assert excinfo.value.code == BillingErrorCode.ALREADY_SUBSCRIBED


def test_cancel_reaches_gateway_when_plan_price_is_unknown(monkeypatch):
    organization = _organization()
    _linked_subscription(organization, Plan.get_free())
    agora = timezone.now()
    # Preço desconhecido no catálogo: o espelho mantém o gratuito, mas a assinatura continua cobrando.
    remote = _remote_with_price(organization, "price_desconhecido", cancel_at=agora + timedelta(days=30))
    gateway = patch_billing_gateway(
        monkeypatch,
        {
            CancelRemoteSubscription: remote,
            RetrieveSubscription: remote,
        },
    )

    subscription = Subscriptions.cancel(organization.pk, idempotency_key="idem-cancel")

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 1
    assert cancellations[0].external_id == "sub_1"
    assert cancellations[0].timing == CancellationTiming.PERIOD_END
    assert subscription.is_cancel_scheduled is True


def test_change_to_paid_plan_refused_when_current_plan_is_unknown(monkeypatch):
    organization = _organization()
    _linked_subscription(organization, Plan.get_free())
    pro = _plan("pro", price="price_pro", rank=2)
    gateway = patch_billing_gateway(monkeypatch, {})

    with pytest.raises(APIError) as excinfo:
        Subscriptions.change_plan(organization.pk, plan=pro, idempotency_key="idem-2")

    assert excinfo.value.code == BillingErrorCode.CURRENT_PLAN_UNKNOWN
    assert excinfo.value.status_code == 409
    assert gateway.commands == []
