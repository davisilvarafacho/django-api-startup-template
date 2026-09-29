from django.contrib import admin

from apps.api.base.admin import BaseModelAdmin
from apps.parametros.models import Parametro


@admin.register(Parametro)
class ParametroAdmin(BaseModelAdmin):
    list_display = ("code", "description", "organizacao", "workspace", "is_active", "is_deleted")
    list_filter = ("is_active", "is_deleted")
    search_fields = ("code", "description", "organizacao__nome", "workspace__nome")
