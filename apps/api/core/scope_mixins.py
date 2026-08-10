"""Mixin de resolução de scopes para ViewSets (DRF genérico ou `BaseModelViewSet`).

Fica fora de `apps.api.base` porque nem todo ViewSet do projeto herda de
`BaseModelViewSet`/`UtilsViewSetMixin` (ex.: os de `apps.organizacoes`), mas
todos precisam da mesma tradução `resource:action`.
"""

from .scope_registry import ScopeAction

# Mapeamento imutável: action do ViewSet -> action do scope registry.
SCOPE_ACTIONS_BY_VIEWSET_ACTION = {
    "list": ScopeAction.READ,
    "retrieve": ScopeAction.READ,
    "grid": ScopeAction.READ,
    "form": ScopeAction.READ,
    "create": ScopeAction.CREATE,
    "bulk_create": ScopeAction.CREATE,
    "clonar": ScopeAction.CREATE,
    "update": ScopeAction.UPDATE,
    "partial_update": ScopeAction.UPDATE,
    "bulk_update": ScopeAction.UPDATE,
    "ativar": ScopeAction.UPDATE,
    "inativar": ScopeAction.UPDATE,
    "invalidate_cache": ScopeAction.UPDATE,
    "destroy": ScopeAction.DELETE,
}


class ScopeResourceMixin:
    """Deriva `resource:action` da view para `TokenScopePermission`."""

    def get_scope_resource(self):
        """Recurso público (`resource:action`) desta view.

        Usa `scope_resource` quando declarado na própria view (override); caso
        contrário, cai para o default do model consultado pelo `queryset`.
        """
        override = getattr(self, "scope_resource", None)
        if override is not None:
            return override

        queryset = getattr(self, "queryset", None)
        model = queryset.model if queryset is not None else None
        return getattr(model, "api_scope_resource", None) if model is not None else None

    def get_required_token_scopes(self):
        """Scopes exigidos pela action corrente: decorator > CRUD derivado do recurso."""
        action_name = getattr(self, "action", None)
        action_method = getattr(self, action_name, None) if action_name else None
        declared = getattr(action_method, "_required_token_scopes", None)
        if declared is not None:
            return list(declared)

        resource = self.get_scope_resource()
        if resource is None:
            return []

        scope_action = SCOPE_ACTIONS_BY_VIEWSET_ACTION.get(action_name)
        if scope_action is None:
            return []

        return [f"{resource}:{scope_action.value}"]
