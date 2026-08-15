# Built-in MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar `apps.api.mcp` como servidor MCP built-in com a tool `health`, `stdio` local e Streamable HTTP protegido por OIDC em produção.

**Architecture:** Uma única factory constrói `MCPServer` e registra as tools. `__main__.py` adapta essa factory a `stdio`; `asgi.py` a adapta a Streamable HTTP e OAuth. As verificações de banco/cache vivem num módulo profundo do `core`, reutilizado pelo readiness REST e pela tool MCP.

**Tech Stack:** Python 3.12, Django 5.2, `mcp>=2.0.0,<3`, PyJWT 2.13+, Pydantic, Starlette/Uvicorn, PostgreSQL, Redis, Docker Compose, nginx, pytest/pytest-django.

## Global Constraints

- Usar `from mcp.server import MCPServer`; não usar `FastMCP`.
- Declarar `mcp>=2.0.0,<3` e `PyJWT[crypto]>=2.13.0,<3` em `[project].dependencies`; não instalar `mcp[cli]`.
- Manter uma única `create_mcp_server()` e um único registro da tool `health`.
- Executar Streamable HTTP somente com OAuth completo; configuração incompleta falha no startup.
- Executar `stdio` sem OAuth, reservando `stdin`/`stdout` ao protocolo e logs a `stderr`.
- Não duplicar SQL ou round-trip de cache no app MCP; essas operações pertencem a `apps.api.core`.
- Nunca devolver mensagem de exceção, URL, host, credencial ou trace pela interface MCP.
- Verificar todos os aliases de `DATABASES` e `CACHES`, mesmo depois de falha parcial.
- Fechar conexões de banco e tentar cleanup de cache em `finally`.
- Manter a API REST no Gunicorn WSGI atual; o MCP HTTP roda em processo ASGI separado.
- Não publicar a porta do container MCP; somente nginx/LB alcança o serviço.
- Usar os helpers de `utils/env.py` para toda configuração versionada.
- Seguir TDD: cada mudança comportamental começa por um teste que falha pela razão esperada.

---

## File map

| Arquivo | Responsabilidade |
|---|---|
| `pyproject.toml`, `uv.lock` | SDK MCP e JWT no runtime da imagem. |
| `apps/api/mcp/` | App Django, factory MCP e adapters de transporte/autenticação. |
| `apps/api/core/dependency_health.py` | Interface profunda para checar todos os aliases de DB/cache e sanitizar resultados. |
| `apps/api/core/health_check.py` | Views REST existentes, reutilizando os novos primitivos. |
| `utils/env.py`, `api/settings.py`, `.env.example` | Configuração tipada do servidor HTTP/OIDC. |
| `docker-compose.yml`, `Makefile` | Processo MCP opcional no profile `mcp`. |
| `docker/nginx/sites/production/mcp.conf` | Hostname MCP e proxy para `/mcp`/RFC 9728. |
| `docs/how-to/mcp.md` | Uso local, configuração OIDC e deploy. |
| `docs/reference/estrutura-de-diretorios.md`, `mkdocs.yml`, `README.md` | Descoberta e localidade da capacidade MCP. |

### Task 1: Add runtime dependencies and scaffold `apps.api.mcp`

**Files:**
- Create: `tests/architecture/test_mcp_runtime.py`
- Create: `apps/api/mcp/` via `start_api_app`
- Modify: `api/settings.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: `BUSINESS_APPS` and the project app generator.
- Produces: importable installed app `apps.api.mcp`; runtime imports `mcp` and `jwt`.

- [ ] **Step 1: Write the failing architecture tests**

```python
from pathlib import Path

from packaging.requirements import Requirement

from django.conf import settings

import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_mcp_app_is_installed():
    assert "apps.api.mcp" in settings.BUSINESS_APPS


