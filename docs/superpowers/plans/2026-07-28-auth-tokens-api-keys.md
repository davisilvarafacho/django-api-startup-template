# Auth Tokens and API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o modelo swappable de token, login/logout refatorados, sessões gerenciáveis, step-up authentication e API keys multi-tenant com scopes.

**Architecture:** Um único `AuthToken` próprio implementa a interface esperada pelo Knox e concentra estado de sessão/reset/API key. `TokenMetaData` mantém dados operacionais. Services transacionais encapsulam emissão, rotação e revogação; views finas expõem superfícies separadas para sessões e API keys.

**Tech Stack:** Django 5.2, DRF 3.16, django-rest-knox 5.0.2, django-rls, django-guardian, django-rules, auditlog, drf-spectacular, pytest-django.

## Global Constraints

- A spec normativa é `docs/superpowers/specs/2026-07-28-auth-tokens-api-keys-design.md`.
- Executar primeiro `2026-07-28-api-errors.md` e `2026-07-28-base-resource-permissions.md`.
- Preservar `Authorization: Token ...`, expiração e contrato externo do Knox.
- Tipos: sessão `1`, reset `2`, API key `999`.
- Um único modelo de token; não criar modelo `APIKey`.
- Campo público de responsável: `responsavel`; criador: `created_by`.
- API key pertence a exatamente uma organização e usa UUID nas URLs.
- Plain token aparece somente na criação/rotação.
- URLs usam `_`, nunca `-`.
- API keys têm scopes independentes das permissions do responsável.
- Não criar nem alterar migrations. Usar `--nomigrations` nos testes de banco até o reset completo anterior ao lançamento.
- `pyproject.toml` e `uv.lock` já podem conter a atualização de `requests` feita por outro fluxo; não misturar essa mudança nos commits deste plano.

---

### Task 1: Implementar o modelo swappable compatível com Knox

**Files:**
- Modify: `apps/api/autenticacao/models.py`
- Modify: `apps/api/autenticacao/admin.py`
- Modify: `apps/api/autenticacao/tests/test_token_types.py`
- Create: `apps/api/autenticacao/tests/test_token_model.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `TokenType`, `AuthTokenManager.create(user, expiry, prefix, **kwargs)`.
- Produces: `AuthToken` com `uuid`, `digest`, `token_key`, `responsavel`,
  `created_at`, `last_modified_at`, `expiry`, `type`, `name`, `organization`,
  `created_by`, `scopes`, estado de revogação/suspensão e `replaced_by`.
- Compatibility aliases: `AuthToken.user`, `AuthToken.created`.

- [ ] **Step 1: Escrever testes falhando do contrato Knox**

```python
def test_settings_aponta_para_token_proprio(settings):
    assert settings.KNOX_TOKEN_MODEL == "autenticacao.AuthToken"


@pytest.mark.django_db
def test_manager_retorna_instancia_e_plain_token(usuario):
    instance, plain = AuthToken.objects.create(user=usuario)
    assert instance.responsavel == usuario
    assert instance.user == usuario
    assert instance.created == instance.created_at
    assert plain.startswith(instance.token_key)
    assert instance.digest != plain
```

Cobrir UUID, related name `auth_token_set`, type default, aliases, expiry,
constraints de API key e `Meta.swappable`.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_token_model.py apps/api/autenticacao/tests/test_token_types.py -q`

Expected: FAIL porque o modelo ativo ainda é `knox.AuthToken`.

- [ ] **Step 3: Implementar manager e modelo**

O manager mantém a assinatura do Knox e mapeia `user` para `responsavel`:

```python
class AuthTokenManager(models.Manager):
    def create(self, user=None, expiry=knox_settings.TOKEN_TTL, prefix=knox_settings.TOKEN_PREFIX, **kwargs):
        responsavel = kwargs.pop("responsavel", user)
        plain_token = prefix + crypto.create_token_string()
        digest = crypto.hash_token(plain_token)
        expires_at = timezone.now() + expiry if expiry is not None else None
        instance = super().create(
            digest=digest,
            token_key=plain_token[:CONSTANTS.TOKEN_KEY_LENGTH],
            responsavel=responsavel,
            expiry=expires_at,
            **kwargs,
        )
        return instance, plain_token
```

