# API Deprecation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the exclusive `@api_deprecated` ViewSet-handler decorator defined by ADR 0004, emitting standards-based HTTP headers and matching OpenAPI metadata.

**Architecture:** Implement one focused module under `apps.api.core` containing immutable metadata, import-time validation, response-header application, and `drf-spectacular` integration. The wrapper only modifies normal Django/DRF responses returned by the decorated handler; it does not catch exceptions or introduce middleware, registries, metrics, or automatic route removal.

**Tech Stack:** Python 3.12, Django 5.2, Django REST Framework 3.16, drf-spectacular 0.30, django-cors-headers 4.9, pytest.

## Global Constraints

- ADR `docs/adr/0004-politica-de-deprecacao-de-api.md` is normative.
- `@api_deprecated` is the only permitted source of `Deprecation` and `Sunset`.
- Required parameters: `since`, `sunset`, and `documentation`; `replacement` is optional.
- Dates use `YYYY-MM-DD` at `00:00:00 UTC`; `sunset - since` must be at least 90 days.
- `since` may be future and headers are emitted immediately.
- Do not compare configured dates to the current clock during import or request handling.
- Accept only HTTP(S) URIs with a host or absolute paths beginning with one `/`.
- Apply deprecation per Python handler, preserving DRF `MethodMapper` granularity.
- Do not catch exceptions; framework-generated responses outside the handler receive no headers.
- Do not add metrics, a central registry, a ViewSet mixin, middleware, `410 Gone`, or automatic removal.
- Do not add a replacement response header; expose it only in OpenAPI and migration docs.

---

### Task 1: Metadata validation and HTTP headers

**Files:**
- Create: `apps/api/core/deprecation.py`
- Create: `apps/api/core/tests/test_deprecation.py`

**Interfaces:**
- Produces: `api_deprecated(*, since: str, sunset: str, documentation: str, replacement: str | None = None)`.
- Produces internally: immutable `ApiDeprecation` metadata attached to a handler as `__api_deprecation__`.
- Consumes: Django `HttpResponseBase` and drf-spectacular `extend_schema`.

- [ ] **Step 1: Write failing unit tests**

Create `apps/api/core/tests/test_deprecation.py`:

```python
from django.http import HttpResponse

import pytest

from apps.api.core.deprecation import api_deprecated


def decorar(**overrides):
    parametros = {
        "since": "2026-08-01",
        "sunset": "2026-11-01",
        "documentation": "/docs/deprecations/usuarios/",
        "replacement": "/api/v2/usuarios/",
    }
    parametros.update(overrides)
    return api_deprecated(**parametros)


def test_decorator_adiciona_headers_e_preserva_link_existente():
    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = '</api/schema/>; rel="service-desc"'
        return response

    response = handler()

    assert response["Deprecation"] == "@1785542400"
    assert response["Sunset"] == "Sun, 01 Nov 2026 00:00:00 GMT"
    assert response["Link"] == (
        '</api/schema/>; rel="service-desc", '
        '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'
    )


def test_decorator_nao_duplica_link_de_deprecacao():
    link = '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'

    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = link
        return response

    assert handler()["Link"] == link


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("since", "01/08/2026", "since"),
        ("sunset", "2026-10-29", "90 dias"),
        ("documentation", "docs/deprecations/usuarios", "documentation"),
        ("documentation", "ftp://example.com/guia", "documentation"),
        ("replacement", "//example.com/api/v2", "replacement"),
    ],
)
def test_decorator_rejeita_configuracao_invalida(campo, valor, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        decorar(**{campo: valor})


def test_decorator_rejeita_aplicacao_duplicada():
    primeiro = decorar()

    with pytest.raises(ValueError, match="mais de uma vez"):
        decorar()(primeiro(lambda: HttpResponse()))


def test_retorno_invalido_fica_para_validacao_do_drf():
    @decorar()
    def handler():
        return {"invalido": True}

    assert handler() == {"invalido": True}
```

