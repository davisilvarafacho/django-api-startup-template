from django.contrib import admin

from apps.workspaces.models import VinculoWorkspace, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("nome", "organizacao", "is_active", "is_deleted")
    list_filter = ("is_active", "is_deleted")
    search_fields = ("nome", "slug", "organizacao__nome")


@admin.register(VinculoWorkspace)
class VinculoWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("vinculo", "workspace", "organizacao", "is_active", "selected_for_view", "is_deleted")
    list_filter = ("is_active", "selected_for_view", "is_deleted")
    list_select_related = ("vinculo__usuario", "workspace__organizacao")
    search_fields = ("vinculo__usuario__email", "workspace__nome", "workspace__organizacao__nome")

    @admin.display(ordering="workspace__organizacao__nome", description="organização")
    def organizacao(self, obj):
        return obj.workspace.organizacao

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