Adicionar constraints condicionais por `type`; `replaced_by` é self-FK
anulável; `digest` permanece PK; `uuid` é único e indexado.

- [ ] **Step 4: Ativar modelo e admin**

Definir `KNOX_TOKEN_MODEL = "autenticacao.AuthToken"`. O admin nunca mostra
digest completo, token key ou metadata sensível; lista UUID, tipo, nome,
organização, responsável, expiração e estado.

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_token_model.py apps/api/autenticacao/tests/test_token_types.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/models.py apps/api/autenticacao/admin.py apps/api/autenticacao/tests/test_token_model.py apps/api/autenticacao/tests/test_token_types.py api/settings.py
git commit -m "feat(auth): add swappable token model"
```

---

### Task 2: Reorientar `TokenMetaData` e emissão transacional

**Files:**
- Modify: `apps/api/autenticacao/models.py`
- Create: `apps/api/autenticacao/services.py`
- Create: `apps/api/autenticacao/tests/test_token_services.py`
- Modify: `apps/api/autenticacao/utils.py`

**Interfaces:**
- Produces: `issue_token(*, responsavel, token_type, created_by, expiry, metadata_input, organization=None, name="", scopes=()) -> IssuedToken`.
- Produces: `IssuedToken(instance: AuthToken, plain_token: str)`.
- Produces: `build_token_metadata(request, payload) -> dict`.

- [ ] **Step 1: Escrever testes de atomicidade**

Cobrir:

```python
@pytest.mark.django_db
def test_issue_token_cria_token_e_metadata_na_mesma_transacao(...):
    issued = issue_token(...)
    assert issued.instance.metadata.device_name == "Notebook"
    assert issued.plain_token


def test_metadata_nao_possui_type_nem_scopes():
    assert not hasattr(TokenMetaData, "type")
    assert not hasattr(TokenMetaData, "scopes")
```

Simular falha na criação do metadata e afirmar que nenhum token permanece.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_token_services.py -q`

Expected: FAIL por serviço inexistente.

- [ ] **Step 3: Alterar relacionamento e mover campos**

`TokenMetaData.token` usa `settings.KNOX_TOKEN_MODEL`. Remover `type`/`scopes`.
Adicionar `reauthenticated_at` e o nível de autenticação recente necessário ao
Task 5.

- [ ] **Step 4: Implementar emissão**

`issue_token()` abre `transaction.atomic()`, chama o manager e cria metadata.
Geolocalização e parsing de user agent ficam em helpers; PostHog e avaliação de
risco não ficam dentro do model.

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_token_services.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/models.py apps/api/autenticacao/services.py apps/api/autenticacao/utils.py apps/api/autenticacao/tests/test_token_services.py
git commit -m "refactor(auth): centralize token issuance"
```

---

### Task 3: Autenticar estado, tipo e tenant da API key

**Files:**
- Modify: `apps/api/autenticacao/authentications.py`
- Modify: `apps/api/autenticacao/middleware.py`
- Modify: `apps/api/autenticacao/permissions.py`
- Modify: `apps/api/autenticacao/tests/test_token_types.py`
- Modify: `apps/api/autenticacao/tests/test_passthrough.py`
- Create: `apps/api/autenticacao/tests/test_api_key_tenancy.py`
- Modify: `apps/organizacoes/permissions.py`
- Modify: `apps/organizacoes/middleware.py`

**Interfaces:**
- Produces: `TypedTokenAuthentication.validate_user(auth_token)`.
- Produces: `resolve_token_organization(request, token)`.
- Session pipeline: header -> membership -> Django permissions.
- API key pipeline: token organization -> RLS -> scopes.

- [ ] **Step 1: Escrever matriz de autenticação**

Testar sessão, reset recusado, API key ativa, expirada, revogada, suspensa,
responsável inativo e responsável sem vínculo. Testar header ausente,
coincidente e conflitante para API key.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_token_types.py apps/api/autenticacao/tests/test_api_key_tenancy.py -q`