def test_mcp_dependencies_are_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {Requirement(value).name.lower() for value in project["dependencies"]}

    assert {"mcp", "pyjwt"} <= names
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest --nomigrations tests/architecture/test_mcp_runtime.py -q
```

Expected: two failures because `apps.api.mcp` and both runtime dependencies are absent.

- [ ] **Step 3: Generate the app through the repository command**

Run, as separate commands:

```bash
mkdir -p apps/api/mcp
uv run python manage.py start_api_app mcp apps/api/mcp
```

Expected: `apps.api.mcp` is inserted alphabetically into `BUSINESS_APPS` and the canonical app skeleton is created.

- [ ] **Step 4: Add the runtime dependencies**

Run:

```bash
uv add 'mcp>=2.0.0,<3' 'PyJWT[crypto]>=2.13.0,<3'
```

Expected: both `pyproject.toml` and `uv.lock` change; `mcp[cli]` is absent.

- [ ] **Step 5: Verify GREEN and imports**

Run:

```bash
uv run pytest --nomigrations tests/architecture/test_mcp_runtime.py -q
uv run python -c 'from mcp.server import MCPServer; import jwt; print(MCPServer.__name__, jwt.__version__)'
```

Expected: `2 passed`; the import command prints `MCPServer` and a PyJWT version `>=2.13.0,<3`.

- [ ] **Step 6: Commit**

```bash
git add tests/architecture/test_mcp_runtime.py apps/api/mcp api/settings.py pyproject.toml uv.lock
git commit -m "build: add MCP runtime and app"
```

### Task 2: Extract the shared dependency-health module

**Files:**
- Create: `apps/api/core/dependency_health.py`
- Create: `apps/api/core/tests/test_dependency_health.py`
- Modify: `apps/api/core/health_check.py`
- Test: `apps/api/core/tests/test_health_check.py`

**Interfaces:**
- Produces: `check_database(alias: str) -> None`, `check_cache(alias: str) -> None`, `collect_dependency_health() -> DependencyHealthReport`.
- Produces types: `HealthyCheck`, `UnhealthyCheck`, `CheckResult`, `DependencyHealthReport`.

- [ ] **Step 1: Write failing collector and cleanup tests**

```python
from contextlib import nullcontext

from django.test import override_settings

from apps.api.core import dependency_health as modulo


@override_settings(
    DATABASES={"default": {}, "logging": {}},
    CACHES={"default": {}, "permissions": {}},
)
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
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run pytest --nomigrations apps/api/core/tests/test_dependency_health.py -q
```

Expected: collection fails because `apps.api.core.dependency_health` does not exist.

- [ ] **Step 3: Implement the deep module**

Create `apps/api/core/dependency_health.py`:

```python
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
    key = f"mcp-health:{uuid4().hex}"
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
            backend.delete(key)
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
```

- [ ] **Step 4: Reuse the primitives from REST readiness**

In `apps/api/core/health_check.py`, import `check_cache` and `check_database`; replace `_checar_banco()` and `_checar_cache()` bodies with:

```python
def _checar_banco():
    check_database("default")


def _checar_cache():
    check_cache("default")
```

Remove the now-unused imports `cache`, `connection`, and `CHAVE_CACHE_HEALTH`.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
uv run pytest --nomigrations apps/api/core/tests/test_dependency_health.py apps/api/core/tests/test_health_check.py -q
```

Expected: all focused tests pass and the existing REST response contract remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add apps/api/core/dependency_health.py apps/api/core/health_check.py apps/api/core/tests/test_dependency_health.py
git commit -m "feat(core): share dependency health checks"
```

### Task 3: Build the single MCP server and structured `health` tool

**Files:**
- Create: `apps/api/mcp/server.py`
- Create: `apps/api/mcp/tools.py`
- Create: `apps/api/mcp/tests/test_server.py`

**Interfaces:**
- Consumes: `collect_dependency_health() -> DependencyHealthReport`.
- Produces: `create_mcp_server(*, token_verifier: TokenVerifier | None = None, auth: AuthSettings | None = None) -> MCPServer`.

- [ ] **Step 1: Write the failing in-memory protocol test**

```python
import pytest
from mcp import Client

from apps.api.mcp import tools
from apps.api.mcp.server import create_mcp_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_is_discoverable_and_returns_structured_content(monkeypatch):
    expected = {
        "status": "healthy",
        "checks": {
            "databases": {"default": {"status": "healthy"}},
            "caches": {"default": {"status": "healthy"}},
        },
    }
    monkeypatch.setattr(tools, "collect_dependency_health", lambda: expected)

    async with Client(create_mcp_server(), raise_exceptions=True) as client:
        discovered = await client.list_tools()
        result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert discovered.tools[0].output_schema["type"] == "object"
    assert result.is_error is False
    assert result.structured_content == expected


@pytest.mark.anyio
async def test_health_hides_unexpected_adapter_error(monkeypatch):
    def fail():
        raise ValueError("secret database URL")

    monkeypatch.setattr(tools, "collect_dependency_health", fail)

    async with Client(create_mcp_server()) as client:
        result = await client.call_tool("health")

    assert result.is_error is True
    assert "Health check failed" in str(result.content)
    assert "secret database URL" not in str(result.content)
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_server.py -q
```

Expected: import failure because `server.py` and `tools.py` do not exist.

- [ ] **Step 3: Implement tool registration**

Create `apps/api/mcp/tools.py`:

```python
import logging

