from django.contrib import admin

from apps.api.base.admin import BaseModelAdmin

from .models import Plano, PrecoPlano, VersaoPlano


@admin.register(Plano)
class PlanoAdmin(BaseModelAdmin):
    list_display = ("codigo", "nome", "visivel", "is_active")
    list_filter = ("visivel", "is_active", "is_deleted")
    search_fields = ("codigo", "nome")


@admin.register(VersaoPlano)
class VersaoPlanoAdmin(BaseModelAdmin):
    list_display = ("plano", "numero", "atual", "is_active", "publicada_em")
    list_filter = ("atual", "is_active", "is_deleted")
    search_fields = ("plano__codigo", "plano__nome")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.publicada_em is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrecoPlano)
class PrecoPlanoAdmin(BaseModelAdmin):
    list_display = ("versao_plano", "periodicidade", "moeda", "valor_base_centavos", "valor_seat_centavos", "is_active")
    list_filter = ("periodicidade", "moeda", "is_active", "is_deleted")
    search_fields = ("versao_plano__plano__codigo", "versao_plano__plano__nome")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.versao_plano.publicada_em is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
