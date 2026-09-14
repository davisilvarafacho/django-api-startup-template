"""Permissões que consomem o contexto resolvido pelo middleware de tenant."""

from rest_framework.permissions import BasePermission

from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Papel


def _is_api_key(request):
    return getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY


class TenantPermission(BasePermission):
    """Exige o contexto que o ``OrganizacaoMiddleware`` já preparou."""

    message = "Usuário sem vínculo ativo nesta organização."

    def has_permission(self, request, view):
        if getattr(request, "tenant_required", True) is False:
            return True

        return getattr(request, "tenant", None) is not None


class PapelMinimoPermission(BasePermission):
    """Exige um papel minimo por action, usando o vinculo resolvido no tenant.

    Não se aplica a API keys: papel é uma propriedade do vínculo pessoal do
    responsável, que a autorização de API key deliberadamente ignora (só os
    scopes da própria credencial mandam).
    """

    message = "Papel insuficiente nesta organização."

    def has_permission(self, request, view):
        if _is_api_key(request):
            return True

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        papel_minimo = self.get_papel_minimo(view)
        if not tenant.has_minimum_role(papel_minimo):
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=403, message=self.message)

        return True

    def get_papel_minimo(self, view):
        papeis_por_action = getattr(view, "papeis_por_action", {})
        action = getattr(view, "action", None)
        return papeis_por_action.get(action, getattr(view, "papel_minimo", Papel.VISUALIZADOR))
