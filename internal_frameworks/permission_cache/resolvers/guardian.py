from django.contrib.auth.models import Permission
from django.db import DEFAULT_DB_ALIAS
from django.db.models import Model

from cachalot.api import cachalot_disabled
from guardian.core import ObjectPermissionChecker
from guardian.ctypes import get_content_type

from internal_frameworks.permission_cache.keys import global_scope, guardian_object_scope, layer_scope, user_scope
from internal_frameworks.permission_cache.store import PermissionCacheStore
from internal_frameworks.permission_cache.types import GuardianPermissionSnapshot


class GuardianPermissionResolver:
    def __init__(self, store: PermissionCacheStore | None = None):
        self.store = store or PermissionCacheStore()

    def resolve(self, user_obj, obj) -> GuardianPermissionSnapshot:
        if (
            getattr(user_obj, "is_anonymous", True)
            or not getattr(user_obj, "is_active", False)
            or getattr(user_obj, "pk", None) is None
            or not isinstance(obj, Model)
            or obj.pk is None
        ):
            return GuardianPermissionSnapshot(frozenset(), frozenset())

        database_alias = obj._state.db or DEFAULT_DB_ALIAS
        content_type = get_content_type(obj)
        return self.store.resolve(
            layer="guardian",
            database_alias=database_alias,
            identity=("user", user_obj.pk, "content_type", content_type.pk, "object", str(obj.pk)),
            scopes=(
                global_scope(),
                layer_scope("guardian"),
                user_scope("guardian", user_obj.pk),
                guardian_object_scope(content_type.pk, obj.pk),
            ),
            loader=lambda: self._load(user_obj, obj, content_type, database_alias),
            encode=self._encode,
            decode=self._decode,
        )

    @staticmethod
    def _load(user_obj, obj, content_type, database_alias: str) -> GuardianPermissionSnapshot:
        with cachalot_disabled(all_queries=True):
            checker = ObjectPermissionChecker(user_obj)
            group_permissions = frozenset(checker.get_group_perms(obj))
            if user_obj.is_superuser:
                user_permissions = frozenset(
                    Permission.objects.using(database_alias).filter(content_type=content_type).values_list("codename", flat=True)
                )
            else:
                user_permissions = frozenset(checker.get_user_perms(obj))
        return GuardianPermissionSnapshot(user_permissions, group_permissions)

    @staticmethod
    def _encode(snapshot: GuardianPermissionSnapshot) -> dict[str, object]:
        return {
            "user_permissions": sorted(snapshot.user_permissions),
            "group_permissions": sorted(snapshot.group_permissions),
        }

    @staticmethod
    def _decode(payload: dict[str, object] | None) -> GuardianPermissionSnapshot:
        if payload is None:
            return GuardianPermissionSnapshot(frozenset(), frozenset())
        return GuardianPermissionSnapshot(
            GuardianPermissionResolver._decode_permissions(payload["user_permissions"]),
            GuardianPermissionResolver._decode_permissions(payload["group_permissions"]),
        )

    @staticmethod
    def _decode_permissions(value: object) -> frozenset[str]:
        if not isinstance(value, list) or not all(isinstance(permission, str) and permission for permission in value):
            raise TypeError("Lista de permissões Guardian inválida no cache de autorização.")
        return frozenset(value)
