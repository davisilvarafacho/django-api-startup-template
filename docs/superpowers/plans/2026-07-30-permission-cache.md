# Permission Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar um cache semântico compartilhado para permissões globais do Django, acesso ao tenant, permissões por objeto do Guardian e fatos estáveis consumidos por Rules.

**Architecture:** Um pacote Python comum em `common/permission_cache` implementará snapshots primitivos, cache-aside, epochs atômicos e invalidação após commit usando o Django Cache Framework no alias Redis `permissions`. Backends compatíveis substituirão os backends padrão do Django e Guardian; resolvers de tenant alimentarão DRF e Rules, enquanto loaders de banco ignorarão o Cachalot apenas durante a recomposição.

**Tech Stack:** Python 3.12, Django 5.2, Django REST Framework 3.16, django-redis 7, Redis 7, django-cachalot 2.8, django-guardian 3.3, django-rules 3.5, PostgreSQL 16, Prometheus e pytest.

## Global Constraints

- O pacote deve ficar em `common/permission_cache` na raiz e não será um Django app.
- O cache cobre permissões globais diretas e por grupo, tenant/vínculo/papel, Guardian direto e por grupo e fatos estáveis usados por Rules.
- Token scopes, entitlements de checkout/planos e predicates arbitrários permanecem fora do escopo.
- O Redis nunca será fonte de verdade; falha de leitura ou escrita causa fallback ao banco.
- Erro do banco deve propagar e nunca pode usar stale-on-error.
- O TTL padrão é centralizado em `AUTHORIZATION_CACHE["TIMEOUT"] = 1800`.
- `AUTHORIZATION_CACHE["ENABLED"]` é o kill switch e deve preservar as mesmas interfaces.
- Produção usa o alias `permissions`, Redis primário e database lógico `/4`; Cachalot permanece no `/3`.
- Snapshots contêm somente JSON primitivo versionado; nenhum model Django será serializado.
- Cache positivo e negativo são obrigatórios e `None` nunca representará simultaneamente miss e resposta negativa.
- Epoch ausente recebe seed aleatório de alta entropia com `add`/`SET NX`, não zero; epochs não têm TTL.
- Invalidações ORM são agendadas com `transaction.on_commit()`.
- A janela de inconsistência aceita após falha de invalidação é o TTL restante, no máximo 30 minutos; não haverá outbox.
- `QuerySet.update()`, `bulk_create()` e `bulk_update()` não podem ser usados diretamente para fatos de autorização; os wrappers oficiais devem invalidar explicitamente.
- Loaders dos resolvers usam `cachalot_disabled(all_queries=True)` localmente; queries normais continuam elegíveis ao Cachalot.
- Backends cacheados substituem, não duplicam, `ModelBackend` e `ObjectPermissionBackend`.
- Não haverá lock distribuído, single-flight ou leitura de réplica nesta versão.
- Métricas usam labels fechados e nunca incluem usuário, organização, codename, content type ou PK.
- Cada tarefa segue RED → GREEN → REFACTOR e termina em um Conventional Commit estreito.

---

## File structure

| File | Responsibility |
| --- | --- |
| `api/settings.py` | Configuração central, alias `permissions`, TTL, prefixo e backends. |
| `utils/env.py`, `.env.example` | Variáveis opcionais do cache de autorização. |
| `common/__init__.py` | Tornar `common` um pacote Python comum. |
| `common/permission_cache/config.py` | Ler e validar `AUTHORIZATION_CACHE`. |
| `common/permission_cache/types.py` | Snapshots imutáveis, envelopes JSON e decode validado. |
| `common/permission_cache/keys.py` | Escopos de epoch e chaves determinísticas com database alias. |
| `common/permission_cache/epochs.py` | Inicialização, leitura e incremento atômico de epochs. |
| `common/permission_cache/store.py` | Algoritmo cache-aside, retry de churn e fallback seguro. |
| `common/permission_cache/metrics.py` | Counters/histogram Prometheus com cardinalidade limitada. |
| `common/permission_cache/invalidation.py` | Incrementos imediatos ou após commit, logs e métricas. |
| `common/permission_cache/backends.py` | Backends compatíveis com Django e Guardian, incluindo async. |
| `common/permission_cache/resolvers/django.py` | Snapshot global direto/grupo sem Cachalot. |
| `common/permission_cache/resolvers/tenant.py` | `TenantAccess` por slug ou ID. |
| `common/permission_cache/resolvers/guardian.py` | Snapshot de objeto direto/grupo sem Cachalot. |
| `common/permission_cache/signals/django.py` | Matriz de invalidação de auth, groups, permissions e usuários. |
| `common/permission_cache/signals/tenant.py` | Invalidação de vínculo e organização com valores anterior/novo. |
| `common/permission_cache/signals/guardian.py` | Invalidação dos models de object permission. |
| `common/permission_cache/mutations.py` | Wrappers oficiais para mutações bulk de autorização. |
| `common/permission_cache/tests/` | Testes unitários e de integração do pacote. |
| `apps/api/autenticacao/apps.py` | Registrar signals Django e Guardian no `ready()`. |
| `apps/organizacoes/apps.py` | Registrar signals de tenant no `ready()`. |
| `apps/organizacoes/{middleware,permissions,rules,views,serializers}.py` | Migrar consumidores para `request.tenant` e resolver de fatos. |
| `apps/organizacoes/tests/test_permission_cache.py` | Integração DRF, Rules, RLS e invalidação de tenant. |
| `apps/api/autenticacao/tests/test_permission_cache.py` | Compatibilidade dos backends Django/Guardian. |
| `apps/api/core/management/commands/invalidate_permission_cache.py` | Incrementar o epoch global em O(1). |
| `apps/api/core/tests/test_permission_cache_command.py` | Sucesso e falha do comando operacional. |
| `conftest.py` | Fixture Redis real isolada por prefixo para testes distribuídos. |
| `.github/workflows/ci.yml` | Explicitar URL Redis do alias de autorização nos testes. |
| `docs/ROADMAP.md` | Marcar o cache de permissões entregue sem alterar o item futuro de entitlements. |

### Task 1: Establish configuration, keys, and immutable value contracts

**Files:**

- Create: `common/__init__.py`
- Create: `common/permission_cache/__init__.py`
- Create: `common/permission_cache/config.py`
- Create: `common/permission_cache/keys.py`
- Create: `common/permission_cache/types.py`
- Create: `common/permission_cache/tests/__init__.py`
- Create: `common/permission_cache/tests/test_config.py`
- Create: `common/permission_cache/tests/test_keys.py`
- Create: `common/permission_cache/tests/test_types.py`
- Modify: `api/settings.py:423-459`
- Modify: `utils/env.py:4-76`
- Modify: `.env.example:30-34`

**Interfaces:**

- Produces: `AuthorizationCacheConfig`, `get_authorization_cache_config()`.
- Produces: `epoch_key(database_alias, scope)`, `snapshot_key(layer, database_alias, identity, epochs)`.
- Produces: scope helpers `global_scope()`, `layer_scope(layer)`, `user_scope(layer, user_id)` and `guardian_object_scope(content_type_id, object_pk)`.
- Produces: `DjangoPermissionSnapshot`, `GuardianPermissionSnapshot`, `TenantAccess`, `encode_envelope()` and `decode_envelope()`.

- [ ] **Step 1: Write failing configuration tests**

Create `common/permission_cache/tests/test_config.py`:

```python
from django.core.cache import caches
from django.test import override_settings

from common.permission_cache.config import get_authorization_cache_config


def test_default_authorization_cache_contract(settings):
    config = get_authorization_cache_config()

    assert config.enabled is True
    assert config.alias == "permissions"
    assert config.timeout == 1800
    assert config.key_prefix.startswith("authz:v1:")
    assert caches["permissions"] is not None


@override_settings(
    AUTHORIZATION_CACHE={
        "ENABLED": False,
        "ALIAS": "default",
        "TIMEOUT": 60,
        "KEY_PREFIX": "authz:test",
        "MAX_RETRIES": 1,
    }
)
def test_config_reads_override_without_process_cache():
    config = get_authorization_cache_config()

    assert config.enabled is False
    assert config.alias == "default"
    assert config.timeout == 60
    assert config.max_retries == 1
```

- [ ] **Step 2: Run the configuration tests to verify RED**

Run: `uv run pytest common/permission_cache/tests/test_config.py -q`

Expected: FAIL because `common.permission_cache` and the `permissions` alias do not exist.

- [ ] **Step 3: Add the central settings and environment variables**

Add these names to both `ENVS` and `EnviromentVar` in `utils/env.py`:

```python
"AUTHORIZATION_CACHE_ENABLED",
"AUTHORIZATION_CACHE_KEY_PREFIX",
"AUTHORIZATION_REDIS_URL",
```

Add this configuration beside the existing cache block in `api/settings.py`:

```python
AUTHORIZATION_CACHE = {
    "ENABLED": get_bool_from_env("AUTHORIZATION_CACHE_ENABLED", True),
    "ALIAS": "permissions",
    "TIMEOUT": 60 * 30,
    "KEY_PREFIX": get_env_var(
        "AUTHORIZATION_CACHE_KEY_PREFIX",
        f"authz:v1:{ENVIROMENT or CONFIG_ENVIRONMENT}",
    ),
    "MAX_RETRIES": 2,
}

AUTHORIZATION_REDIS_URL = get_env_var("AUTHORIZATION_REDIS_URL") or f"{REDIS_URL}/4"
```