- [ ] **Step 2: Run the unit tests and verify the module is missing**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
```

Expected: collection fails with `ModuleNotFoundError` for
`apps.api.core.deprecation`.

- [ ] **Step 3: Implement metadata, validation, headers, and schema decoration**

Create `apps/api/core/deprecation.py`:

```python
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import format_datetime
from functools import wraps
from urllib.parse import urlsplit

from django.http.response import HttpResponseBase

from drf_spectacular.utils import extend_schema

_METADATA_ATTRIBUTE = "__api_deprecation__"


def _parse_date(name, value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} deve usar YYYY-MM-DD") from exc


def _validate_uri(name, value):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} deve ser uma URI válida")

    parsed = urlsplit(value)
    is_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    is_absolute_path = (
        not parsed.scheme
        and not parsed.netloc
        and value.startswith("/")
        and not value.startswith("//")
    )
    if not (is_http or is_absolute_path):
        raise ValueError(f"{name} deve ser HTTP(S) ou caminho absoluto")
    return value


@dataclass(frozen=True)
class ApiDeprecation:
    since: date
    sunset: date
    documentation: str
    replacement: str | None

    @classmethod
    def from_strings(cls, *, since, sunset, documentation, replacement):
        since_date = _parse_date("since", since)
        sunset_date = _parse_date("sunset", sunset)
        if sunset_date - since_date < timedelta(days=90):
            raise ValueError("sunset deve ficar pelo menos 90 dias depois de since")
        return cls(
            since=since_date,
            sunset=sunset_date,
            documentation=_validate_uri("documentation", documentation),
            replacement=(
                _validate_uri("replacement", replacement)
                if replacement is not None
                else None
            ),
        )

    @property
    def deprecation_header(self):
        instant = datetime.combine(self.since, time.min, tzinfo=UTC)
        return f"@{int(instant.timestamp())}"

    @property
    def sunset_header(self):
        instant = datetime.combine(self.sunset, time.min, tzinfo=UTC)
        return format_datetime(instant, usegmt=True)

    @property
    def documentation_link(self):
        return (
            f'<{self.documentation}>; rel="deprecation"; type="text/html"'
        )

    @property
    def openapi_extensions(self):
        extensions = {
            "x-deprecation-since": self.since.isoformat(),
            "x-sunset": self.sunset.isoformat(),
        }
        if self.replacement is not None:
            extensions["x-replacement"] = self.replacement
        return extensions

    def apply_headers(self, response):
        response["Deprecation"] = self.deprecation_header
        response["Sunset"] = self.sunset_header
        current_link = response.headers.get("Link")
        if current_link and self.documentation_link not in current_link:
            response["Link"] = f"{current_link}, {self.documentation_link}"
        elif not current_link:
            response["Link"] = self.documentation_link


def api_deprecated(*, since, sunset, documentation, replacement=None):
    metadata = ApiDeprecation.from_strings(
        since=since,
        sunset=sunset,
        documentation=documentation,
        replacement=replacement,
    )

    def decorator(handler):
        if hasattr(handler, _METADATA_ATTRIBUTE):
            raise ValueError("@api_deprecated não pode ser aplicado mais de uma vez")

        @wraps(handler)
        def wrapped(*args, **kwargs):
            response = handler(*args, **kwargs)
            if isinstance(response, HttpResponseBase):
                metadata.apply_headers(response)
            return response

        setattr(wrapped, _METADATA_ATTRIBUTE, metadata)
        return extend_schema(
            deprecated=True,
            external_docs={"url": metadata.documentation},
            extensions=metadata.openapi_extensions,
        )(wrapped)

    return decorator
```

- [ ] **Step 4: Run unit tests and lint**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
uv run ruff check apps/api/core/deprecation.py apps/api/core/tests/test_deprecation.py
```

Expected: all tests pass and Ruff exits `0`.

- [ ] **Step 5: Commit the decorator core**

```bash
git add apps/api/core/deprecation.py apps/api/core/tests/test_deprecation.py
git commit -m "feat: add API deprecation decorator"
```

---

### Task 2: DRF handler and error-flow integration

