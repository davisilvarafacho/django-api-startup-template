"""Registry `resource:action`: a linguagem pública de scopes e permissions.

Todo model de negócio pode declarar `api_scope_resource = "recurso"`; o
registry traduz esse contrato estável (`resource:action`) para os codenames
internos do Django (`app_label.action_model`), usados por scopes de API key e
por `user.has_perm()`. Ver a spec normativa em
`.ai/brainstorming/spec/2026-07-28-auth-tokens-api-keys-design.md`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum

from django.apps import apps as django_apps
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.db import models

SCOPE_TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ScopeAction(str, Enum):  # noqa: UP042 -- StrEnum exige Python 3.11+; o projeto suporta 3.10.
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


# Prefixo de codename que o Django cria automaticamente para cada model.
CRUD_DJANGO_PREFIXES = {
    ScopeAction.READ: "view",
    ScopeAction.CREATE: "add",
    ScopeAction.UPDATE: "change",
    ScopeAction.DELETE: "delete",
}


@dataclass(frozen=True)
class ScopeDefinition:
    resource: str
    model: type[models.Model] | None
    action_permissions: Mapping[str, str] = dataclass_field(default_factory=dict)
    custom_actions: frozenset = frozenset()


def parse_scope(value):
    """Valida e decompõe um scope em `(resource, action)`.

    `*` sozinho é o wildcard global; `resource:*` é o wildcard do recurso.
    """
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

    def register(self, resource, *, model=None, custom_actions=None):
        if not SCOPE_TOKEN_PATTERN.match(resource):
            raise ImproperlyConfigured(f"Nome de recurso de scope inválido: '{resource}'.")

        if resource in self._resources:
            raise ImproperlyConfigured(f"Recurso de scope duplicado: '{resource}'.")

        action_permissions = {}
        if model is not None:
            model_name = model._meta.model_name
            action_permissions = {action.value: f"{prefix}_{model_name}" for action, prefix in CRUD_DJANGO_PREFIXES.items()}

        custom_actions = custom_actions or {}
        if not isinstance(custom_actions, Mapping):
            raise ImproperlyConfigured(f"Actions customizadas de '{resource}' devem mapear action para codename Django.")

        for action, codename in custom_actions.items():
            if not SCOPE_TOKEN_PATTERN.match(action):
                raise ImproperlyConfigured(f"Action de scope inválida: '{action}'.")
            if not SCOPE_TOKEN_PATTERN.match(codename):
                raise ImproperlyConfigured(f"Codename Django inválido: '{codename}'.")
            if action in action_permissions:
                raise ImproperlyConfigured(f"Action de scope duplicada para '{resource}': '{action}'.")
            action_permissions[action] = codename

        definition = ScopeDefinition(
            resource=resource,
            model=model,
            action_permissions=action_permissions,
            custom_actions=frozenset(custom_actions),
        )
        self._resources[resource] = definition
        return definition

    def lookup(self, resource):
        return self._resources.get(resource)

    def display_permissions_for(self, user):
        """Traduz as permissions Django concedidas a `user` para `resource:action`.

        Útil para exibir, na linguagem pública, o que um usuário pode delegar
        a uma API key (ver `apps.api.autenticacao.scope_delegation`).
        """
        scopes = []
        for resource, definition in self._resources.items():
            if definition.model is None:
                continue

            app_label = definition.model._meta.app_label
            for action, codename in definition.action_permissions.items():
                if user.has_perm(f"{app_label}.{codename}"):
                    scopes.append(f"{resource}:{action}")

        return scopes

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


scope_registry = ScopeRegistry()


def discover_scope_resources(registry=None, *, force=False):
    """Registra todo model concreto com `api_scope_resource` declarado (!= None)."""
    registry = registry or scope_registry

    if registry.discovered and not force:
        return registry

    if force:
        registry.reset()

    for model in django_apps.get_models():
        resource = getattr(model, "api_scope_resource", None)
        if resource is None:
            continue
        registry.register(
            resource,
            model=model,
            custom_actions=getattr(model, "api_scope_custom_actions", None),
        )

    registry.discovered = True
    return registry


@register()
def check_scope_registry(app_configs, **kwargs):
    return scope_registry.check()


def required_django_permissions(scope):
    """Traduz um scope concreto (`resource:action` ou `*`) para `app_label.codename`."""
    resource, action = parse_scope(scope)

    definitions = scope_registry.all_resources().values() if resource == "*" else [_get_definition(resource)]

    permissions = []
    for definition in definitions:
        if definition.model is None:
            continue

        app_label = definition.model._meta.app_label
        codenames = _codenames_for_action(definition, action)
        permissions.extend(f"{app_label}.{codename}" for codename in codenames)

    return permissions


def validate_registered_scope(scope):
    """Valida que um scope usa recurso e action existentes no registry."""
    resource, action = parse_scope(scope)
    if resource == "*":
        return

    definition = _get_definition(resource)
    _codenames_for_action(definition, action)


def _get_definition(resource):
    definition = scope_registry.lookup(resource)
    if definition is None:
        raise ImproperlyConfigured(f"Recurso de scope desconhecido: '{resource}'.")
    return definition


def _codenames_for_action(definition, action):
    if action == "*":
        return list(definition.action_permissions.values())

    codename = definition.action_permissions.get(action)
    if codename is None and action not in definition.custom_actions:
        raise ImproperlyConfigured(f"Action '{action}' não é válida para o recurso '{definition.resource}'.")

    return [codename] if codename else []