from mcp.server import MCPServer

from apps.api.core.dependency_health import DependencyHealthReport, collect_dependency_health

logger = logging.getLogger(__name__)


def register_tools(server: MCPServer) -> None:
    @server.tool(name="health", structured_output=True)
    def health() -> DependencyHealthReport:
        try:
            return collect_dependency_health()
        except Exception:
            logger.exception("Unexpected MCP health adapter failure")
            raise RuntimeError("Health check failed") from None
```

Create `apps/api/mcp/server.py`:

```python
from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .tools import register_tools


def create_mcp_server(
    *,
    token_verifier: TokenVerifier | None = None,
    auth: AuthSettings | None = None,
) -> MCPServer:
    server = MCPServer(
        name="django-api",
        instructions="Operational tools for the Django API.",
        token_verifier=token_verifier,
        auth=auth,
    )
    register_tools(server)
    return server
```

- [ ] **Step 4: Run and verify GREEN**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_server.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/mcp/server.py apps/api/mcp/tools.py apps/api/mcp/tests/test_server.py
git commit -m "feat(mcp): expose structured health tool"
```

### Task 4: Add the local `stdio` adapter and subprocess smoke test

**Files:**
- Create: `apps/api/mcp/bootstrap.py`
- Create: `apps/api/mcp/__main__.py`
- Create: `apps/api/mcp/tests/settings.py`
- Create: `apps/api/mcp/tests/test_stdio.py`

**Interfaces:**
- Produces: `setup_django() -> None` and command `python -m apps.api.mcp`.
- Consumes: `create_mcp_server()` with no OAuth arguments.

- [ ] **Step 1: Write the failing subprocess test**

Create `apps/api/mcp/tests/settings.py`:

```python
from api.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
```

Create `apps/api/mcp/tests/test_stdio.py`:

```python
import io
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stdio_boots_django_and_calls_health():
    stderr = io.StringIO()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apps.api.mcp"],
        env={"DJANGO_SETTINGS_MODULE": "apps.api.mcp.tests.settings"},
    )

    async with Client(stdio_client(parameters, errlog=stderr), raise_exceptions=True) as client:
        discovered = await client.list_tools()
        result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert result.is_error is False
    assert result.structured_content["status"] == "healthy"
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_stdio.py -q
```

Expected: subprocess exits because `apps.api.mcp.__main__` is absent.

- [ ] **Step 3: Implement bootstrap and entrypoint**

Create `apps/api/mcp/bootstrap.py`:

```python
import os

import django
import dotenv


def setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")
    dotenv.load_dotenv(".env", override=False)
    django.setup()
```

Create `apps/api/mcp/__main__.py`:

```python
from .bootstrap import setup_django


def main() -> None:
    setup_django()

    from .server import create_mcp_server

    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run and verify GREEN**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_stdio.py apps/api/mcp/tests/test_server.py -q
```

Expected: both tests pass. A successful protocol exchange proves that ordinary bootstrap output did not corrupt `stdout`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/mcp/bootstrap.py apps/api/mcp/__main__.py apps/api/mcp/tests/settings.py apps/api/mcp/tests/test_stdio.py
git commit -m "feat(mcp): add stdio transport"
```

### Task 5: Implement OIDC/JWKS token verification and settings

**Files:**
- Create: `apps/api/mcp/authentications.py`
- Create: `apps/api/mcp/tests/test_authentications.py`
- Modify: `utils/env.py`
- Modify: `api/settings.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `OidcTokenVerifier.verify_token(token: str) -> AccessToken | None`.
- Produces: `build_http_auth() -> tuple[TokenVerifier, AuthSettings]`.
- Consumes settings: `MCP_SERVER_URL`, `MCP_AUTH_ISSUER_URL`, `MCP_AUTH_AUDIENCE`, `MCP_AUTH_JWKS_URL`, `MCP_AUTH_ALGORITHMS`.

- [ ] **Step 1: Write failing verifier tests with a real RSA signature**

