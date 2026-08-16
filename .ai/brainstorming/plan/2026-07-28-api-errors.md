# API Errors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Padronizar todas as falhas HTTP da API com códigos tipados, mensagens traduzíveis, status coerentes e request ID.

**Architecture:** Cada app declara seus códigos como `models.TextChoices` em `errors.py`. `apps.api.core.errors` oferece a interface profunda (`APIError`, registry, normalizador e renderer), enquanto um exception handler do DRF e os handlers/middlewares Django convergem para o mesmo envelope.

**Tech Stack:** Django 5.2, Django REST Framework 3.16, drf-spectacular, pytest-django, gettext.

## Global Constraints

- A spec normativa é `.ai/brainstorming/spec/2026-07-28-api-errors-design.md`.
- Códigos usam `dominio.erro`, em inglês, e só podem vir de `models.TextChoices` registrados.
- Cada app mantém seus códigos em `<app>/errors.py`.
- Mensagens são traduzíveis; clientes tomam decisões pelo código.
- Respostas de sucesso não recebem envelope.
- `ValidationError` responde `422`; JSON malformado permanece `400`.
- Toda falha inclui `request_id`; respostas `500` não expõem detalhes internos.
- Não criar nem alterar migrations; o projeto fará reset completo antes do lançamento.

---

### Task 1: Primitivos tipados e registry de códigos

**Files:**
- Create: `apps/api/core/errors.py`
- Create: `apps/api/core/tests/test_errors.py`
- Modify: `apps/api/core/apps.py`

**Interfaces:**
- Produces: `CoreErrorCode`, `ValidationErrorCode`, `APIError`, `APIErrorItem`, `ErrorCodeRegistry`, `error_codes`, `discover_error_codes()`, `error_response()`.
- `APIError(code, *, status_code, message=None, field=None, path=None, context=None)` aceita somente membro de `models.TextChoices`.

- [ ] **Step 1: Escrever testes falhando do contrato e do registry**

```python
class ExampleErrorCode(models.TextChoices):
    INVALID = "example.invalid", _("Valor inválido.")


def test_api_error_usa_valor_e_label_do_textchoices():
    error = APIError(ExampleErrorCode.INVALID, status_code=422)
    assert error.code == "example.invalid"
    assert str(error.message) == "Valor inválido."


def test_registry_rejeita_codigo_duplicado():
    registry = ErrorCodeRegistry()
    registry.register(ExampleErrorCode)
    with pytest.raises(ImproperlyConfigured, match="example.invalid"):
        registry.register(ExampleErrorCode)
```

Cobrir também formato inválido, string solta, lookup e descoberta de enums nos
`errors.py` dos apps.

- [ ] **Step 2: Executar os testes e confirmar RED**

Run: `uv run pytest apps/api/core/tests/test_errors.py -q`

Expected: FAIL por `apps.api.core.errors` inexistente.

- [ ] **Step 3: Implementar o núcleo mínimo**

```python
class ValidationErrorCode(models.TextChoices):
    REQUIRED = "validation.required", _("Este campo é obrigatório.")
    INVALID = "validation.invalid", _("Valor inválido.")
    MALFORMED = "validation.malformed", _("Requisição malformada.")


@dataclass(frozen=True)
class APIErrorItem:
    code: str
    message: str
    field: str | None = None
    path: tuple[str | int, ...] | None = None
    context: Mapping[str, object] = field(default_factory=dict)


class APIError(APIException):
    def __init__(
        self,
        code: models.TextChoices,
        *,
        status_code: int,
        message=None,
        field=None,
        path=None,
        context=None,
    ):
        ...
```

`ErrorCodeRegistry.register()` deve iterar pelos membros do `TextChoices`,
validar `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` e rejeitar duplicatas. A descoberta
importa `<app>.errors` dos apps instalados e registra todas as subclasses
concretas encontradas. `APIError` deve validar em runtime que `code` é membro
de uma classe derivada de `models.TextChoices` já registrada; não basta aceitar
qualquer `str` ou outro tipo de `Choices`.

- [ ] **Step 4: Ligar descoberta ao startup e criar system check**

No `CoreConfig.ready()`, chamar `discover_error_codes()` antes das demais
configurações. Registrar um check que converta inconsistências do registry em
`django.core.checks.Error`.

- [ ] **Step 5: Executar testes e lint**

Run: `uv run pytest apps/api/core/tests/test_errors.py -q`

Expected: PASS.

Run: `uv run ruff check apps/api/core/errors.py apps/api/core/apps.py apps/api/core/tests/test_errors.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/core/errors.py apps/api/core/apps.py apps/api/core/tests/test_errors.py
git commit -m "feat(errors): add typed error registry"
```

