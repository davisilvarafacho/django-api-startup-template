# Context Variables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** substituir `django-threadlocals` por um framework interno tipado, seguro para threads e tasks assíncronas.

**Architecture:** `internal_frameworks.context` encapsula um `ContextVar` por instância e expõe ausência semântica por sentinela privado. `apps.api.core.context` declara request, usuário e token e fornece o middleware externo que publica a request e limpa o contexto ao término. Consumidores Django leem essas instâncias; `request_id.py` preserva seus helpers públicos sobre a nova abstração.

**Tech Stack:** Python 3.12, Django 5.2, `contextvars`, pytest-django, Ruff e uv.

## Global Constraints

- O framework não conhece Django, request, usuário, token ou Celery.
- `clear()` e `clear_context()` escrevem o sentinela de ausência; nunca tentam remover um valor sem token.
- Todo `reset(token)` pendente ocorre antes da limpeza coletiva; tokens não atravessam `clear()` nem `clear_context()`.
- O middleware de contexto fica antes de `RequestIDMiddleware`, para que o unwind restaure o token de request ID antes do `finally` coletivo.
- Não criar migration nem alterar a semântica de RLS.
- Remover toda referência executável a `django-threadlocals`, incluindo a dependência travada.
- Executar testes com `uv run --group test pytest --nomigrations` e as variáveis locais de banco.

---

### Task 1: Criar o framework genérico e sua especificação executável

**Files:**
- Create: `internal_frameworks/context/__init__.py`
- Create: `internal_frameworks/context/variable.py`
- Create: `internal_frameworks/context/tests/__init__.py`
- Create: `internal_frameworks/context/tests/test_variable.py`

**Interfaces:**
- Produces: `ContextVariable[T]`, `ContextVariableNotSetError`, `from_var(name, default=...)`, `set`, `get`, `is_set`, `reset`, `clear`, `use` and `clear_context`.

- [ ] **Step 1: Write the failing tests**

```python
def test_required_get_fails_when_value_is_absent():
    value = ContextVariable[str].from_var("value", default="fallback")

    with pytest.raises(ContextVariableNotSetError, match="value"):
        value.get(raise_exception=True)


def test_set_none_is_distinct_from_absence():
    value = ContextVariable[str | None].from_var("value")
    value.set(None)

    assert value.is_set() is True
    assert value.get(raise_exception=True) is None


def test_use_restores_the_previous_value_after_an_exception():
    value = ContextVariable[str].from_var("value")
    value.set("before")

    with pytest.raises(RuntimeError), value.use("during"):
        assert value.get() == "during"
        raise RuntimeError

    assert value.get() == "before"
```

Also add focused tests for default values, same-name independence, `clear()`, nested `use()`, invalid/reused tokens, weak-reference collection, a separate thread, and two concurrent `asyncio` tasks.

- [ ] **Step 2: Verify RED**

Run: `uv run --group test pytest --nomigrations internal_frameworks/context/tests/test_variable.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'internal_frameworks.context'`.

- [ ] **Step 3: Implement the minimal generic API**

```python
# internal_frameworks/context/variable.py
from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import Lock
from typing import ClassVar, Generic, Literal, Self, TypeVar, overload
from weakref import WeakSet

T = TypeVar("T")
_UNSET = object()


class ContextVariableNotSetError(LookupError):
    pass


class ContextVariable(Generic[T]):
    _instances: ClassVar[WeakSet[Self]] = WeakSet()
    _instances_lock: ClassVar[Lock] = Lock()

    def __init__(self, name: str, default: T | None = None):
        self._name = name
        self._default = default
        self._variable: ContextVar[T | object] = ContextVar(name, default=_UNSET)
        with self._instances_lock:
            self._instances.add(self)

    @classmethod
    def from_var(cls, name: str, default: T | None = None) -> Self:
        return cls(name, default)

    def set(self, value: T) -> Token[T | object]:
        return self._variable.set(value)

    @overload
    def get(self, *, raise_exception: Literal[True]) -> T: ...

    @overload
    def get(self, *, raise_exception: Literal[False] = False) -> T | None: ...

    def get(self, *, raise_exception=False):
        value = self._variable.get()
        if value is _UNSET:
            if raise_exception:
                raise ContextVariableNotSetError(f"Context variable '{self._name}' is not set.")
            return self._default
        return value
```

