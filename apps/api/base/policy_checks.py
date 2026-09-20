"""Validate routed resource policies without querying the permissions table."""

import re

from django.apps import apps
from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLResolver, get_resolver

from rest_framework.permissions import AllowAny

from apps.api.base.permissions import ModelPermissionMixin
from apps.api.base.resource_policies import ResourcePolicy
from apps.api.core.route_markers import MARCADOR_PUBLICA, tem_marcador

_SCOPE_COMPONENT = re.compile(r"[a-z][a-z0-9_]*\Z")


def routed_model_viewsets():
    """Load every included URLconf and collect actual DRF action mappings.

    No subclass registry: the URL resolver is the source of exposed views,
    including method mappings on custom actions and manually mounted views.
    """
    result = {}

    def visit(patterns):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                visit(pattern.url_patterns)
                continue
            callback = pattern.callback
            view = getattr(callback, "cls", None)
            if view is not None and issubclass(view, ModelPermissionMixin):
                actions = result.setdefault(view, set())
                allowed_methods = getattr(callback, "initkwargs", {}).get("http_method_names", view.http_method_names)
                actions.update(name for method, name in getattr(callback, "actions", {}).items() if method in allowed_methods)

    visit(get_resolver().url_patterns)
    return result


def declared_permissions():
    """Django generates default and custom codenames from model metadata."""
    result = set()
    for model in apps.get_models():
        options = model._meta
        result.update(f"{options.app_label}.{action}_{options.model_name}" for action in options.default_permissions)
        result.update(f"{options.app_label}.{codename}" for codename, _ in options.permissions)
    return result


def validate_resource_policy(view, actions, permissions):
    if tem_marcador(view, MARCADOR_PUBLICA):
        raise ImproperlyConfigured("View pública não pode usar ModelPermissionMixin.")
    policy = view.authorization_policy
    if not isinstance(policy.resource, str) or not _SCOPE_COMPONENT.fullmatch(policy.resource):
        raise ImproperlyConfigured("Nome de recurso inválido.")
    policy.get_model(view)
    unknown = set(policy.custom_actions) - actions
    if unknown:
        raise ImproperlyConfigured(f"Actions configuradas mas não expostas: {sorted(unknown)}.")
    rules = {}
    for action_name in sorted(actions):
        handler = getattr(view, action_name, None)
        if not callable(handler):
            raise ImproperlyConfigured(f"Action {action_name} precisa ter handler callable.")
        if tem_marcador(handler, MARCADOR_PUBLICA):
            raise ImproperlyConfigured("Action pública não pode usar ModelPermissionMixin.")
        rule = policy.resolve(view, action_name)
        if not isinstance(rule.action, str) or not _SCOPE_COMPONENT.fullmatch(rule.action):
            raise ImproperlyConfigured(f"Scope inválido: {rule.scope}.")
        if rule.permission not in permissions:
            raise ImproperlyConfigured(f"Codename Django inexistente: {rule.permission}.")
        if rule.scope in rules and rule != rules[rule.scope]:
            raise ImproperlyConfigured(f"Scope duplicado com regras divergentes: {rule.scope}.")
        rules[rule.scope] = rule
        handler = getattr(view, action_name, None)
        extra = getattr(handler, "kwargs", {}).get("permission_classes", ())
        if AllowAny in [*view.permission_classes, *extra]:
            raise ImproperlyConfigured("AllowAny não é permitido em ModelPermissionMixin.")
    unknown_forbidden = policy.api_key_forbidden_actions - {rule.action for rule in rules.values()}
    if unknown_forbidden:
        raise ImproperlyConfigured(f"Actions proibidas inexistentes: {sorted(unknown_forbidden)}.")
    if policy.api_key_enabled and not any(rule.api_key_allowed for rule in rules.values()):
        raise ImproperlyConfigured("Wildcard sem actions disponíveis; use api_key_enabled=False.")
    return tuple(rules.values())


@register(Tags.security)
def check_resource_policies(app_configs, **kwargs):
    """Fail startup for missing, incomplete, conflicting or unknown rules."""
    errors = []
    resources = {}
    permissions = declared_permissions()
    for view, actions in routed_model_viewsets().items():
        policy = getattr(view, "authorization_policy", None)
        if not isinstance(policy, ResourcePolicy):
            errors.append(Error("ViewSet concreto sem ResourcePolicy.", obj=view, id="base.E001"))
            continue
        try:
            validate_resource_policy(view, actions, permissions)
        except ImproperlyConfigured as exc:
            errors.append(Error(str(exc), obj=view, id="base.E002"))
            continue
        if policy.resource in resources:
            errors.append(Error(f"Recurso duplicado: {policy.resource}.", obj=view, id="base.E003"))
        resources[policy.resource] = view
    return errors