```python
import time

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from apps.api.mcp.authentications import OidcTokenVerifier, build_http_auth


class StaticJwkClient:
    def __init__(self, public_key):
        self.signing_key = jwt.PyJWK.from_json(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))

    def get_signing_key_from_jwt(self, token):
        return self.signing_key


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def make_token(private_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://auth.example.com",
        "aud": "https://mcp.example.com/mcp",
        "exp": now + 300,
        "nbf": now - 1,
        "client_id": "test-client",
        "sub": "user-1",
        "scope": "health:read",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.mark.anyio
async def test_accepts_valid_oidc_token(key_pair):
    private_key, public_key = key_pair
    verifier = OidcTokenVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=["RS256"],
        jwks_client=StaticJwkClient(public_key),
    )

    access = await verifier.verify_token(make_token(private_key))

    assert access is not None
    assert access.client_id == "test-client"
    assert access.scopes == ["health:read"]
    assert access.resource == "https://mcp.example.com/mcp"


@pytest.mark.anyio
async def test_rejects_wrong_audience_without_leaking_token(key_pair):
    private_key, public_key = key_pair
    verifier = OidcTokenVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=["RS256"],
        jwks_client=StaticJwkClient(public_key),
    )

    access = await verifier.verify_token(make_token(private_key, aud="https://other.example.com"))

    assert access is None


@override_settings(
    IN_PRODUCTION=True,
    MCP_SERVER_URL="http://mcp.example.com/mcp",
    MCP_AUTH_ISSUER_URL="https://auth.example.com",
    MCP_AUTH_AUDIENCE="https://mcp.example.com/mcp",
    MCP_AUTH_JWKS_URL="https://auth.example.com/.well-known/jwks.json",
    MCP_AUTH_ALGORITHMS=["RS256"],
)
def test_http_auth_rejects_insecure_production_url():
    with pytest.raises(ImproperlyConfigured, match="HTTPS"):
        build_http_auth()


@override_settings(
    IN_PRODUCTION=False,
    MCP_SERVER_URL="http://127.0.0.1:8001/mcp",
    MCP_AUTH_ISSUER_URL="http://127.0.0.1:9000",
    MCP_AUTH_AUDIENCE="http://127.0.0.1:8001/mcp",
    MCP_AUTH_JWKS_URL="http://127.0.0.1:9000/jwks",
    MCP_AUTH_ALGORITHMS=["HS256"],
)
def test_http_auth_rejects_symmetric_algorithm():
    with pytest.raises(ImproperlyConfigured, match="asymmetric"):
        build_http_auth()
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_authentications.py -q
```

Expected: import failure because `authentications.py` does not exist.

- [ ] **Step 3: Add typed environment names and Django settings**

Add these names to both `ENVS` and `EnviromentVar` in `utils/env.py`:

```python
"MCP_SERVER_URL",
"MCP_AUTH_ISSUER_URL",
"MCP_AUTH_AUDIENCE",
"MCP_AUTH_JWKS_URL",
"MCP_AUTH_ALGORITHMS",
"MCP_ALLOWED_HOSTS",
"MCP_ALLOWED_ORIGINS",
```

Add to `api/settings.py`:

```python
MCP_SERVER_URL = get_env_var("MCP_SERVER_URL")
MCP_AUTH_ISSUER_URL = get_env_var("MCP_AUTH_ISSUER_URL")
MCP_AUTH_AUDIENCE = get_env_var("MCP_AUTH_AUDIENCE")
MCP_AUTH_JWKS_URL = get_env_var("MCP_AUTH_JWKS_URL")
MCP_AUTH_ALGORITHMS = get_list_from_env("MCP_AUTH_ALGORITHMS", ["RS256"])
MCP_ALLOWED_HOSTS = get_list_from_env("MCP_ALLOWED_HOSTS")
MCP_ALLOWED_ORIGINS = get_list_from_env("MCP_ALLOWED_ORIGINS")
```

Add an `MCP` section to `.env.example` containing the seven variables, with empty provider values, `MCP_AUTH_ALGORITHMS=RS256`, and comments that HTTP fails closed while `stdio` does not need them.

- [ ] **Step 4: Implement the verifier and auth builder**

Create `apps/api/mcp/authentications.py`:

