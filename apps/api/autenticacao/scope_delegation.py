"""Delegate only policy scopes available to integrations and authorized by the actor."""

from django.core.exceptions import ImproperlyConfigured

from apps.api.core.errors import APIError
from apps.api.core.scope_registry import ScopeNotAvailable, discover_scope_resources, required_django_permissions

from .errors import AuthErrorCode

GLOBAL_WILDCARD_PERMISSION = "autenticacao.grant_unrestricted_apikey"
BROAD_DELEGATION_PERMISSION = "autenticacao.grant_api_scopes"


def validate_scope_delegation(user, scopes):
    """Return validated scopes, reporting the payload index of each rejected item."""
    if not user.is_active or getattr(user, "is_deleted", False):
        raise APIError(
            AuthErrorCode.SCOPE_NOT_DELEGABLE,
            status_code=403,
            field="scopes",
            path=("scopes", 0) if scopes else ("scopes",),
        )
    validated = []
    for index, scope in enumerate(scopes):
        error = {"field": "scopes", "path": ("scopes", index)}
        try:
            permissions = required_django_permissions(scope)
        except ScopeNotAvailable as exc:
            raise APIError(AuthErrorCode.SCOPE_NOT_AVAILABLE, status_code=422, message=str(exc), **error) from exc
        except (ValueError, ImproperlyConfigured) as exc:
            raise APIError(AuthErrorCode.INVALID_SCOPE, status_code=422, message=str(exc), **error) from exc

        if scope == "*":
            delegable = user.has_perm(GLOBAL_WILDCARD_PERMISSION)
        else:
            delegable = user.has_perm(BROAD_DELEGATION_PERMISSION) or all(user.has_perm(permission) for permission in permissions)
        if not delegable:
            raise APIError(AuthErrorCode.SCOPE_NOT_DELEGABLE, status_code=403, **error)
        validated.append(scope)
    return tuple(validated)


def scope_catalog(user):
    """Expose platform availability separately from the caller's grant authority."""
    registry = discover_scope_resources()
    actions = []
    wildcards = {}
    for resource, definition in sorted(registry.all_resources().items()):
        available_scopes = []
        for action in sorted(definition.action_permissions):
            scope = f"{resource}:{action}"
            available = action not in definition.unavailable_actions
            delegable = False
            reason = "session_only"
            if available:
                available_scopes.append(scope)
                try:
                    validate_scope_delegation(user, [scope])
                except APIError:
                    reason = "missing_permission"
                else:
                    delegable = True
                    reason = None
            actions.append(
                {
                    "resource": resource,
                    "action": action,
                    "scope": scope,
                    "available_for_api_key": available,
                    "delegable_by_current_user": delegable,
                    "reason": reason,
                }
            )
        wildcards[f"{resource}:*"] = available_scopes
    return {"actions": actions, "wildcards": wildcards}
