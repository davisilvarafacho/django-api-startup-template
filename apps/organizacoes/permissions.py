"""Permissão de tenant: o fallback global do framework.

Está em `DEFAULT_PERMISSION_CLASSES`, então **toda** rota exige um
`X-Organization` válido por padrão. A sobrescrita é global e declarativa: cada
app lista suas exceções em `public_routes.py` (rotas sem token) ou
`tenant_free_routes.py` (rotas com token, sem organização).
"""

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from apps.api.core.route_markers import MARCADOR_SEM_TENANCY, tem_marcador
from apps.api.core.routes_registry import routes_registry
from apps.organizacoes.constants import HEADER_ORGANIZACAO
from apps.organizacoes.context import definir_organizacao_atual
from apps.organizacoes.models import Papel
from apps.organizacoes.routes import tenant_free_registry
from common.permission_cache.resolvers.tenant import TenantAccessResolver


class TenantPermission(BasePermission):
    """Resolve a organização da request e aplica o contexto de RLS.

    Aplica `SET LOCAL` dentro da transação aberta pelo `OrganizacaoMiddleware` e
    expõe `request.tenant` para as views.
    """

    message = "Usuário sem vínculo ativo nesta organização."

    def has_permission(self, request, view):
        path = request.path_info

        # Sem token não há vínculo a validar; isenção explícita idem.
        # O DRF já entrega a view resolvida, então aqui não é preciso resolver a URL.
        if routes_registry.matches(path) or tenant_free_registry.matches(path):
            return True

        if tem_marcador(view, MARCADOR_SEM_TENANCY):
            return True

        slug = getattr(request, "organizacao_slug", None)
        if not slug:
            raise ValidationError({HEADER_ORGANIZACAO: "Header obrigatório."})

        if not request.user or not request.user.is_authenticated:
            return False

        tenant = TenantAccessResolver().by_slug(request.user.pk, slug)

        if tenant is None:
            raise PermissionDenied(self.message)

        request.tenant = tenant

        definir_organizacao_atual(tenant.organization_id)

        return True


class PapelMinimoPermission(BasePermission):
    """Exige um papel minimo por action, usando o vinculo resolvido no tenant."""

    message = "Papel insuficiente nesta organização."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False

        papel_minimo = self.get_papel_minimo(view)
        return tenant.has_minimum_role(papel_minimo)

    def get_papel_minimo(self, view):
        papeis_por_action = getattr(view, "papeis_por_action", {})
        action = getattr(view, "action", None)
        return papeis_por_action.get(action, getattr(view, "papel_minimo", Papel.VISUALIZADOR))