Implement `is_set()` using identity against `_UNSET`; `reset()` by delegation; `clear()` via `set(_UNSET)`; `use()` with `try/finally`; and `clear_context()` from a lock-protected tuple snapshot. Export only the public class and error from `__init__.py`.

- [ ] **Step 4: Document the cleanup-token boundary in a regression**

```python
def test_reset_after_clear_follows_native_contextvar_semantics():
    value = ContextVariable[str].from_var("value")
    token = value.set("before-clear")
    value.clear()

    value.reset(token)

    assert value.get() is None
```

The reset restores the predecessor state (`None` in this case), documenting why callers must never retain tokens across `clear()`.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run --group test pytest --nomigrations internal_frameworks/context/tests/test_variable.py -q && uv run ruff check internal_frameworks/context && uv run ruff format --check internal_frameworks/context`

Expected: PASS.

```bash
git add internal_frameworks/context
git commit -m "feat: adicionar variáveis de contexto"
```

### Task 2: Publish request context and migrate HTTP consumers

**Files:**
- Create: `apps/api/core/context.py`
- Create: `apps/api/core/tests/test_context.py`
- Modify: `api/settings.py`
- Modify: `apps/api/autenticacao/middleware.py`
- Modify: `apps/api/base/models.py`
- Modify: `apps/api/base/views.py`
- Modify: `api/logging_config.py`
- Modify: `apps/api/base/tests/test_auditlog.py`
- Modify: the auth/token test fixtures importing `threadlocals`

**Interfaces:**
- Consumes: `ContextVariable` from Task 1.
- Produces: `request_atual`, `usuario_atual`, `token_atual`, and `RequestContextMiddleware` from `apps.api.core.context`.

- [ ] **Step 1: Write the failing request-lifecycle tests**

```python
def test_request_context_publishes_and_cleans_the_request():
    request = RequestFactory().get("/health/")
    seen = {}

    def response(inner_request):
        seen["request"] = request_atual.get(raise_exception=True)
        return HttpResponse("ok")

    RequestContextMiddleware(response)(request)

    assert seen["request"] is request
    assert request_atual.is_set() is False
```

Add equivalent tests for authentication success, an authentication short-circuit, and a view exception: after each response, request/user/token must be unset. Update audit tests to use `with usuario_atual.use(autor):` and verify automatic `created_by` plus cloning.

- [ ] **Step 2: Verify RED**

Run: `uv run --group test pytest --nomigrations apps/api/core/tests/test_context.py apps/api/base/tests/test_auditlog.py apps/api/autenticacao/tests/test_passthrough.py -q`

Expected: FAIL because the application variables and middleware do not exist.

- [ ] **Step 3: Implement the application context**

```python
# apps/api/core/context.py
from typing import TYPE_CHECKING

from internal_frameworks.context import ContextVariable

if TYPE_CHECKING:
    from django.http import HttpRequest
    from apps.api.autenticacao.models import AuthToken
    from apps.usuarios.models import Usuario

request_atual = ContextVariable["HttpRequest"].from_var("request_atual")
usuario_atual = ContextVariable["Usuario"].from_var("usuario_atual")
token_atual = ContextVariable["AuthToken"].from_var("token_atual")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_atual.set(request)
        try:
            return self.get_response(request)
        finally:
            ContextVariable.clear_context()