Add `permissions` to both branches of `CACHES`; production must be exactly:

```python
"permissions": {
    "BACKEND": "django_redis.cache.RedisCache",
    "LOCATION": AUTHORIZATION_REDIS_URL,
    "KEY_PREFIX": AUTHORIZATION_CACHE["KEY_PREFIX"],
    "OPTIONS": {
        "CLIENT_CLASS": "django_redis.client.DefaultClient",
        "REDIS_CLIENT_KWARGS": {
            "socket_connect_timeout": 1,
            "socket_timeout": 1,
        },
    },
},
```

The testing alias is:

```python
"permissions": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "KEY_PREFIX": AUTHORIZATION_CACHE["KEY_PREFIX"],
},
```

Document in `.env.example`:

```dotenv
# Opcional; por padrão usa redis://REDIS_HOST:REDIS_PORT/4.
# AUTHORIZATION_REDIS_URL=redis://127.0.0.1:6379/4
AUTHORIZATION_CACHE_ENABLED=True
AUTHORIZATION_CACHE_KEY_PREFIX=authz:v1:development
```

- [ ] **Step 4: Implement validated configuration access**

Create `common/permission_cache/config.py`:

```python
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
```

- [ ] **Step 5: Write failing key and value-object tests**

Create `common/permission_cache/tests/test_keys.py`:

```python
from common.permission_cache.keys import (
    epoch_key,
    global_scope,
    guardian_object_scope,
    snapshot_key,
    user_scope,
)


def test_keys_include_database_layer_identity_and_epochs():
    assert epoch_key("replica", user_scope("django", 42)) == "epoch:replica:django:user:42"
    assert guardian_object_scope(7, "item/42") == "guardian:object:7:item%2F42"

    key_a = snapshot_key("django", "default", ("user", 42), (10, 20, 30))
    key_b = snapshot_key("django", "other", ("user", 42), (10, 20, 30))

    assert key_a.startswith("snapshot:default:django:")
    assert key_a.endswith(":10-20-30")
    assert key_a != key_b
    assert global_scope() == "global"
```

Create `common/permission_cache/tests/test_types.py`:

```python
import pytest

from common.permission_cache.types import (
    DjangoPermissionSnapshot,
    InvalidEnvelope,
    TenantAccess,
    decode_envelope,
    encode_envelope,
)


def test_permission_snapshot_preserves_direct_group_and_union():
    snapshot = DjangoPermissionSnapshot(
        user_permissions=frozenset({"core.add_item"}),
        group_permissions=frozenset({"core.view_item"}),
    )

    assert snapshot.all_permissions == frozenset({"core.add_item", "core.view_item"})


def test_tenant_access_is_immutable_and_compares_role():
    tenant = TenantAccess(organization_id=10, organization_slug="acme", membership_id=27, role=30)

    assert tenant.has_minimum_role(20)
    with pytest.raises(AttributeError):
        tenant.role = 10


def test_envelope_distinguishes_negative_hit_and_invalid_schema():
    assert decode_envelope(encode_envelope(None)) == {"schema": 1, "found": False}
    with pytest.raises(InvalidEnvelope):
        decode_envelope('{"schema": 999, "found": false}')
```

- [ ] **Step 6: Run value tests to verify RED**

Run: `uv run pytest common/permission_cache/tests/test_keys.py common/permission_cache/tests/test_types.py -q`

Expected: FAIL because the key builders and immutable snapshots do not exist.

- [ ] **Step 7: Implement exact key and snapshot contracts**

Implement in `keys.py`:

```python
import hashlib
import json
from urllib.parse import quote


def global_scope() -> str:
    return "global"


def layer_scope(layer: str) -> str:
    return f"{layer}:global"


def user_scope(layer: str, user_id: object) -> str:
    return f"{layer}:user:{quote(str(user_id), safe='')}"


def guardian_object_scope(content_type_id: object, object_pk: object) -> str:
    return f"guardian:object:{quote(str(content_type_id), safe='')}:{quote(str(object_pk), safe='')}"


def epoch_key(database_alias: str, scope: str) -> str:
    return f"epoch:{quote(database_alias, safe='')}:{scope}"


def snapshot_key(layer: str, database_alias: str, identity: tuple[object, ...], epochs: tuple[int, ...]) -> str:
    serialized = json.dumps([str(part) for part in identity], separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.blake2s(serialized.encode(), digest_size=16).hexdigest()
    versions = "-".join(str(epoch) for epoch in epochs)
    return f"snapshot:{quote(database_alias, safe='')}:{layer}:{digest}:{versions}"
```

Implement in `types.py`:

```python
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1


class InvalidEnvelope(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DjangoPermissionSnapshot:
    user_permissions: frozenset[str]
    group_permissions: frozenset[str]

    @property
    def all_permissions(self) -> frozenset[str]:
        return self.user_permissions | self.group_permissions


@dataclass(frozen=True, slots=True)
class GuardianPermissionSnapshot:
    user_permissions: frozenset[str]
    group_permissions: frozenset[str]

    @property
    def all_permissions(self) -> frozenset[str]:
        return self.user_permissions | self.group_permissions


@dataclass(frozen=True, slots=True)
class TenantAccess:
    organization_id: int
    organization_slug: str
    membership_id: int
    role: int

    def has_minimum_role(self, minimum_role: int) -> bool:
        return self.role >= minimum_role


def encode_envelope(payload: dict[str, Any] | None) -> str:
    envelope = {"schema": SCHEMA_VERSION, "found": payload is not None}
    if payload is not None:
        envelope["payload"] = payload
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True)


def decode_envelope(raw: object) -> dict[str, Any]:
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError) as exc:
        raise InvalidEnvelope("Payload do cache de autorização não é JSON válido.") from exc
    if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA_VERSION:
        raise InvalidEnvelope("Schema desconhecido no cache de autorização.")
    if not isinstance(envelope.get("found"), bool):
        raise InvalidEnvelope("Envelope sem marcador found válido.")
    if envelope["found"] and not isinstance(envelope.get("payload"), dict):
        raise InvalidEnvelope("Envelope positivo sem payload primitivo.")
    return envelope
```

- [ ] **Step 8: Run Task 1 tests and commit**

Run: `uv run pytest common/permission_cache/tests/test_config.py common/permission_cache/tests/test_keys.py common/permission_cache/tests/test_types.py -q`

Expected: PASS.

```bash
git add .env.example api/settings.py utils/env.py common
git commit -m "feat: configure authorization cache contracts"
```

### Task 2: Implement epochs, cache-aside, fallback, and metrics

**Files:**

- Create: `common/permission_cache/epochs.py`
- Create: `common/permission_cache/store.py`
- Create: `common/permission_cache/metrics.py`
- Create: `common/permission_cache/invalidation.py`
- Create: `common/permission_cache/tests/test_epochs.py`
- Create: `common/permission_cache/tests/test_store.py`
- Create: `common/permission_cache/tests/test_metrics.py`

**Interfaces:**

- Consumes: `get_authorization_cache_config()`, `epoch_key()`, `snapshot_key()`, envelopes from Task 1.
- Produces: `EpochStore.read(scopes, database_alias) -> tuple[int, ...]` and `EpochStore.bump(scope, database_alias) -> int`.
- Produces: `PermissionCacheStore.resolve(*, layer, metric_layer, database_alias, identity, scopes, loader, encode, decode) -> T`.
- Produces: `schedule_epoch_bumps(scopes, *, database_alias, layer)` and `bump_epoch_scopes(scopes, *, database_alias, layer, raise_errors=False)`.

- [ ] **Step 1: Write failing epoch tests**

Create `test_epochs.py` with a fresh LocMem backend:

```python
from django.core.cache.backends.locmem import LocMemCache

from common.permission_cache.epochs import EpochStore


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
```

Import `Mock` and `pytest`; the Redis integration version in Task 9 narrows the exception to `redis.exceptions.ResponseError`.

- [ ] **Step 2: Verify epoch tests are RED**

Run: `uv run pytest common/permission_cache/tests/test_epochs.py -q`

Expected: FAIL because `EpochStore` does not exist.

- [ ] **Step 3: Implement atomic epoch initialization and increment**

Create `epochs.py`:

```python
import secrets

from django.core.cache import caches

from common.permission_cache.config import get_authorization_cache_config
from common.permission_cache.keys import epoch_key

MINIMUM_SEED = 2**52
SEED_VARIANTS = 2**52


class EpochStore:
    def __init__(self, cache_backend=None):
        config = get_authorization_cache_config()
        self.cache = cache_backend or caches[config.alias]

    def _get_or_create(self, key: str) -> int:
        value = self.cache.get(key)
        if value is None:
            seed = MINIMUM_SEED + secrets.randbelow(SEED_VARIANTS)
            self.cache.add(key, seed, timeout=None)
            value = self.cache.get(key)
        if not isinstance(value, int):
            raise TypeError(f"Epoch inválido em {key}.")
        return value

    def read(self, scopes: tuple[str, ...], database_alias: str) -> tuple[int, ...]:
        keys = tuple(epoch_key(database_alias, scope) for scope in scopes)
        values = self.cache.get_many(keys)
        missing = [key for key in keys if key not in values]
        for key in missing:
            seed = MINIMUM_SEED + secrets.randbelow(SEED_VARIANTS)
            self.cache.add(key, seed, timeout=None)
        if missing:
            values = self.cache.get_many(keys)
        epochs = tuple(values[key] for key in keys)
        if not all(isinstance(value, int) for value in epochs):
            raise TypeError("Epoch inválido no cache de autorização.")
        return epochs

    def bump(self, scope: str, database_alias: str) -> int:
        key = epoch_key(database_alias, scope)
        self._get_or_create(key)
        value = self.cache.incr(key)
        if not isinstance(value, int):
            raise TypeError(f"Incremento inválido em {key}.")
        return value
```

Add `test_existing_epochs_are_read_with_one_get_many_call`: mock `get_many` with all requested keys present, call `read()` for three scopes, and assert `get_many` was called once while `get` and `add` were never called.

- [ ] **Step 4: Run epoch tests GREEN**

Run: `uv run pytest common/permission_cache/tests/test_epochs.py -q`

Expected: PASS.

- [ ] **Step 5: Write failing cache-aside tests**

Create `test_store.py` using `Mock` and the Task 1 envelope:

```python
from unittest.mock import Mock

from django.core.cache.backends.locmem import LocMemCache
from django.test import override_settings

from common.permission_cache.epochs import EpochStore
from common.permission_cache.store import PermissionCacheStore


def build_store():
    cache = LocMemCache("permission-store-tests", {})
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

    assert store.resolve(
        layer="django",
        database_alias="default",
        identity=("user", 1),
        scopes=("global",),
        loader=loader,
        encode=encode_name,
        decode=decode_name,
    ) == "database"
    epochs.read.assert_not_called()
```

Also add these failure-path tests:

```python
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
```

Import `pytest`, `DatabaseError`, `snapshot_key`, and `decode_envelope` at the top of the test file. Expose the store dependencies as read-only attributes `cache` and `epoch_store` so the invalid-payload test can seed the exact versioned key.

- [ ] **Step 6: Verify cache-aside tests are RED**

Run: `uv run pytest common/permission_cache/tests/test_store.py -q`

Expected: FAIL because `PermissionCacheStore` does not exist.

- [ ] **Step 7: Implement cache-aside and closed-label metrics**

In `metrics.py`, define these exact collectors and helper functions:

```python
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
```

Every helper validates membership before calling the collector’s `.labels()` with the validated closed-set values; invalid values raise `ValueError`. Do not expose raw IDs or permission names.

In `store.py`, implement `PermissionCacheStore.resolve()` with this signature:

```python
T = TypeVar("T")

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
```

The body must execute this exact sequence:

1. Set the observability label to `metric_layer or layer`; keep `layer` itself for the snapshot key.
2. When disabled, record fallback `disabled` and return `loader()`.
3. Read epochs; on exception log `warning` with `layer` only, record `epoch_error`, return `loader()`.
4. `cache.get(key, _MISS)`; on exception record `read_error`, return `loader()`.
5. Decode a hit; `found=False` calls `decode(None)` and counts `negative_hit`.
6. Invalid JSON/schema counts `decode_error` and continues as miss.
7. Call the loader and JSON-encode the returned primitive envelope.
8. Re-read epochs; when changed, retry from the beginning up to `MAX_RETRIES`.
9. If stable, `cache.set(key, encoded, timeout=config.timeout)`.
10. A write failure records `write_error` but still returns the DB result.
11. At retry exhaustion, return the last DB result without a cache write and record `churn`.

Before `json.dumps`, recursively accept only `None`, `bool`, `int`, `float`, `str`, `list`, and `dict[str, primitive]`; raise `TypeError` for models or arbitrary objects.
Wrap hit decode/return in `RESOLVE_SECONDS.labels(observed_layer, "cache").time()` and every loader invocation in `RESOLVE_SECONDS.labels(observed_layer, "database").time()`. Unit tests patch the histogram child and assert a hit times `cache`, a miss times `database`, and no dynamic identifier is passed as a label.

- [ ] **Step 8: Implement on-commit invalidation**

Create `invalidation.py`:

```python
import logging

from django.db import DEFAULT_DB_ALIAS, transaction

from common.permission_cache.epochs import EpochStore
from common.permission_cache.metrics import record_invalidation

logger = logging.getLogger(__name__)


def bump_epoch_scopes(
    scopes: tuple[str, ...],
    *,
    database_alias: str = DEFAULT_DB_ALIAS,
    layer: str,
    raise_errors: bool = False,
) -> dict[str, int]:
    store = EpochStore()
    changed = {}
    try:
        for scope in dict.fromkeys(scopes):
            changed[scope] = store.bump(scope, database_alias)
    except Exception:
        record_invalidation(layer, "error")
        logger.exception("Falha ao invalidar cache de autorização.", extra={"authorization_layer": layer})
        if raise_errors:
            raise
        return {}
    record_invalidation(layer, "success")
    return changed


def schedule_epoch_bumps(
    scopes: tuple[str, ...],
    *,
    database_alias: str = DEFAULT_DB_ALIAS,
    layer: str,
) -> None:
    transaction.on_commit(
        lambda: bump_epoch_scopes(scopes, database_alias=database_alias, layer=layer),
        using=database_alias,
    )
```

- [ ] **Step 9: Run Task 2 tests and commit**

Run: `uv run pytest common/permission_cache/tests/test_epochs.py common/permission_cache/tests/test_store.py common/permission_cache/tests/test_metrics.py -q`

Expected: PASS, including failure and retry branches.

```bash
git add common/permission_cache
git commit -m "feat: add epoch based permission cache store"
```

### Task 3: Cache global Django permissions without instance L1 staleness

**Files:**

- Create: `common/permission_cache/resolvers/__init__.py`
- Create: `common/permission_cache/resolvers/django.py`
- Create: `common/permission_cache/backends.py`
- Create: `apps/api/autenticacao/tests/test_permission_cache.py`
- Modify: `api/settings.py:228-232`

**Interfaces:**

- Consumes: `PermissionCacheStore`, Django snapshot and epoch scopes.
- Produces: `DjangoPermissionResolver.resolve(user_obj) -> DjangoPermissionSnapshot`.
- Produces: `CachedModelBackend`, preserving sync, async, authentication, `with_perm()` and module checks.

- [ ] **Step 1: Write Django compatibility tests**

Create `apps/api/autenticacao/tests/test_permission_cache.py`:

```python
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group, Permission
from django.test.utils import CaptureQueriesContext
from django.db import connection

import pytest

from common.permission_cache.backends import CachedModelBackend
from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db


def permission(codename):
    return Permission.objects.get(content_type__app_label="organizacoes", codename=codename)


def test_direct_and_group_permissions_are_separate_and_cached():
    user = UsuarioFactory()
    direct = permission("add_organizacao")
    inherited = permission("view_organizacao")
    group = Group.objects.create(name="readers")
    group.permissions.add(inherited)
    user.user_permissions.add(direct)
    user.groups.add(group)
    backend = CachedModelBackend()

    assert backend.get_user_permissions(user) == {"organizacoes.add_organizacao"}
    assert backend.get_group_permissions(user) == {"organizacoes.view_organizacao"}
    with CaptureQueriesContext(connection) as queries:
        assert backend.get_all_permissions(user) == {
            "organizacoes.add_organizacao",
            "organizacoes.view_organizacao",
        }
    assert len(queries) == 0


def test_backend_does_not_create_modelbackend_l1_attributes():
    user = UsuarioFactory()
    CachedModelBackend().get_all_permissions(user)

    assert not hasattr(user, "_perm_cache")
    assert not hasattr(user, "_user_perm_cache")
    assert not hasattr(user, "_group_perm_cache")


def test_inactive_anonymous_object_and_superuser_match_modelbackend():
    backend = CachedModelBackend()
    inactive = UsuarioFactory(is_active=False)
    superuser = UsuarioFactory(is_superuser=True, is_staff=True)
    obj = Group.objects.create(name="object")

    assert backend.get_all_permissions(inactive) == set()
    assert backend.get_all_permissions(inactive, obj=obj) == set()
    assert backend.has_perm(superuser, "organizacoes.view_organizacao") is True


def test_async_methods_use_same_semantic_snapshot():
    user = UsuarioFactory()
    user.user_permissions.add(permission("view_organizacao"))
    backend = CachedModelBackend()

    assert async_to_sync(backend.ahas_perm)(user, "organizacoes.view_organizacao") is True
    assert async_to_sync(backend.aget_all_permissions)(user) == {"organizacoes.view_organizacao"}
```

Add parity cases for `has_perms`, `has_module_perms`, `with_perm`, authentication, kill switch, empty permission negative hit, database alias isolation, and two distinct `Usuario` instances with the same PK.

