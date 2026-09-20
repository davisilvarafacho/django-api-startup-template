from rest_framework.permissions import BasePermission, IsAdminUser

from apps.api.core.errors import APIError
from apps.api.core.scope_registry import matches_scope

from .errors import AuthErrorCode
from .models import TokenType


class TokenScopePermission(BasePermission):
    """Aplica escopos declarados na view apenas para tokens do tipo API key.

    Para API keys, o scope é a **única** autorização: sem scope exigido pela
    view a credencial é recusada (fail-closed), já que
    permissions e papel pessoais não autorizam esse tipo de token.
    `view.session_only = True`
    recusa API keys de saída, antes de qualquer avaliação de scope.
    """

    message = "Token sem escopo suficiente para este endpoint."
    view_attribute = "required_token_scopes"

    def has_permission(self, request, view):
        auth_token = getattr(request, "auth", None)
        token_type = getattr(auth_token, "type", TokenType.TOKEN)

        if token_type != TokenType.API_KEY:
            return True

        session_only_actions = getattr(view, "session_only_actions", ())
        if getattr(view, "session_only", False) or getattr(view, "action", None) in session_only_actions:
            return False

        required_scopes = self.get_required_scopes(request, view)
        if not required_scopes:
            raise APIError(AuthErrorCode.INSUFFICIENT_SCOPE, status_code=403)

        granted_scopes = set(getattr(auth_token, "scopes", []) or [])

        try:
            authorized = all(any(matches_scope(granted, required) for granted in granted_scopes) for required in required_scopes)
        except (TypeError, ValueError):
            authorized = False

        if not authorized:
            raise APIError(AuthErrorCode.INSUFFICIENT_SCOPE, status_code=403)

        return True

    def get_required_scopes(self, request, view):
        get_required_token_scopes = getattr(view, "get_required_token_scopes", None)
        if callable(get_required_token_scopes):
            scopes = get_required_token_scopes()
            if scopes:
                return list(scopes)

        # Compatibilidade com o atributo estático legado (dict por action/method,
        # lista ou string única).
        scopes = getattr(view, self.view_attribute, None)
        if not scopes:
            return []

        if isinstance(scopes, str):
            return [scopes]

        if isinstance(scopes, dict):
            action = getattr(view, "action", None)
            if action and action in scopes:
                return self.normalize_scopes(scopes[action])

            method = getattr(request, "method", "").upper()
            return self.normalize_scopes(scopes.get(method, []))

        return self.normalize_scopes(scopes)

    @staticmethod
    def normalize_scopes(scopes):
        if not scopes:
            return []
        if isinstance(scopes, str):
            return [scopes]
        return list(scopes)


class IsSuperUser(IsAdminUser):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and not getattr(request.user, "is_deleted", False) and request.user.is_superuser)


class APIKeyPermissions(BasePermission):
    """Permissions humanas explícitas de `/auth/api_keys/`.

    Não usa os codenames default do model (`view_authtoken`/...): ser criador
    ou responsável de uma key não concede autoridade administrativa sobre
    ela, então os codenames são os customizados em `AuthToken.Meta.permissions`
    (`view_apikey`, `add_apikey`, `change_apikey`, `delete_apikey`,
    `rotate_apikey`).
    """

    perms_map = {
        "GET": ["autenticacao.view_apikey"],
        "POST": ["autenticacao.add_apikey"],
        "PUT": ["autenticacao.change_apikey"],
        "PATCH": ["autenticacao.change_apikey"],
        "DELETE": ["autenticacao.delete_apikey"],
    }
    action_perms_map = {
        "suspend": ["autenticacao.change_apikey"],
        "resume": ["autenticacao.change_apikey"],
        "rotate": ["autenticacao.rotate_apikey"],
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        action = getattr(view, "action", None)
        if action in self.action_perms_map:
            required = self.action_perms_map[action]
        elif request.method in self.perms_map:
            required = self.perms_map[request.method]
        else:
            return False

        return request.user.has_perms(required)
