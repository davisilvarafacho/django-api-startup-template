from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from hijack.contrib.admin import HijackUserAdminMixin

from apps.api.base.admin import BaseModelAdmin

from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(BaseModelAdmin, HijackUserAdminMixin, BaseUserAdmin):
    # campos de listagem
    list_display = ("id", "email", "first_name", "last_name", "is_active", "is_staff", "is_superuser", "created_at")
    list_display_links = ("id", "email")
    list_filter = ("is_active", "is_staff", "is_superuser", "created_at")
    search_fields = ("id", "email", "first_name", "last_name")
    ordering = ("-id",)

    # campos readonly - combina os do BaseModelAdmin com os específicos de usuário
    readonly_fields = (
        "last_login",
        "date_joined",
        "created_at",
        "last_modified_at",
    )

    # Fieldsets para edição (sem username, usando email como identificador principal)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Informações Pessoais"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissões"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    # "user_permissions",
                ),
            },
        ),
        (
            _("Datas Importantes"),
            {"fields": ("last_login", "date_joined")},
        ),
        (
            _("Metadados"),
            {
                "fields": (
                    "created_at",
                    "last_modified_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    # Fieldsets para adicionar novo usuário
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "is_staff", "password1", "password2"),
            },
        ),
    )

    # Autocomplete para grupos e permissões
    filter_horizontal = ("groups", "user_permissions")

    def get_search_results(self, request, queryset, search_term):
        """Sobrescrever para usar email como campo principal de busca."""
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        return queryset, use_distinct

    def get_clone_field_overrides(self, obj):
        """Sobrescreve campos ao clonar usuário."""
        return {
            "email": f"clone_{obj.email}",
            "is_active": False,
        }