---

### Task 2: Normalização DRF e envelope único

**Files:**
- Modify: `apps/api/core/errors.py`
- Create: `apps/api/core/tests/test_exception_handler.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `api_exception_handler(exc, context) -> Response`.
- Produces: `flatten_validation_errors(detail, path=()) -> list[APIErrorItem]`.
- Consumes: `error_response()` e `get_request_id()`.

- [ ] **Step 1: Escrever testes falhando para o handler**

Cobrir:

```python
def test_validation_error_aninhado_vira_422_com_paths():
    exc = serializers.ValidationError({"members": [{"email": ["Este campo é obrigatório."]}]})
    response = api_exception_handler(exc, {"request": request})
    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "validation.required"
    assert response.data["errors"][0]["path"] == ["members", 0, "email"]


def test_internal_error_nao_vaza_detalhe():
    response = api_exception_handler(RuntimeError("segredo"), {"request": request})
    assert response.status_code == 500
    assert "segredo" not in str(response.data)
```

Adicionar casos para `APIError`, parse error, `NotAuthenticated`,
`AuthenticationFailed`, `PermissionDenied`, `NotFound`, `Throttled` e
`django.core.exceptions.ValidationError`.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/core/tests/test_exception_handler.py -q`

Expected: FAIL por handler inexistente.

- [ ] **Step 3: Implementar flattening e mapeamento**

O handler deve:

1. preservar `APIError`;
2. converter validação recursivamente;
3. mapear exceptions DRF para códigos core;
4. delegar exceptions desconhecidas ao logger/Sentry e devolver
   `core.internal_error`;
5. sempre produzir:

```python
{
    "errors": [asdict(item) for item in items],
    "request_id": get_request_id(),
}
```

- [ ] **Step 4: Configurar o DRF**

Adicionar:

```python
"EXCEPTION_HANDLER": "apps.api.core.errors.api_exception_handler",
```

em `REST_FRAMEWORK`.

- [ ] **Step 5: Verificar GREEN**

Run: `uv run pytest apps/api/core/tests/test_errors.py apps/api/core/tests/test_exception_handler.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/core/errors.py apps/api/core/tests/test_exception_handler.py api/settings.py
git commit -m "feat(errors): normalize drf exceptions"
```

---

### Task 3: Unificar middleware e handlers Django

**Files:**
- Modify: `apps/api/core/status_handlers.py`
- Create: `apps/api/core/tests/test_status_handlers.py`
- Create: `apps/api/autenticacao/errors.py`
- Modify: `apps/api/autenticacao/middleware.py`
- Modify: `apps/api/autenticacao/tests/test_passthrough.py`
- Modify: `api/urls.py`

**Interfaces:**
- Consumes: `error_response(code, *, status_code, ...)`.
- Produces: `AuthErrorCode.TOKEN_NOT_PROVIDED`, `INVALID_TOKEN`,
  `USER_INACTIVE`.

- [ ] **Step 1: Escrever expectativas do novo envelope**

Atualizar os testes do middleware:

```python
payload = json.loads(response.content)
assert payload["errors"][0]["code"] == AuthErrorCode.TOKEN_NOT_PROVIDED
assert payload["request_id"] == request.id
```

Criar testes para handlers `400/401/403/404/500`; corrigir a anomalia atual em
que `custom_404_handler` devolve `400`.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/core/tests/test_status_handlers.py apps/api/autenticacao/tests/test_passthrough.py -q`

Expected: FAIL com payload legado `mensagem`.

- [ ] **Step 3: Implementar `AuthErrorCode` e trocar respostas manuais**

```python
class AuthErrorCode(models.TextChoices):
    TOKEN_NOT_PROVIDED = "auth.token_not_provided", _("Token não fornecido.")
    INVALID_TOKEN = "auth.invalid_token", _("Token inválido.")
    USER_INACTIVE = "auth.user_inactive", _("Usuário inativo.")
