from django.core.exceptions import ImproperlyConfigured

from rest_framework import exceptions
from rest_framework.permissions import BasePermission, DjangoModelPermissions, IsAdminUser

from apps.api.core.scope_registry import matches_scope

from .models import TokenType


def require_token_scopes(*scopes):
    """Declara os scopes exigidos por uma action customizada de ViewSet.

    `UtilsViewSetMixin.get_required_token_scopes()` lê esse metadado quando a
    action não tem mapeamento CRUD automático (`resource:read/create/...`).
    """

    def decorator(func):
        func._required_token_scopes = scopes
        return func

    return decorator


class TokenScopePermission(BasePermission):
    """Aplica escopos declarados na view apenas para tokens do tipo API key.

    Para API keys, o scope é a **única** autorização: sem scope exigido pela
    view a credencial é recusada (fail-closed), já que
    `CustomDjangoModelPermissions`/`PapelMinimoPermission` ignoram permissions
    e papel pessoais para esse tipo de token. `view.session_only = True`
    recusa API keys de saída, antes de qualquer avaliação de scope.
    """

    message = "Token sem escopo suficiente para este endpoint."
    view_attribute = "required_token_scopes"

    def has_permission(self, request, view):
        auth_token = getattr(request, "auth", None)
        token_type = getattr(auth_token, "type", TokenType.TOKEN)

        if token_type != TokenType.API_KEY:
            return True

        if getattr(view, "session_only", False):
            return False

        required_scopes = self.get_required_scopes(request, view)
        if not required_scopes:
            return False

        granted_scopes = set(getattr(auth_token, "scopes", []) or [])

        return any(
            matches_scope(granted, required) for required in required_scopes for granted in granted_scopes
        )

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


class CustomDjangoModelPermissions(DjangoModelPermissions):
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
        # default actions
        "grid": ["%(app_label)s.view_%(model_name)s"],
        "form": ["%(app_label)s.view_%(model_name)s"],
        "ativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "inativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "lookup": ["%(app_label)s.add_%(model_name)s"],
        "invalidate_cache": ["%(app_label)s.change_%(model_name)s"],
    }

    def is_action(self, request):
        path = request.path_info.strip("/")

        parts = path.split("/")

        if len(parts) >= 4 and parts[0] == "v1":
            if (len(parts) == 5) or (len(parts) == 4):
                return True

        return False

    def get_action_endpoint(self, request):
        path = request.path_info.strip("/")
        parts = path.split("/")

        if len(parts) >= 4 and parts[0] == "v1":
            if len(parts) == 5:
                return parts[4]

            if len(parts) == 4:
                return parts[3]

        raise ValueError("O endpoint da request atual não possui uma action")

    def get_required_permissions(self, request, model_cls):
        kwargs = {"app_label": model_cls._meta.app_label, "model_name": model_cls._meta.model_name}

        if request.method not in self.perms_map:
            raise exceptions.MethodNotAllowed(request.method)

        if self.is_action(request):
            action = self.get_action_endpoint(request)
            if action not in self.perms_map:
                raise ImproperlyConfigured(f"Não existe permissão configurada para a action '{action}'")

            return [perm % kwargs for perm in self.perms_map[action]]

        return [perm % kwargs for perm in self.perms_map[request.method]]

    def has_permission(self, request, view):
        if not request.user or (not request.user.is_authenticated and self.authenticated_users_only):
            return False

        if getattr(view, "_ignore_model_permissions", False):
            return True

        # Autorização de API key vem só de `TokenScopePermission`: as
        # permissions Django pessoais do responsável não valem para a key.
        auth_token = getattr(request, "auth", None)
        if getattr(auth_token, "type", None) == TokenType.API_KEY:
            return True

        queryset = self._queryset(view)
        perms = self.get_required_permissions(request, queryset.model)

        return request.user.has_perms(perms)


class IsSuperUser(IsAdminUser):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and request.user.is_superuser)
