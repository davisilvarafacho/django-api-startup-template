"""Admin da cobrança: o catálogo de planos é editável; assinatura e eventos são só leitura."""

from functools import wraps

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from apps.api.base.admin import BaseModelAdmin
from apps.billing.models import BillingEvent, Plan, Subscription
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

SESSION_ORGANIZATION_KEY = "billing_admin_organization_id"
ORGANIZATION_FILTER_PARAM = "organizacao__id__exact"

# Campos que deixam de ser editáveis depois que o plano é salvo. `is_free` precisa
# ficar fixo: desmarcar o gratuito faria `Plan.get_free()` falhar para todas as
# organizações. `slug` é o identificador que o frontend envia; `rank` decide upgrade
# × downgrade. `gateway_price_id` mapeia o preço do gateway a um único plano.
FIELDS_LOCKED_AFTER_SAVE = ("slug", "rank", "is_free", "gateway_price_id")


@admin.register(Plan)
class PlanAdmin(BaseModelAdmin):
    """Catálogo de planos. Depois de salvo, a identidade do plano não muda: preço novo vira plano novo."""

    list_display = ("name", "slug", "rank", "member_limit", "is_free", "is_active")
    list_filter = ("is_free", "billing_cycle", "is_active")
    search_fields = ("name", "slug", "gateway_price_id")
    # Clonar um plano duplicaria slug, preço do gateway e o gratuito único: a ação não se aplica ao catálogo.
    actions = ("ativar_registros", "inativar_registros")

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if obj is not None and obj.pk is not None:
            return (*readonly, *FIELDS_LOCKED_AFTER_SAVE)
        return readonly


class OrganizacaoAdminMixin:
    """Admin de um modelo isolado por organização (RLS), usado pelo staff sem contexto de tenant.

    As rotas de `/admin/` não passam pelo `OrganizacaoMiddleware`, então a query levantaria
    `RLSContextRequiredError`. Como no admin de metadados, as views são embrulhadas em
    `organizacao_atual_privilegiada`, uma organização por vez: a escolhida no filtro da
    listagem, a lembrada na sessão ou a primeira cadastrada.
    """

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        organization_id = request.session.get(SESSION_ORGANIZATION_KEY)
        if organization_id is not None:
            queryset = queryset.filter(organizacao_id=organization_id)
        return queryset

    def get_urls(self):
        """Embrulha todas as views do admin no contexto de tenant, para que a renderização rode dentro dele."""
        urls = super().get_urls()
        for url in urls:
            url.callback = self._with_organization(url.callback)
        return urls

    def _with_organization(self, view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            organization_id = self._resolve_organization(request)
            if organization_id is None:
                self.message_user(
                    request,
                    _("Cadastre uma organização antes de acessar as assinaturas."),
                    level=messages.WARNING,
                )
                return redirect("admin:index")

            with organizacao_atual_privilegiada(organization_id):
                response = view(request, *args, **kwargs)
                # Forçar a renderização mantém a query dentro do contexto de tenant.
                if hasattr(response, "render") and callable(response.render):
                    response.render()
                return response

        return wrapper

    def _resolve_organization(self, request):
        """Organização vigente: filtro da listagem > sessão > primeira cadastrada."""
        from_filter = request.GET.get(ORGANIZATION_FILTER_PARAM, "")
        if from_filter.isdigit():
            organization_id = int(from_filter)
            request.session[SESSION_ORGANIZATION_KEY] = organization_id
            return organization_id

        from_session = request.session.get(SESSION_ORGANIZATION_KEY)
        if from_session is not None and Organizacao.objects.filter(pk=from_session).exists():
            return from_session

        first_organization = Organizacao.objects.values_list("pk", flat=True).first()
        if first_organization is not None:
            request.session[SESSION_ORGANIZATION_KEY] = first_organization
        return first_organization


@admin.register(Subscription)
class SubscriptionAdmin(OrganizacaoAdminMixin, admin.ModelAdmin):
    """Espelho da assinatura no gateway, uma organização por vez. Nada é editado aqui: a escrita vem só da sincronização."""

    list_display = ("organizacao", "plan", "status", "current_period_end", "cancel_at", "synced_at")
    list_filter = ("organizacao", "status", "plan")
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
