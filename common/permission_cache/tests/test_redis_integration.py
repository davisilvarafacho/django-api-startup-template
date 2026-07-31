import multiprocessing
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import caches
from django.test import override_settings

import pytest
import redis
from django_redis import get_redis_connection
from redis.exceptions import ResponseError

from common.permission_cache.epochs import EpochStore
from common.permission_cache.invalidation import bump_epoch_scopes
from common.permission_cache.keys import epoch_key, global_scope, guardian_object_scope, layer_scope, snapshot_key, user_scope
from common.permission_cache.store import PermissionCacheStore

pytestmark = pytest.mark.django_db(transaction=True)


def _redis_epoch_worker(redis_url, physical_key, operation, result_queue):
    client = redis.Redis.from_url(redis_url)
    try:
        value = client.incr(physical_key) if operation == "increment" else client.get(physical_key)
        result_queue.put(int(value))
    finally:
        client.close()


def _encode_name(value):
    return {"name": value}


def _decode_name(payload):
    return payload["name"]


def _resolve(store, loader, *, layer="django", identity=("user", 1), scopes=("global",)):
    return store.resolve(
        layer=layer,
        database_alias="default",
        identity=identity,
        scopes=scopes,
        loader=loader,
        encode=_encode_name,
        decode=_decode_name,
    )


def _unused_local_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_concurrent_epoch_increments_are_atomic(redis_permission_cache):
    store = EpochStore()
    initial = store.read(("global",), "default")[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        returned = list(executor.map(lambda _: EpochStore().bump("global", "default"), range(32)))

    assert len(set(returned)) == 32
    assert store.read(("global",), "default") == (initial + 32,)


def test_two_spawned_processes_observe_same_epoch(redis_permission_cache):
    store = EpochStore()
    initial = store.read((global_scope(),), "default")[0]
    physical_key = store.cache.make_key(epoch_key("default", global_scope()))
    context = multiprocessing.get_context("spawn")

    increment_queue = context.Queue()
    incrementer = context.Process(
        target=_redis_epoch_worker,
        args=(redis_permission_cache, physical_key, "increment", increment_queue),
    )
    incrementer.start()
    incrementer.join(timeout=5)
    if incrementer.is_alive():
        incrementer.terminate()
        incrementer.join(timeout=5)
    assert incrementer.exitcode == 0
    assert increment_queue.get(timeout=5) == initial + 1

    read_queue = context.Queue()
    reader = context.Process(
        target=_redis_epoch_worker,
        args=(redis_permission_cache, physical_key, "read", read_queue),
    )
    reader.start()
    reader.join(timeout=5)
    if reader.is_alive():
        reader.terminate()
        reader.join(timeout=5)
    assert reader.exitcode == 0
    assert read_queue.get(timeout=5) == initial + 1
    assert store.read((global_scope(),), "default") == (initial + 1,)


def test_two_independent_django_clients_observe_same_snapshot(redis_permission_cache):
    first = PermissionCacheStore()
    identity = ("user", 101)
    assert _resolve(first, Mock(return_value="database"), identity=identity) == "database"

    caches.close_all()
    second = PermissionCacheStore()
    loader = Mock(side_effect=AssertionError("cache miss inesperado"))

    assert _resolve(second, loader, identity=identity) == "database"
    loader.assert_not_called()


def test_snapshot_ttl_is_1800_seconds(redis_permission_cache):
    store = PermissionCacheStore()
    scopes = (global_scope(),)
    identity = ("user", 102)
    epochs = store.epoch_store.read(scopes, "default")
    logical_key = snapshot_key("django", "default", identity, epochs)

    assert _resolve(store, Mock(return_value="database"), identity=identity, scopes=scopes) == "database"

    client = get_redis_connection("permissions")
    assert 1798 <= client.ttl(store.cache.make_key(logical_key)) <= 1800
    assert client.ttl(store.cache.make_key(epoch_key("default", global_scope()))) == -1


def test_isolated_epoch_loss_cannot_resurrect_snapshot(redis_permission_cache):
    store = PermissionCacheStore()
    identity = ("user", 103)
    assert _resolve(store, Mock(return_value="old"), identity=identity) == "old"
    old_seed = store.epoch_store.read((global_scope(),), "default")[0]
    physical_epoch_key = store.cache.make_key(epoch_key("default", global_scope()))

    get_redis_connection("permissions").delete(physical_epoch_key)

    loader = Mock(return_value="new")
    assert _resolve(PermissionCacheStore(), loader, identity=identity) == "new"
    assert EpochStore().read((global_scope(),), "default")[0] != old_seed
    loader.assert_called_once_with()


def test_late_writer_under_old_epoch_is_unreachable(redis_permission_cache):
    real_epochs = EpochStore()
    identity = ("user", 104)
    observed_epochs = real_epochs.read((global_scope(),), "default")

    class FrozenEpochStore:
        def read(self, scopes, database_alias):
            return observed_epochs

    loader_started = threading.Event()
    release_loader = threading.Event()
    results = Queue()

    def late_loader():
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return "old"

    def late_writer():
        try:
            results.put(_resolve(PermissionCacheStore(epoch_store=FrozenEpochStore()), late_loader, identity=identity))
        except Exception as exc:
            results.put(exc)

    thread = threading.Thread(target=late_writer)
    thread.start()
    assert loader_started.wait(timeout=5)
    real_epochs.bump(global_scope(), "default")
    release_loader.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert results.get(timeout=5) == "old"
    assert _resolve(PermissionCacheStore(), Mock(return_value="new"), identity=identity) == "new"


def test_reader_retries_when_writer_commits_during_recomposition(redis_permission_cache):
    store = PermissionCacheStore()
    identity = ("user", 105)
    values = iter(("first", "second"))
    loader_calls = 0

    def loader():
        nonlocal loader_calls
        loader_calls += 1
        value = next(values)
        if loader_calls == 1:
            EpochStore().bump(global_scope(), "default")
        return value

    assert _resolve(store, loader, identity=identity) == "second"
    assert loader_calls == 2
    assert _resolve(PermissionCacheStore(), Mock(side_effect=AssertionError("cache miss inesperado")), identity=identity) == "second"


def test_failed_invalidation_window_is_bounded_by_ttl(redis_permission_cache):
    store = PermissionCacheStore()
    current = {"value": "old"}
    scopes = (global_scope(),)
    identity = ("user", 106)
    epochs = store.epoch_store.read(scopes, "default")
    snapshot = snapshot_key("django", "default", identity, epochs)
    assert _resolve(store, lambda: current["value"], identity=identity) == "old"
    current["value"] = "new"

    with patch.object(EpochStore, "bump", side_effect=ConnectionError("redis down")):
        assert bump_epoch_scopes(scopes, layer="django") == {}

    assert _resolve(PermissionCacheStore(), lambda: current["value"], identity=identity) == "old"
    store.cache.delete(snapshot)
    assert _resolve(PermissionCacheStore(), lambda: current["value"], identity=identity) == "new"


def test_cache_outage_falls_back_to_database(redis_permission_cache):
    unavailable_url = f"redis://127.0.0.1:{_unused_local_port()}/15"
    unavailable_cache = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": unavailable_url,
        "KEY_PREFIX": "authz:test:unavailable",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            "REDIS_CLIENT_KWARGS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05},
        },
    }

    caches.close_all()
    with override_settings(CACHES={**settings.CACHES, "permissions": unavailable_cache}):
        caches.close_all()
        assert _resolve(PermissionCacheStore(), Mock(return_value=False)) is False

        cache = caches["permissions"]
        epochs = Mock()
        epochs.read.return_value = (1,)
        with (
            patch.object(cache, "get", side_effect=lambda key, default: default),
            patch.object(cache, "set", wraps=cache.set) as cache_set,
        ):
            assert _resolve(PermissionCacheStore(cache_backend=cache, epoch_store=epochs), Mock(return_value=False)) is False
        cache_set.assert_called_once()
    caches.close_all()


