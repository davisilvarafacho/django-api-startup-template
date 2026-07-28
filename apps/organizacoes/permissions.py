"""Permissão de tenant: o fallback global do framework.

Está em `DEFAULT_PERMISSION_CLASSES`, então **toda** rota exige um
`X-Organization` válido por padrão. A sobrescrita é global e declarativa: cada
app lista suas exceções em `public_routes.py` (rotas sem token) ou
`tenant_free_routes.py` (rotas com token, sem organização).
"""
from rest_framework.permissions import BasePermission

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError
from apps.api.core.route_markers import MARCADOR_SEM_TENANCY, tem_marcador
from apps.api.core.routes_registry import routes_registry
from apps.organizacoes.constants import HEADER_ORGANIZACAO
from apps.organizacoes.context import definir_organizacao_atual
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Papel, Vinculo
from apps.organizacoes.routes import tenant_free_registry


def _is_api_key(request):
    return getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY


class TenantPermission(BasePermission):
    """Resolve a organização da request e aplica o contexto de RLS.

    Aplica `SET LOCAL` dentro da transação aberta pelo `OrganizacaoMiddleware` e
    expõe `request.organizacao` e `request.vinculo` para as views.
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
            raise APIError(
                OrganizationErrorCode.HEADER_REQUIRED,
                status_code=422,
                field=HEADER_ORGANIZACAO,
            )

        if not request.user or not request.user.is_authenticated:
            return False

        auth_token = getattr(request, "auth", None)
        if _is_api_key(request):
            # O tenant de uma API key vem só da própria credencial (já validado
            # contra o header em `resolve_token_organization`). Não usamos o
            # Vinculo para decidir permissions (autorização é só por scopes),
            # mas exigimos que ele exista: se o responsável perdeu o vínculo
            # com a organização, a key fica inutilizável até ele ser retomado.
            # `ensure_api_key_still_valid` materializa a suspensão (fail-closed
            # com trilha de auditoria) em vez de só recusar na hora.
            from apps.api.autenticacao.services import ensure_api_key_still_valid

            ensure_api_key_still_valid(auth_token)
            if auth_token.suspended_at is not None:
                raise APIError(AuthErrorCode.API_KEY_SUSPENDED, status_code=401)

            organizacao = auth_token.organization
            request.organizacao = organizacao
            definir_organizacao_atual(organizacao.id)
            return True

        vinculo = (
            Vinculo.objects.select_related("organizacao")
            .filter(organizacao__slug=slug, usuario=request.user, ativo=True)
            .first()
        )

        if vinculo is None:
            raise APIError(OrganizationErrorCode.MEMBERSHIP_REQUIRED, status_code=403, message=self.message)

        request.organizacao = vinculo.organizacao
        request.vinculo = vinculo

        definir_organizacao_atual(vinculo.organizacao_id)

        return True


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

        vinculo = getattr(request, "vinculo", None)
        if vinculo is None:
            return False

        papel_minimo = self.get_papel_minimo(view)
        if not vinculo.tem_papel_minimo(papel_minimo):
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=403, message=self.message)

        return True

    def get_papel_minimo(self, view):
        papeis_por_action = getattr(view, "papeis_por_action", {})
        action = getattr(view, "action", None)
        return papeis_por_action.get(action, getattr(view, "papel_minimo", Papel.VISUALIZADOR))
