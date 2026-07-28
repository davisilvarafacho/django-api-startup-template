# Base and Resource Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernizar os campos comuns dos models e oferecer uma linguagem única `resource:action` para scopes e permissions humanas.

**Architecture:** Um mixin abstrato concentra autoria e timestamps, `BaseGlobal`/`Base` o reutilizam, e um registry de recursos traduz o contrato público estável para codenames Django. Models fornecem o recurso padrão; ViewSets podem sobrescrevê-lo; actions customizadas usam decorator.

**Tech Stack:** Django 5.2 models/system checks, DRF ViewSets/permissions, django-guardian, django-rules, pytest-django.

## Global Constraints

- A spec normativa é `docs/superpowers/specs/2026-07-28-auth-tokens-api-keys-design.md`, seções “Campos comuns da Base”, “Permissions humanas” e “Scopes”.
- Campos finais: `created_by`, `created_at`, `last_modified_at`; remover `owner` e os quatro campos separados de data/hora.
- Não existe `last_modified_by`.
- `resource:action` é a interface pública; codenames Django permanecem internos.
- Ações CRUD: `read`, `create`, `update`, `delete`; custom actions são permitidas.
- Wildcards: `resource:*` e `*`.
- Default no model, override no ViewSet.
- Não criar nem alterar migrations. Durante esta fase, testes de banco que dependam do estado novo usam `--nomigrations`; o reset completo ocorrerá antes do lançamento.

---

### Task 1: Extrair campos de auditoria e refatorar `BaseGlobal`

**Files:**
- Modify: `apps/api/base/models.py`
- Modify: `apps/api/base/tests/test_auditlog.py`
- Modify: `apps/api/base/tests/test_bulk_update.py`
- Modify: `apps/organizacoes/models.py`
- Modify: `apps/organizacoes/tests/test_api.py`
- Modify: `apps/usuarios/models.py`
- Modify: `apps/usuarios/factories.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `CreationAuditMixin` com `created_by`, `created_at`,
  `last_modified_at`.
- `BaseGlobal(CreationAuditMixin)` e `Base(BaseGlobal, RLSModel)`.

- [ ] **Step 1: Escrever testes falhando dos campos comuns**

```python
def test_base_global_define_campos_de_auditoria():
    assert BaseGlobal._meta.get_field("created_by").remote_field.on_delete is models.PROTECT
    assert BaseGlobal._meta.get_field("created_at").auto_now_add is True
    assert BaseGlobal._meta.get_field("last_modified_at").auto_now is True
    for antigo in ("owner", "data_criacao", "hora_criacao", "data_ultima_alteracao", "hora_ultima_alteracao"):
        with pytest.raises(FieldDoesNotExist):
            BaseGlobal._meta.get_field(antigo)
```

Cobrir preenchimento automático de `created_by`, criação por sistema (`None`),
clonagem, `internal_fields`, `read_only_fields` e exclusões do auditlog.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/base/tests/test_auditlog.py apps/api/base/tests/test_bulk_update.py -q`

Expected: FAIL porque os campos novos não existem.

- [ ] **Step 3: Implementar `CreationAuditMixin`**

```python
class CreationAuditMixin(models.Model):
    created_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name=_("criado por"),
        related_name="+",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    last_modified_at = models.DateTimeField(_("última alteração em"), auto_now=True)

    class Meta:
        abstract = True
```

Mover para o mixin somente os campos e a resolução do criador. Atualizar
`BaseGlobal.save()`, `clonar()`, campos internos/read-only e reset de clone para
os novos nomes.

- [ ] **Step 4: Atualizar consumidores**

Remover `owner = None`; usar `created_by = None` somente nos models que
deliberadamente não registram criador. Trocar factories/testes que passam
`owner=` por `created_by=`. Atualizar `BASE_AUDITLOG_EXCLUDE_FIELDS` para
`created_at` e `last_modified_at`.

- [ ] **Step 5: Verificar GREEN sem migrations**

