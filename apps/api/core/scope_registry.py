"""The single scope registry, derived from ResourcePolicy and routed handlers."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum

from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.db import models

SCOPE_TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ScopeAction(str, Enum):  # noqa: UP042 -- StrEnum exige Python 3.11+; o projeto suporta 3.10.
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class ScopeDefinition:
    resource: str
    model: type[models.Model] | None
    action_permissions: Mapping[str, str] = dataclass_field(default_factory=dict)
    unavailable_actions: frozenset = frozenset()


def parse_scope(value):
    """Valida e decompõe um scope em `(resource, action)`.

    `*` sozinho é o wildcard global; `resource:*` é o wildcard do recurso.
    """
    if not isinstance(value, str):
        raise ValueError("Scope precisa ser uma string.")
    if value == "*":
        return ("*", "*")
    resource, sep, action = value.partition(":")
    if not sep:
        raise ValueError(f"Scope '{value}' precisa seguir o formato 'resource:action' ou '*'.")
    if resource != "*" and not SCOPE_TOKEN_PATTERN.match(resource):
        raise ValueError(f"Recurso de scope inválido: '{resource}'.")
    if action != "*" and not SCOPE_TOKEN_PATTERN.match(action):
        raise ValueError(f"Action de scope inválida: '{action}'.")
    if resource == "*":
        raise ValueError("O wildcard global deve ser usado sozinho como '*'.")
    return (resource, action)


def matches_scope(granted, required):
    """`required` (sempre concreto) é satisfeito pelo scope `granted` (pode ter wildcard)?"""
    granted_resource, granted_action = parse_scope(granted)
    required_resource, required_action = parse_scope(required)
    if granted_resource == "*":
        return True
    if granted_resource != required_resource:
        return False
    return granted_action == "*" or granted_action == required_action


class ScopeRegistry:
    """Registro central dos recursos `resource:action` da aplicação."""

    def __init__(self):
        self._resources: dict[str, ScopeDefinition] = {}
        self.discovered = False

    def register(self, view, actions):
        """Register only operations resolved from exposed handlers and their policy."""
        from apps.api.base.policy_checks import declared_permissions, validate_resource_policy
        from apps.api.base.resource_policies import ResourcePolicy

        policy = getattr(view, "authorization_policy", None)
        if not isinstance(policy, ResourcePolicy):
            raise ImproperlyConfigured("ViewSet concreto sem ResourcePolicy.")
        rules = validate_resource_policy(view, set(actions), declared_permissions())
        if policy.resource in self._resources:
            raise ImproperlyConfigured(f"Recurso de scope duplicado: '{policy.resource}'.")
        definition = ScopeDefinition(
            resource=policy.resource,
            model=policy.get_model(view),
            action_permissions={rule.action: rule.permission for rule in rules},
            unavailable_actions=frozenset(rule.action for rule in rules if not rule.api_key_allowed),
        )
        self._resources[policy.resource] = definition
        return definition

    def expand(self, scope):
        """Expand a wildcard into available scopes; reject unavailable concrete actions."""
        resource, action = parse_scope(scope)
        definitions = self._resources.values() if resource == "*" else [self.lookup(resource)]
        scopes = []
        for definition in definitions:
            if definition is None:
                raise ImproperlyConfigured(f"Recurso de scope desconhecido: '{resource}'.")
            if action != "*" and action not in definition.action_permissions:
                raise ImproperlyConfigured(f"Action '{action}' não é válida para o recurso '{resource}'.")
            available = sorted(set(definition.action_permissions) - definition.unavailable_actions)
            if action != "*":
                if action not in available:
                    raise ScopeNotAvailable(f"Scope '{scope}' indisponível para API key.")
                available = [action]
            scopes.extend(f"{definition.resource}:{operation}" for operation in available)
        if not scopes:
            raise ScopeNotAvailable(f"Scope '{scope}' sem actions disponíveis para API key.")
        return sorted(scopes)

    def lookup(self, resource):
        return self._resources.get(resource)

    def display_permissions_for(self, user):
        """Traduz as permissions Django concedidas a `user` para `resource:action`.

        Útil para exibir, na linguagem pública, o que um usuário pode delegar
        a uma API key (ver `apps.api.autenticacao.scope_delegation`).
        """
        return [
            f"{resource}:{action}"
            for resource, definition in self._resources.items()
            for action, permission in definition.action_permissions.items()
            if action not in definition.unavailable_actions and user.has_perm(permission)
        ]

    def all_resources(self):
        return dict(self._resources)

    def reset(self):
        self._resources.clear()
        self.discovered = False

    def check(self):
        errors = []
        for resource in self._resources:
            if not SCOPE_TOKEN_PATTERN.match(resource):
                errors.append(
                    Error(
                        f"Recurso de scope '{resource}' não segue o formato esperado.",
                        id="api.scopes.E001",
                    )
                )
        return errors


class ScopeNotAvailable(ValueError):
    """A known operation is unavailable to API keys."""


scope_registry = ScopeRegistry()


def discover_scope_resources(registry=None, *, force=False):
    """Load URL patterns first, then atomically publish their validated policies."""
    from apps.api.base.policy_checks import routed_model_viewsets

    registry = registry if registry is not None else scope_registry
    if registry.discovered and not force:
        return registry
    discovered = ScopeRegistry()
    try:
        for view, actions in routed_model_viewsets().items():
            discovered.register(view, actions)
    except ImproperlyConfigured:
        registry.reset()
        raise
    registry._resources = discovered._resources
    registry.discovered = True
    return registry


@register()
def check_scope_registry(app_configs, **kwargs):
    try:
        discover_scope_resources(force=True)
    except ImproperlyConfigured as exc:
        return [Error(str(exc), id="api.scopes.E001")]
    return scope_registry.check()


def required_django_permissions(scope):
    """Translate available concrete scopes or wildcards into Django permissions."""
    registry = discover_scope_resources()
    return [registry.lookup(resource).action_permissions[action] for resource, action in (parse_scope(value) for value in registry.expand(scope))]


def validate_registered_scope(scope):
    """Validate known exposed actions, including session-only actions on old keys.

    Issuance additionally checks availability in validate_scope_delegation. Keeping
    structural validation separate lets existing keys be revoked or suspended.
    """
    resource, action = parse_scope(scope)
    if resource == "*":
        return
    definition = discover_scope_resources().lookup(resource)
    if definition is None:
        raise ImproperlyConfigured(f"Recurso de scope desconhecido: '{resource}'.")
    if action != "*" and action not in definition.action_permissions:
        raise ImproperlyConfigured(f"Action '{action}' não é válida para o recurso '{resource}'.")
