import logging
from collections.abc import Callable
from typing import Literal, TypedDict
from uuid import uuid4

from django.conf import settings
from django.core.cache import caches
from django.db import connections

logger = logging.getLogger(__name__)


class CacheRoundTripError(RuntimeError):
    pass


class CacheCleanupError(RuntimeError):
    pass


class HealthyCheck(TypedDict):
    status: Literal["healthy"]


class UnhealthyCheck(TypedDict):
    status: Literal["unhealthy"]
    error_type: str


CheckResult = HealthyCheck | UnhealthyCheck


class DependencyChecks(TypedDict):
    databases: dict[str, CheckResult]
    caches: dict[str, CheckResult]


class DependencyHealthReport(TypedDict):
    status: Literal["healthy", "unhealthy"]
    checks: DependencyChecks


def check_database(alias: str) -> None:
    connection = connections[alias]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    finally:
        connection.close()


def check_cache(alias: str) -> None:
    backend = caches[alias]
    key = f"dependency-health:{uuid4().hex}"
    value = uuid4().hex
    failure: Exception | None = None

    try:
        backend.set(key, value, timeout=30)
        if backend.get(key) != value:
            raise CacheRoundTripError("cache round-trip returned a different value")
    except Exception as exc:
        failure = exc
    finally:
        try:
            deleted = backend.delete(key)
            if deleted is False and failure is None:
                failure = CacheCleanupError("cache cleanup returned false")
        except Exception as cleanup_error:
            if failure is None:
                failure = cleanup_error

    if failure is not None:
        raise failure


def _run(check: Callable[[str], None], alias: str, kind: str) -> CheckResult:
    try:
        check(alias)
    except Exception as exc:
        logger.exception("Dependency health check failed", extra={"dependency_kind": kind, "dependency_alias": alias})
        return {"status": "unhealthy", "error_type": type(exc).__name__}
    return {"status": "healthy"}


def collect_dependency_health() -> DependencyHealthReport:
    databases = {alias: _run(check_database, alias, "database") for alias in settings.DATABASES}
    cache_results = {alias: _run(check_cache, alias, "cache") for alias in settings.CACHES}
    healthy = all(item["status"] == "healthy" for item in (*databases.values(), *cache_results.values()))
    return {
        "status": "healthy" if healthy else "unhealthy",
        "checks": {"databases": databases, "caches": cache_results},
    }