Run: `uv run pytest --nomigrations apps/api/base/tests apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

Run: `uv run ruff check apps/api/base apps/organizacoes apps/usuarios api/settings.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/base/models.py apps/api/base/tests apps/organizacoes/models.py apps/organizacoes/tests/test_api.py apps/usuarios/models.py apps/usuarios/factories.py api/settings.py
git commit -m "refactor(base): unify audit fields"
```

---

### Task 2: Criar registry `resource:action`

**Files:**
- Create: `apps/api/core/scope_registry.py`
- Create: `apps/api/core/tests/test_scope_registry.py`
- Modify: `apps/api/core/apps.py`

**Interfaces:**
- Produces: `ScopeAction`, `ScopeDefinition`, `ScopeRegistry`,
  `scope_registry`, `discover_scope_resources()`.
- Produces: `parse_scope(value)`, `matches_scope(granted, required)`,
  `required_django_permissions(scope)`.

- [ ] **Step 1: Escrever testes do parser e wildcards**

```python
@pytest.mark.parametrize(
    ("granted", "required"),
    [
        ("organizations:read", "organizations:read"),
        ("organizations:*", "organizations:delete"),
        ("*", "teams:update"),
    ],
)
def test_scope_concedido_satisfaz_requisito(granted, required):
    assert matches_scope(granted, required)
```

Cobrir formato inválido, recurso duplicado, action desconhecida, lookup e
tradução para `app_label.codename`.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/core/tests/test_scope_registry.py -q`

Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar interfaces**

```python
class ScopeAction(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class ScopeDefinition:
    resource: str
    model: type[models.Model] | None
    action_permissions: Mapping[str, str]
    custom_actions: frozenset[str] = frozenset()
```

O registry valida nomes públicos, resolve CRUD para `view/add/change/delete` e
aceita mapeamentos customizados. Não acoplar a API pública ao nome da classe.

- [ ] **Step 4: Descobrir models e registrar system check**

Models com `api_scope_resource = None` não são expostos. Recursos duplicados
falham no startup, exceto quando são overrides deliberados de ViewSet e não
defaults de model.

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest apps/api/core/tests/test_scope_registry.py -q`

Expected: PASS.

```bash
git add apps/api/core/scope_registry.py apps/api/core/tests/test_scope_registry.py apps/api/core/apps.py
git commit -m "feat(authz): add resource scope registry"
```

---

### Task 3: Derivar scopes de models e ViewSets

**Files:**
- Modify: `apps/api/base/models.py`
- Modify: `apps/api/base/views.py`
- Create: `apps/api/base/tests/test_scopes.py`
- Modify: `apps/api/autenticacao/permissions.py`
- Modify: `apps/api/autenticacao/tests/test_token_scopes.py`

**Interfaces:**
- Produces: `BaseGlobal.api_scope_resource = None`.
- Produces: `UtilsViewSetMixin.get_scope_resource()` e
  `get_required_token_scopes()`.
- Produces: `@require_token_scopes(*scopes)` para custom actions.

- [ ] **Step 1: Escrever testes de resolução automática**

```python
@pytest.mark.parametrize(
    ("action", "scope"),
    [
        ("list", "users:read"),
        ("retrieve", "users:read"),
        ("create", "users:create"),
        ("partial_update", "users:update"),
        ("destroy", "users:delete"),
    ],
)
def test_viewset_deriva_scope_crud(action, scope):
    view = UserViewSet()
    view.action = action
    assert view.get_required_token_scopes() == [scope]
```

Cobrir override `scope_resource = "profile"`, model sem recurso e decorator em
action customizada.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/base/tests/test_scopes.py apps/api/autenticacao/tests/test_token_scopes.py -q`

Expected: FAIL com API antiga `required_token_scopes`.

- [ ] **Step 3: Implementar defaults e override**

`get_scope_resource()` usa primeiro o atributo do ViewSet e depois
`queryset.model.api_scope_resource`. O mapping de actions CRUD é uma constante
imutável. O decorator grava metadado na função; a permission resolve o método
da action sem embrulhar a view.

- [ ] **Step 4: Atualizar `TokenScopePermission`**

Manter temporariamente compatibilidade de leitura com `TokenMetaData.type` até
o plano de tokens mover o campo. Adicionar `resource:*` e `*`; código novo usa
`view.get_required_token_scopes()`/decorator.

- [ ] **Step 5: Verificar e commitar**

Run: `uv run pytest apps/api/base/tests/test_scopes.py apps/api/autenticacao/tests/test_token_scopes.py -q`

Expected: PASS.

