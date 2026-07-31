"""Middleware de tenancy.

Responsabilidades: ler o header `X-Organization` e abrir a transação que
envolve a request.

O contexto RLS em si **não** é aplicado aqui, e sim depois da autenticação do
DRF (ver `apps.organizacoes.permissions.TenantPermission`): com autenticação por
token, `request.user` só é resolvido no dispatch da view, então validar o
vínculo neste ponto encontraria sempre um usuário anônimo. A transação aberta
aqui é o que permite usar `SET LOCAL` mais adiante.
"""

from django.db import transaction

from django_rls.context import clear_rls_context

from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError, error_response_for_api_error
from apps.organizacoes.constants import HEADER_ORGANIZACAO, META_HEADER_ORGANIZACAO
from apps.organizacoes.context import CHAVE_TENANT
from apps.organizacoes.errors import OrganizationErrorCode


def resolve_token_organization(request, token):
    """Resolve o slug de tenant a partir do header e/ou do token.

    Sessões humanas continuam usando só o header. Uma API key sempre deriva o
    tenant da própria credencial; um header divergente é rejeitado — a key
    nunca opera em outra organização.
    """
    header_slug = request.META.get(META_HEADER_ORGANIZACAO) or None

    token_type = getattr(token, "type", None)
    if token_type != TokenType.API_KEY:
        return header_slug

    organizacao = getattr(token, "organization", None)
    if organizacao is None:
        return header_slug

    if header_slug and header_slug != organizacao.slug:
        raise APIError(
            OrganizationErrorCode.TENANT_MISMATCH,
            status_code=409,
            field=HEADER_ORGANIZACAO,
        )

    return organizacao.slug


class OrganizacaoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            request.organizacao_slug = resolve_token_organization(request, getattr(request, "auth", None))
        except APIError as exc:
            return error_response_for_api_error(exc)

        request.tenant = None

        with transaction.atomic():
            try:
                return self.get_response(request)
            finally:
                # O `SET LOCAL` morre no commit, mas o rastreio em memória da lib
                # não. Sem limpar, a próxima request na mesma thread herdaria a
                # crença de que já existe contexto — e passaria pelo guard sem
                # ter tenant algum aplicado no banco.
                clear_rls_context({CHAVE_TENANT})
