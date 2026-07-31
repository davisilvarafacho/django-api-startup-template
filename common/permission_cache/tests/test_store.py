from unittest.mock import Mock

from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError
from django.test import override_settings

import pytest

from common.permission_cache.epochs import EpochStore
from common.permission_cache.keys import snapshot_key
from common.permission_cache.store import PermissionCacheStore
from common.permission_cache.types import decode_envelope


def build_store():
    cache = LocMemCache("permission-store-tests", {})
    cache.clear()
    return PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))


def encode_name(value):
    return None if value is None else {"name": value}


def decode_name(payload):
    return None if payload is None else payload["name"]


def test_miss_loads_once_and_second_call_is_hit():
    loader = Mock(return_value="database")
    store = build_store()
    kwargs = {
        "layer": "tenant",
        "database_alias": "default",
        "identity": ("slug", 10, "acme"),
        "scopes": ("global", "tenant:global", "tenant:user:10"),
        "loader": loader,
        "encode": encode_name,
        "decode": decode_name,
    }

    assert store.resolve(**kwargs) == "database"
    assert store.resolve(**kwargs) == "database"
    loader.assert_called_once_with()


def test_negative_result_is_a_hit():
    loader = Mock(return_value=None)
    store = build_store()
    kwargs = {
        "layer": "tenant",
        "database_alias": "default",
        "identity": ("slug", 10, "missing"),
        "scopes": ("global", "tenant:global", "tenant:user:10"),
        "loader": loader,
        "encode": encode_name,
        "decode": decode_name,
    }

    assert store.resolve(**kwargs) is None
    assert store.resolve(**kwargs) is None
    loader.assert_called_once_with()


def test_epoch_change_during_load_retries_under_new_key():
    loader = Mock(side_effect=["old", "new"])
    epochs = Mock()
    epochs.read.side_effect = [(1, 1), (1, 2), (1, 2), (1, 2)]
    cache = LocMemCache("permission-store-churn", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    result = store.resolve(
        layer="django",
        database_alias="default",
        identity=("user", 1),
        scopes=("global", "django:user:1"),
        loader=loader,
        encode=encode_name,
        decode=decode_name,
    )

    assert result == "new"
    assert loader.call_count == 2


@override_settings(
    AUTHORIZATION_CACHE={
        "ENABLED": False,
        "ALIAS": "permissions",
        "TIMEOUT": 1800,
        "KEY_PREFIX": "authz:test",
        "MAX_RETRIES": 2,
    }
)
def test_kill_switch_bypasses_cache_and_epochs():
    loader = Mock(return_value="database")
    epochs = Mock()
    store = PermissionCacheStore(cache_backend=Mock(), epoch_store=epochs)

    assert (
        store.resolve(
            layer="django",
            database_alias="default",
            identity=("user", 1),
            scopes=("global",),
            loader=loader,
            encode=encode_name,
            decode=decode_name,
        )
        == "database"
    )
    epochs.read.assert_not_called()


def resolve_with(store, loader, encode=encode_name):
    return store.resolve(
        layer="django",
        database_alias="default",
        identity=("user", 1),
        scopes=("global",),
        loader=loader,
        encode=encode,
        decode=decode_name,
    )


def test_invalid_json_is_decode_miss_and_is_replaced():
    store = build_store()
    epochs = store.epoch_store.read(("global",), "default")
    key = snapshot_key("django", "default", ("user", 1), epochs)
    store.cache.set(key, "not-json", 1800)

    assert resolve_with(store, Mock(return_value="fresh")) == "fresh"
    assert decode_envelope(store.cache.get(key))["payload"] == {"name": "fresh"}


def test_cache_read_error_falls_back_to_loader():
    cache = Mock()
    cache.get.side_effect = ConnectionError("redis down")
    epochs = Mock()
    epochs.read.return_value = (1,)
    loader = Mock(return_value="database")
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    assert resolve_with(store, loader) == "database"
    loader.assert_called_once_with()
    cache.set.assert_not_called()


def test_cache_write_error_returns_database_result():
    cache = Mock()
    cache.get.side_effect = lambda key, default: default
    cache.set.side_effect = ConnectionError("redis down")
    epochs = Mock()
    epochs.read.return_value = (1,)
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    assert resolve_with(store, Mock(return_value="database")) == "database"


def test_epoch_read_error_falls_back_without_cache_write():
    cache = Mock()
    epochs = Mock()
    epochs.read.side_effect = ConnectionError("redis down")
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    assert resolve_with(store, Mock(return_value="database")) == "database"
    cache.get.assert_not_called()
    cache.set.assert_not_called()


@override_settings(
    AUTHORIZATION_CACHE={
        "ENABLED": True,
        "ALIAS": "permissions",
        "TIMEOUT": 1800,
        "KEY_PREFIX": "authz:test",
        "MAX_RETRIES": 2,
    }
)
def test_churn_limit_returns_last_database_result_without_write():
    cache = Mock()
    cache.get.side_effect = lambda key, default: default
    epochs = Mock()
    epochs.read.side_effect = [(1,), (2,), (2,), (3,), (3,), (4,)]
    loader = Mock(side_effect=["first", "second", "third"])
    store = PermissionCacheStore(cache_backend=cache, epoch_store=epochs)

    assert resolve_with(store, loader) == "third"
    assert loader.call_count == 3
    cache.set.assert_not_called()


def test_arbitrary_object_from_encoder_is_rejected_before_cache_write():
    store = build_store()

    with pytest.raises(TypeError):
        resolve_with(store, Mock(return_value=object()), encode=lambda value: {"object": value})


def test_database_error_propagates_without_stale_fallback():
    loader = Mock(side_effect=DatabaseError("database down"))
    store = build_store()

    with pytest.raises(DatabaseError, match="database down"):
        resolve_with(store, loader)
