"""Impede que um usuário conceda a uma API key mais do que ele mesmo pode fazer.

Cada scope delegado precisa corresponder a uma permission Django que o
usuário responsável já possui; o wildcard global (`*`) exige superuser ou a
permission especial `autenticacao.grant_unrestricted_apikey`.
"""
from django.core.exceptions import ImproperlyConfigured

from apps.api.core.errors import APIError
from apps.api.core.scope_registry import parse_scope, required_django_permissions

from .errors import AuthErrorCode

GLOBAL_WILDCARD_PERMISSION = "autenticacao.grant_unrestricted_apikey"


def validate_scope_delegation(user, scopes):
    """Valida `scopes` contra as permissions de `user`; devolve a tupla validada.

    Levanta `APIError(auth.scope_not_delegable)` no primeiro scope que o
    usuário não tem autoridade para conceder.
    """
    validated = []

    for scope in scopes:
        resource, _action = parse_scope(scope)

        if resource == "*":
            if not (user.is_superuser or user.has_perm(GLOBAL_WILDCARD_PERMISSION)):
                raise APIError(
                    AuthErrorCode.SCOPE_NOT_DELEGABLE,
                    status_code=403,
                    field="scopes",
                    message="Você não pode conceder o scope '*'.",
                )
            validated.append(scope)
            continue

        try:
            permissoes_necessarias = required_django_permissions(scope)
        except ImproperlyConfigured as exc:
            raise APIError(
                AuthErrorCode.SCOPE_NOT_DELEGABLE,
                status_code=422,
                field="scopes",
                message=str(exc),
            ) from exc

        if not all(user.has_perm(permissao) for permissao in permissoes_necessarias):
            raise APIError(
                AuthErrorCode.SCOPE_NOT_DELEGABLE,
                status_code=403,
                field="scopes",
                message=f"Você não pode conceder o scope '{scope}'.",
            )

        validated.append(scope)

    return tuple(validated)
