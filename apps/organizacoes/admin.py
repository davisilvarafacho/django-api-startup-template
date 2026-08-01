from django.contrib import admin

from apps.organizacoes.models import Convite, Organizacao, Time, Vinculo


@admin.register(Organizacao)
class OrganizacaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "slug", "is_active")
    search_fields = ("nome", "slug")


@admin.register(Time)
class TimeAdmin(admin.ModelAdmin):
    list_display = ("nome", "organizacao", "is_active")
    list_filter = ("organizacao",)
    search_fields = ("nome",)


@admin.register(Vinculo)
class VinculoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "organizacao", "papel", "is_active")
    list_filter = ("organizacao", "papel")
    filter_horizontal = ("times",)


@admin.register(Convite)
class ConviteAdmin(admin.ModelAdmin):
    list_display = ("email", "organizacao", "papel", "expira_em", "aceito_em")
    list_filter = ("organizacao", "papel")
    search_fields = ("email",)