```python
import logging
from typing import Protocol
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import anyio
import jwt
from jwt.exceptions import PyJWKClientError, PyJWTError
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

logger = logging.getLogger(__name__)

ASYMMETRIC_JWT_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"})


class JwkClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        raise NotImplementedError


class OidcTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: list[str],
        jwks_client: JwkClient | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms
        self.jwks_client = jwks_client or jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)

    def _decode(self, token: str) -> dict[str, object]:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key,
            algorithms=self.algorithms,
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iss", "aud"], "strict_aud": True},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
            client_id = claims.get("client_id") or claims.get("azp")
            if not isinstance(client_id, str):
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                return None
            subject = claims.get("sub")
            expires_at = claims.get("exp")
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=scope.split(),
                expires_at=int(expires_at) if isinstance(expires_at, int | float) else None,
                resource=self.audience,
                subject=subject if isinstance(subject, str) else None,
                claims=claims,
            )
        except (PyJWTError, PyJWKClientError, TypeError, ValueError) as exc:
            logger.warning("MCP bearer token rejected", extra={"error_type": type(exc).__name__})
            return None


def build_http_auth() -> tuple[TokenVerifier, AuthSettings]:
    values = {
        "MCP_SERVER_URL": settings.MCP_SERVER_URL,
        "MCP_AUTH_ISSUER_URL": settings.MCP_AUTH_ISSUER_URL,
        "MCP_AUTH_AUDIENCE": settings.MCP_AUTH_AUDIENCE,
        "MCP_AUTH_JWKS_URL": settings.MCP_AUTH_JWKS_URL,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ImproperlyConfigured(f"Missing MCP HTTP settings: {', '.join(missing)}")
    if not settings.MCP_AUTH_ALGORITHMS:
        raise ImproperlyConfigured("MCP_AUTH_ALGORITHMS must not be empty")
    unsupported = set(settings.MCP_AUTH_ALGORITHMS) - ASYMMETRIC_JWT_ALGORITHMS
    if unsupported:
        raise ImproperlyConfigured("MCP_AUTH_ALGORITHMS must contain only supported asymmetric algorithms")
    if settings.IN_PRODUCTION:
        insecure = [name for name, value in values.items() if urlsplit(value).scheme != "https"]
        if insecure:
            raise ImproperlyConfigured(f"MCP HTTP settings must use HTTPS in production: {', '.join(insecure)}")

    verifier = OidcTokenVerifier(
        issuer=settings.MCP_AUTH_ISSUER_URL,
        audience=settings.MCP_AUTH_AUDIENCE,
        jwks_url=settings.MCP_AUTH_JWKS_URL,
        algorithms=settings.MCP_AUTH_ALGORITHMS,
    )
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(settings.MCP_AUTH_ISSUER_URL),
        resource_server_url=AnyHttpUrl(settings.MCP_SERVER_URL),
        required_scopes=["health:read"],
    )
    return verifier, auth
```

- [ ] **Step 5: Run and verify GREEN**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_authentications.py -q
uv run ruff check apps/api/mcp/authentications.py apps/api/mcp/tests/test_authentications.py utils/env.py api/settings.py
```

Expected: verifier tests and Ruff pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/mcp/authentications.py apps/api/mcp/tests/test_authentications.py utils/env.py api/settings.py .env.example
git commit -m "feat(mcp): validate OIDC bearer tokens"
```

### Task 6: Add the authenticated Streamable HTTP ASGI adapter

**Files:**
- Create: `apps/api/mcp/asgi.py`
- Create: `apps/api/mcp/tests/test_http.py`

**Interfaces:**
- Produces: `create_asgi_app(*, token_verifier: TokenVerifier | None = None, auth: AuthSettings | None = None, allowed_hosts: list[str] | None = None, allowed_origins: list[str] | None = None) -> ASGIApp` for Uvicorn `--factory`.
- Consumes: `create_mcp_server()`, `build_http_auth()`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`.

- [ ] **Step 1: Write failing HTTP transport tests**

Create `apps/api/mcp/tests/test_http.py`:

```python
import socket
import time
from threading import Thread

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from apps.api.mcp import tools
from apps.api.mcp.asgi import create_asgi_app


class StaticTokenVerifier:
    async def verify_token(self, token):
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=["health:read"],
            resource="http://127.0.0.1/mcp",
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def http_server(monkeypatch):
    monkeypatch.setattr(
        tools,
        "collect_dependency_health",
        lambda: {
            "status": "healthy",
            "checks": {
                "databases": {"default": {"status": "healthy"}},
                "caches": {"default": {"status": "healthy"}},
            },
        },
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    auth = AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        resource_server_url=AnyHttpUrl(f"{base_url}/mcp"),
        required_scopes=["health:read"],
    )
    app = create_asgi_app(
        token_verifier=StaticTokenVerifier(),
        auth=auth,
        allowed_hosts=[f"127.0.0.1:{port}"],
        allowed_origins=["https://client.example"],
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    assert not thread.is_alive()


@pytest.mark.anyio
async def test_http_calls_the_same_health_tool(http_server):
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer valid-token"},
        follow_redirects=True,
    ) as http_client:
        transport = streamable_http_client(f"{http_server}/mcp", http_client=http_client)
        async with Client(transport, raise_exceptions=True) as client:
            discovered = await client.list_tools()
            result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert result.is_error is False
    assert result.structured_content["status"] == "healthy"


