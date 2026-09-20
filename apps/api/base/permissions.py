"""Mandatory, additive authorization for model ViewSets."""

from django.core.exceptions import ImproperlyConfigured

from rest_framework.exceptions import MethodNotAllowed
from rest_framework.permissions import BasePermission, IsAuthenticated

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission
from apps.api.base.resource_policies import ResourcePolicy
from apps.api.core.errors import APIError
from apps.api.core.scope_registry import matches_scope
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.permissions import TenantPermission


class ResourceAccessPermission(BasePermission):
    """Resolve the DRF action before choosing human or API key authorization."""

    def has_permission(self, request, view):
        # Only policy-bound views participate in discovery and additive authorization.
        if not isinstance(view, ModelPermissionMixin):
            return False
        # WSGI/ASGI servers may skip system checks; callbacks must still fail closed.
        if {"authorization_policy", "queryset"}.intersection(vars(view)):
            return False
        policy = getattr(view, "authorization_policy", None)
        if not isinstance(policy, ResourcePolicy):
            return False
        method = getattr(request, "method", "")
        if method == "OPTIONS":
            # DRF calls OPTIONS "metadata"; it must not borrow the data action.
            return False
        if getattr(view, "action", None) is None and method.lower() not in getattr(view, "action_map", {}):
            raise MethodNotAllowed(method)
        try:
            rule = policy.resolve(view, getattr(view, "action", None))
        except ImproperlyConfigured:
            return False
        token = getattr(request, "auth", None)
        if getattr(token, "type", None) == TokenType.API_KEY:
            if not rule.api_key_allowed:
                return False
            # Middleware authenticates the key and prepares its own tenant.
            tenant = getattr(request, "tenant", None)
            if tenant is None or tenant.organization_id != getattr(token, "organization_id", None):
                return False
            try:
                granted = getattr(token, "scopes", ()) or ()
                authorized = isinstance(granted, (list, tuple, set, frozenset)) and any(matches_scope(scope, rule.scope) for scope in granted)
            except (TypeError, ValueError):
                authorized = False
            if not authorized:
                raise APIError(AuthErrorCode.INSUFFICIENT_SCOPE, status_code=403)
            return True
        if not request.user or not request.user.is_authenticated or not request.user.has_perm(rule.permission):
            return False
        if rule.minimum_role is None:
            # A missing role never implicitly makes a tenant-bound action free.
            return getattr(request, "tenant_required", True) is False
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        if not tenant.has_minimum_role(rule.minimum_role):
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=403)
        return True


class ModelPermissionMixin:
    """Always enforce authentication, tenant and policy, then additional rules.

    Class and ``@action`` permission classes are additive; a repeated class is
    instantiated once, in mandatory/class/action order. Self-service and public
    endpoints must use a base without this mixin.
    """

    authorization_policy = None
    permission_classes = [RecentAuthenticationPermission]

    def get_permissions(self):
        mandatory = [IsAuthenticated, TenantPermission, ResourceAccessPermission]
        declared = list(type(self).permission_classes)
        additional = list(self.permission_classes)
        classes = dict.fromkeys([*mandatory, *declared, *additional])
        return [permission_class() for permission_class in classes]
