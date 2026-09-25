"""Permissões globais relacionadas ao Workspace atual."""

from rest_framework.permissions import BasePermission

from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError
from apps.workspaces.errors import WorkspaceErrorCode


class WorkspacePermission(BasePermission):
    """Exige Workspace atual somente em rotas humanas de negócio."""

    def has_permission(self, request, view):
        if getattr(request, "tenant_required", True) is False:
            return True
        if getattr(request, "workspace_required", True) is False:
            return True
        if getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY:
            return True
        if getattr(request, "current_workspace", None) is None:
            raise APIError(WorkspaceErrorCode.CURRENT_REQUIRED, status_code=409)
        return True
