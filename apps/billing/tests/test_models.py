"""Testes dos models de cobrança: regras de acesso (sem banco) e restrições (com banco)."""

from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest

from apps.billing.handlers import ensure_free_plan
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.organizacoes.context import organizacao_atual
from apps.organizacoes.models import Organizacao


def _plan(**overrides):
    campos = {"name": "Pro", "slug": "pro", "rank": 1, "member_limit": 10, "is_free": False}
    campos.update(overrides)
    return Plan(**campos)


def _free_plan():
    return Plan(name="Gratuito", slug="gratuito", rank=0, member_limit=3, is_free=True)


@pytest.mark.parametrize(
    ("status", "expected_access"),
    [
        (SubscriptionStatus.ACTIVE, True),
        (SubscriptionStatus.TRIALING, True),
        (SubscriptionStatus.PAST_DUE, True),
        (SubscriptionStatus.INCOMPLETE, False),
        (SubscriptionStatus.PAUSED, False),
        (SubscriptionStatus.UNPAID, False),
        (SubscriptionStatus.CANCELED, False),
        (SubscriptionStatus.EXPIRED, False),
    ],
)
def test_has_access_by_status(status, expected_access):
    assert Subscription(plan=_plan(), status=status).has_access is expected_access


def test_effective_plan_falls_back_to_free_without_access(monkeypatch):
    free_plan = _free_plan()
    monkeypatch.setattr(Plan, "get_free", classmethod(lambda cls: free_plan))
    paid_plan = _plan()

    subscription = Subscription(plan=paid_plan, status=SubscriptionStatus.CANCELED)
    assert subscription.effective_plan is free_plan

    subscription.status = SubscriptionStatus.ACTIVE
    assert subscription.effective_plan is paid_plan


def test_member_limit_uses_effective_plan(monkeypatch):
    free_plan = _free_plan()
    monkeypatch.setattr(Plan, "get_free", classmethod(lambda cls: free_plan))
    paid_plan = _plan(member_limit=10)

    assert Subscription(plan=paid_plan, status=SubscriptionStatus.ACTIVE).member_limit == 10
    assert Subscription(plan=paid_plan, status=SubscriptionStatus.UNPAID).member_limit == 3


def test_is_cancel_scheduled_requires_cancel_at_and_access():
    moment = timezone.now()

    assert Subscription(plan=_plan(), status=SubscriptionStatus.ACTIVE, cancel_at=moment).is_cancel_scheduled is True
    assert Subscription(plan=_plan(), status=SubscriptionStatus.ACTIVE, cancel_at=None).is_cancel_scheduled is False
    assert Subscription(plan=_plan(), status=SubscriptionStatus.CANCELED, cancel_at=moment).is_cancel_scheduled is False


@pytest.mark.django_db
def test_only_one_free_plan_allowed():
    ensure_free_plan(sender=django_apps.get_app_config("billing"), using="default")

    with pytest.raises(IntegrityError, match="billing_plan_gratuito_unico"), transaction.atomic():
        Plan.objects.create(name="Outro gratuito", slug="outro-gratuito", rank=0, member_limit=5, is_free=True)


@pytest.mark.django_db
def test_one_subscription_per_organization():
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    plan = Plan.get_free()

    with organizacao_atual(organization.id):
        Subscription.objects.create(organizacao=organization, plan=plan, status=SubscriptionStatus.ACTIVE)
        with pytest.raises(IntegrityError, match="billing_subscription_unica_por_organizacao"), transaction.atomic():
            Subscription.objects.create(organizacao=organization, plan=plan, status=SubscriptionStatus.ACTIVE)


@pytest.mark.django_db
def test_billing_event_unique_per_variant_and_event_id():
    campos = {
        "event_id": "evt_1",
        "event_type": "invoice.paid",
        "resource_kind": "invoice",
        "resource_id": "in_1",
        "occurred_at": timezone.now(),
    }
    BillingEvent.objects.create(gateway_variant="stripe", **campos)
    BillingEvent.objects.create(gateway_variant="stripe_outra", **campos)

    with pytest.raises(IntegrityError, match="billing_event_unico_por_variante"), transaction.atomic():
        BillingEvent.objects.create(gateway_variant="stripe", **campos)


@pytest.mark.django_db
def test_get_free_returns_soft_deleted_free_plan():
    free_plan = Plan.all_objects.get(is_free=True)
    free_plan.delete()

    assert Plan.get_free().pk == free_plan.pk