```

O middleware usa `error_response()`; os status handlers usam códigos
`core.bad_request`, `auth.not_authenticated`, `core.not_found`,
`auth.permission_denied`, `core.internal_error`.

- [ ] **Step 4: Verificar handlers e middleware**

Run: `uv run pytest apps/api/core/tests/test_status_handlers.py apps/api/autenticacao/tests/test_passthrough.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/core/status_handlers.py apps/api/core/tests/test_status_handlers.py apps/api/autenticacao/errors.py apps/api/autenticacao/middleware.py apps/api/autenticacao/tests/test_passthrough.py api/urls.py
git commit -m "refactor(errors): unify django error responses"
```

---

### Task 4: Migrar erros de domínio e respostas manuais

**Files:**
- Create: `apps/organizacoes/errors.py`
- Modify: `apps/organizacoes/permissions.py`
- Modify: `apps/organizacoes/serializers.py`
- Modify: `apps/organizacoes/tests/test_api.py`
- Modify: `apps/api/base/views.py`
- Modify: `apps/api/base/tests/test_bulk_update.py`

**Interfaces:**
- Produces: `OrganizationErrorCode` e códigos core de operações CRUD.
- Consumes: `APIError` com membro de `TextChoices`.

- [ ] **Step 1: Escrever testes para códigos de negócio**

Cobrir header ausente, vínculo inválido, papel insuficiente, convite inválido,
expirado/usado, e-mail diferente, `ProtectedError`, payload de bulk update
inválido e registro inexistente.

Exemplo:

```python
assert response.status_code == 422
assert response.data["errors"][0]["code"] == "organizations.header_required"
assert response.data["errors"][0]["field"] == "X-Organization"
```

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/organizacoes/tests/test_api.py apps/api/base/tests/test_bulk_update.py -q`

Expected: FAIL com `detail`/`mensagem` ou status antigo.

- [ ] **Step 3: Declarar códigos em `apps/organizacoes/errors.py`**

Incluir, no mínimo:

```python
class OrganizationErrorCode(models.TextChoices):
    HEADER_REQUIRED = "organizations.header_required", _("Header obrigatório.")
    MEMBERSHIP_REQUIRED = "organizations.membership_required", _("Vínculo ativo obrigatório.")
    ROLE_INSUFFICIENT = "organizations.role_insufficient", _("Papel insuficiente.")
    INVITATION_INVALID = "organizations.invitation_invalid", _("Convite inválido.")
    INVITATION_EXPIRED = "organizations.invitation_expired", _("Convite expirado ou utilizado.")
    INVITATION_EMAIL_MISMATCH = "organizations.invitation_email_mismatch", _("Convite pertence a outro e-mail.")
```

- [ ] **Step 4: Substituir respostas manuais por exceptions**

Permissões e serializers levantam `APIError`; `BaseModelViewSet` usa códigos
core para protected delete e payloads inválidos. Não construir envelopes
diretamente fora de `error_response()`/handler.

- [ ] **Step 5: Verificar GREEN**

Run: `uv run pytest apps/organizacoes/tests/test_api.py apps/api/base/tests/test_bulk_update.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/organizacoes/errors.py apps/organizacoes/permissions.py apps/organizacoes/serializers.py apps/organizacoes/tests/test_api.py apps/api/base/views.py apps/api/base/tests/test_bulk_update.py
git commit -m "refactor(errors): migrate domain error codes"
```

---

### Task 5: OpenAPI, convenções e verificação integral

**Files:**
- Modify: `apps/api/core/errors.py`
- Create: `apps/api/core/schema.py`
- Create: `apps/api/core/tests/test_error_schema.py`
- Modify: `.ai/CONVENTIONS.md`
- Modify: `docs/reference/api.md`
- Modify: `docs/explanation/arquitetura.md`

**Interfaces:**
- Produces: componentes OpenAPI `APIErrorItem` e `APIErrorResponse`.
- Produces: helper/decorator para documentar códigos possíveis por operação.

- [ ] **Step 1: Escrever teste de schema**

Gerar o schema e afirmar que `APIErrorResponse` contém `errors`, `request_id`,
`code`, `message`, `field`, `path`, `context`, e que uma operação de exemplo
documenta `401/403/422`.

- [ ] **Step 2: Implementar extensão do drf-spectacular**

Manter a integração pequena: serializers de schema em `schema.py` e um
decorator declarativo que recebe membros de `TextChoices`, sem duplicar o
registry.

- [ ] **Step 3: Atualizar convenções**

Documentar a exceção à regra “choices em `models.py`”: códigos de erro são
`TextChoices` e ficam obrigatoriamente no `errors.py` do app. Documentar que
strings de código soltas são proibidas.

- [ ] **Step 4: Verificar documentação e suíte**

Run: `uv run pytest apps/api/core/tests apps/api/autenticacao/tests apps/api/base/tests apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

Run: `uv run python manage.py spectacular --validate --file /tmp/api-errors-schema.yml`

Expected: exit 0.

Run: `uv run mkdocs build --strict`

Expected: exit 0.

Run: `uv run ruff check api apps`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/core/errors.py apps/api/core/schema.py apps/api/core/tests/test_error_schema.py .ai/CONVENTIONS.md docs/reference/api.md docs/explanation/arquitetura.md
git commit -m "docs(errors): publish api error contract"
```
