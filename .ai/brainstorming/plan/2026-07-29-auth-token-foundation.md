# Auth Token Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a fundação de tokens tipados, emissão centralizada, reautenticação recente e limpeza diária que os fluxos de senha e MFA consumirão.

**Architecture:** Um `AuthToken` swappable compatível com Knox concentra tipo, estado e expiração; `TokenMetaData` mantém dados operacionais. Services transacionais emitem/revogam tokens, e um serviço idempotente de cleanup é compartilhado por command e Celery Beat.

**Tech Stack:** Django 5.2, DRF 3.16, django-rest-knox 5.0.2, Celery 5.6, django-celery-beat 2.9, PostgreSQL, pytest-django.

## Global Constraints

- Spec normativa: `.ai/brainstorming/spec/2026-07-29-mfa-password-security-design.md`.
- Branch: `feat/mfa-2fa-hibp`; sincronizar `main` antes de alterar código.
- Tipos: `TOKEN=1`, `RESET_PASSWORD=2`, `PRE_AUTH=3`, `API_KEY=999`.
- `AuthToken.EPHEMERAL_TYPES` contém somente `PRE_AUTH` e `RESET_PASSWORD`.
- `TypedTokenAuthentication` aceita somente `TOKEN` e `API_KEY`.
- Nenhum segredo de token aparece em logs, analytics ou serialização de leitura.
- O histórico de migrations de autenticação será consolidado antes do primeiro release; não executar reset de banco local automaticamente.
- Cada task termina com testes focados e commit Conventional Commit.

---

## File Structure

- `apps/api/core/errors.py`: envelope e exceção tipada compartilhada.
- `apps/api/autenticacao/errors.py`: códigos públicos de autenticação.
- `apps/api/autenticacao/models.py`: `AuthToken`, manager, tipos e metadata.
- `apps/api/autenticacao/services.py`: emissão/revogação transacional.
- `apps/api/autenticacao/recent_auth.py`: decorator e permission step-up.
- `apps/api/autenticacao/token_cleanup.py`: seleção e remoção de expirados.
- `apps/api/autenticacao/tasks.py`: adapter Celery para cleanup.
- `apps/api/autenticacao/management/commands/cleanup_expired_auth_tokens.py`: adapter CLI.
- `apps/api/autenticacao/migrations/0001_initial.py`: baseline consolidado.

### Task 1: Sincronizar a branch e portar erros tipados mínimos

**Files:**
- Modify: `apps/api/core/status_handlers.py`
- Create: `apps/api/core/errors.py`
- Create: `apps/api/autenticacao/errors.py`
- Create: `apps/api/core/tests/test_errors.py`

**Interfaces:**
- Produces: `APIError(code: str, *, status_code: int, message: str | None = None, context: dict | None = None)`.
- Produces: `AuthErrorCode` com códigos usados nos três planos.

- [ ] **Step 1: Sincronizar a branch**

Run:

```bash
git status --short
git merge main
```

Expected: status inicialmente limpo e merge sem descartar o commit da spec.

- [ ] **Step 2: Escrever testes do envelope**

```python
def test_api_error_expoe_codigo_e_mensagem():
    error = APIError("auth.invalid_token", status_code=401, message="Token inválido.")
    assert error.detail == {
        "code": "auth.invalid_token",
        "message": "Token inválido.",
    }


def test_api_error_nao_expoe_contexto_interno():
    error = APIError("auth.invalid_token", status_code=401, context={"digest": "secret"})
    assert "digest" not in str(error.detail)
```

- [ ] **Step 3: Confirmar RED**

Run: `uv run pytest apps/api/core/tests/test_errors.py -q`

Expected: FAIL com `ModuleNotFoundError: apps.api.core.errors`.

- [ ] **Step 4: Implementar a exceção e o registry**

```python
class APIError(APIException):
    def __init__(self, code, *, status_code, message=None, context=None):
        self.status_code = status_code
        self.code = code
        self.context = context or {}
        super().__init__({"code": code, "message": message or code})
```

