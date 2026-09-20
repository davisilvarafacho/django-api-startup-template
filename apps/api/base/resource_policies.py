"""Immutable authorization rules shared by requests, checks and the scope catalog."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model

from apps.organizacoes.models import Papel

CRUD_ACTIONS = MappingProxyType(
    {
        "list": ("read", "view"),
        "retrieve": ("read", "view"),
        "create": ("create", "add"),
        "update": ("update", "change"),
        "partial_update": ("update", "change"),
        "destroy": ("delete", "delete"),
    }
)


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Map a DRF action to a Django codename and a stable logical operation."""

    action: str
    permission: str
    api_key_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedActionPolicy:
    action: str
    permission: str
    minimum_role: int | None
    api_key_allowed: bool
    scope: str


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Require explicit roles; ``None`` explicitly denotes a tenant-free operation.

    ``model`` is only necessary for views without a static queryset. Custom
    actions require an explicit permission and opt in separately to API keys.
    """

    resource: str
    minimum_roles: Mapping[str, int | None] = field(default_factory=dict)
    custom_actions: Mapping[str, ActionPolicy] = field(default_factory=dict)
    api_key_forbidden_actions: frozenset[str] = field(default_factory=frozenset)
    api_key_enabled: bool = True
    model: type[Model] | None = None

    def __post_init__(self):
        object.__setattr__(self, "minimum_roles", MappingProxyType(dict(self.minimum_roles)))
        object.__setattr__(self, "custom_actions", MappingProxyType(dict(self.custom_actions)))
        object.__setattr__(self, "api_key_forbidden_actions", frozenset(self.api_key_forbidden_actions))

    def get_model(self, view):
        queryset = getattr(view, "queryset", None)
        model = queryset.model if queryset is not None else self.model
        if model is None or not isinstance(model, type) or not issubclass(model, Model):
            raise ImproperlyConfigured("A policy exige queryset.model ou model explícito.")
        if queryset is not None and self.model is not None and self.model is not model:
            raise ImproperlyConfigured("O model explícito diverge de queryset.model.")
        return model

    def resolve(self, view, action_name):
        model = self.get_model(view)
        if action_name in self.custom_actions:
            rule = self.custom_actions[action_name]
            if not isinstance(rule, ActionPolicy):
                raise ImproperlyConfigured(f"ActionPolicy inválida: {action_name}.")
            operation, permission = rule.action, rule.permission
            key_allowed = rule.api_key_allowed
        elif action_name in CRUD_ACTIONS:
            operation, prefix = CRUD_ACTIONS[action_name]
            permission = f"{prefix}_{model._meta.model_name}"
            key_allowed = True
        else:
            raise ImproperlyConfigured(f"Action sem policy: {action_name}.")
        if not permission or not isinstance(permission, str):
            raise ImproperlyConfigured(f"Action sem permission: {action_name}.")
        if not isinstance(self.api_key_enabled, bool) or not isinstance(key_allowed, bool):
            raise ImproperlyConfigured("Disponibilidade para API key deve ser bool.")
        if not isinstance(operation, str) or operation not in self.minimum_roles:
            raise ImproperlyConfigured(f"Action sem papel explícito: {action_name}.")
        minimum_role = self.minimum_roles[operation]
        if minimum_role is not None and (isinstance(minimum_role, bool) or minimum_role not in Papel.values):
            raise ImproperlyConfigured(f"Papel inválido: {minimum_role}.")
        if "." not in permission:
            permission = f"{model._meta.app_label}.{permission}"
        return ResolvedActionPolicy(
            action=operation,
            permission=permission,
            minimum_role=minimum_role,
            api_key_allowed=self.api_key_enabled and key_allowed and operation not in self.api_key_forbidden_actions,
            scope=f"{self.resource}:{operation}",
        )