```bash
git add apps/api/base/models.py apps/api/base/views.py apps/api/base/tests/test_scopes.py apps/api/autenticacao/permissions.py apps/api/autenticacao/tests/test_token_scopes.py
git commit -m "feat(authz): derive scopes from api resources"
```

---

### Task 4: Traduzir permissions humanas e validar delegação

**Files:**
- Modify: `apps/api/core/scope_registry.py`
- Modify: `apps/api/core/tests/test_scope_registry.py`
- Create: `apps/api/autenticacao/scope_delegation.py`
- Create: `apps/api/autenticacao/tests/test_scope_delegation.py`

**Interfaces:**
- Produces: `scope_registry.display_permissions_for(user)`.
- Produces: `validate_scope_delegation(user, scopes) -> tuple[str, ...]`.
- Usa permission especial `autenticacao.grant_unrestricted_apikey`.

- [ ] **Step 1: Escrever testes de não elevação**

Cobrir:

```python
def test_usuario_nao_delega_scope_sem_permission(usuario):
    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["users:delete"])
    assert exc.value.code == "auth.scope_not_delegable"


def test_resource_wildcard_exige_todas_as_permissions(...): ...
def test_global_wildcard_exige_permission_especial_ou_superuser(...): ...
```

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest --nomigrations apps/api/autenticacao/tests/test_scope_delegation.py -q`

Expected: FAIL por serviço inexistente.

- [ ] **Step 3: Implementar validação**

Para cada scope concreto, resolver permission Django e usar
`user.has_perm()`. Expandir `resource:*` para todas as actions registradas.
Aceitar `*` somente para superuser ou
`autenticacao.grant_unrestricted_apikey`.

- [ ] **Step 4: Verificar e commitar**

Run: `uv run pytest --nomigrations apps/api/core/tests/test_scope_registry.py apps/api/autenticacao/tests/test_scope_delegation.py -q`

Expected: PASS.

```bash
git add apps/api/core/scope_registry.py apps/api/core/tests/test_scope_registry.py apps/api/autenticacao/scope_delegation.py apps/api/autenticacao/tests/test_scope_delegation.py
git commit -m "feat(authz): enforce scope delegation limits"
```

---

### Task 5: Adotar recursos estáveis nas APIs existentes

**Files:**
- Modify: `apps/usuarios/models.py`
- Modify: `apps/organizacoes/models.py`
- Modify: `apps/organizacoes/views.py`
- Modify: `apps/organizacoes/tests/test_api.py`
- Modify: `.ai/CONVENTIONS.md`
- Modify: `docs/explanation/autenticacao.md`

**Interfaces:**
- Registers: `users`, `organizations`, `teams`, `memberships`, `invitations`.
- Custom scope: `invitations:accept`.

- [ ] **Step 1: Atualizar testes para os recursos públicos**

Trocar expectativas `org:read/write` pelos scopes CRUD específicos e adicionar
testes de `invitations:accept`.

- [ ] **Step 2: Declarar defaults nos models**

```python
class Usuario(...):
    api_scope_resource = "users"

class Organizacao(...):
    api_scope_resource = "organizations"
```

Declarar também `teams`, `memberships` e `invitations`.

- [ ] **Step 3: Remover mapas manuais redundantes dos ViewSets**

Usar derivação automática. Marcar `aceitar` com:

```python
@require_token_scopes("invitations:accept")
```

Preservar overrides apenas quando a superfície pública divergir do model.

- [ ] **Step 4: Atualizar convenções e documentação**

Documentar `api_scope_resource`, `scope_resource`, CRUD mapping, custom actions,
wildcards, tradução interna para Django e regra de delegação.

- [ ] **Step 5: Verificação integral**

Run: `uv run pytest --nomigrations apps/api/core/tests/test_scope_registry.py apps/api/base/tests/test_scopes.py apps/api/autenticacao/tests/test_token_scopes.py apps/api/autenticacao/tests/test_scope_delegation.py apps/organizacoes/tests/test_api.py -q`

Expected: PASS.

Run: `uv run ruff check apps/api apps/organizacoes apps/usuarios`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/usuarios/models.py apps/organizacoes/models.py apps/organizacoes/views.py apps/organizacoes/tests/test_api.py .ai/CONVENTIONS.md docs/explanation/autenticacao.md
git commit -m "refactor(authz): adopt resource action permissions"
```