**Files:**
- Modify: `apps/api/core/tests/test_deprecation.py`

**Interfaces:**
- Consumes: `api_deprecated` from Task 1.
- Produces: regression coverage for standard actions, custom handlers, explicit errors, permissions, and DRF exception handling.

- [ ] **Step 1: Extend the import block**

Add these imports to the existing import block at the top of
`apps/api/core/tests/test_deprecation.py`:

```python
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
```

- [ ] **Step 2: Add ViewSet fixtures and integration tests**

Append to `apps/api/core/tests/test_deprecation.py`:

```python


class NegarTudo(BasePermission):
    def has_permission(self, request, view):
        return False


class DeprecatedViewSet(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [AllowAny]

    @api_deprecated(
        since="2026-08-01",
        sunset="2026-11-01",
        documentation="/docs/deprecations/listagem/",
        replacement="/api/v2/itens/",
    )
    def list(self, request):
        return Response({"ok": True})

    @action(detail=False, methods=["get"])
    @api_deprecated(
        since="2026-08-01",
        sunset="2026-11-01",
        documentation="/docs/deprecations/relatorio/",
    )
    def relatorio(self, request):
        return Response({"ok": True})

    @relatorio.mapping.delete
    def apagar_relatorio(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    @api_deprecated(
        since="2026-08-01",
        sunset="2026-11-01",
        documentation="/docs/deprecations/validacao/",
    )
    def resposta_400(self, request):
        return Response({"erro": True}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"])
    @api_deprecated(
        since="2026-08-01",
        sunset="2026-11-01",
        documentation="/docs/deprecations/excecao/",
    )
    def excecao_400(self, request):
        raise ValidationError("inválido")


class ProtectedDeprecatedViewSet(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [NegarTudo]

    @api_deprecated(
        since="2026-08-01",
        sunset="2026-11-01",
        documentation="/docs/deprecations/protegido/",
    )
    def list(self, request):
        return Response({"ok": True})


def executar(viewset, method, action_name, path="/teste/"):
    request = getattr(APIRequestFactory(), method)(path, {}, format="json")
    return viewset.as_view({method: action_name})(request)


def test_action_padrao_recebe_headers():
    response = executar(DeprecatedViewSet, "get", "list")

    assert response.status_code == 200
    assert response["Deprecation"] == "@1785542400"


def test_deprecacao_segue_o_handler_do_method_mapper():
    get_response = executar(DeprecatedViewSet, "get", "relatorio")
    delete_response = executar(DeprecatedViewSet, "delete", "apagar_relatorio")

    assert "Deprecation" in get_response
    assert "Deprecation" not in delete_response


def test_resposta_400_retornada_pela_action_recebe_headers():
    response = executar(DeprecatedViewSet, "post", "resposta_400")

    assert response.status_code == 400
    assert "Deprecation" in response


def test_excecao_convertida_pelo_drf_nao_recebe_headers():
    response = executar(DeprecatedViewSet, "post", "excecao_400")

    assert response.status_code == 400
    assert "Deprecation" not in response


def test_permissao_negada_antes_da_action_nao_recebe_headers():
    response = executar(ProtectedDeprecatedViewSet, "get", "list")

    assert response.status_code == 403
    assert "Deprecation" not in response
```

- [ ] **Step 3: Run the integration tests**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
```

Expected: all unit and integration tests pass.

- [ ] **Step 4: Commit DRF behavior coverage**

```bash
git add apps/api/core/tests/test_deprecation.py
git commit -m "test: cover deprecated ViewSet handlers"
```

---

### Task 3: OpenAPI single-source verification

**Files:**
- Modify: `apps/api/core/tests/test_deprecation.py`

**Interfaces:**
- Consumes: metadata applied by `api_deprecated`.
- Produces: schema assertions for `deprecated`, `externalDocs`, and `x-*` extensions.

- [ ] **Step 1: Extend the import block**

Add these imports to the existing import block at the top of
`apps/api/core/tests/test_deprecation.py`:

```python
from django.urls import include, path

from rest_framework.routers import SimpleRouter