Expected: FAIL com leitura antiga de `metadata.type` e tenant por header.

- [ ] **Step 3: Implementar validação sem hard delete**

Sobrescrever a limpeza do Knox: tokens inválidos geram `APIError` tipado e API
keys permanecem no banco. `validate_user()` lê `auth_token.type` e devolve
`auth_token.responsavel`.

- [ ] **Step 4: Resolver tenant da credencial**

Para API key, preencher `request.organizacao` pelo token, rejeitar header
conflitante e chamar `definir_organizacao_atual()`. Não buscar `Vinculo` para
decidir permissions do responsável; buscar apenas para validar que o responsável
continua ativo na organização.

- [ ] **Step 5: Separar pipelines de permission**

`CustomDjangoModelPermissions` ignora permissions pessoais quando
`request.auth.type == API_KEY`; `TokenScopePermission` passa a ser obrigatória
nesse caso. Endpoints administrativos podem declarar `session_only = True`.

- [ ] **Step 6: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_passthrough.py apps/api/autenticacao/tests/test_token_types.py apps/api/autenticacao/tests/test_api_key_tenancy.py apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/authentications.py apps/api/autenticacao/middleware.py apps/api/autenticacao/permissions.py apps/api/autenticacao/tests apps/organizacoes/permissions.py apps/organizacoes/middleware.py
git commit -m "feat(auth): enforce token state and api key tenant"
```

---

### Task 4: Refatorar login e avaliação de risco

**Files:**
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/services.py`
- Create: `apps/api/autenticacao/risk.py`
- Modify: `apps/api/autenticacao/views.py`
- Create: `apps/api/autenticacao/tests/test_login.py`
- Modify: `apps/api/autenticacao/public_routes.py`

**Interfaces:**
- Produces: `LoginSerializer`, `LoginResponseSerializer`.
- Produces: `evaluate_login_risk(token_metadata) -> RiskAssessment`.
- `LoginView.post()` apenas valida, emite, avalia risco, sinaliza e responde.

- [ ] **Step 1: Escrever testes de contrato de login**

Cobrir credencial inválida, token limit, metadata de dispositivo, token tipo
sessão, evento de login, risco por mudança de país e ausência de segredo em
analytics/logs.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_login.py -q`

Expected: FAIL com view monolítica atual.

- [ ] **Step 3: Implementar serializers e risk service**

Mover autenticação de credenciais para serializer; mover comparação de logins
recentes para `risk.py`; manter PostHog fora da transação e enviar apenas
campos scrubbed.

- [ ] **Step 4: Reescrever view fina**

Usar `issue_token()` e serializar:

```python
{
    "token": issued.plain_token,
    "expiry": issued.instance.expiry,
    "session": {"uuid": issued.instance.uuid, ...},
}
```

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_login.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/serializers.py apps/api/autenticacao/services.py apps/api/autenticacao/risk.py apps/api/autenticacao/views.py apps/api/autenticacao/tests/test_login.py apps/api/autenticacao/public_routes.py
git commit -m "refactor(auth): simplify login flow"
```

---

### Task 5: Implementar step-up authentication reutilizável

**Files:**
- Create: `apps/api/autenticacao/recent_auth.py`
- Modify: `apps/api/autenticacao/permissions.py`
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Create: `apps/api/autenticacao/tests/test_recent_auth.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `@require_recent_auth(max_age=300, require_mfa=None)`.
- Produces: `RecentAuthenticationPermission`.
- Produces: `POST /auth/reauthenticate/`.

- [ ] **Step 1: Escrever testes do decorator e permission**

Cobrir decorator em método/action e classe, default de 300 segundos, override,
sessão recente/antiga, API key recusada, senha inválida e MFA obrigatório quando
ativo.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_recent_auth.py -q`

Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar marcador**

O decorator grava configuração no objeto decorado e devolve o mesmo objeto. A
permission resolve primeiro método da action e depois classe/view, sem envolver
a execução.

