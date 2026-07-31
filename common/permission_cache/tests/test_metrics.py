from contextlib import nullcontext
from unittest.mock import Mock, call

from django.core.cache.backends.locmem import LocMemCache

import pytest
from prometheus_client import generate_latest

from common.permission_cache import metrics
from common.permission_cache.epochs import EpochStore
from common.permission_cache.store import PermissionCacheStore


@pytest.mark.parametrize(
    ("helper", "args", "collector_name"),
    [
        (metrics.record_operation, ("user:42", "hit"), "OPERATIONS"),
        (metrics.record_operation, ("django", "user:42"), "OPERATIONS"),
        (metrics.record_invalidation, ("tenant", "timeout"), "INVALIDATIONS"),
        (metrics.record_fallback, ("guardian", "user:42"), "FALLBACKS"),
        (metrics.time_resolve, ("rules", "user:42"), "RESOLVE_SECONDS"),
    ],
)
def test_metric_helpers_reject_unbounded_labels_before_collector_access(monkeypatch, helper, args, collector_name):
    collector = Mock()
    monkeypatch.setattr(metrics, collector_name, collector)

    with pytest.raises(ValueError, match="Valor inválido"):
        helper(*args)

    collector.labels.assert_not_called()


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


def test_prometheus_exposition_never_contains_authorization_identifiers():
    sentinels = (
        "user-991337-sensitive",
        "organization-secret-slug",
        "change_sensitive_record",
        "sensitive_content_type",
        "object-pk-884422-sensitive",
    )
    cache = LocMemCache("permission-metrics-identifiers", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))

    assert (
        store.resolve(
            layer="guardian",
            database_alias="default",
            identity=sentinels,
            scopes=("global",),
            loader=lambda: {"permission": sentinels[2]},
            encode=lambda value: value,
            decode=lambda payload: payload,
        )["permission"]
        == sentinels[2]
    )

    exposition = generate_latest().decode()
    for sentinel in sentinels:
        assert sentinel not in exposition


def test_cache_exception_log_has_layer_and_exception_class_without_payload_or_pii(caplog):
    class CacheWriteFailure(RuntimeError):
        pass

    sentinels = ("user-991337-sensitive", "organization-secret-slug", "change_sensitive_record")
    cache = Mock()
    cache.get.side_effect = lambda _key, default: default
    cache.set.side_effect = CacheWriteFailure("cache unavailable")
    epochs = Mock()
    epochs.read.return_value = (101,)
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    with caplog.at_level("WARNING", logger="common.permission_cache.store"):
        result = store.resolve(
            layer="guardian",
            database_alias="default",
            identity=sentinels,
            scopes=("global",),
            loader=lambda: {"permission": sentinels[2]},
            encode=lambda value: value,
            decode=lambda payload: payload,
        )

    assert result == {"permission": sentinels[2]}
    record = caplog.records[-1]
    assert record.authorization_layer == "guardian"
    assert record.exc_info[0] is CacheWriteFailure
    assert "CacheWriteFailure" in caplog.text
    for sentinel in sentinels:
        assert sentinel not in caplog.text