from drf_spectacular.generators import SchemaGenerator
```

- [ ] **Step 2: Add local router and schema tests**

Append to `apps/api/core/tests/test_deprecation.py`:

```python


def gerar_schema():
    router = SimpleRouter()
    router.register("deprecated-test", DeprecatedViewSet, basename="deprecated-test")
    patterns = [path("", include(router.urls))]
    return SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)


def test_openapi_recebe_metadados_do_mesmo_decorator():
    operation = gerar_schema()["paths"]["/deprecated-test/"]["get"]

    assert operation["deprecated"] is True
    assert operation["externalDocs"] == {
        "url": "/docs/deprecations/listagem/"
    }
    assert operation["x-deprecation-since"] == "2026-08-01"
    assert operation["x-sunset"] == "2026-11-01"
    assert operation["x-replacement"] == "/api/v2/itens/"


def test_openapi_omite_replacement_quando_nao_configurado():
    operation = gerar_schema()["paths"]["/deprecated-test/relatorio/"]["get"]

    assert "x-replacement" not in operation
```

- [ ] **Step 3: Run the schema tests**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
```

Expected: schema contains the exact fields above and the complete file passes.

- [ ] **Step 4: Generate the project schema**

Run:

```bash
DJANGO_ENVIRONMENT=test \
DJANGO_SECRET_KEY=test-secret \
DATABASE_NAME=base \
DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 \
DATABASE_PORT=5432 \
uv run python manage.py spectacular --validate --file /tmp/api-schema.yaml
```

Expected: command exits `0`; no deprecation-extension validation error appears.

- [ ] **Step 5: Commit OpenAPI coverage**

```bash
git add apps/api/core/tests/test_deprecation.py
git commit -m "test: verify deprecation OpenAPI metadata"
```

---

### Task 4: CORS and permanent policy documentation

**Files:**
- Modify: `api/settings.py`
- Modify: `apps/api/core/tests/test_deprecation.py`
- Create: `docs/reference/deprecacao-api.md`
- Modify: `docs/how-to/publicar-versao.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `mkdocs.yml`

**Interfaces:**
- Consumes: runtime headers and ADR 0004.
- Produces: browser-readable headers, public decorator reference, and release-removal checklist.

- [ ] **Step 1: Add a failing CORS configuration test**

Append to `apps/api/core/tests/test_deprecation.py`:

```python
def test_cors_expoe_headers_de_deprecacao(settings):
    assert settings.CORS_EXPOSE_HEADERS == ["Deprecation", "Sunset", "Link"]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py::test_cors_expoe_headers_de_deprecacao -q
```

Expected: FAIL because `CORS_EXPOSE_HEADERS` is not configured.

- [ ] **Step 3: Configure exposed response headers**

In `api/settings.py`, immediately after `CSRF_TRUSTED_ORIGINS`, add:

```python
CORS_EXPOSE_HEADERS = ["Deprecation", "Sunset", "Link"]
```

- [ ] **Step 4: Create the public API-deprecation reference**

Create `docs/reference/deprecacao-api.md` with these sections and exact policy:

````markdown
# Deprecação de API

Use exclusivamente `@api_deprecated` em handlers de ViewSets. É proibido
escrever `Deprecation` ou `Sunset` manualmente, configurar depreciação apenas no
OpenAPI ou gerar esses headers em middleware e gateways.

## Declarar

```python
@action(detail=False, methods=["get"])
@api_deprecated(
    since="2026-08-01",
    sunset="2026-11-01",
    documentation="/docs/deprecations/usuarios/",
    replacement="/api/v2/usuarios/",
)
def usuarios(self, request):
    ...