```

Insert this middleware immediately before `apps.api.core.request_id.RequestIDMiddleware`. In authentication, set `usuario_atual` and `token_atual` after `request.user` and `request.auth`. Replace `get_current_user()`, `get_request_variable("token")`, and logging's `get_current_request()` with the application variables. The creation-audit fallback may consult `request_atual.get()` only when `usuario_atual` is absent.

- [ ] **Step 4: Migrate fixtures without compatibility wrappers**

```python
@pytest.fixture(autouse=True)
def _clear_context():
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()
```

Replace every `set_current_user(None)` and `set_thread_variable("request", None)` with this fixture. Tests that need state must use `usuario_atual.use(actor)` or `request_atual.use(request)`; do not recreate the legacy threadlocal API.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run --group test pytest --nomigrations apps/api/core/tests/test_context.py apps/api/core/tests/test_request_id.py apps/api/base/tests/test_auditlog.py apps/api/autenticacao/tests -q`

Expected: PASS with no leak between sequential requests.

```bash
git add api/settings.py api/logging_config.py apps/api/core/context.py apps/api/core/tests apps/api/base apps/api/autenticacao
git commit -m "refactor: migrar contexto HTTP para contextvars"
```

### Task 3: Adapt request ID and Celery; remove the legacy dependency

**Files:**
- Modify: `apps/api/core/request_id.py`
- Modify: `apps/api/core/tests/test_request_id.py`
- Modify: `api/celery.py`
- Create: `api/tests/test_celery.py`
- Modify: `pyproject.toml`, `uv.lock`, and `docs/frameworks.md`

**Interfaces:**
- Consumes: `ContextVariable.clear_context()` and the app-level context from Tasks 1–2.
- Preserves: `get_request_id()`, `set_request_id()`, and `reset_request_id()`.

- [ ] **Step 1: Write the failing ordered-cleanup test**

```python
def test_task_cleanup_resets_request_id_before_collective_cleanup(task):
    token = set_request_id("request-1")
    celery_module._tokens["task-1"] = token
    token_atual.set(object())

    limpar_request_id(task_id="task-1")

    assert get_request_id() is None
    assert token_atual.is_set() is False
```

Also assert that the request-ID middleware keeps its header and access logging behavior while the outer request-context middleware cleans the complete registry.

- [ ] **Step 2: Verify RED**

Run: `uv run --group test pytest --nomigrations api/tests/test_celery.py apps/api/core/tests/test_request_id.py -q`

Expected: FAIL because Celery only resets request ID and does not run collective cleanup.

- [ ] **Step 3: Implement ordered cleanup and remove the package**

```python
# apps/api/core/request_id.py
from internal_frameworks.context import ContextVariable

_request_id = ContextVariable[str].from_var("request_id")


def get_request_id():
    return _request_id.get()


def set_request_id(request_id):
    return _request_id.set(request_id)


def reset_request_id(token):
    _request_id.reset(token)
```

Keep `RequestIDMiddleware._limpar_contexto()` as a token reset: it unwinds before the outer middleware's collective cleanup. In `api.celery.limpar_request_id`, pop and reset the task token first, then invoke `ContextVariable.clear_context()`; leave RLS cleanup independent.

- [ ] **Step 4: Remove dependency and verify all gates**

```bash
uv remove django-threadlocals
rg -n "django-threadlocals|threadlocals\." api apps internal_frameworks tests pyproject.toml uv.lock
uv run ruff check api apps internal_frameworks tests
uv run ruff format --check api apps internal_frameworks tests
uv run python manage.py makemigrations --check --dry-run
uv run --group test pytest --nomigrations -q
uv run mkdocs build --strict
```

Expected: dependency manifests change, the search has no executable match, and every gate exits 0. Update `docs/frameworks.md` to name `internal_frameworks.context`.

- [ ] **Step 5: Audit and commit**

```bash
git diff --check
git status --short
git add api/celery.py apps/api/core/request_id.py apps/api/core/tests api/tests pyproject.toml uv.lock docs/frameworks.md
git commit -m "refactor: remover django threadlocals"
```

Confirm that unrelated untracked files remain unstaged.
