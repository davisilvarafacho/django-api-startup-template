"""Admin da cobrança: o catálogo de planos é editável; assinatura e eventos são só leitura."""

from django.contrib import admin

from apps.api.base.admin import BaseModelAdmin
from apps.billing.models import BillingEvent, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(BaseModelAdmin):
    """Catálogo de planos. O `gateway_price_id` é fixado depois de salvo: preço novo vira plano novo."""

    list_display = ("name", "slug", "rank", "member_limit", "is_free", "is_active")
    list_filter = ("is_free", "billing_cycle", "is_active")
    search_fields = ("name", "slug", "gateway_price_id")
    # Clonar um plano duplicaria slug, preço do gateway e o gratuito único: a ação não se aplica ao catálogo.
    actions = ("ativar_registros", "inativar_registros")

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if obj is not None and obj.pk is not None:
            return (*readonly, "gateway_price_id")
        return readonly


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Espelho da assinatura no gateway. Nada é editado aqui: a escrita vem só da sincronização."""

    list_display = ("organizacao", "plan", "status", "current_period_end", "cancel_at", "synced_at")
    list_filter = ("status", "plan")
    search_fields = ("organizacao__nome", "external_id")
    list_select_related = ("organizacao", "plan", "pending_plan")
    readonly_fields = (
        "organizacao",
        "plan",
        "pending_plan",
        "status",
        "gateway_variant",
        "external_id",
        "customer_id",
        "current_period_end",
        "cancel_at",
        "synced_at",
        "created_at",
        "last_modified_at",
        "created_by",
    )
    fields = readonly_fields
    # Sem ações em lote: uma ação customizada não passa pelas checagens de criação e alteração.
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingEvent)
class BillingEventAdmin(admin.ModelAdmin):
    """Caixa de entrada dos eventos do gateway, consultada para diagnosticar a reconciliação."""

    list_display = ("gateway_variant", "event_id", "event_type", "occurred_at", "processed_at", "attempts")
    list_filter = ("gateway_variant", "event_type")
    search_fields = ("event_id", "subscription_external_id")
    readonly_fields = (
        "gateway_variant",
        "event_id",
        "event_type",
        "resource_kind",
        "resource_id",
        "subscription_external_id",
        "occurred_at",
        "processed_at",
        "attempts",
        "last_error",
        "created_at",
        "last_modified_at",
    )
    fields = readonly_fields
    # Sem ações em lote: uma ação customizada não passa pelas checagens de criação e alteração.
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
