import secrets

from django.core.cache import caches

from common.permission_cache.config import get_authorization_cache_config
from common.permission_cache.keys import epoch_key

MINIMUM_SEED = 2**52
SEED_VARIANTS = 2**52


class EpochStore:
    def __init__(self, cache_backend=None):
        config = get_authorization_cache_config()
        self.cache = cache_backend or caches[config.alias]

    def _get_or_create(self, key: str) -> int:
        value = self.cache.get(key)
        if value is None:
            seed = MINIMUM_SEED + secrets.randbelow(SEED_VARIANTS)
            self.cache.add(key, seed, timeout=None)
            value = self.cache.get(key)
        if not isinstance(value, int):
            raise TypeError(f"Epoch inválido em {key}.")
        return value

    def read(self, scopes: tuple[str, ...], database_alias: str) -> tuple[int, ...]:
        keys = tuple(epoch_key(database_alias, scope) for scope in scopes)
        values = self.cache.get_many(keys)
        missing = [key for key in keys if key not in values]
        for key in missing:
            seed = MINIMUM_SEED + secrets.randbelow(SEED_VARIANTS)
            self.cache.add(key, seed, timeout=None)
        if missing:
            values = self.cache.get_many(keys)
        epochs = tuple(values[key] for key in keys)
        if not all(isinstance(value, int) for value in epochs):
            raise TypeError("Epoch inválido no cache de autorização.")
        return epochs

    def bump(self, scope: str, database_alias: str) -> int:
        key = epoch_key(database_alias, scope)
        self._get_or_create(key)
        value = self.cache.incr(key)
        if not isinstance(value, int):
            raise TypeError(f"Incremento inválido em {key}.")
        return value
