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

from apps.organizacoes.constants import META_HEADER_ORGANIZACAO


class OrganizacaoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organizacao_slug = request.META.get(META_HEADER_ORGANIZACAO) or None
        request.organizacao = None
        request.vinculo = None

        with transaction.atomic():
            return self.get_response(request)
