from contextlib import nullcontext
from unittest.mock import Mock, call

from django.core.cache.backends.locmem import LocMemCache

import pytest

from common.permission_cache import metrics
from common.permission_cache.epochs import EpochStore
from common.permission_cache.store import PermissionCacheStore


@pytest.mark.parametrize(
    ("helper", "args"),
    [
        (metrics.record_operation, ("user:42", "hit")),
        (metrics.record_operation, ("django", "user:42")),
        (metrics.record_invalidation, ("tenant", "timeout")),
        (metrics.record_fallback, ("guardian", "user:42")),
        (metrics.time_resolve, ("rules", "user:42")),
    ],
)
def test_metric_helpers_reject_unbounded_labels(helper, args):
    with pytest.raises(ValueError, match="Valor inválido"):
        helper(*args)


def test_metric_helpers_pass_only_closed_labels_to_collectors(monkeypatch):
    operations = Mock()
    invalidations = Mock()
    fallbacks = Mock()
    resolve_seconds = Mock()
    timer = Mock()
    resolve_seconds.labels.return_value.time.return_value = timer
    monkeypatch.setattr(metrics, "OPERATIONS", operations)
    monkeypatch.setattr(metrics, "INVALIDATIONS", invalidations)
    monkeypatch.setattr(metrics, "FALLBACKS", fallbacks)
    monkeypatch.setattr(metrics, "RESOLVE_SECONDS", resolve_seconds)

    metrics.record_operation("django", "hit")
    metrics.record_invalidation("tenant", "success")
    metrics.record_fallback("guardian", "read_error")
    assert metrics.time_resolve("rules", "database") is timer

    operations.labels.assert_called_once_with("django", "hit")
    invalidations.labels.assert_called_once_with("tenant", "success")
    fallbacks.labels.assert_called_once_with("guardian", "read_error")
    resolve_seconds.labels.assert_called_once_with("rules", "database")


def test_resolve_times_hit_and_miss_without_dynamic_identifiers(monkeypatch):
    timer = Mock()
    timer.return_value = nullcontext()
    monkeypatch.setattr("common.permission_cache.store.time_resolve", timer)
    cache = LocMemCache("permission-metrics-tests", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))
    loader = Mock(return_value="database")
    kwargs = {
        "layer": "tenant",
        "metric_layer": "rules",
        "database_alias": "default",
        "identity": ("slug", 10, "secret-tenant"),
        "scopes": ("global",),
        "loader": loader,
        "encode": lambda value: {"name": value},
        "decode": lambda payload: payload["name"],
    }

    assert store.resolve(**kwargs) == "database"
    assert store.resolve(**kwargs) == "database"

    assert timer.call_args_list == [call("rules", "database"), call("rules", "cache")]
