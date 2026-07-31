from prometheus_client import Counter, Histogram

OPERATIONS = Counter("authorization_cache_operations_total", "Operações do cache de autorização.", ("layer", "outcome"))
INVALIDATIONS = Counter("authorization_cache_invalidations_total", "Invalidações do cache de autorização.", ("layer", "status"))
FALLBACKS = Counter("authorization_cache_fallback_total", "Fallbacks do cache de autorização.", ("layer", "reason"))
RESOLVE_SECONDS = Histogram("authorization_cache_resolve_seconds", "Tempo para resolver autorização.", ("layer", "source"))

LAYERS = frozenset({"django", "tenant", "guardian", "rules"})
OUTCOMES = frozenset({"hit", "negative_hit", "miss", "write", "decode_error", "retry"})
STATUSES = frozenset({"success", "error"})
REASONS = frozenset({"disabled", "read_error", "write_error", "epoch_error", "decode_error", "churn"})
SOURCES = frozenset({"cache", "database"})


def _validate(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"Valor inválido para {label}: {value!r}.")
    return value


def record_operation(layer: str, outcome: str) -> None:
    validated_layer = _validate(layer, LAYERS, "layer")
    validated_outcome = _validate(outcome, OUTCOMES, "outcome")
    OPERATIONS.labels(validated_layer, validated_outcome).inc()


def record_invalidation(layer: str, status: str) -> None:
    validated_layer = _validate(layer, LAYERS, "layer")
    validated_status = _validate(status, STATUSES, "status")
    INVALIDATIONS.labels(validated_layer, validated_status).inc()


def record_fallback(layer: str, reason: str) -> None:
    validated_layer = _validate(layer, LAYERS, "layer")
    validated_reason = _validate(reason, REASONS, "reason")
    FALLBACKS.labels(validated_layer, validated_reason).inc()


def time_resolve(layer: str, source: str):
    validated_layer = _validate(layer, LAYERS, "layer")
    validated_source = _validate(source, SOURCES, "source")
    return RESOLVE_SECONDS.labels(validated_layer, validated_source).time()