`AuthErrorCode` deve declarar, com valores literais, `INVALID_CREDENTIALS`,
`INVALID_TOKEN`, `EXPIRED_TOKEN`, `REVOKED_TOKEN`,
`REAUTHENTICATION_REQUIRED`, `INVALID_CHALLENGE`, `INVALID_OTP`,
`OTP_COOLDOWN`, `TOO_MANY_ATTEMPTS`, `PWNED_PASSWORD` e
`DELIVERY_UNAVAILABLE`.

- [ ] **Step 5: Normalizar o handler e verificar**

`custom_exception_handler()` preserva `{"code", "message"}` de `APIError` e
normaliza exceções DRF sem incluir `context`.

Run: `uv run pytest apps/api/core/tests/test_errors.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/core/errors.py apps/api/core/status_handlers.py apps/api/core/tests/test_errors.py apps/api/autenticacao/errors.py
git commit -m "feat(errors): add typed authentication errors"
```

### Task 2: Implementar o modelo swappable e emissão centralizada

**Files:**
- Modify: `apps/api/autenticacao/models.py`
- Create: `apps/api/autenticacao/services.py`
- Modify: `apps/api/autenticacao/authentications.py`
- Modify: `apps/api/autenticacao/utils.py`
- Modify: `api/settings.py`
- Replace: `apps/api/autenticacao/migrations/0001_initial.py`
- Delete: `apps/api/autenticacao/migrations/0002_tokenmetadata_type.py`
- Create: `apps/api/autenticacao/tests/test_token_model.py`
- Create: `apps/api/autenticacao/tests/test_token_services.py`
- Modify: `conftest.py`

**Interfaces:**
- Produces: `TokenType`, `AuthTokenManager`, `AuthToken`, `TokenMetaData`.
- Produces: `IssuedToken(instance: AuthToken, plain_token: str)`.
- Produces: `issue_token(*, responsavel, token_type, expiry, metadata_input) -> IssuedToken`.
- Produces: `revoke_tokens(user, *, types, exclude_digest=None) -> int`.

- [ ] **Step 1: Escrever testes do contrato**

Adicionar fixtures compartilhadas:

```python
@pytest.fixture
def usuario(db):
    return UsuarioFactory(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()
```

```python
def test_tipos_efemeros_sao_explicitos():
    assert AuthToken.EPHEMERAL_TYPES == frozenset(
        {TokenType.PRE_AUTH, TokenType.RESET_PASSWORD}
    )


@pytest.mark.django_db
def test_manager_retorna_token_puro_uma_vez(usuario):
    instance, plain = AuthToken.objects.create(user=usuario, type=TokenType.TOKEN)
    assert instance.responsavel == usuario
    assert plain.startswith(instance.token_key)
    assert instance.digest != plain


def test_is_expired(settings):
    token = AuthToken(expiry=timezone.now())
    assert token.is_expired is True
    assert AuthToken(expiry=None).is_expired is False
```

Adicionar testes para: efêmero sem `expiry` recusado, reset/pre-auth recusados
por `TypedTokenAuthentication`, metadata criada atomicamente e rollback quando
metadata falha.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_token_model.py apps/api/autenticacao/tests/test_token_services.py -q`

Expected: FAIL porque `AuthToken` próprio e `issue_token` não existem.

- [ ] **Step 3: Implementar manager e estado**

O manager usa `knox.crypto.create_token_string()` e `hash_token()`, converte TTL
relativo em data absoluta e chama `full_clean()` antes de persistir. O modelo
expõe aliases `user` e `created` para Knox, `is_expired` com docstring e
constraints que exigem `expiry` para `EPHEMERAL_TYPES`.

- [ ] **Step 4: Implementar emissão transacional**

```python
@dataclass(frozen=True)
class IssuedToken:
    instance: AuthToken
    plain_token: str


