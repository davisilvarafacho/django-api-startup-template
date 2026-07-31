import logging

from django.db import DEFAULT_DB_ALIAS, transaction

from common.permission_cache.epochs import EpochStore
from common.permission_cache.metrics import record_invalidation

logger = logging.getLogger(__name__)


def bump_epoch_scopes(
    scopes: tuple[str, ...],
    *,
    database_alias: str = DEFAULT_DB_ALIAS,
    layer: str,
    raise_errors: bool = False,
) -> dict[str, int]:
    store = EpochStore()
    changed = {}
    try:
        for scope in dict.fromkeys(scopes):
            changed[scope] = store.bump(scope, database_alias)
    except Exception:
        record_invalidation(layer, "error")
        if raise_errors:
            raise
        logger.exception("Falha ao invalidar cache de autorização.", extra={"authorization_layer": layer})
        return {}
    record_invalidation(layer, "success")
    return changed


def schedule_epoch_bumps(
    scopes: tuple[str, ...],
    *,
    database_alias: str = DEFAULT_DB_ALIAS,
    layer: str,
) -> None:
    transaction.on_commit(
        lambda: bump_epoch_scopes(scopes, database_alias=database_alias, layer=layer),
        using=database_alias,
    )
