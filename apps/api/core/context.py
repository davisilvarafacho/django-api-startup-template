"""Contexto de execução específico da API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from internal_frameworks.context import ContextVariable

if TYPE_CHECKING:
    from django.http import HttpRequest  # noqa: F401

    from apps.api.autenticacao.models import AuthToken  # noqa: F401
    from apps.usuarios.models import Usuario  # noqa: F401

request_atual = ContextVariable["HttpRequest"].from_var("request_atual")
usuario_atual = ContextVariable["Usuario"].from_var("usuario_atual")
token_atual = ContextVariable["AuthToken"].from_var("token_atual")


class RequestContextMiddleware:
    """Publica a request e descarta o contexto ao encerrar seu ciclo."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_atual.set(request)
        try:
            return self.get_response(request)
        finally:
            ContextVariable.clear_context()
