"""Autenticação por token resolvida no nível do middleware.

Por que aqui e não no DRF: a autenticação padrão do DRF só roda dentro do
`dispatch()` da view, ou seja, **depois** de toda a cadeia de middlewares.
Qualquer middleware que precise do usuário (auditlog, PostHog, tenancy/RLS)
enxergaria `AnonymousUser`. Resolvendo aqui, todos os middlewares internos já
recebem `request.user` preenchido, e o DRF apenas reaproveita o resultado via
`apps.api.autenticacao.authentications.PassthroughAuthentication`.
"""

import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed

from apps.api.core.route_markers import MARCADOR_PUBLICA, tem_marcador, view_do_path
from apps.api.core.routes_registry import routes_registry

from .authentications import PreAuthTokenAuthentication, QueryParamTokenAuthentication, TypedTokenAuthentication
from .constants import REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE, RESOLVED_PUBLIC
from .errors import AuthErrorCode
from .models import TokenType

logger = logging.getLogger(__name__)


# Liberadas apenas quando DEBUG=True — em produção continuam exigindo token.
DEBUG_PREFIXES = (
    "/media/",
    "/static/",
    "/__debug__/",
    "/silk/",
    "/hijack/",
    "/api/docs/",
    "/api/schema/",
    "/sentry-error/",
)


class AuthenticationMiddleware:
    # Ordem importa: a primeira que devolver um usuário vence.
    authentication_classes = (TypedTokenAuthentication, QueryParamTokenAuthentication)

    def __init__(self, get_response):
        self.get_response = get_response
        self.authenticators = [klass() for klass in self.authentication_classes]

    def __call__(self, request):
        path = request.path_info

        if settings.DEBUG and self.is_debug_route(path):
            logger.debug("Rota de debug acessada: %s", path)
            self.mark_public(request)
            return self.get_response(request)

        if self.is_public_route(path):
            logger.debug("Rota pública acessada: %s", path)
            self.mark_public(request)
            return self.get_response(request)

        error_response = self.authenticate(request)
        if error_response is not None:
            return error_response

        return self.get_response(request)

    def authenticate(self, request):
        """Resolve o usuário ou devolve a resposta de erro que encerra a request."""
        try:
            result = self.run_authenticators(request)
        except AuthenticationFailed as exc:
            return JsonResponse(
                {"code": AuthErrorCode.INVALID_TOKEN.value, "message": str(exc.detail)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if result is None:
            return JsonResponse(
                {"code": AuthErrorCode.INVALID_TOKEN.value, "message": "Token não fornecido."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user, auth_token = result

        if getattr(auth_token, "type", None) == TokenType.PRE_AUTH and not request.path_info.startswith("/auth/mfa/challenge/"):
            return JsonResponse(
                {"code": AuthErrorCode.INVALID_TOKEN.value, "message": "Pré-autenticação não permite acesso a esta rota."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Não usamos `set_current_user`: ele grava num global da thread que nunca é
        # limpo, e `get_current_user()` já resolve `request.user` a partir da request
        # que o ThreadLocalMiddleware guarda por requisição.
        request.user = user
        request.auth = auth_token
        setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE)

        return None

    def run_authenticators(self, request):
        authenticators = self.authenticators
        if request.META.get("HTTP_AUTHORIZATION", "").startswith("PreAuth "):
            authenticators = [PreAuthTokenAuthentication()]
        for authenticator in authenticators:
            result = authenticator.authenticate(request)
            if result is not None:
                return result

        return None

    @staticmethod
    def mark_public(request):
        setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PUBLIC)

    @staticmethod
    def is_debug_route(path):
        return path.startswith(DEBUG_PREFIXES)

    @staticmethod
    def is_public_route(path):
        """Rota pública por prefixo registrado ou por `@public` na view."""
        if routes_registry.matches(path):
            return True

        return tem_marcador(view_do_path(path), MARCADOR_PUBLICA)


class UpdateTokenLastUsedMiddleware:
    """Atualiza o campo last_used toda vez que o token é usado"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "auth") and request.auth:
            if hasattr(request.auth, "metadata"):
                request.auth.metadata.last_used = timezone.now()
                request.auth.metadata.save(update_fields=["last_used"])

        return self.get_response(request)