- [ ] **Step 2: Run Django backend tests RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_permission_cache.py -q`

Expected: FAIL because the resolver and backend do not exist.

- [ ] **Step 3: Implement the Django resolver**

Create `resolvers/django.py` with these public methods:

```python
class DjangoPermissionResolver:
    def __init__(self, store=None):
        self.store = store or PermissionCacheStore()

    def resolve(self, user_obj) -> DjangoPermissionSnapshot:
        if user_obj.is_anonymous or not user_obj.is_active or user_obj.pk is None:
            return DjangoPermissionSnapshot(frozenset(), frozenset())
        database_alias = user_obj._state.db or DEFAULT_DB_ALIAS
        return self.store.resolve(
            layer="django",
            database_alias=database_alias,
            identity=("user", user_obj.pk),
            scopes=(global_scope(), layer_scope("django"), user_scope("django", user_obj.pk)),
            loader=lambda: self._load(user_obj, database_alias),
            encode=self._encode,
            decode=self._decode,
        )
```

`_load()` must be inside `with cachalot_disabled(all_queries=True)`. For normal users, query `user_obj.user_permissions.using(database_alias)` and `Permission.objects.using(database_alias).filter(group__user=user_obj)` as ordered `(app_label, codename)` pairs. For an active superuser, load all permissions and put the full set in both snapshot fields to preserve `ModelBackend.get_user_permissions()` and `get_group_permissions()`. `_encode()` writes sorted lists; `_decode()` returns frozensets.

- [ ] **Step 4: Implement `CachedModelBackend`**

Subclass `django.contrib.auth.backends.ModelBackend`. Override:

```python
get_user_permissions
get_group_permissions
get_all_permissions
has_perm
has_module_perms
aget_user_permissions
aget_group_permissions
aget_all_permissions
ahas_perm
ahas_module_perms
```

Sync methods read `DjangoPermissionResolver.resolve()`. Async methods use `sync_to_async(sync_method, thread_sensitive=True)` so they never call `ModelBackend._aget_permissions()` or write `_perm_cache`. Keep `authenticate`, `aauthenticate`, `get_user`, `aget_user`, `user_can_authenticate`, and `with_perm` inherited.

Replace only the Django backend in settings:

```python
AUTHENTICATION_BACKENDS = [
    "rules.permissions.ObjectPermissionBackend",
    "common.permission_cache.backends.CachedModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]
```

- [ ] **Step 5: Run Django backend tests GREEN and commit**

Run: `uv run pytest apps/api/autenticacao/tests/test_permission_cache.py -q`

Expected: all Django-only cases PASS; Guardian cases are added in Task 7.

```bash
git add api/settings.py common/permission_cache apps/api/autenticacao/tests/test_permission_cache.py
git commit -m "feat: cache global django permissions"
```

### Task 4: Invalidate Django permissions after committed ORM mutations

**Files:**

- Create: `common/permission_cache/signals/__init__.py`
- Create: `common/permission_cache/signals/django.py`
- Create: `common/permission_cache/tests/test_django_signals.py`
- Modify: `apps/api/autenticacao/apps.py:1-6`

**Interfaces:**

- Consumes: `schedule_epoch_bumps()` and scope helpers.
- Produces: idempotent `connect_django_signals()` called by `AutenticacaoConfig.ready()`.

- [ ] **Step 1: Write transactional signal tests**

Start `test_django_signals.py` with this transaction proof:

```python
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.db import transaction

import pytest

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_direct_permission_bumps_only_after_commit():
    user = UsuarioFactory()
    permission = Permission.objects.first()

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            user.user_permissions.add(permission)
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"django:user:{user.pk}",),
            database_alias="default",
            layer="django",
        )


def test_rollback_does_not_bump_user_epoch():
    user = UsuarioFactory()
    permission = Permission.objects.first()

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                user.user_permissions.add(permission)
                raise RuntimeError("rollback")

        bump.assert_not_called()
```

Add the remaining tests with the same real `atomic()`/patched callback pattern and these exact operations/assertions:

| Test | ORM operation | Expected scopes and layer |
| --- | --- | --- |
| `test_user_group_add_remove_and_clear_bump_both_user_layers` | On three fresh users call `groups.add(group)`, `groups.remove(group)`, and `groups.clear()` | For each operation: `django:user:<id>` with layer `django`, then `guardian:user:<id>` with layer `guardian`. |
| `test_reverse_group_clear_captures_users_in_pre_clear` | Add two users, then `group.user_set.clear()` | Both user IDs appear in deduplicated Django and Guardian user scopes after commit. |
| `test_group_permission_change_bumps_django_global` | Call `group.permissions.add(permission)`, `.remove(permission)`, and `.clear()` in separate transactions | `django:global`, layer `django`. |
| `test_group_delete_bumps_django_and_guardian_global` | Delete a persisted group | `django:global` and `guardian:global` in their respective callbacks. |
| `test_user_active_or_superuser_change_bumps_all_user_layers` | Change `is_active`, then independently `is_superuser`, using `save(update_fields=[field])` | `django:user:<id>`, `tenant:user:<id>`, and `guardian:user:<id>`. |
| `test_user_delete_bumps_all_user_layers` | Delete a persisted user | The same three user scopes using the retained PK. |
| `test_permission_and_content_type_changes_bump_global_layers` | Save/delete a custom `Permission`, then rename/restore its `ContentType.model` | Django and Guardian global layer scopes. |
| `test_post_migrate_bumps_global_layers` | Send `post_migrate` with the authentication app config | Django and Guardian global layer scopes. |

Do not patch `schedule_epoch_bumps`: patch `common.permission_cache.invalidation.bump_epoch_scopes`, because the former is invoked inside the transaction only to register the callback.

- [ ] **Step 2: Run signal tests RED**

Run: `uv run pytest common/permission_cache/tests/test_django_signals.py -q`

Expected: FAIL because no signal module is registered.

- [ ] **Step 3: Implement the full Django invalidation matrix**

In `signals/django.py`:

- Connect `m2m_changed` to `Usuario.user_permissions.through`, `Usuario.groups.through`, and `Group.permissions.through`.
- Handle `post_add`, `post_remove`, and `post_clear`.
- For reverse `pre_clear`, store primitive affected user IDs in a module-local dictionary keyed by `(sender, instance.pk, using)`; consume and delete it in `post_clear`.
- On user-group changes, schedule both `user_scope("django", id)` and `user_scope("guardian", id)`.
- On group permission changes, bump `layer_scope("django")`.
- Use `pre_save` to load only previous `is_active` and `is_superuser`; on changed `post_save`, bump user scopes for `django`, `tenant`, and `guardian`.
- On `post_delete(Usuario)`, bump those same three user scopes.
- On structural `Group` save/delete, bump Django and Guardian layer scopes.
- On `Permission` or `ContentType` save/delete and on `post_migrate`, bump Django and Guardian layer scopes.
- Supply stable `dispatch_uid` values and make `connect_django_signals()` safe to call twice.

Register in `AutenticacaoConfig.ready()`:

```python
def ready(self):
    from common.permission_cache.signals.django import connect_django_signals

    connect_django_signals()
```

- [ ] **Step 4: Run signal and backend regression tests GREEN**

Run: `uv run pytest common/permission_cache/tests/test_django_signals.py apps/api/autenticacao/tests/test_permission_cache.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/autenticacao/apps.py common/permission_cache/signals common/permission_cache/tests/test_django_signals.py
git commit -m "feat: invalidate django permission snapshots"
```

### Task 5: Resolve tenant access and migrate the request contract

**Files:**

- Create: `common/permission_cache/resolvers/tenant.py`
- Create: `common/permission_cache/tests/test_tenant_resolver.py`
- Create: `apps/organizacoes/tests/test_permission_cache.py`
- Modify: `apps/organizacoes/middleware.py:20-38`
- Modify: `apps/organizacoes/permissions.py:18-79`
- Modify: `apps/organizacoes/views.py:47-113`
- Modify: `apps/organizacoes/serializers.py:55-109`

**Interfaces:**

- Produces: `TenantAccessResolver.by_slug(user_id: int, organization_slug: str, database_alias: str = "default", metric_layer: str = "tenant") -> TenantAccess | None`.
- Produces: `TenantAccessResolver.by_organization_id(user_id: int, organization_id: int, database_alias: str = "default", metric_layer: str = "tenant") -> TenantAccess | None`.
- Produces: canonical request attribute `request.tenant: TenantAccess | None`.

- [ ] **Step 1: Write resolver tests for positive, negative, dual lookup, and Cachalot**

In `test_tenant_resolver.py`, start with:

```python
from contextlib import contextmanager
from unittest.mock import Mock

from django.core.cache import caches

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.resolvers.tenant import TenantAccessResolver
from common.permission_cache.types import TenantAccess

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_permission_cache():
    caches["permissions"].clear()


def test_by_slug_returns_primitives_and_second_read_has_no_query(django_assert_num_queries):
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    resolver = TenantAccessResolver()

    first = resolver.by_slug(user.pk, "acme")
    with django_assert_num_queries(0):
        second = resolver.by_slug(user.pk, "acme")

    assert first == second == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)


