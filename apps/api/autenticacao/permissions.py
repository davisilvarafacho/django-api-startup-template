from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from rest_framework import exceptions, status
from rest_framework.permissions import BasePermission, DjangoModelPermissions, IsAdminUser

from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .models import TokenType


class TokenScopePermission(BasePermission):
    """Aplica escopos declarados na view apenas para tokens do tipo API key."""

    message = "Token sem escopo suficiente para este endpoint."
    view_attribute = "required_token_scopes"

    def has_permission(self, request, view):
        required_scopes = self.get_required_scopes(request, view)
        if not required_scopes:
            return True

        auth_token = getattr(request, "auth", None)
        token_type = getattr(auth_token, "type", TokenType.TOKEN)

        if token_type != TokenType.API_KEY:
            return True

        granted_scopes = set(getattr(auth_token, "scopes", []) or [])

        return "*" in granted_scopes or set(required_scopes).issubset(granted_scopes)

    def get_required_scopes(self, request, view):
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


class RecentAuthenticationPermission(BasePermission):
    """Exige uma sessão Knox com confirmação de senha ainda válida."""

    message = "Reautenticação recente obrigatória."

    def has_permission(self, request, view):
        requirement = self.get_requirement(request, view)
        if requirement is None:
            return True

        auth_token = getattr(request, "auth", None)
        if auth_token is None or auth_token.type != TokenType.TOKEN:
            self.raise_reauthentication_required()

        metadata = getattr(auth_token, "metadata", None)
        reauthenticated_at = getattr(metadata, "reauthenticated_at", None)
        if reauthenticated_at is None:
            self.raise_reauthentication_required()

        max_age = requirement["max_age"]
        if reauthenticated_at < timezone.now() - timedelta(seconds=max_age):
            self.raise_reauthentication_required()

        return True

    @staticmethod
    def get_requirement(request, view):
        action_name = getattr(view, "action", None)
        action = getattr(view, action_name, None) if action_name else None
        method_name = getattr(request, "method", "").lower()
        method_handler = getattr(view, method_name, None) if method_name else None

        for target in (action, method_handler, getattr(view, "handler", None), view):
            requirement = getattr(target, "_recent_auth_required", None)
            if requirement is not None:
                return requirement

        return None

    def raise_reauthentication_required(self):
        """Interrompe a request marcada com o envelope de step-up padronizado."""
        raise APIError(
            AuthErrorCode.REAUTHENTICATION_REQUIRED,
            status_code=status.HTTP_403_FORBIDDEN,
            message=self.message,
        )


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

        queryset = self._queryset(view)
        perms = self.get_required_permissions(request, queryset.model)

        return request.user.has_perms(perms)


class IsSuperUser(IsAdminUser):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and request.user.is_superuser)
