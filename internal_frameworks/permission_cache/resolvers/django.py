from django.contrib.auth.models import Permission
from django.db import DEFAULT_DB_ALIAS

from cachalot.api import cachalot_disabled

from internal_frameworks.permission_cache.keys import global_scope, layer_scope, user_scope
from internal_frameworks.permission_cache.store import PermissionCacheStore
from internal_frameworks.permission_cache.types import DjangoPermissionSnapshot


class DjangoPermissionResolver:
    def __init__(self, store=None):
        self.store = store or PermissionCacheStore()

    def resolve(self, user_obj) -> DjangoPermissionSnapshot:
        # `is_deleted` é checado junto de `is_active`: uma conta excluída antes
        # desta correção continuou ativa, e nem ela pode carregar snapshot.
        if user_obj.is_anonymous or not user_obj.is_active or getattr(user_obj, "is_deleted", False) or user_obj.pk is None:
            return DjangoPermissionSnapshot(frozenset(), frozenset())
        database_alias = user_obj._state.db or DEFAULT_DB_ALIAS
        return self.store.resolve(
            layer="django",
            database_alias=database_alias,
            identity=("user", user_obj.pk),
            scopes=(global_scope(), layer_scope("django"), user_scope("django", user_obj.pk)),
            loader=lambda: self._load(user_obj, database_alias),
            encode=self._encode,
            decode=self._decode,
        )

    def _load(self, user_obj, database_alias) -> DjangoPermissionSnapshot:
        with cachalot_disabled(all_queries=True):
            if user_obj.is_superuser:
                permissions = Permission.objects.using(database_alias).values_list("content_type__app_label", "codename").order_by()
                all_permissions = self._permission_names(permissions)
                return DjangoPermissionSnapshot(all_permissions, all_permissions)

            user_permissions = user_obj.user_permissions.using(database_alias).values_list("content_type__app_label", "codename").order_by()
            group_permissions = (
                Permission.objects.using(database_alias).filter(group__user=user_obj).values_list("content_type__app_label", "codename").order_by()
            )
            return DjangoPermissionSnapshot(
                self._permission_names(user_permissions),
                self._permission_names(group_permissions),
            )

    @staticmethod
    def _permission_names(permissions) -> frozenset[str]:
        return frozenset(f"{app_label}.{codename}" for app_label, codename in permissions)

    @staticmethod
    def _encode(snapshot: DjangoPermissionSnapshot) -> dict[str, object]:
        return {
            "user_permissions": sorted(snapshot.user_permissions),
            "group_permissions": sorted(snapshot.group_permissions),
        }

    @staticmethod
    def _decode(payload: dict[str, object] | None) -> DjangoPermissionSnapshot:
        if payload is None:
            return DjangoPermissionSnapshot(frozenset(), frozenset())
        return DjangoPermissionSnapshot(
            DjangoPermissionResolver._decode_permissions(payload["user_permissions"]),
            DjangoPermissionResolver._decode_permissions(payload["group_permissions"]),
        )

    @staticmethod
    def _decode_permissions(value: object) -> frozenset[str]:
        if not isinstance(value, list) or not all(DjangoPermissionResolver._is_permission_name(permission) for permission in value):
            raise TypeError("Lista de permissões inválida no cache de autorização.")
        return frozenset(value)

    @staticmethod
    def _is_permission_name(permission: object) -> bool:
        if not isinstance(permission, str):
            return False
        parts = permission.split(".")
        return len(parts) == 2 and all(parts)