def test_permissions_and_cachalot_aliases_are_isolated(redis_permission_cache):
    assert settings.AUTHORIZATION_REDIS_URL.endswith("/4")
    assert f"{settings.REDIS_URL}/3".endswith("/3")

    permissions = caches["permissions"]
    cachalot = caches["cachalot"]
    logical_key = "shared-logical-key"
    permissions.set(logical_key, "permissions", timeout=1800)
    cachalot.set(logical_key, "cachalot", timeout=1800)

    assert permissions.get(logical_key) == "permissions"
    assert cachalot.get(logical_key) == "cachalot"
    EpochStore().bump(global_scope(), "default")
    assert cachalot.get(logical_key) == "cachalot"


def test_permissions_alias_has_one_primary_location(redis_permission_cache):
    configuration = settings.CACHES["permissions"]
    options = configuration["OPTIONS"]

    assert isinstance(configuration["LOCATION"], str)
    assert options["CLIENT_CLASS"] == "django_redis.client.DefaultClient"
    assert not {"SENTINELS", "SENTINEL_KWARGS", "READ_FROM_REPLICAS", "REPLICA_READ_ONLY"} & options.keys()


def test_global_epoch_changes_every_layer_key(redis_permission_cache):
    store = EpochStore()
    cases = (
        ("django", ("user", 7), (global_scope(), layer_scope("django"), user_scope("django", 7))),
        ("tenant", ("slug", 7, "acme"), (global_scope(), layer_scope("tenant"), user_scope("tenant", 7))),
        (
            "guardian",
            ("user", 7, 41, "99"),
            (global_scope(), layer_scope("guardian"), user_scope("guardian", 7), guardian_object_scope(41, "99")),
        ),
    )
    before = {layer: snapshot_key(layer, "default", identity, store.read(scopes, "default")) for layer, identity, scopes in cases}

    store.bump(global_scope(), "default")

    after = {layer: snapshot_key(layer, "default", identity, store.read(scopes, "default")) for layer, identity, scopes in cases}
    assert all(before[layer] != after[layer] for layer in before)
    assert all(before[layer].split(":")[-2] == after[layer].split(":")[-2] for layer in before)


def test_redis_overflow_is_reported_without_reset(redis_permission_cache):
    store = EpochStore()
    physical_key = store.cache.make_key(epoch_key("default", global_scope()))
    client = get_redis_connection("permissions")
    client.set(physical_key, 2**63 - 1)

    with pytest.raises(ResponseError):
        store.bump(global_scope(), "default")

    assert int(client.get(physical_key)) == 2**63 - 1
