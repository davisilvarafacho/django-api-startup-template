"""Testes dos models de cobrança: regras de acesso (sem banco) e restrições (com banco)."""

from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest

from apps.billing.handlers import ensure_free_plan
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.organizacoes.context import organizacao_atual
from apps.organizacoes.models import Organizacao


def _plano(**overrides):
    campos = {"name": "Pro", "slug": "pro", "rank": 1, "member_limit": 10, "is_free": False}
    campos.update(overrides)
    return Plan(**campos)


def _gratuito():
    return Plan(name="Gratuito", slug="gratuito", rank=0, member_limit=3, is_free=True)


@pytest.mark.parametrize(
    ("status", "tem_acesso"),
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
def test_has_access_by_status(status, tem_acesso):
    assert Subscription(plan=_plano(), status=status).has_access is tem_acesso


def test_effective_plan_falls_back_to_free_without_access(monkeypatch):
    gratuito = _gratuito()
    monkeypatch.setattr(Plan, "get_free", classmethod(lambda cls: gratuito))
    pago = _plano()

    assinatura = Subscription(plan=pago, status=SubscriptionStatus.CANCELED)
    assert assinatura.effective_plan is gratuito

    assinatura.status = SubscriptionStatus.ACTIVE
    assert assinatura.effective_plan is pago


def test_member_limit_uses_effective_plan(monkeypatch):
    gratuito = _gratuito()
    monkeypatch.setattr(Plan, "get_free", classmethod(lambda cls: gratuito))
    pago = _plano(member_limit=10)

    assert Subscription(plan=pago, status=SubscriptionStatus.ACTIVE).member_limit == 10
    assert Subscription(plan=pago, status=SubscriptionStatus.UNPAID).member_limit == 3


def test_is_cancel_scheduled_requires_cancel_at_and_access():
    agora = timezone.now()

    assert Subscription(plan=_plano(), status=SubscriptionStatus.ACTIVE, cancel_at=agora).is_cancel_scheduled is True
    assert Subscription(plan=_plano(), status=SubscriptionStatus.ACTIVE, cancel_at=None).is_cancel_scheduled is False
    assert Subscription(plan=_plano(), status=SubscriptionStatus.CANCELED, cancel_at=agora).is_cancel_scheduled is False


@pytest.mark.django_db
def test_only_one_free_plan_allowed():
    ensure_free_plan(sender=django_apps.get_app_config("billing"), using="default")

    with pytest.raises(IntegrityError, match="billing_plan_gratuito_unico"), transaction.atomic():
        Plan.objects.create(name="Outro gratuito", slug="outro-gratuito", rank=0, member_limit=5, is_free=True)


@pytest.mark.django_db
def test_one_subscription_per_organization():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    plano = Plan.get_free()

    with organizacao_atual(organizacao.id):
        Subscription.objects.create(organizacao=organizacao, plan=plano, status=SubscriptionStatus.ACTIVE)
        with pytest.raises(IntegrityError, match="billing_subscription_unica_por_organizacao"), transaction.atomic():
            Subscription.objects.create(organizacao=organizacao, plan=plano, status=SubscriptionStatus.ACTIVE)


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

