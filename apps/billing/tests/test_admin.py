"""Testes do admin da cobrança: o que pode ser editado e o que é só leitura."""

from django.contrib.admin.sites import AdminSite

import pytest

from apps.billing.admin import BillingEventAdmin, PlanAdmin, SubscriptionAdmin
from apps.billing.models import BillingEvent, Plan, Subscription


@pytest.mark.django_db
def test_plan_gateway_price_id_read_only_after_save(rf):
    """`gateway_price_id` é editável só na criação; salvo, vira somente leitura."""
    admin_plano = PlanAdmin(Plan, AdminSite())
    request = rf.get("/")

    assert "gateway_price_id" not in admin_plano.get_readonly_fields(request, obj=None)

    plano = Plan.objects.create(
        name="Pro",
        slug="pro-admin",
        rank=1,
        member_limit=10,
        gateway_price_id="price_pro_admin",
    )

    assert "gateway_price_id" in admin_plano.get_readonly_fields(request, obj=plano)


@pytest.mark.parametrize("admin_class", [SubscriptionAdmin, BillingEventAdmin])
def test_subscription_and_billing_event_admin_is_read_only(admin_class, rf):
    """Assinatura e evento são espelho/caixa de entrada: o admin não cria, altera nem apaga."""
    model = Subscription if admin_class is SubscriptionAdmin else BillingEvent
    admin_modelo = admin_class(model, AdminSite())
    request = rf.get("/")

    assert admin_modelo.has_add_permission(request) is False
    assert admin_modelo.has_change_permission(request) is False
    assert admin_modelo.has_delete_permission(request) is False
    # Ação em lote customizada não passa pelas checagens de criação e alteração: nenhuma pode existir.
    assert admin_modelo.get_actions(request) == {}