def test_by_id_returns_the_same_tenant_fact():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    assert TenantAccessResolver().by_organization_id(user.pk, organization.pk) == TenantAccess(
        organization.pk,
        "acme",
        membership.pk,
        Papel.MEMBRO,
    )


def test_missing_membership_is_a_negative_hit(django_assert_num_queries):
    user = UsuarioFactory()
    resolver = TenantAccessResolver()

    assert resolver.by_slug(user.pk, "missing") is None
    with django_assert_num_queries(0):
        assert resolver.by_slug(user.pk, "missing") is None


def test_loader_disables_cachalot_for_all_queries(monkeypatch):
    calls = []

    @contextmanager
    def recording_context(*, all_queries):
        calls.append(all_queries)
        yield

    monkeypatch.setattr("common.permission_cache.resolvers.tenant.cachalot_disabled", recording_context)
    user = UsuarioFactory()

    assert TenantAccessResolver().by_slug(user.pk, "missing") is None
    assert calls == [True]
```

Add parametrized variants that set `Vinculo.ativo=False` or `Organizacao.ativo=False` and assert `None`; resolve the same user/slug through database aliases `default` and a mocked second alias and assert different keys; with the kill switch disabled, call twice under `django_assert_num_queries(1)` each time.

- [ ] **Step 2: Run resolver tests RED**

Run: `uv run pytest common/permission_cache/tests/test_tenant_resolver.py -q`

Expected: FAIL because `TenantAccessResolver` does not exist.

- [ ] **Step 3: Implement both tenant lookup forms**

Both public methods call one `_resolve(identity, scopes, loader)` helper. The database query must use:

```python
Vinculo.objects.using(database_alias).filter(
    usuario_id=user_id,
    ativo=True,
    organizacao__ativo=True,
    # plus organizacao__slug or organizacao_id
).values(
    "id",
    "papel",
    "organizacao_id",
    "organizacao__slug",
).first()
```

Wrap only that evaluation in `cachalot_disabled(all_queries=True)`. Encode to:

```python
{
    "organization_id": access.organization_id,
    "organization_slug": access.organization_slug,
    "membership_id": access.membership_id,
    "role": access.role,
}
```

Use scopes `(global_scope(), layer_scope("tenant"), user_scope("tenant", user_id))`. Return `None` for a negative hit.
Pass `layer="tenant"` and the public `metric_layer` argument separately to `PermissionCacheStore.resolve()`, so a Rules consumer changes only observability and never the cache namespace.
Define both as instance methods and accept `store: PermissionCacheStore | None = None` in `__init__`. DRF and Rules create the lightweight resolver at the call site; tests may inject a store.

- [ ] **Step 4: Write request-contract regression tests**

In `apps/organizacoes/tests/test_permission_cache.py`, include this canonical permission test:

```python
from unittest.mock import Mock, patch

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.organizacoes.views import TimeViewSet
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.types import TenantAccess


@pytest.mark.django_db
def test_tenant_permission_sets_request_tenant_and_rls_context():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    raw_request = APIRequestFactory().get("/times/", HTTP_X_ORGANIZATION="acme")
    force_authenticate(raw_request, user=user)
    request = Request(raw_request)
    request.organizacao_slug = "acme"

    with patch("apps.organizacoes.permissions.definir_organizacao_atual") as define_rls:
        assert TenantPermission().has_permission(request, TimeViewSet()) is True

    assert request.tenant == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)
    define_rls.assert_called_once_with(organization.pk)


def test_papel_minimo_permission_reads_tenant_dataclass():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    view = Mock(action="create", papeis_por_action={"create": Papel.GESTOR})

    assert PapelMinimoPermission().has_permission(request, view) is True
```

Add HTTP assertions using the existing `client_autenticado()` helper:

| Test | Request | Assertion |
| --- | --- | --- |
| `test_tenant_permission_denies_cached_missing_membership` | Perform the same `/times/` request twice for a user without membership | Both responses are 403 and the second membership lookup executes no SQL. |
| `test_time_view_filters_and_creates_by_tenant_organization_id` | GET then POST `/times/` with `X-Organization: acme` | Only Acme rows are returned and the new row has `organizacao_id == acme.pk`. |
| `test_vinculo_serializer_compares_tenant_role` | Try to grant `PROPRIETARIO` while request tenant is `GESTOR` | Serializer returns the existing “papel acima do seu” validation error. |
| `test_convite_serializer_compares_tenant_role` | Try the equivalent invitation | The same validation rule is preserved. |
| `test_middleware_initializes_only_request_tenant` | Invoke `OrganizacaoMiddleware` with a recording response | The response sees `request.tenant is None` and no `organizacao`/`vinculo` attribute. |

Also update existing `test_api.py` expectations so no assertion relies on `request.organizacao` or `request.vinculo`.

- [ ] **Step 5: Run request tests RED**

Run: `uv run pytest apps/organizacoes/tests/test_permission_cache.py apps/organizacoes/tests/test_api.py -q`

Expected: FAIL because current consumers attach Django models to the request.

- [ ] **Step 6: Migrate every request consumer**

Apply these exact replacements:

- Middleware sets `request.tenant = None`; remove `request.organizacao` and `request.vinculo`.
- `TenantPermission` calls `TenantAccessResolver().by_slug(request.user.pk, slug)`, stores the result in `request.tenant`, and calls `definir_organizacao_atual(tenant.organization_id)`.
- `PapelMinimoPermission` calls `request.tenant.has_minimum_role(papel_minimo)`.
- `TenantViewSetMixin.get_organizacao_id()` returns `request.tenant.organization_id`.
- Querysets filter with `organizacao_id=self.get_organizacao_id()`.
- Serializer saves use `organizacao_id=self.get_organizacao_id()`.
- `VinculoSerializer` filters teams using `request.tenant.organization_id`.
- Role validation compares with `request.tenant.role`.

Run this guard after editing:

```bash
rg -n 'request\.(organizacao|vinculo)|self\.request\.organizacao' apps --glob '*.py'
```

Expected: no production-code matches.

- [ ] **Step 7: Run tenant resolver/API tests GREEN and commit**

Run: `uv run pytest common/permission_cache/tests/test_tenant_resolver.py apps/organizacoes/tests/test_permission_cache.py apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

```bash
git add common/permission_cache/resolvers/tenant.py common/permission_cache/tests/test_tenant_resolver.py apps/organizacoes
git commit -m "feat: cache tenant access facts"
```

### Task 6: Invalidate tenant snapshots and route Rules through stable facts

**Files:**

- Create: `common/permission_cache/signals/tenant.py`
- Create: `common/permission_cache/tests/test_tenant_signals.py`
- Modify: `apps/organizacoes/apps.py:1-15`
- Modify: `apps/organizacoes/rules.py:1-50`
- Modify: `apps/organizacoes/tests/test_permission_cache.py`

**Interfaces:**

- Consumes: tenant resolver, scope helpers and on-commit invalidation.
- Produces: `connect_tenant_signals()` and Rules predicate backed by `TenantAccessResolver`.

- [ ] **Step 1: Write exact tenant invalidation tests**

Start with a committed mutation test:

```python
@pytest.mark.django_db(transaction=True)
def test_membership_role_change_bumps_user_scope_after_commit():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.papel = Papel.GESTOR
            membership.save(update_fields=["papel"])
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"tenant:user:{user.pk}",),
            database_alias="default",
            layer="tenant",
        )
```

Add this exact matrix with the same callback patch:

| Test | Mutation | Assertion after commit |
| --- | --- | --- |
| `test_membership_create_and_delete_bump_user_scope` | Create then delete separate memberships | Each affected user scope is bumped once per transaction. |
| `test_membership_user_change_bumps_old_and_new_users` | Change `usuario_id` with `save(update_fields=["usuario"])` | Both old and new `tenant:user` scopes are present. |
| `test_membership_organization_change_keeps_user_invalidation` | Change `organizacao_id` | The linked user scope is bumped, making both slug/ID indices unreachable. |
| `test_membership_active_change_invalidates_positive_and_negative_snapshots` | Toggle `ativo` false then true | Each commit changes the user epoch and the resolver result flips between `None` and `TenantAccess`. |
| `test_organization_slug_active_and_delete_bump_tenant_global` | Rename slug, toggle `ativo`, and delete separate organizations | Each transaction bumps `tenant:global`. |
| `test_nested_atomic_rollback_does_not_invalidate` | Save a role inside nested `atomic()`, then roll back the outer block | `bump_epoch_scopes` is never called. |

Patch `common.permission_cache.invalidation.bump_epoch_scopes`, not `schedule_epoch_bumps`; assert previous IDs by checking the old and new scope strings after the callback executes.

- [ ] **Step 2: Run tenant signal tests RED**

Run: `uv run pytest common/permission_cache/tests/test_tenant_signals.py -q`

Expected: FAIL because tenant signals do not exist.

- [ ] **Step 3: Implement tenant signals**

`connect_tenant_signals()` connects:

- `pre_save(Vinculo)` to load previous `usuario_id`, `organizacao_id`, `papel`, and `ativo`.
- `post_save(Vinculo)` to bump the union of old/new `tenant:user:<id>` scopes whenever created or any tracked field changed.
- `pre_delete/post_delete(Vinculo)` to retain and invalidate the deleted user.
- `pre_save(Organizacao)` to load old `slug` and `ativo`.
- `post_save/post_delete(Organizacao)` to bump `tenant:global` on create, slug/state change, or delete.

