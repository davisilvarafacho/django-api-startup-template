from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from apps.api.core import dependency_health as modulo


def test_collects_every_alias_and_isolates_failures(monkeypatch):
    visited = []

    def database(alias):
        visited.append(("database", alias))
        if alias == "logging":
            raise ConnectionError("secret database host")

    def cache(alias):
        visited.append(("cache", alias))

    monkeypatch.setattr(modulo, "check_database", database)
    monkeypatch.setattr(modulo, "check_cache", cache)
    monkeypatch.setattr(
        modulo,
        "settings",
        SimpleNamespace(DATABASES={"default": {}, "logging": {}}, CACHES={"default": {}, "permissions": {}}),
    )

    result = modulo.collect_dependency_health()

    assert visited == [
        ("database", "default"),
        ("database", "logging"),
        ("cache", "default"),
        ("cache", "permissions"),
    ]
    assert result["status"] == "unhealthy"
    assert result["checks"]["databases"]["logging"] == {
        "status": "unhealthy",
        "error_type": "ConnectionError",
    }
    assert "secret database host" not in repr(result)


def test_check_cache_always_deletes_its_unique_key(monkeypatch):
    class Cache:
        def __init__(self):
            self.deleted = []

        def set(self, key, value, timeout):
            self.key = key
            self.value = value

        def get(self, key):
            return self.value

        def delete(self, key):
            self.deleted.append(key)

    cache = Cache()
    monkeypatch.setattr(modulo, "caches", {"default": cache})

    modulo.check_cache("default")

    assert cache.deleted == [cache.key]


def test_check_cache_reports_cleanup_failure(monkeypatch):
    class Cache:
        def set(self, key, value, timeout):
            self.value = value

        def get(self, key):
            return self.value

        def delete(self, key):
            raise TimeoutError("cleanup failed")

    monkeypatch.setattr(modulo, "caches", {"default": Cache()})

    with pytest.raises(TimeoutError, match="cleanup failed"):
        modulo.check_cache("default")


def test_check_cache_preserves_primary_failure_when_cleanup_also_fails(monkeypatch):
    class Cache:
        def set(self, key, value, timeout):
            raise ConnectionError("write failed")

        def delete(self, key):
            raise TimeoutError("cleanup failed")

    monkeypatch.setattr(modulo, "caches", {"default": Cache()})

    with pytest.raises(ConnectionError, match="write failed"):
        modulo.check_cache("default")


def test_check_database_closes_connection(monkeypatch):
    class Cursor:
        def execute(self, sql):
            assert sql == "SELECT 1"

        def fetchone(self):
            return (1,)

    class Connection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return nullcontext(Cursor())

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(modulo, "connections", {"default": connection})

    modulo.check_database("default")

    assert connection.closed is True


def test_check_database_closes_connection_after_failure(monkeypatch):
    class Connection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            raise ConnectionError("database unavailable")

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(modulo, "connections", {"default": connection})

    with pytest.raises(ConnectionError, match="database unavailable"):
        modulo.check_database("default")

    assert connection.closed is True
