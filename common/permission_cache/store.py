import logging
from collections.abc import Callable
from typing import TypeVar

from django.core.cache import caches

from common.permission_cache.config import get_authorization_cache_config
from common.permission_cache.epochs import EpochStore
from common.permission_cache.keys import snapshot_key
from common.permission_cache.metrics import record_fallback, record_operation, time_resolve
from common.permission_cache.types import InvalidEnvelope, decode_envelope, encode_envelope

logger = logging.getLogger(__name__)

T = TypeVar("T")
_MISS = object()


def _validate_json_primitive(value: object) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_primitive(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Chaves do payload do cache de autorização devem ser strings.")
            _validate_json_primitive(item)
        return
    raise TypeError("Payload do cache de autorização contém valor não primitivo.")


class PermissionCacheStore:
    def __init__(self, cache_backend=None, epoch_store=None):
        config = get_authorization_cache_config()
        self._cache = cache_backend or caches[config.alias]
        self._epoch_store = epoch_store or EpochStore(cache_backend=self._cache)

    @property
    def cache(self):
        return self._cache

    @property
    def epoch_store(self):
        return self._epoch_store

    def resolve(
        self,
        *,
        layer: str,
        metric_layer: str | None = None,
        database_alias: str,
        identity: tuple[object, ...],
        scopes: tuple[str, ...],
        loader: Callable[[], T],
        encode: Callable[[T], dict[str, object] | None],
        decode: Callable[[dict[str, object] | None], T],
    ) -> T:
        config = get_authorization_cache_config()
        observed_layer = metric_layer or layer

        def load() -> T:
            with time_resolve(observed_layer, "database"):
                return loader()

        if not config.enabled:
            record_fallback(observed_layer, "disabled")
            return load()

        last_result: T
        for attempt in range(config.max_retries + 1):
            try:
                epochs = self.epoch_store.read(scopes, database_alias)
            except Exception:
                logger.warning(
                    "Falha ao ler epochs do cache de autorização.",
                    extra={"authorization_layer": observed_layer},
                    exc_info=True,
                )
                record_fallback(observed_layer, "epoch_error")
                return load()

            key = snapshot_key(layer, database_alias, identity, epochs)
            try:
                raw = self.cache.get(key, _MISS)
            except Exception:
                logger.warning(
                    "Falha ao ler cache de autorização.",
                    extra={"authorization_layer": observed_layer},
                    exc_info=True,
                )
                record_fallback(observed_layer, "read_error")
                return load()

            if raw is not _MISS:
                try:
                    with time_resolve(observed_layer, "cache"):
                        envelope = decode_envelope(raw)
                        if envelope["found"]:
                            result = decode(envelope["payload"])
                            outcome = "hit"
                        else:
                            result = decode(None)
                            outcome = "negative_hit"
                    record_operation(observed_layer, outcome)
                    return result
                except (InvalidEnvelope, KeyError, TypeError, ValueError):
                    record_operation(observed_layer, "decode_error")
                    record_fallback(observed_layer, "decode_error")

            record_operation(observed_layer, "miss")
            last_result = load()
            payload = encode(last_result)
            if payload is not None and not isinstance(payload, dict):
                raise TypeError("Encoder do cache de autorização deve retornar dict ou None.")
            _validate_json_primitive(payload)
            encoded = encode_envelope(payload)

            try:
                current_epochs = self.epoch_store.read(scopes, database_alias)
            except Exception:
                logger.warning(
                    "Falha ao reler epochs do cache de autorização.",
                    extra={"authorization_layer": observed_layer},
                    exc_info=True,
                )
                record_fallback(observed_layer, "epoch_error")
                return last_result

            if current_epochs != epochs:
                record_operation(observed_layer, "retry")
                if attempt >= config.max_retries:
                    record_fallback(observed_layer, "churn")
                    return last_result
                continue

            try:
                self.cache.set(key, encoded, timeout=config.timeout)
            except Exception:
                logger.warning(
                    "Falha ao gravar cache de autorização.",
                    extra={"authorization_layer": observed_layer},
                    exc_info=True,
                )
                record_fallback(observed_layer, "write_error")
                return last_result
            record_operation(observed_layer, "write")
            return last_result

        raise RuntimeError("Fluxo de resolução do cache de autorização terminou sem resultado.")
