from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True, slots=True)
class AuthorizationCacheConfig:
    enabled: bool
    alias: str
    timeout: int
    key_prefix: str
    max_retries: int


def get_authorization_cache_config() -> AuthorizationCacheConfig:
    raw = settings.AUTHORIZATION_CACHE
    config = AuthorizationCacheConfig(
        enabled=bool(raw["ENABLED"]),
        alias=str(raw["ALIAS"]),
        timeout=int(raw["TIMEOUT"]),
        key_prefix=str(raw["KEY_PREFIX"]),
        max_retries=int(raw.get("MAX_RETRIES", 2)),
    )
    if config.timeout <= 0:
        raise ImproperlyConfigured("AUTHORIZATION_CACHE.TIMEOUT deve ser maior que zero.")
    if config.max_retries < 0:
        raise ImproperlyConfigured("AUTHORIZATION_CACHE.MAX_RETRIES não pode ser negativo.")
    return config