def issue_token(*, responsavel, token_type, expiry, metadata_input):
    with transaction.atomic():
        instance, plain_token = AuthToken.objects.create(
            responsavel=responsavel,
            type=token_type,
            expiry=expiry,
        )
        TokenMetaData.objects.create(token=instance, **metadata_input)
    return IssuedToken(instance=instance, plain_token=plain_token)
```

`build_token_metadata(request, payload)` continuará responsável por device,
IP, user-agent e geolocalização.

- [ ] **Step 5: Consolidar migrations**

Gerar um único `0001_initial.py` que cria `AuthToken` antes de
`TokenMetaData`, depende de `settings.AUTH_USER_MODEL` e `knox.0009`, e usa FK
direta para `autenticacao.AuthToken`. Não apagar banco local. Validar o
baseline em banco de teste novo:

Run: `uv run python manage.py makemigrations --check --dry-run`

Expected: `No changes detected`.

- [ ] **Step 6: Ativar e verificar**

Definir `KNOX_TOKEN_MODEL = "autenticacao.AuthToken"` e mover `type`/`scopes`
de metadata para o token sem mudar o contrato atual de scopes.

Run: `uv run pytest apps/api/autenticacao/tests/test_token_model.py apps/api/autenticacao/tests/test_token_services.py apps/api/autenticacao/tests/test_token_types.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/settings.py apps/api/autenticacao
git commit -m "feat(auth): add swappable typed token model"
```

### Task 3: Centralizar login e reautenticação recente

**Files:**
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Modify: `apps/api/autenticacao/permissions.py`
- Create: `apps/api/autenticacao/recent_auth.py`
- Create: `apps/api/autenticacao/tests/test_login.py`
- Create: `apps/api/autenticacao/tests/test_recent_auth.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `LoginSerializer`, `LoginResponseSerializer`.
- Produces: `require_recent_auth(max_age=300, require_mfa=None)`.
- Produces: `RecentAuthenticationPermission`.
- Produces: `POST /auth/reauthenticate/`.

- [ ] **Step 1: Escrever testes do login e step-up**

```python
@pytest.mark.django_db
def test_login_emite_sessao_pelo_service(api_client, usuario):
    response = api_client.post(
        "/auth/login/",
        {"email": usuario.email, "password": "senha-valida"},
    )
    assert response.status_code == 200
    assert AuthToken.objects.get().type == TokenType.TOKEN


def test_decorator_marca_idade_maxima():
    @require_recent_auth(max_age=120)
    def action():
        return None

    assert action._recent_auth_required == {"max_age": 120, "require_mfa": None}
```

Cobrir credencial inválida, throttle `auth_login=10/min`, sessão recente,
sessão expirada, API key recusada e throttle `auth_reauthenticate=5/min`.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_login.py apps/api/autenticacao/tests/test_recent_auth.py -q`

Expected: FAIL por emissão direta/ausência de `recent_auth`.

- [ ] **Step 3: Refatorar login**

`LoginSerializer.validate()` usa `authenticate()`. `LoginView` chama
`issue_token(responsavel=user, token_type=TokenType.TOKEN,
expiry=self.get_token_ttl(), metadata_input=metadata_input)`, envia
`user_logged_in` e retorna token, expiração e sessão. Risco/PostHog rodam depois
do commit e nunca recebem token puro.

- [ ] **Step 4: Implementar step-up**

`RecentAuthenticationPermission` resolve marcador na action, handler ou classe,
aceita somente `TokenType.TOKEN` e compara `metadata.reauthenticated_at`.
`ReauthenticateView` valida senha e atualiza o metadata; MFA será conectado no
terceiro plano.

- [ ] **Step 5: Verificar e commit**

Run: `uv run pytest apps/api/autenticacao/tests/test_login.py apps/api/autenticacao/tests/test_recent_auth.py apps/api/autenticacao/tests/test_passthrough.py -q`

Expected: PASS.

```bash
git add api/settings.py apps/api/autenticacao
git commit -m "feat(auth): centralize login and recent authentication"
```

### Task 4: Implementar cleanup, command e Beat

**Files:**
- Create: `apps/api/autenticacao/token_cleanup.py`
- Create: `apps/api/autenticacao/tasks.py`
- Create: `apps/api/autenticacao/management/__init__.py`
- Create: `apps/api/autenticacao/management/commands/__init__.py`
- Create: `apps/api/autenticacao/management/commands/cleanup_expired_auth_tokens.py`
- Create: `apps/api/autenticacao/tests/test_token_cleanup.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `CleanupResult(examined: dict[int, int], deleted: dict[int, int])`.
- Produces: `cleanup_expired_tokens(*, now, batch_size, session_retention)`.
- Produces Celery task `autenticacao.cleanup_expired_tokens`.

