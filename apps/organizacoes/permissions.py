"""Permissão de tenant: o fallback global do framework.

Está em `DEFAULT_PERMISSION_CLASSES`, então **toda** rota exige um
`X-Organization` válido por padrão. A sobrescrita é global e declarativa: cada
app lista suas exceções em `public_routes.py` (rotas sem token) ou
`tenant_free_routes.py` (rotas com token, sem organização).
"""
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from apps.api.core.routes_registry import routes_registry
from apps.organizacoes.constants import HEADER_ORGANIZACAO
from apps.organizacoes.context import definir_organizacao_atual
from apps.organizacoes.models import Vinculo
from apps.organizacoes.routes import tenant_free_registry


class TenantPermission(BasePermission):
    """Resolve a organização da request e aplica o contexto de RLS.

    Aplica `SET LOCAL` dentro da transação aberta pelo `OrganizacaoMiddleware` e
    expõe `request.organizacao` e `request.vinculo` para as views.
    """

    message = "Usuário sem vínculo ativo nesta organização."

    def has_permission(self, request, view):
        path = request.path_info

        # Sem token não há vínculo a validar; isenção explícita idem.
        if routes_registry.matches(path) or tenant_free_registry.matches(path):
            return True

        slug = getattr(request, "organizacao_slug", None)
        if not slug:
            raise ValidationError({HEADER_ORGANIZACAO: "Header obrigatório."})

        if not request.user or not request.user.is_authenticated:
            return False

        vinculo = (
            Vinculo.objects.select_related("organizacao")
            .filter(organizacao__slug=slug, usuario=request.user, ativo=True)
            .first()
        )

        if vinculo is None:
            raise PermissionDenied(self.message)

        request.organizacao = vinculo.organizacao
        request.vinculo = vinculo

        definir_organizacao_atual(vinculo.organizacao_id)

        return True