def test_http_requires_bearer_token(http_server):
    response = httpx.post(f"{http_server}/mcp", json={})
    assert response.status_code == 401


def test_liveness_is_public_and_contains_no_dependency_details(http_server):
    response = httpx.get(f"{http_server}/live")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_rejects_untrusted_host(http_server):
    response = httpx.post(f"{http_server}/mcp", headers={"Host": "evil.example"}, json={})
    assert response.status_code == 421


def test_rejects_untrusted_origin(http_server):
    response = httpx.post(
        f"{http_server}/mcp",
        headers={"Authorization": "Bearer valid-token", "Origin": "https://evil.example"},
        json={},
    )
    assert response.status_code == 403


def test_publishes_protected_resource_metadata(http_server):
    response = httpx.get(f"{http_server}/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json()["resource"] == f"{http_server}/mcp"
    assert response.json()["authorization_servers"] == ["https://auth.example.com/"]


def test_trusted_browser_origin_receives_cors_headers(http_server):
    response = httpx.options(
        f"{http_server}/mcp",
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,mcp-protocol-version",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example"
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_http.py -q
```

Expected: import failure because `apps.api.mcp.asgi` does not exist.

- [ ] **Step 3: Implement the ASGI factory**

Create `apps/api/mcp/asgi.py`:

```python
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from .authentications import build_http_auth
from .server import create_mcp_server


def create_asgi_app(
    *,
    token_verifier: TokenVerifier | None = None,
    auth: AuthSettings | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    if token_verifier is None and auth is None:
        token_verifier, auth = build_http_auth()
    elif token_verifier is None or auth is None:
        raise ImproperlyConfigured("token_verifier and auth must be supplied together")

    hosts = settings.MCP_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    origins = settings.MCP_ALLOWED_ORIGINS if allowed_origins is None else allowed_origins
    if not hosts:
        raise ImproperlyConfigured("MCP_ALLOWED_HOSTS must not be empty")

    server = create_mcp_server(token_verifier=token_verifier, auth=auth)

    @server.custom_route("/live", methods=["GET"], include_in_schema=False)
    async def live(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host="0.0.0.0",
    )
    if not origins:
        return app
    return CORSMiddleware(
        app,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Mcp-Protocol-Version", "Mcp-Session-Id"],
        expose_headers=["Mcp-Session-Id"],
    )
```

- [ ] **Step 4: Run and verify GREEN**

```bash
uv run pytest --nomigrations apps/api/mcp/tests/test_http.py apps/api/mcp/tests/test_server.py apps/api/mcp/tests/test_stdio.py -q
```

Expected: all three adapters/contract test modules pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/mcp/asgi.py apps/api/mcp/tests/test_http.py
git commit -m "feat(mcp): serve authenticated streamable HTTP"
```

### Task 7: Add the optional production process and nginx proxy

**Files:**
- Create: `tests/architecture/test_mcp_deployment.py`
- Create: `docker/nginx/sites/production/mcp.conf`
- Modify: `docker-compose.yml`
- Modify: `Makefile`

**Interfaces:**
- Produces: Compose profile `mcp`, internal port `8001`, `/live` healthcheck, public host `mcp.localhost` through nginx.
- Consumes: `apps.api.mcp.asgi:create_asgi_app` as a Uvicorn factory.

- [ ] **Step 1: Write failing deployment-shape tests**

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_mcp_compose_service_is_internal_and_opt_in():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["mcp"]

    assert service["profiles"] == ["mcp"]
    assert "ports" not in service
    assert service["expose"] == ["8001"]
    assert "--factory" in service["command"]
    assert service["healthcheck"]["test"][-1] == "http://127.0.0.1:8001/live"


def test_nginx_uses_late_dns_and_does_not_publish_liveness():
    config = (ROOT / "docker/nginx/sites/production/mcp.conf").read_text(encoding="utf-8")

    assert "server mcp:8001 resolve;" in config
    assert "server_name mcp.localhost;" in config
    assert "location = /mcp" in config
    assert "oauth-protected-resource/mcp" in config
    assert "/live" not in config
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest --nomigrations tests/architecture/test_mcp_deployment.py -q
```

Expected: missing `mcp` service and missing nginx file.

- [ ] **Step 3: Add the Compose service**

Add this service beside `web`:

```yaml
  mcp:
    profiles: ["mcp"]
    build: .
    command:
      - uvicorn
      - apps.api.mcp.asgi:create_asgi_app
      - --factory
      - --host
      - 0.0.0.0
      - --port
      - "8001"
      - --workers
      - "1"
    env_file: .env
    environment:
      DATABASE_HOST: db
      REDIS_HOST: redis
    expose:
      - "8001"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8001/live"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 4: Add the nginx MCP virtual host**

Create `docker/nginx/sites/production/mcp.conf`:

```nginx
upstream mcp_backend {
    zone mcp_backend 64k;
    resolver 127.0.0.11 valid=10s ipv6=off;
    server mcp:8001 resolve;
    keepalive 16;
}

server {
    listen 80;
    server_name mcp.localhost;

    access_log /var/log/nginx/mcp_access.log main;
    error_log /var/log/nginx/mcp_error.log warn;
    include /etc/nginx/snippets/security-headers.conf;

    location = /mcp {
        include /etc/nginx/snippets/proxy.conf;
        proxy_pass http://mcp_backend;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location = /.well-known/oauth-protected-resource/mcp {
        include /etc/nginx/snippets/proxy.conf;
        proxy_pass http://mcp_backend;
    }

    location / {
        return 404;
    }
}
```

- [ ] **Step 5: Add Make targets**

Add `mcp-up` and `mcp-down` to `.PHONY`, then:

```make
mcp-up: ## Sobe o MCP HTTP e o nginx (requer OIDC configurado)
	docker compose --profile mcp up -d --build mcp nginx

mcp-down: ## Para o MCP HTTP sem afetar a API
	docker compose --profile mcp stop mcp
```

- [ ] **Step 6: Verify deployment configuration**

```bash
uv run pytest --nomigrations tests/architecture/test_mcp_deployment.py -q
docker compose --profile mcp config --quiet
make nginx-test
```

Expected: architecture tests pass, Compose exits zero, both nginx environments pass `nginx -t`.

- [ ] **Step 7: Build the production image and prove runtime imports**

```bash
docker build -t drf-base-api-mcp:test .
docker run --rm drf-base-api-mcp:test python -c 'from mcp.server import MCPServer; import jwt; print(MCPServer.__name__, jwt.__version__)'
```

Expected: image builds with `--no-dev`; runtime imports succeed.

- [ ] **Step 8: Commit**

```bash
git add tests/architecture/test_mcp_deployment.py docker/nginx/sites/production/mcp.conf docker-compose.yml Makefile
git commit -m "feat(mcp): add production service and proxy"
```

### Task 8: Document local use, OIDC and production deployment

**Files:**
- Create: `docs/how-to/mcp.md`
- Modify: `docs/reference/estrutura-de-diretorios.md`
- Modify: `docs/how-to/proxy-nginx.md`
- Modify: `mkdocs.yml`
- Modify: `README.md`

**Interfaces:**
- Documents commands: local `stdio`, `make mcp-up`, `make mcp-down`.
- Documents public contract: `/mcp`, scope `health:read`, hostname/TLS/OIDC requirements.

- [ ] **Step 1: Write the how-to with exact operator flow**

Create `docs/how-to/mcp.md` with this content:

````markdown
# Servidor MCP built-in

O template expõe um único servidor lógico com dois transportes: `stdio` para
clientes locais e Streamable HTTP para produção. Os dois registram a mesma tool
`health`; somente o adapter HTTP aplica OAuth.

## Usar localmente por `stdio`

Configure o cliente para iniciar o processo na raiz do repositório:

```json
{
  "mcpServers": {
    "django-api": {
      "command": "uv",
      "args": ["run", "--frozen", "python", "-m", "apps.api.mcp"],
      "cwd": "/caminho/absoluto/do/projeto"
    }
  }
}
```

O processo carrega `.env` sem sobrescrever variáveis fornecidas pelo cliente.
`stdout` pertence ao protocolo; logs são enviados a `stderr`.

## Configurar Streamable HTTP

O endpoint HTTP é um OAuth 2.1 Resource Server. Configure um Authorization
Server OIDC externo e preencha:

```dotenv
MCP_SERVER_URL=https://mcp.example.com/mcp
MCP_AUTH_ISSUER_URL=https://auth.example.com
MCP_AUTH_AUDIENCE=https://mcp.example.com/mcp
MCP_AUTH_JWKS_URL=https://auth.example.com/.well-known/jwks.json
MCP_AUTH_ALGORITHMS=RS256
MCP_ALLOWED_HOSTS=mcp.example.com
MCP_ALLOWED_ORIGINS=
```

`MCP_AUTH_ISSUER_URL` identifica o emissor; `MCP_AUTH_AUDIENCE` prende o token
a este servidor; `MCP_AUTH_JWKS_URL` fornece as chaves de assinatura; e
`MCP_AUTH_ALGORITHMS` é uma allowlist, nunca um valor lido do header do token.
Deixe `MCP_ALLOWED_ORIGINS` vazio quando não houver cliente browser. A tool
`health` exige o scope `health:read`.

O processo HTTP falha no startup se a configuração estiver incompleta. O
template valida tokens, mas não implementa login, consentimento nem emissão.

## Subir o serviço

Depois de configurar o provedor:

```bash
make mcp-up
```

O container expõe `8001` somente na rede do Compose. O nginx publica
`http://mcp.localhost/mcp` no ambiente local. Em produção, substitua
`mcp.localhost` pelo hostname real e termine TLS no nginx ou no load balancer.

Para parar apenas esse processo:

```bash
make mcp-down
```

`GET /live` é o liveness interno e não consulta dependências. Ele não é
publicado pelo nginx. A tool autenticada `health` executa `SELECT 1` em todos os
aliases de banco e round-trip em todos os aliases de cache.

## Diagnóstico

| Sintoma | Causa provável |
|---|---|
| `401` | Token ausente, expirado, assinatura/issuer/audience inválidos. |
| `403` | Token válido sem `health:read` ou Origin fora da allowlist. |
| `421` | Header `Host` fora de `MCP_ALLOWED_HOSTS`. |
| `502` | Profile `mcp` desligado ou processo HTTP fora do ar. |
| Timeout de JWKS | Authorization Server inacessível e chave não presente no cache. |
| `status: unhealthy` | Uma dependência falhou; consulte logs pelo alias e `error_type`. |
````

- [ ] **Step 2: Update navigation and repository maps**

Add this nav entry under `Como fazer` in `mkdocs.yml`:

```yaml
      - Servidor MCP built-in: how-to/mcp.md
```

Add `mcp` to the API-infrastructure list in `docs/reference/estrutura-de-diretorios.md`. Add a `Servidor MCP` subsection to `docs/how-to/proxy-nginx.md` stating that `mcp.localhost` proxies only `/mcp` and RFC 9728 metadata, resolves `mcp:8001` lazily, and never proxies `/live`.

- [ ] **Step 3: Add the README entrypoint**

Add this paragraph to `README.md` without duplicating environment variables:

```markdown
## MCP

O template inclui um [servidor MCP built-in](docs/how-to/mcp.md): use `stdio`
para integração local e Streamable HTTP protegido por OIDC em produção.
```

- [ ] **Step 4: Validate docs and links**

```bash
uv run mkdocs build --strict
rg -n 'FastMCP|mcp_server/' README.md docs .ai/brainstorming/spec/2026-08-15-built-in-mcp-server.md
```

Expected: MkDocs exits zero. Matches for obsolete names exist only inside historical research/spec files explicitly marked as superseded.

- [ ] **Step 5: Commit**

```bash
git add docs/how-to/mcp.md docs/reference/estrutura-de-diretorios.md docs/how-to/proxy-nginx.md mkdocs.yml README.md
git commit -m "docs: explain built-in MCP server"
```

### Task 9: Final verification and migration guard

**Files:**
- Modify only files required by failures attributable to this feature.

**Interfaces:**
- Produces: evidence that code, transports, image, proxy and documentation agree with the spec.

- [ ] **Step 1: Run focused MCP/core tests**

```bash
uv run pytest --nomigrations apps/api/core/tests/test_dependency_health.py apps/api/core/tests/test_health_check.py apps/api/mcp/tests tests/architecture/test_mcp_runtime.py tests/architecture/test_mcp_deployment.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run static and Django gates**

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

Expected: all commands exit zero; `makemigrations` reports no changes.

- [ ] **Step 3: Run full suite and docs**

```bash
make test
uv run mkdocs build --strict
```

Expected: full test suite and strict documentation build pass.

- [ ] **Step 4: Revalidate container/proxy artifacts**

```bash
docker compose --profile mcp config --quiet
make nginx-test
docker build -t drf-base-api-mcp:test .
docker run --rm drf-base-api-mcp:test python -c 'from mcp.server import MCPServer; import jwt; print(MCPServer.__name__, jwt.__version__)'
```

Expected: configuration, proxy syntax, image build and runtime imports all succeed.

- [ ] **Step 5: Check the final diff and commit any verification-only correction**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and no unintended files. If an attributable correction was necessary, commit only its exact files with a Conventional Commit message describing that correction; otherwise create no empty commit.
