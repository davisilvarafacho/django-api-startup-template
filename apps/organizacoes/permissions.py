"""Permissão DRF que resolve a organização e aplica o contexto de RLS."""
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from apps.organizacoes.constants import HEADER_ORGANIZACAO
from apps.organizacoes.context import definir_organizacao_atual
from apps.organizacoes.models import Vinculo


class TenantPermission(BasePermission):
    """Exige um `X-Organization` válido e um vínculo ativo do usuário.

    Roda depois da autenticação do DRF e aplica o contexto de RLS com
    `SET LOCAL`, dentro da transação aberta pelo `OrganizacaoMiddleware`.
    Expõe `request.organizacao` e `request.vinculo` para as views.
    """

    message = "Usuário sem vínculo ativo nesta organização."

    def has_permission(self, request, view):
        slug = getattr(request, "organizacao_slug", None)
        if not slug:
            raise NotFound(f"O header '{HEADER_ORGANIZACAO}' é obrigatório.")

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