- [ ] **Step 4: Implementar endpoint**

`ReauthenticateSerializer` valida senha e chama a interface MFA (stub seguro
enquanto a spec MFA não estiver implementada). Em sucesso, atualiza apenas o
metadata da sessão atual:

```python
metadata.reauthenticated_at = timezone.now()
metadata.save(update_fields=["reauthenticated_at"])
```

- [ ] **Step 5: Adicionar permission global opt-in**

Incluir `RecentAuthenticationPermission` nas defaults; sem marcador ela retorna
`True`. Falhas usam `auth.reauthentication_required`.

- [ ] **Step 6: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_recent_auth.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/recent_auth.py apps/api/autenticacao/permissions.py apps/api/autenticacao/serializers.py apps/api/autenticacao/views.py apps/api/autenticacao/urls.py apps/api/autenticacao/tests/test_recent_auth.py api/settings.py
git commit -m "feat(auth): add recent authentication guard"
```

---

### Task 6: Entregar gerenciamento de sessões

**Files:**
- Modify: `apps/api/autenticacao/services.py`
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Create: `apps/api/autenticacao/tests/test_sessions_api.py`

**Interfaces:**
- Produces: `SessionViewSet`.
- Produces endpoints `/auth/logout/`, `/auth/logout_all/`,
  `/auth/sessions/`, `/auth/sessions/current/`,
  `/auth/sessions/{uuid}/`, `/auth/sessions/revoke_all_except_current/`.

- [ ] **Step 1: Escrever testes HTTP de sessões**

Cobrir listagem somente do usuário, current, rename, revoke específica,
logout atual, logout_all e revoke_all_except_current. Afirmar que operações em
sessão nunca removem API key/reset e nunca expõem digest/token_key/plain token.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_sessions_api.py -q`

Expected: FAIL com endpoints antigos.

- [ ] **Step 3: Implementar services de revogação filtrados**

Todas as queries incluem `type=TokenType.SESSION`. Logout atual revoga/remove
somente `request.auth` quando for sessão. Revogação por UUID verifica
responsável. `logout_all` inclui a atual; `revoke_all_except_current` preserva-a.

- [ ] **Step 4: Implementar serializers/views/URLs**

Serializer de saída: UUID, device, localização aproximada, created_at,
last_used, expiry, risk e `is_current`. PATCH aceita somente `device_name`.

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_sessions_api.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/services.py apps/api/autenticacao/serializers.py apps/api/autenticacao/views.py apps/api/autenticacao/urls.py apps/api/autenticacao/tests/test_sessions_api.py
git commit -m "feat(auth): add session management api"
```

---

### Task 7: Entregar CRUD e rotação de API keys

**Files:**
- Modify: `apps/api/autenticacao/models.py`
- Modify: `apps/api/autenticacao/services.py`
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Create: `apps/api/autenticacao/tests/test_api_keys.py`
- Modify: `apps/organizacoes/models.py`

**Interfaces:**
- Produces: `APIKeyViewSet`.
- Produces: `rotate_api_key()`, `suspend_api_key()`, `resume_api_key()`,
  `revoke_api_key()`.
- Endpoints em `/auth/api_keys/` e actions `rotate`, `suspend`, `resume`.

- [ ] **Step 1: Escrever testes HTTP e de serviço**

Cobrir criação, detalhe, patch, revogação lógica, rotação atômica, suspensão,
retomada, expiração opcional, permissões por action, autenticação recente,
delegação de scopes, organização fixa e plain token exibido uma vez.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_api_keys.py -q`

Expected: FAIL por ViewSet/services inexistentes.

- [ ] **Step 3: Declarar permissions do modelo**

Além das defaults, criar `rotate_apikey` e
`grant_unrestricted_apikey`. Mapear actions administrativas:

```text
list/retrieve -> view_apikey
create -> add_apikey
partial_update/suspend/resume -> change_apikey
destroy -> delete_apikey
rotate -> rotate_apikey
```