- [ ] **Step 1: Escrever testes de política**

```python
@pytest.mark.django_db
def test_cleanup_remove_efemeros_e_preserva_api_key(token_factory):
    token_factory(type=TokenType.PRE_AUTH, expiry=timezone.now() - timedelta(seconds=1))
    api_key = token_factory(type=TokenType.API_KEY, expiry=timezone.now() - timedelta(days=1))
    result = cleanup_expired_tokens(
        now=timezone.now(),
        batch_size=50,
        session_retention=timedelta(days=90),
    )
    assert result.deleted[TokenType.PRE_AUTH] == 1
    assert AuthToken.objects.filter(pk=api_key.pk).exists()
```

Cobrir reset imediato, sessão dentro/fora da retenção, `expiry=None`, dry-run,
lotes, command e chamada da task.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_token_cleanup.py -q`

Expected: FAIL com módulo inexistente.

- [ ] **Step 3: Implementar serviço e adapters**

O serviço seleciona efêmeros com `expiry__lte=now`, sessões com
`expiry__lte=now-session_retention`, nunca seleciona API key e deleta PKs em
lotes transacionais. O command apenas converte argumentos; a task chama o mesmo
serviço e usa `autoretry_for=(OperationalError,)`, backoff e máximo de três
retries.

- [ ] **Step 4: Configurar Beat**

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-auth-tokens": {
        "task": "autenticacao.cleanup_expired_tokens",
        "schedule": crontab(hour=0, minute=0),
    },
}
AUTH_TOKEN_SESSION_RETENTION_DAYS = 90
```

Manter `CELERY_TIMEZONE = TIME_ZONE`, portanto meia-noite significa
`America/Sao_Paulo`.

- [ ] **Step 5: Verificar e commit**

Run: `uv run pytest apps/api/autenticacao/tests/test_token_cleanup.py -q`

Expected: PASS.

Run: `uv run python manage.py cleanup_expired_auth_tokens --dry-run`

Expected: exit 0 e contagens por tipo.

```bash
git add api/settings.py apps/api/autenticacao/token_cleanup.py apps/api/autenticacao/tasks.py apps/api/autenticacao/management apps/api/autenticacao/tests/test_token_cleanup.py
git commit -m "feat(auth): clean expired tokens daily"
```

### Task 5: Verificar a fundação

**Files:**
- Modify: `docs/explanation/autenticacao.md`
- Modify: `.env.example`

- [ ] **Step 1: Documentar tipos, step-up e cleanup**

Documentar os quatro tipos, `EPHEMERAL_TYPES`, `is_expired`, retenção, command,
task e execução às 00:00.

- [ ] **Step 2: Executar gates**

Run: `uv run pytest apps/api/autenticacao/tests apps/api/core/tests -q`

Expected: PASS.

Run: `uv run ruff check api apps`

Expected: PASS.

Run: `uv run python manage.py check`

Expected: `System check identified no issues`.

Run: `uv run python manage.py makemigrations --check --dry-run`

Expected: `No changes detected`.

- [ ] **Step 3: Commit**

```bash
git add docs/explanation/autenticacao.md .env.example
git commit -m "docs(auth): document token foundation"
```
