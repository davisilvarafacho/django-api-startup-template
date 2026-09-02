from django.contrib import admin

from apps.api.base.admin import BaseModelAdmin

from .models import (
    AssinaturaGateway,
    CheckoutCobranca,
    EventoCobranca,
    FaturaAssinatura,
    ReferenciaPrecoGateway,
    SolicitacaoReconciliacaoCobranca,
)
from .processing import reabrir_evento_operacional, solicitar_reconciliacao_operacional


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
class AssinaturaGatewayAdmin(SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "identificador_externo", "is_active")
    search_fields = ("identificador_externo", "organizacao__nome")


@admin.register(ReferenciaPrecoGateway)
class ReferenciaPrecoGatewayAdmin(BaseModelAdmin):
    list_display = ("id", "preco_plano", "variante", "componente", "is_active")
    search_fields = ("identificador_externo",)
    exclude = ("created_by",)


@admin.register(FaturaAssinatura)
class FaturaAssinaturaAdmin(SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "status", "total_centavos", "moeda")
    exclude = ("url_hospedada",)


@admin.register(CheckoutCobranca)
class CheckoutCobrancaAdmin(SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "finalidade", "status", "variante", "valor_esperado_centavos", "moeda_esperada")
    exclude = ("url",)


@admin.register(EventoCobranca)
class EventoCobrancaAdmin(SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "tipo", "status", "tentativas_processamento")
    exclude = ("payload_normalizado", "hash_payload", "erro")
    actions: tuple[str, ...] = ("reabrir_falhos", "reconciliar_variantes")

    def has_change_permission(self, request, obj=None):
        return bool(request is not None and request.user.has_perm("faturamento.change_eventocobranca"))

    @admin.action(description="Reabrir eventos falhos selecionados")
    def reabrir_falhos(self, request, queryset):
        for evento in queryset:
            reabrir_evento_operacional(
                evento=evento,
                ator=request.user,
                motivo="Retry operacional pelo Django Admin.",
                chave_idempotencia=f"admin:{evento.pk}:{evento.tentativas_processamento}",
            )
            self.log_change(request, evento, "Evento reaberto para retry operacional auditado.")

    @admin.action(description="Agendar reconciliação das variantes selecionadas")
    def reconciliar_variantes(self, request, queryset):
        for evento in queryset:
            solicitar_reconciliacao_operacional(
                evento=evento,
                ator=request.user,
                motivo="Reconciliação operacional pelo Django Admin.",
                chave_idempotencia=f"admin-reconcile:{evento.pk}:{evento.tentativas_processamento}",
            )
            self.log_change(request, evento, "Reconciliação operacional agendada pelo Django Admin.")


@admin.register(SolicitacaoReconciliacaoCobranca)
class SolicitacaoReconciliacaoCobrancaAdmin(SomenteLeituraAdmin):
    list_display = ("id", "organizacao", "variante", "ator", "resultado", "janela_inicio", "janela_fim")
    exclude = ("parametros",)
