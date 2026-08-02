import os
from unittest.mock import Mock, call
from uuid import uuid4

from django.conf import settings
from django.core.cache.backends.locmem import LocMemCache

import pytest
from django_redis.cache import RedisCache
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from internal_frameworks.permission_cache import invalidation
from internal_frameworks.permission_cache.epochs import EpochStore
from internal_frameworks.permission_cache.keys import epoch_key


def make_cache():
    return LocMemCache("permission-epoch-tests", {})


def test_missing_epoch_gets_nonzero_seed_and_is_stable():
    store = EpochStore(cache_backend=make_cache())

    first = store.read(("django:user:42",), "default")
    second = store.read(("django:user:42",), "default")

    assert first == second
    assert first[0] >= 2**52


def test_bump_is_visible_to_a_second_store():
    cache = make_cache()
    first = EpochStore(cache_backend=cache)
    second = EpochStore(cache_backend=cache)
    before = first.read(("global",), "default")[0]

    after = first.bump("global", "default")

    assert after == before + 1
    assert second.read(("global",), "default") == (after,)


def test_lost_epoch_never_reuses_the_old_version():
    cache = make_cache()
    store = EpochStore(cache_backend=cache)
    old = store.read(("global",), "default")[0]
    cache.delete("epoch:default:global")

    replacement = store.read(("global",), "default")[0]

    assert replacement != old
    assert replacement >= 2**52


def test_epoch_increment_error_never_resets_to_zero():
    cache = Mock()
    cache.get.return_value = 2**63 - 1
    cache.incr.side_effect = OverflowError("epoch overflow")
    store = EpochStore(cache_backend=cache)

    with pytest.raises(OverflowError, match="epoch overflow"):
        store.bump("global", "default")

    cache.add.assert_not_called()
    cache.set.assert_not_called()


def test_django_redis_overflow_propagates_and_preserves_epoch():
    redis_url = os.environ.get("PERMISSION_CACHE_TEST_REDIS_URL", settings.AUTHORIZATION_REDIS_URL)

    cache = RedisCache(
        redis_url,
        {
            "KEY_PREFIX": f"permission-epoch-integration-{uuid4()}",
            "OPTIONS": {"SERIALIZER": "django_redis.serializers.json.JSONSerializer"},
        },
    )
    client = cache.client.get_client(write=True)
    try:
        client.ping()
    except RedisConnectionError:
        pytest.skip("Redis não está acessível para a integração de epochs.")

    store = EpochStore(cache_backend=cache)
    scope = "overflow"
    key = epoch_key("default", scope)
    cache.delete(key)

    try:
        before = store.read((scope,), "default")[0]
        assert before >= 2**52
        assert store.bump(scope, "default") == before + 1

        cache.set(key, 2**63 - 1, timeout=None)
        with pytest.raises(ResponseError):
            store.bump(scope, "default")

        assert cache.get(key) == 2**63 - 1
    finally:
        cache.delete(key)


def test_existing_epochs_are_read_with_one_get_many_call():
    cache = Mock()
    cache.get_many.return_value = {
        "epoch:default:global": 10,
        "epoch:default:django:global": 20,
        "epoch:default:django:user:42": 30,
    }
    store = EpochStore(cache_backend=cache)

    result = store.read(("global", "django:global", "django:user:42"), "default")

    assert result == (10, 20, 30)
    cache.get_many.assert_called_once_with(
        (
            "epoch:default:global",
            "epoch:default:django:global",
            "epoch:default:django:user:42",
        )
    )
    cache.get.assert_not_called()
    cache.add.assert_not_called()


def test_bump_epoch_scopes_deduplicates_scopes_and_uses_database_alias(monkeypatch):
    store = Mock()
    store.bump.side_effect = [11, 21]
    epoch_store = Mock(return_value=store)
    record_invalidation = Mock()
    monkeypatch.setattr(invalidation, "EpochStore", epoch_store)
    monkeypatch.setattr(invalidation, "record_invalidation", record_invalidation)

    changed = invalidation.bump_epoch_scopes(
        ("global", "django:user:42", "global"),
        database_alias="replica",
        layer="django",
    )

    assert changed == {"global": 11, "django:user:42": 21}
    assert store.bump.call_args_list == [
        call("global", "replica"),
        call("django:user:42", "replica"),
    ]
    record_invalidation.assert_called_once_with("django", "success")


def test_bump_epoch_scopes_records_and_suppresses_cache_error(monkeypatch):
    store = Mock()
    store.bump.side_effect = ConnectionError("redis down")
    record_invalidation = Mock()
    monkeypatch.setattr(invalidation, "EpochStore", Mock(return_value=store))
    monkeypatch.setattr(invalidation, "record_invalidation", record_invalidation)

    assert invalidation.bump_epoch_scopes(("global",), layer="tenant") == {}

    record_invalidation.assert_called_once_with("tenant", "error")


def test_bump_epoch_scopes_can_raise_cache_error(monkeypatch):
    store = Mock()
    store.bump.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(invalidation, "EpochStore", Mock(return_value=store))
    monkeypatch.setattr(invalidation, "record_invalidation", Mock())

    with pytest.raises(ConnectionError, match="redis down"):
        invalidation.bump_epoch_scopes(("global",), layer="guardian", raise_errors=True)


def test_schedule_epoch_bumps_runs_after_commit_on_selected_database(monkeypatch):
    on_commit = Mock()
    bump_epoch_scopes = Mock()
    monkeypatch.setattr(invalidation.transaction, "on_commit", on_commit)
    monkeypatch.setattr(invalidation, "bump_epoch_scopes", bump_epoch_scopes)

    invalidation.schedule_epoch_bumps(("global",), database_alias="replica", layer="rules")

    callback = on_commit.call_args.args[0]
    assert on_commit.call_args.kwargs == {"using": "replica"}
    bump_epoch_scopes.assert_not_called()
    callback()
    bump_epoch_scopes.assert_called_once_with(("global",), database_alias="replica", layer="rules")