Use the primitive instance attributes `_permission_cache_previous_user_id`, `_permission_cache_previous_organization_id`, `_permission_cache_previous_role`, `_permission_cache_previous_active`, and `_permission_cache_previous_slug`, plus stable `dispatch_uid`, the signal’s `using`, and `transaction.on_commit`.

Register from `OrganizacoesConfig.ready()` after the existing route discovery:

```python
from common.permission_cache.signals.tenant import connect_tenant_signals

connect_tenant_signals()
```

- [ ] **Step 4: Write and implement Rules integration**

Add this test:

```python
def test_papel_minimo_rule_reuses_tenant_resolver(monkeypatch, usuario, objeto):
    resolver = Mock(return_value=TenantAccess(1, "acme", 2, Papel.GESTOR))
    monkeypatch.setattr(TenantAccessResolver, "by_organization_id", resolver)

    assert e_gestor.test(usuario, objeto) is True
    resolver.assert_called_once_with(
        usuario.pk,
        objeto.organizacao_id,
        database_alias=usuario._state.db or "default",
        metric_layer="rules",
    )
```

Import `Mock` from `unittest.mock` and `TenantAccessResolver` in the test. Replace the direct `Vinculo.objects.filter(usuario=usuario, organizacao_id=organizacao_id, ativo=True, papel__gte=papel).exists()` in `papel_minimo` with `TenantAccessResolver().by_organization_id(usuario.pk, organizacao_id, database_alias=usuario._state.db or DEFAULT_DB_ALIAS, metric_layer="rules")`; return `False` when it returns `None`, otherwise call `has_minimum_role(papel)`. Keep each predicate evaluation uncached; only the fact is cached.

- [ ] **Step 5: Run tenant/Rules tests and commit**

Run: `uv run pytest common/permission_cache/tests/test_tenant_signals.py apps/organizacoes/tests/test_permission_cache.py -q`

Expected: PASS.

```bash
git add common/permission_cache/signals/tenant.py common/permission_cache/tests/test_tenant_signals.py apps/organizacoes
git commit -m "feat: invalidate tenant facts after commit"
```

### Task 7: Cache Guardian object permissions with backend parity

**Files:**

- Create: `common/permission_cache/resolvers/guardian.py`
- Create: `common/permission_cache/tests/test_guardian_resolver.py`
- Modify: `common/permission_cache/backends.py`
- Modify: `api/settings.py:228-232`
- Modify: `apps/api/autenticacao/tests/test_permission_cache.py`

**Interfaces:**

- Produces: `GuardianPermissionResolver.resolve(user_obj, obj) -> GuardianPermissionSnapshot`.
- Produces: `CachedObjectPermissionBackend` with sync and async Guardian behavior.

- [ ] **Step 1: Write Guardian resolver/backend tests**

Use `guardian.shortcuts.assign_perm` and start with:

```python
from django.contrib.auth.models import Group

from guardian.shortcuts import assign_perm

from apps.organizacoes.models import Organizacao
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.resolvers.guardian import GuardianPermissionResolver


@pytest.mark.django_db
def test_guardian_snapshot_separates_direct_and_group_codenames():
    user = UsuarioFactory()
    group = Group.objects.create(name="object-readers")
    user.groups.add(group)
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("change_organizacao", user, organization)
    assign_perm("view_organizacao", group, organization)

    snapshot = GuardianPermissionResolver().resolve(user, organization)

    assert snapshot.user_permissions == frozenset({"change_organizacao"})
    assert snapshot.group_permissions == frozenset({"view_organizacao"})
    assert snapshot.all_permissions == frozenset({"change_organizacao", "view_organizacao"})
```

Add these exact parity cases:

| Test | Setup | Assertion |
| --- | --- | --- |
| `test_guardian_empty_snapshot_is_negative_hit_without_second_query` | User/object without assignment | Second resolve runs under `django_assert_num_queries(0)` and returns two empty frozensets. |
| `test_guardian_superuser_matches_upstream` | Active superuser plus one real group object permission | Cached/upstream `has_perm` and `get_all_permissions` match; cached `get_group_permissions` returns only the real group codename. |
| `test_guardian_unsupported_inputs_fail_closed` | Inactive user, `AnonymousUser`, `obj=None`, unsaved organization, and a plain object | `has_perm` is false and permission getters return empty sets. |
| `test_guardian_backend_accepts_prefixed_and_unprefixed_codename` | Direct `view_organizacao` assignment | Both `"view_organizacao"` and `"organizacoes.view_organizacao"` return true. |
| `test_guardian_backend_raises_wrong_app_error_like_upstream` | Call with `"usuarios.view_organizacao"` | Both upstream and cached backends raise `WrongAppError`. |
| `test_guardian_async_methods_use_same_snapshot` | Direct assignment | `async_to_sync(backend.ahas_perm)` and async getters equal sync results. |
| `test_user_has_perm_keeps_backend_or_semantics` | Grant only through Rules, only through Django, and only through Guardian to three users | Each matching `user.has_perm()` call returns true while unrelated permission calls return false. |

For every parity case, instantiate both `guardian.backends.ObjectPermissionBackend` and the cached backend and compare `has_perm`, `get_group_permissions`, and `get_all_permissions` where the upstream API supports the input.

- [ ] **Step 2: Run Guardian tests RED**

Run: `uv run pytest common/permission_cache/tests/test_guardian_resolver.py apps/api/autenticacao/tests/test_permission_cache.py -q`

Expected: FAIL because the cached Guardian backend is absent.

- [ ] **Step 3: Implement Guardian snapshot loading**

`GuardianPermissionResolver.resolve()` must:

- Fail closed with empty sets for inactive/anonymous users, missing PKs, or non-model objects.
- Use the object database alias and `get_content_type(obj)`.
- Use identity `("user", user.pk, "content_type", content_type.pk, "object", str(obj.pk))`.
- Use scopes `global`, `guardian:global`, `guardian:user:<id>`, and `guardian:object:<ct>:<pk>`.
- Inside `cachalot_disabled(all_queries=True)`, create a fresh `ObjectPermissionChecker(user)` and evaluate `get_user_perms(obj)` and `get_group_perms(obj)`.
- For superuser, load every codename for that content type into direct permissions while preserving actual group permissions separately.
- Encode sorted primitive lists and decode frozensets.

- [ ] **Step 4: Implement compatible sync and async Guardian backend**

Subclass `guardian.backends.ObjectPermissionBackend`. Preserve `check_support()` and app-label validation from upstream. Override:

```python
has_perm
get_group_permissions
get_all_permissions
ahas_perm
aget_group_permissions
aget_all_permissions
```

`has_perm` strips an optional app prefix only after validating it and checks the codename in `snapshot.all_permissions`; active superusers return `True`. Each async method delegates to its corresponding bound sync method with `sync_to_async(bound_method, thread_sensitive=True)`.

Replace only the Guardian backend:

```python
AUTHENTICATION_BACKENDS = [
    "rules.permissions.ObjectPermissionBackend",
    "common.permission_cache.backends.CachedModelBackend",
    "common.permission_cache.backends.CachedObjectPermissionBackend",
]
```

Because Guardian 3.3 checks for its backend by exact dotted-path string, add:

```python
SILENCED_SYSTEM_CHECKS = ["guardian.W001"]
```

Place a comment immediately above it explaining that `CachedObjectPermissionBackend` subclasses and compatibility-tests the Guardian backend. Add a test that runs Django’s checks, asserts no Guardian warning, and asserts the custom backend is present exactly once while the upstream backend is absent.

- [ ] **Step 5: Run all backend tests and commit**

Run: `uv run pytest common/permission_cache/tests/test_guardian_resolver.py apps/api/autenticacao/tests/test_permission_cache.py -q`

Expected: PASS.

```bash
git add api/settings.py common/permission_cache apps/api/autenticacao/tests/test_permission_cache.py
git commit -m "feat: cache guardian object permissions"
```

### Task 8: Invalidate Guardian and provide executable bulk mutation paths

**Files:**

- Create: `common/permission_cache/signals/guardian.py`
- Create: `common/permission_cache/mutations.py`
- Create: `common/permission_cache/tests/test_guardian_signals.py`
- Create: `common/permission_cache/tests/test_mutations.py`
- Modify: `apps/api/autenticacao/apps.py:6-10`

**Interfaces:**

- Produces: `connect_guardian_signals()`.
- Produces: `bulk_create_memberships()`, `bulk_update_memberships()`, `update_memberships()`.
- Produces: `guardian_bulk_assign()`, `guardian_assign_to_many()`, `guardian_bulk_remove()`, `guardian_remove_from_many()`.
- Produces: `bulk_create_permissions()`, `bulk_update_permissions()`, `update_user_authorization_state()`.

- [ ] **Step 1: Write Guardian signal tests**

Resolve the configured models with `get_user_obj_perms_model()` and `get_group_obj_perms_model()` and start with:

