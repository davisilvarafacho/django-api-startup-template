from functools import wraps

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from apps.api.base.admin import BaseModelAdmin
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

from .models import (
    AssinaturaGateway,
    CheckoutCobranca,
    EventoCobranca,
    FaturaAssinatura,
    ReaberturaEventoCobranca,
    ReferenciaPrecoGateway,
    SolicitacaoReconciliacaoCobranca,
)
from .processing import reabrir_evento_operacional, solicitar_reconciliacao_operacional

CHAVE_SESSAO_ORGANIZACAO_FATURAMENTO = "billing_admin_organizacao_id"
PARAMETRO_ORGANIZACAO = "organizacao__id__exact"


class TenantSeguroAdmin:
    """Mantém consulta, actions e renderização do Admin no mesmo contexto RLS."""

    def get_queryset(self, request):
        queryset = super().get_queryset(request)  # type: ignore[misc]
        organizacao_id = request.session.get(CHAVE_SESSAO_ORGANIZACAO_FATURAMENTO)
        return queryset.filter(organizacao_id=organizacao_id) if organizacao_id is not None else queryset.none()

    def get_urls(self):
        urls = super().get_urls()  # type: ignore[misc]
        for url in urls:
            url.callback = self._com_organizacao(url.callback)
        return urls

    def _com_organizacao(self, view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            organizacao_id = self._resolver_organizacao(request)
            if organizacao_id is None:
                self.message_user(  # type: ignore[attr-defined]
                    request,
                    "Cadastre uma organização antes de acessar o faturamento.",
                    level=messages.WARNING,
                )
                return redirect("admin:index")
            with organizacao_atual_privilegiada(organizacao_id):
                response = view(request, *args, **kwargs)
                if hasattr(response, "render") and callable(response.render):
                    response.render()
                return response

        return wrapper

    @staticmethod
    def _resolver_organizacao(request):
        filtro = request.GET.get(PARAMETRO_ORGANIZACAO, "")
        if filtro.isdigit() and Organizacao.objects.filter(pk=int(filtro)).exists():
            organizacao_id = int(filtro)
            request.session[CHAVE_SESSAO_ORGANIZACAO_FATURAMENTO] = organizacao_id
            return organizacao_id
        da_sessao = request.session.get(CHAVE_SESSAO_ORGANIZACAO_FATURAMENTO)
        if da_sessao is not None and Organizacao.objects.filter(pk=da_sessao).exists():
            return da_sessao
        primeira = Organizacao.objects.values_list("pk", flat=True).first()
        if primeira is not None:
            request.session[CHAVE_SESSAO_ORGANIZACAO_FATURAMENTO] = primeira
        return primeira


class SomenteLeituraAdmin(BaseModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssinaturaGateway)
class AssinaturaGatewayAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "identificador_externo", "is_active")
    list_filter = ("organizacao", "variante", "is_active")
    search_fields = ("identificador_externo", "organizacao__nome")


@admin.register(ReferenciaPrecoGateway)
class ReferenciaPrecoGatewayAdmin(BaseModelAdmin):
    list_display = ("id", "preco_plano", "variante", "componente", "is_active")
    search_fields = ("identificador_externo",)
    exclude = ("created_by",)


@admin.register(FaturaAssinatura)
class FaturaAssinaturaAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "status", "total_centavos", "moeda")
    list_filter = ("organizacao", "variante", "status", "moeda")
    exclude = ("url_hospedada",)


@admin.register(CheckoutCobranca)
class CheckoutCobrancaAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "finalidade", "status", "variante", "valor_esperado_centavos", "moeda_esperada")
    list_filter = ("organizacao", "variante", "finalidade", "status")
    exclude = ("url",)


@admin.register(EventoCobranca)
class EventoCobrancaAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "tipo", "status", "tentativas_processamento")
    list_filter = ("organizacao", "variante", "status")
    exclude = ("payload_normalizado", "hash_payload", "erro")
    actions = ("reabrir_falhos", "reconciliar_variantes")  # type: ignore[assignment]

    def has_retry_failed_permission(self, request):
        return bool(request is not None and request.user.has_perm("faturamento.retry_failed_eventocobranca"))

    def has_reconcile_permission(self, request):
        return bool(request is not None and request.user.has_perm("faturamento.reconcile_eventocobranca"))

    @admin.action(description="Reabrir eventos falhos selecionados", permissions=("retry_failed",))
    def reabrir_falhos(self, request, queryset):
        if not self.has_retry_failed_permission(request):
            raise PermissionDenied("Operador sem permissão de retry financeiro.")
        for evento in queryset:
            reabrir_evento_operacional(
                evento=evento,
                ator=request.user,
                motivo="Retry operacional pelo Django Admin.",
                chave_idempotencia=f"admin:{evento.pk}:{evento.tentativas_processamento}",
            )
            self.log_change(request, evento, "Evento reaberto para retry operacional auditado.")

    @admin.action(description="Agendar reconciliação das variantes selecionadas", permissions=("reconcile",))
    def reconciliar_variantes(self, request, queryset):
        if not self.has_reconcile_permission(request):
            raise PermissionDenied("Operador sem permissão de reconciliação financeira.")
        for evento in queryset:
            solicitar_reconciliacao_operacional(
                evento=evento,
                ator=request.user,
                motivo="Reconciliação operacional pelo Django Admin.",
                chave_idempotencia=f"admin-reconcile:{evento.pk}:{evento.tentativas_processamento}",
            )
            self.log_change(request, evento, "Reconciliação operacional agendada pelo Django Admin.")


@admin.register(ReaberturaEventoCobranca)
class ReaberturaEventoCobrancaAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "evento", "ator", "automatica", "tentativas_anteriores", "created_at")
    list_filter = ("organizacao", "automatica")
    search_fields = ("chave_idempotencia", "motivo")


@admin.register(SolicitacaoReconciliacaoCobranca)
class SolicitacaoReconciliacaoCobrancaAdmin(TenantSeguroAdmin, SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "ator", "resultado", "janela_inicio", "janela_fim")
    list_filter = ("organizacao", "variante", "resultado")
    exclude = ("parametros",)