```

`since`, `sunset` e `documentation` são obrigatórios; `replacement` é opcional.
As datas usam `YYYY-MM-DD`, meia-noite UTC, e devem ter intervalo mínimo de 90
dias. Uma data futura anuncia a depreciação antecipadamente. Datas retroativas
são proibidas e devem ser rejeitadas na revisão.

O decorator atua por handler. Métodos adicionais definidos com
`@action.mapping` precisam de decorator próprio quando também forem
depreciados.

## Resposta e schema

Respostas retornadas normalmente pelo handler recebem `Deprecation`, `Sunset` e
`Link`, inclusive respostas `400` explícitas. Falhas produzidas antes do handler
ou exceções convertidas posteriormente pelo DRF não recebem esses headers.

O OpenAPI recebe `deprecated`, `externalDocs`, `x-deprecation-since`,
`x-sunset` e, quando houver, `x-replacement`. Aplicações web podem ler os três
headers porque eles são expostos por CORS.

## Remover

Uma rota só pode ser removida depois de `sunset` e em uma versão SemVer
incompatível. Em `0.x`, isso significa a próxima minor; a partir de `1.0.0`,
significa a próxima major.

Antes da remoção:

1. confirme pelo changelog que o aviso não foi retroativo;
2. confirme a janela mínima de 90 dias;
3. publique a versão incompatível;
4. mova a entrada de `Deprecated` para `Removed`;
5. mantenha permanentemente o guia de migração e registre nele data e versão da
   remoção.

Depois de removida, a rota responde `404`; não existe tombstone `410` nem
remoção automática por data.
````

- [ ] **Step 5: Expand the release checklist**

Append to `docs/how-to/publicar-versao.md`:

```markdown
## Remover endpoint depreciado

Antes de remover uma rota, confirme que a data `sunset` chegou, que passaram ao
menos 90 dias desde `since` e que a release é SemVer incompatível. Registre a
retirada em `Removed` e preserve permanentemente o guia de migração, anotando
nele a data e a versão da remoção.
```

- [ ] **Step 6: Add navigation and changelog entries**

Under `Referência` in `mkdocs.yml`, add:

```yaml
- Deprecação de API: reference/deprecacao-api.md
```

Under `Unreleased > Added` in `docs/CHANGELOG.md`, add:

```markdown
- Decorator e política única para depreciação gradual de handlers da API.
```

- [ ] **Step 7: Run focused tests, lint, and documentation**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
uv run ruff check apps/api/core/deprecation.py apps/api/core/tests/test_deprecation.py api/settings.py
uv run mkdocs build --strict
```

Expected: tests pass, Ruff exits `0`, and MkDocs exits `0`.

- [ ] **Step 8: Commit CORS and policy docs**

```bash
git add api/settings.py apps/api/core/tests/test_deprecation.py docs/reference/deprecacao-api.md docs/how-to/publicar-versao.md docs/CHANGELOG.md mkdocs.yml
git commit -m "docs: publish API deprecation policy"
```

---

### Task 5: Deprecation feature verification

**Files:**
- Verify only; no expected file changes.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: final evidence for runtime, schema, lint, and documentation.

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run pytest apps/api/core/tests/test_deprecation.py -q
uv run ruff check apps/api/core/deprecation.py apps/api/core/tests/test_deprecation.py api/settings.py
uv run mkdocs build --strict
```

Expected: all commands exit `0`.

- [ ] **Step 2: Run Django and schema checks**

Run:

```bash
DJANGO_ENVIRONMENT=test \
DJANGO_SECRET_KEY=test-secret \
DATABASE_NAME=base \
DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 \
DATABASE_PORT=5432 \
uv run python manage.py check

DJANGO_ENVIRONMENT=test \
DJANGO_SECRET_KEY=test-secret \
DATABASE_NAME=base \
DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 \
DATABASE_PORT=5432 \
uv run python manage.py spectacular --validate --file /tmp/api-schema.yaml
```

Expected: Django reports no issues and schema generation exits `0`.

- [ ] **Step 3: Run the complete suite**

Run:

```bash
docker compose up -d db redis

DJANGO_ENVIRONMENT=test \
DJANGO_SECRET_KEY=test-secret \
DATABASE_NAME=base \
DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 \
DATABASE_PORT=5432 \
REDIS_HOST=127.0.0.1 \
REDIS_PORT=6379 \
uv run pytest
```

Expected: full suite exits `0`.