- [ ] **Step 4: Implementar criação e alteração**

O serializer recebe `name`, `responsavel`, `scopes`, `expiry`; organização vem
de `request.organizacao`. Validar vínculo ativo e
`validate_scope_delegation()`. Aplicar `@require_recent_auth()` em create,
rotate e alterações de responsável/scopes.

- [ ] **Step 5: Implementar rotação atômica**

```python
with transaction.atomic():
    current = AuthToken.objects.select_for_update().get(uuid=uuid, type=TokenType.API_KEY)
    issued = issue_token(...campos_copiados...)
    current.revoked_at = timezone.now()
    current.revoked_by = actor
    current.replaced_by = issued.instance
    current.save(update_fields=["revoked_at", "revoked_by", "replaced_by"])
```

Retornar plain token somente no response de rotação.

- [ ] **Step 6: Implementar suspensão automática**

Autenticação faz fail-closed quando responsável/vínculo fica inválido. Um
service idempotente materializa `suspended_at` e reason para auditoria. Retomar
revalida vínculo; key revogada nunca retoma.

- [ ] **Step 7: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_api_keys.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/models.py apps/api/autenticacao/services.py apps/api/autenticacao/serializers.py apps/api/autenticacao/views.py apps/api/autenticacao/urls.py apps/api/autenticacao/tests/test_api_keys.py apps/organizacoes/models.py
git commit -m "feat(auth): add organization api keys"
```

---

### Task 8: Auditoria, schema, documentação e verificação final

**Files:**
- Modify: `apps/api/autenticacao/admin.py`
- Create: `apps/api/autenticacao/audit.py`
- Create: `apps/api/autenticacao/tests/test_token_audit.py`
- Create: `apps/api/autenticacao/schema.py`
- Create: `apps/api/autenticacao/tests/test_schema.py`
- Modify: `docs/explanation/autenticacao.md`
- Modify: `docs/reference/api.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Produces eventos: create, rotate, suspend, resume, revoke,
  responsible_changed, scopes_changed.
- OpenAPI documenta sessões, API keys, step-up, scopes e códigos de erro.

- [ ] **Step 1: Escrever testes de auditoria e scrub**

Assegurar presença de actor, key UUID, responsável, criador, organização e
request ID. Assegurar ausência de plain token, digest e token_key em auditlog,
logs, Sentry e PostHog.

- [ ] **Step 2: Implementar eventos auditáveis**

Centralizar emissão em `audit.py`; services chamam após a transação. Não
duplicar eventos nas views.

- [ ] **Step 3: Documentar schema**

Schema de criação/rotação marca `token` como write-only/returned-once. Listagem
não inclui segredos. Documentar status/códigos do registry de erros.

- [ ] **Step 4: Atualizar documentação e roadmap**

Remover a descrição antiga de `TokenMetaData.type`; documentar
`KNOX_TOKEN_MODEL`, endpoints com `_`, scopes, RLS, responsável, rotação e
step-up. Marcar apenas itens efetivamente entregues.

- [ ] **Step 5: Executar verificação proporcional ao risco**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests apps/api/core/tests apps/api/base/tests apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

Run: `uv run ruff check api apps`

Expected: PASS.

Run: `uv run python manage.py spectacular --validate --file /tmp/auth-schema.yml`

Expected: exit 0.

Run: `uv run mkdocs build --strict`

Expected: exit 0.

Run: `uv run python manage.py check`

Expected: exit 0.

Não executar `makemigrations --check` como gate desta branch: por decisão
explícita, os models novos permanecerão sem migrations até o reset integral.
Registrar essa exceção no handoff e no roadmap operacional.

- [ ] **Step 6: Commit**

```bash
git add apps/api/autenticacao/admin.py apps/api/autenticacao/audit.py apps/api/autenticacao/tests/test_token_audit.py apps/api/autenticacao/schema.py apps/api/autenticacao/tests/test_schema.py docs/explanation/autenticacao.md docs/reference/api.md docs/ROADMAP.md
git commit -m "docs(auth): publish token management contract"
```