```python
@pytest.mark.django_db(transaction=True)
def test_user_object_permission_create_bumps_object_after_commit():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    content_type = get_content_type(organization)
    permission = Permission.objects.get(content_type=content_type, codename="view_organizacao")
    model = get_user_obj_perms_model(organization)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            model.objects.create(
                user=user,
                permission=permission,
                content_type=content_type,
                object_pk=str(organization.pk),
            )
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"guardian:object:{content_type.pk}:{organization.pk}",),
            database_alias="default",
            layer="guardian",
        )
```

Add:

| Test | Mutation | Assertion |
| --- | --- | --- |
| `test_user_object_permission_update_bumps_old_and_new_objects` | Change `object_pk` from Org A to Org B with `.save(update_fields=["object_pk"])` | Both canonical object scopes are scheduled after commit. |
| `test_user_object_permission_delete_bumps_old_object` | Delete the row | The retained old object scope is scheduled. |
| `test_group_object_permission_create_update_delete_has_same_matrix` | Repeat all three operations with `get_group_obj_perms_model()` | The same object-scope behavior, independent of group fan-out. |
| `test_guardian_signal_canonicalizes_string_pk` | Use numeric model PK and inspect scope | Scope contains `str(pk)` exactly once. |
| `test_guardian_signal_ignores_rollback` | Create inside a rolled-back outer transaction | No epoch callback executes. |

- [ ] **Step 2: Run Guardian signal tests RED**

Run: `uv run pytest common/permission_cache/tests/test_guardian_signals.py -q`

Expected: FAIL because `connect_guardian_signals()` does not exist.

- [ ] **Step 3: Implement Guardian model signals**

At connection time, resolve configured Guardian models. `pre_save` loads previous `content_type_id` and canonical object PK; `post_save` schedules both old and new `guardian_object_scope`. `pre_delete/post_delete` retains and invalidates the deleted object scope. Use the row database alias and stable dispatch UIDs.

Extend `AutenticacaoConfig.ready()`:

```python
from common.permission_cache.signals.guardian import connect_guardian_signals

connect_guardian_signals()
```

- [ ] **Step 4: Write bulk-wrapper tests before implementation**

In `test_mutations.py`, start with:

```python
@pytest.mark.django_db(transaction=True)
def test_bulk_create_memberships_invalidates_every_user_after_commit():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    memberships = [
        Vinculo(usuario=first, organizacao=organization, papel=Papel.MEMBRO),
        Vinculo(usuario=second, organizacao=organization, papel=Papel.GESTOR),
    ]

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            created = bulk_create_memberships(memberships)
            bump.assert_not_called()

        assert {row.usuario_id for row in created} == {first.pk, second.pk}
        bump.assert_called_once_with(
            (f"tenant:user:{first.pk}", f"tenant:user:{second.pk}"),
            database_alias="default",
            layer="tenant",
        )
```

Add:

| Test | Wrapper invocation | Assertion |
| --- | --- | --- |
| `test_bulk_update_memberships_invalidates_old_and_new_users` | Move two materialized memberships to another user, then call `bulk_update_memberships(rows, ["usuario"])` | Old and new user scopes are deduplicated and scheduled. |
| `test_update_memberships_reads_users_before_queryset_update` | `update_memberships(Vinculo.objects.filter(organizacao=organization), papel=Papel.GESTOR)` | Return count equals ORM count; original linked users are invalidated. |
| `test_guardian_bulk_assign_and_remove_invalidate_every_object` | Assign/remove one codename across Org A and Org B | Return values match Guardian manager methods and both object scopes are scheduled. |
| `test_guardian_assign_to_many_and_remove_from_many_invalidate_once` | Assign/remove the same object for two users | The object scope appears only once. |
| `test_permission_bulk_wrappers_bump_global_layers` | Create/update two `Permission` objects through wrappers | Django and Guardian global layer scopes are scheduled. |
| `test_bulk_user_state_update_bumps_all_layers` | Set `is_active=False` for two users | Count is two and all three user-layer scopes are scheduled for both IDs. |
| `test_bulk_user_state_update_rejects_non_authorization_fields` | Pass `first_name="x"` | Raise `ValueError` before issuing SQL or invalidation. |

Patch `common.permission_cache.invalidation.bump_epoch_scopes`, call each wrapper inside `atomic()`, and assert no callback before commit.

- [ ] **Step 5: Implement explicit bulk mutation services**

Implement exact signatures:

```python
def bulk_create_memberships(
    memberships: Iterable[Vinculo],
    *,
    batch_size: int | None = None,
    ignore_conflicts: bool = False,
) -> list[Vinculo]
def bulk_update_memberships(
    memberships: Iterable[Vinculo],
    fields: Iterable[str],
    *,
    batch_size: int | None = None,
) -> int
def update_memberships(queryset: QuerySet[Vinculo], **changes: object) -> int
def guardian_bulk_assign(
    perm: Permission | str,
    user_or_group: Usuario | Group,
    objects: Iterable[models.Model],
    *,
    ignore_conflicts: bool = False,
) -> list[models.Model]
def guardian_assign_to_many(
    perm: Permission | str,
    users_or_groups: Iterable[Usuario | Group],
    obj: models.Model,
    *,
    ignore_conflicts: bool = False,
) -> list[models.Model]
def guardian_bulk_remove(
    perm: Permission | str,
    user_or_group: Usuario | Group,
    objects: Iterable[models.Model],
) -> tuple[int, dict[str, int]]
def guardian_remove_from_many(
    perm: Permission | str,
    users_or_groups: Iterable[Usuario | Group],
    obj: models.Model,
) -> tuple[int, dict[str, int]]
def bulk_create_permissions(
    permissions: Iterable[Permission],
    *,
    batch_size: int | None = None,
    ignore_conflicts: bool = False,
) -> list[Permission]
def bulk_update_permissions(
    permissions: Iterable[Permission],
    fields: Iterable[str],
    *,
    batch_size: int | None = None,
) -> int
def update_user_authorization_state(queryset: QuerySet[Usuario], **changes: bool) -> int
```

Rules for implementation:

- Materialize iterables once.
- Capture old user/object identifiers before mutation.
- Execute mutation inside `transaction.atomic(using=database_alias)`.
- Schedule deduplicated primitive scopes after the mutation and before leaving the transaction.
- Tenant wrappers invalidate old and new user IDs.
- Guardian wrappers derive each content type/PK and invalidate object scopes.
- Permission wrappers bump Django and Guardian layer scopes.
- User-state wrapper accepts only `is_active` and `is_superuser`, rejects other keys with `ValueError`, and bumps all three user layers.
- Never call `FLUSHDB`, `clear()`, or `delete_pattern()`.

- [ ] **Step 6: Add a repository guard test for direct bulk authorization writes**

Add a parametrized source scan in `test_mutations.py` over production `.py` files, excluding `migrations`, tests, and `common/permission_cache/mutations.py`. It must reject direct calls matching authorization managers followed by `.bulk_create(`, `.bulk_update(`, or `.update(` for `Vinculo`, Guardian object-permission models, `Permission`, and authorization state fields. Keep the allowlist empty initially so future violations fail loudly.

- [ ] **Step 7: Run Guardian/bulk tests and commit**

Run: `uv run pytest common/permission_cache/tests/test_guardian_signals.py common/permission_cache/tests/test_mutations.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/apps.py common/permission_cache/signals/guardian.py common/permission_cache/mutations.py common/permission_cache/tests
git commit -m "feat: invalidate bulk authorization mutations"
```

### Task 9: Add emergency operation and real Redis consistency tests

**Files:**

- Create: `apps/api/core/management/commands/invalidate_permission_cache.py`
- Create: `apps/api/core/tests/test_permission_cache_command.py`
- Create: `common/permission_cache/tests/test_redis_integration.py`
- Modify: `conftest.py`
- Modify: `.github/workflows/ci.yml:63-74`

**Interfaces:**

- Consumes: `bump_epoch_scopes((global_scope(),), database_alias=DEFAULT_DB_ALIAS, layer="django", raise_errors=True)`.
- Produces: `python manage.py invalidate_permission_cache`.
- Produces: `redis_permission_cache` fixture using a unique session prefix and real Redis.

- [ ] **Step 1: Write command tests**

Create:

```python
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest


def test_command_prints_only_new_global_epoch():
    stdout = StringIO()
    with patch(
        "apps.api.core.management.commands.invalidate_permission_cache.bump_epoch_scopes",
        return_value={"global": 9001},
    ) as bump:
        call_command("invalidate_permission_cache", stdout=stdout)

    assert stdout.getvalue().strip() == "9001"
    bump.assert_called_once_with(("global",), database_alias="default", layer="django", raise_errors=True)


def test_command_returns_nonzero_on_redis_failure():
    with patch(
        "apps.api.core.management.commands.invalidate_permission_cache.bump_epoch_scopes",
        side_effect=ConnectionError("redis down"),
    ):
        with pytest.raises(CommandError, match="Não foi possível invalidar o cache de permissões."):
            call_command("invalidate_permission_cache")
```

- [ ] **Step 2: Run command tests RED**

Run: `uv run pytest apps/api/core/tests/test_permission_cache_command.py -q`

Expected: FAIL because the command is undiscoverable.

- [ ] **Step 3: Implement the O(1) global invalidation command**

