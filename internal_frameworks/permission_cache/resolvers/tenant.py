from collections.abc import Callable

from cachalot.api import cachalot_disabled

from apps.organizacoes.models import Vinculo
from internal_frameworks.permission_cache.keys import global_scope, layer_scope, user_scope
from internal_frameworks.permission_cache.store import PermissionCacheStore
from internal_frameworks.permission_cache.types import TenantAccess


class TenantAccessResolver:
    def __init__(self, store: PermissionCacheStore | None = None):
        self.store = store or PermissionCacheStore()

    def by_slug(
        self,
        user_id: int,
        organization_slug: str,
        database_alias: str = "default",
        metric_layer: str = "tenant",
    ) -> TenantAccess | None:
        return self._resolve(
            identity=("user", user_id, "organization_slug", organization_slug),
            scopes=(global_scope(), layer_scope("tenant"), user_scope("tenant", user_id)),
            database_alias=database_alias,
            metric_layer=metric_layer,
            loader=lambda: self._load_by_slug(user_id, organization_slug, database_alias),
        )

    def by_organization_id(
        self,
        user_id: int,
        organization_id: int,
        database_alias: str = "default",
        metric_layer: str = "tenant",
    ) -> TenantAccess | None:
        return self._resolve(
            identity=("user", user_id, "organization_id", organization_id),
            scopes=(global_scope(), layer_scope("tenant"), user_scope("tenant", user_id)),
            database_alias=database_alias,
            metric_layer=metric_layer,
            loader=lambda: self._load_by_organization_id(user_id, organization_id, database_alias),
        )

    def _resolve(
        self,
        *,
        identity: tuple[object, ...],
        scopes: tuple[str, ...],
        database_alias: str,
        metric_layer: str,
        loader: Callable[[], TenantAccess | None],
    ) -> TenantAccess | None:
        return self.store.resolve(
            layer="tenant",
            metric_layer=metric_layer,
            database_alias=database_alias,
            identity=identity,
            scopes=scopes,
            loader=loader,
            encode=self._encode,
            decode=self._decode,
        )

    @staticmethod
    def _load_by_slug(user_id: int, organization_slug: str, database_alias: str) -> TenantAccess | None:
        return TenantAccessResolver._load(
            user_id,
            database_alias,
            organizacao__slug=organization_slug,
        )

    @staticmethod
    def _load_by_organization_id(user_id: int, organization_id: int, database_alias: str) -> TenantAccess | None:
        return TenantAccessResolver._load(user_id, database_alias, organizacao_id=organization_id)

    @staticmethod
    def _load(user_id: int, database_alias: str, **organization_filter: object) -> TenantAccess | None:
        with cachalot_disabled(all_queries=True):
            row = (
                Vinculo.objects.using(database_alias)
                .filter(usuario_id=user_id, is_active=True, organizacao__is_active=True, **organization_filter)
                .values("id", "papel", "organizacao_id", "organizacao__slug")
                .first()
            )
        if row is None:
            return None
        return TenantAccess(
            organization_id=row["organizacao_id"],
            organization_slug=row["organizacao__slug"],
            membership_id=row["id"],
            role=row["papel"],
        )

    @staticmethod
    def _encode(access: TenantAccess | None) -> dict[str, object] | None:
        if access is None:
            return None
        return {
            "organization_id": access.organization_id,
            "organization_slug": access.organization_slug,
            "membership_id": access.membership_id,
            "role": access.role,
        }

    @staticmethod
    def _decode(payload: dict[str, object] | None) -> TenantAccess | None:
        if payload is None:
            return None
        organization_id = payload["organization_id"]
        organization_slug = payload["organization_slug"]
        membership_id = payload["membership_id"]
        role = payload["role"]
        if not all(isinstance(value, int) for value in (organization_id, membership_id, role)) or not isinstance(organization_slug, str):
            raise TypeError("TenantAccess inválido no cache de autorização.")
        return TenantAccess(organization_id, organization_slug, membership_id, role)
