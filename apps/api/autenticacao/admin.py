from django.contrib import admin

from .models import AuthToken


@admin.register(AuthToken)
class AuthTokenAdmin(admin.ModelAdmin):
    """Nunca expõe `digest`/`token_key` (segredo) nem o `metadata` operacional."""

    list_display = (
        "uuid",
        "type",
        "name",
        "organization",
        "responsavel",
        "expiry",
        "estado",
    )
    list_filter = ("type", "organization")
    search_fields = ("uuid", "name", "responsavel__email")
    readonly_fields = (
        "uuid",
        "responsavel",
        "type",
        "organization",
        "scopes",
        "expiry",
        "created_by",
        "created_at",
        "last_modified_at",
        "revoked_at",
        "revoked_by",
        "suspended_at",
        "suspended_by",
        "suspension_reason",
        "replaced_by",
    )
    fields = readonly_fields + ("name",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Revogação é lógica (`revoked_at`) e permanente; o registro nunca é
        # apagado, nem pelo admin.
        return False

    @admin.display(description="Estado")
    def estado(self, obj):
        if obj.revoked_at:
            return "Revogado"
        if obj.suspended_at:
            return "Suspenso"
        return "Ativo"