`handle()` calls:

```python
changed = bump_epoch_scopes(
    (global_scope(),),
    database_alias=DEFAULT_DB_ALIAS,
    layer="django",
    raise_errors=True,
)
self.stdout.write(str(changed[global_scope()]))
```

Catch cache/connection exceptions and raise `CommandError("Não foi possível invalidar o cache de permissões.")` without leaking hostnames, keys, users, permissions, or tokens.

- [ ] **Step 4: Add an isolated real-Redis fixture**

In `conftest.py`, build a fixture that:

- Reads `AUTHORIZATION_REDIS_URL` or derives `redis://REDIS_HOST:REDIS_PORT/15` for tests.
- Uses `override_settings` to replace only `CACHES["permissions"]`.
- Sets a random `KEY_PREFIX` such as `authz:test:<uuid4 hex>`.
- Calls `caches.close_all()` before and after.
- Deletes only keys with that unique prefix during teardown; never flushes the database.
- Pings Redis and calls `pytest.skip("Redis real indisponível")` only outside CI; when `CI` is present, connection failure must fail.

- [ ] **Step 5: Write real Redis and concurrency tests**

In `test_redis_integration.py`, mark tests `django_db(transaction=True)` and begin with the atomicity proof:

```python
from concurrent.futures import ThreadPoolExecutor

from django_redis import get_redis_connection

from common.permission_cache.epochs import EpochStore


@pytest.mark.django_db(transaction=True)
def test_concurrent_epoch_increments_are_atomic(redis_permission_cache):
    store = EpochStore()
    initial = store.read(("global",), "default")[0]

    with ThreadPoolExecutor(max_workers=8) as executor:
        returned = list(executor.map(lambda _: EpochStore().bump("global", "default"), range(32)))

    assert len(set(returned)) == 32
    assert store.read(("global",), "default") == (initial + 32,)
```

Add these deterministic cases:

| Test | Mechanism | Required assertion |
| --- | --- | --- |
| `test_two_spawned_processes_observe_same_epoch` | A module-level worker creates `redis.Redis.from_url(fixture_url)`, increments the prefixed raw epoch key, and returns through `multiprocessing.get_context("spawn").Queue()` | The parent and a second spawned worker observe the incremented value; both child exit codes are zero. |
| `test_two_independent_django_clients_observe_same_snapshot` | Build two `PermissionCacheStore` instances after `caches.close_all()` between them | Second instance is a cache hit and its loader is not called. |
| `test_snapshot_ttl_is_1800_seconds` | Resolve once, obtain physical key with `cache.make_key(logical_key)`, call raw Redis `ttl()` | `1798 <= ttl <= 1800`; epoch key has TTL `-1`. |
| `test_isolated_epoch_loss_cannot_resurrect_snapshot` | Cache `"old"`, delete only the physical epoch key, resolve with loader returning `"new"` | New seed differs and `"old"` is never returned. |
| `test_late_writer_under_old_epoch_is_unreachable` | Pause loader with `threading.Event`, bump epoch, release loader | A new store resolves current DB value and never sees the late old value. |
| `test_reader_retries_when_writer_commits_during_recomposition` | Bump between the store’s first and second epoch reads | Loader runs twice and only the second result is reachable. |
| `test_failed_invalidation_window_is_bounded_by_ttl` | Patch bump to fail after an old snapshot, mutate DB, restore Redis | Old value can be read before expiry; after deleting that snapshot to emulate TTL expiry, resolver returns current DB value. |
| `test_cache_outage_falls_back_to_database` | Override alias to unused port with 50 ms socket timeouts | Read and write failures return loader result; no permission is granted by exception. |
| `test_permissions_and_cachalot_aliases_are_isolated` | Assert production settings locations end in `/4` and `/3`; write same logical key through both aliases | Each alias returns its own value and invalidating epochs does not change Cachalot’s value. |
| `test_permissions_alias_has_one_primary_location` | Inspect the Redis fixture alias settings | `LOCATION` is one URL string, not a list/sentinel configuration, and no replica/read client option exists. |
| `test_global_epoch_changes_every_layer_key` | Build logical keys for Django, tenant, and Guardian before/after `bump("global")` | All three keys change while the identity components remain constant. |
| `test_redis_overflow_is_reported_without_reset` | Set the physical global epoch to `2**63 - 1`, then call `EpochStore.bump()` | Redis raises `ResponseError`; the value remains max and is never reset to zero. |

Late-writer tests must use `Event.wait(timeout=5)` and join every thread with a five-second timeout so a regression fails instead of hanging the suite.

- [ ] **Step 6: Make CI exercise Redis `/4` explicitly**

Add to the test job environment:

```yaml
AUTHORIZATION_REDIS_URL: redis://127.0.0.1:6379/4
AUTHORIZATION_CACHE_KEY_PREFIX: authz:v1:ci
```

Do not add a new service; the workflow already provisions Redis 7.

- [ ] **Step 7: Run operational and Redis tests GREEN and commit**

Run:

```bash
uv run pytest apps/api/core/tests/test_permission_cache_command.py common/permission_cache/tests/test_redis_integration.py -q
```

Expected: PASS with local Redis running; no test calls `FLUSHDB`.

```bash
git add conftest.py .github/workflows/ci.yml apps/api/core/management/commands/invalidate_permission_cache.py apps/api/core/tests/test_permission_cache_command.py common/permission_cache/tests/test_redis_integration.py
git commit -m "feat: add permission cache recovery operation"
```

### Task 10: Verify observability, compatibility, documentation, and the complete repository

**Files:**

- Modify: `common/permission_cache/tests/test_metrics.py`
- Modify: `apps/api/core/tests/test_metrics.py`
- Modify: `apps/api/autenticacao/tests/test_permission_cache.py`
- Modify: `apps/organizacoes/tests/test_permission_cache.py`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/explanation/autenticacao.md`

**Interfaces:**

- Consumes: every previous task.
- Produces: executable acceptance coverage and operator/user documentation.

- [ ] **Step 1: Complete observability acceptance tests**

Assert the Prometheus exposition contains:

```text
authorization_cache_operations_total
authorization_cache_invalidations_total
authorization_cache_fallback_total
authorization_cache_resolve_seconds
```

Also assert:

- Existing `/metrics` network/token authorization tests remain unchanged.
- Invalid layer/outcome/reason/source raises before label creation.
- Metric text never contains a sample user ID, organization slug, codename, content type, or object PK.
- Cache exception logs contain `authorization_layer` and exception class but no encoded payload or PII.

- [ ] **Step 2: Add cross-context compatibility tests**

Add cases proving:

- Direct `user.has_perm()` works with cache enabled and disabled.
- DRF `CustomDjangoModelPermissions` uses the cached backend transparently.
- Django admin’s `has_module_perms()` preserves behavior.
- A Celery eager task reloads a user and receives the same permission result.
- Calling resolvers from a management-command context requires no request.
- Rules OR semantics still permit when another backend grants.
- RLS context receives exactly `request.tenant.organization_id`.

- [ ] **Step 3: Run focused acceptance suite**

Run:

```bash
uv run pytest \
  common/permission_cache/tests \
  apps/api/autenticacao/tests/test_permission_cache.py \
  apps/organizacoes/tests/test_permission_cache.py \
  apps/api/core/tests/test_permission_cache_command.py \
  apps/api/core/tests/test_metrics.py -q
```

Expected: PASS.

- [ ] **Step 4: Update durable documentation**

In `docs/explanation/autenticacao.md`, document:

- the four covered fact families;
- `request.tenant` fields;
- 30-minute TTL and immediate epoch invalidation;
- database fallback and accepted outage window;
- prohibition on direct bulk APIs and names of official wrappers;
- relationship with Cachalot;
- kill switch and `invalidate_permission_cache` command.

In `docs/ROADMAP.md`, mark the current permission-cache item complete and retain unchanged the separate future item:

```text
Após o modelo de checkout/planos estar pronto, cache de entitlements para decidir se o plano do usuário libera cada endpoint/feature.
```

- [ ] **Step 5: Run static, migration, documentation, and security guards**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run mkdocs build --strict
rg -n 'FLUSHDB|flushdb|delete_pattern|_perm_cache|request\.(organizacao|vinculo)' common apps --glob '*.py'
```

Expected: every command succeeds; the final search has no production use except assertions explicitly testing absence.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`

Expected: PASS against PostgreSQL and Redis, including the existing RLS tests.

- [ ] **Step 7: Review coverage and final diff**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~9
```

Inspect `coverage.xml` and confirm new code is at least 80% covered. Confirm no migrations or dependency lockfile changes are present.

- [ ] **Step 8: Commit documentation and acceptance coverage**

```bash
git add docs/ROADMAP.md docs/explanation/autenticacao.md common/permission_cache/tests/test_metrics.py apps/api/core/tests/test_metrics.py apps/api/autenticacao/tests/test_permission_cache.py apps/organizacoes/tests/test_permission_cache.py
git commit -m "docs: document permission cache operations"
```

- [ ] **Step 9: Perform final branch verification**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
```

Expected: clean `feature/permission-cache` worktree with the design commits followed by the implementation commits listed in this plan.
