# Workspaces como subtenancy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** adicionar Workspaces como subtenants de uma Organização, com contexto atual persistido, seleção automática de visualização e isolamento de leitura e escrita garantido por PostgreSQL RLS.

**Architecture:** `apps.workspaces` mantém o control plane (`Workspace`, `VinculoWorkspace` e serviços transacionais); todo model de negócio herdado de `Base` recebe um FK opcional e uma policy restritiva que se soma à policy da Organização. O middleware publica um modo de acesso no contexto RLS e uma permissão global exige `current_workspace` nas rotas de negócio, enquanto rotas de controle usam um modo limitado a registros compartilhados.

**Tech Stack:** Python 3.12, Django 5.2.8, Django REST Framework 3.16.1, django-rls 2.x, PostgreSQL, pytest-django e Ruff.

**Spec:** `.ai/brainstorming/spec/2026-09-21-workspaces-design.md`.

## Global Constraints

- Organização continua sendo tenant, unidade comercial e primeira condição de toda policy RLS.
- Papel pertence ao Vínculo organizacional e não varia por Workspace.
- Administradores e Proprietários ativos têm Vínculo de Workspace obrigatório em todo Workspace ativo; o acesso não pode ser revogado enquanto mantiverem o Papel.
- Rebaixar Administrador ou Proprietário mantém os Vínculos de Workspace existentes.
- `current_workspace` pertence ao `Vinculo`, é persistido entre dispositivos e precisa apontar para acesso ativo da mesma Organização.
- Registro com `workspace = NULL` é compartilhado; registro com Workspace só aparece quando o acesso está ativo e selecionado.
- Model opcional com campo omitido grava `NULL`; model obrigatório com campo omitido usa `current_workspace`; `null` explícito é inválido no model obrigatório.
- API keys acessam todos os Workspaces ativos da Organização e não possuem `current_workspace`.
- `Time` continua sendo agrupamento de pessoas e não é substituído por Workspace.
- Nenhuma dependência de produto será adicionada; usar somente Django, DRF, django-rls e PostgreSQL já instalados.

## Review Focus

1. Um `workspace_id` de outra Organização, ainda que conhecido, deve resultar em ausência/negação sem enumerar o tenant e nunca passar no `WITH CHECK`.
2. A criação concorrente de Workspace e a promoção para Administrador não podem terminar com um acesso obrigatório ausente.
3. Remover da visualização o Workspace atual deve falhar atomicamente, sem alterar parte da seleção.
4. Uma API key que omite Workspace em model obrigatório deve falhar; a mesma key pode informar qualquer Workspace ativo de sua Organização.
5. Desativar Workspace ou acesso entre resolução da request e escrita deve fazer a operação falhar no serviço ou no RLS, sem persistência parcial.

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/workspaces/models.py`, `migrations/` | Control plane e backfill de Workspaces existentes |
| `apps/workspaces/workspaces.py` | Ciclo de vida transacional de Workspace |
| `apps/workspaces/accesses.py` | Concessão obrigatória/normal, Workspace atual e seleção |
| `apps/workspaces/policies.py` | Policy restritiva reutilizada pelos models `Base` |
| `apps/workspaces/context.py` | Chaves e modos do contexto RLS de Workspace |
| `apps/workspaces/permissions.py` | Exigência global de Workspace atual |
| `apps/workspaces/serializers.py`, `views.py`, `urls.py` | API do control plane |
| `apps/organizacoes/models.py`, `onboarding.py`, `memberships.py` | FK atual, Workspace inicial e promoções |
| `apps/organizacoes/context.py`, `middleware.py` | Publicação e limpeza do contexto composto |
| `apps/api/base/models.py`, `serializers.py`, `model_checks.py` | FK abstrato, assignment de escrita e guardrails |
| `apps/api/metadata/handlers.py` | Propagação do Workspace do objeto para sua metadata |
| `apps/*/migrations/` | Coluna `workspace_id` nas tabelas concretas herdadas de `Base` |
| `docs/adr/0009-workspaces-como-subtenancy.md`, `docs/reference/`, `docs/explanation/` | Decisão, contrato e arquitetura publicados |

### Task 1: Criar o control plane e migrar Organizações existentes

**Files:**
- Create: `apps/workspaces/__init__.py`
- Create: `apps/workspaces/apps.py`
- Create: `apps/workspaces/models.py`
- Create: `apps/workspaces/admin.py`
- Create: `apps/workspaces/migrations/0001_initial.py`
- Create: `apps/workspaces/migrations/0002_backfill_workspaces_iniciais.py`
- Create: `apps/workspaces/tests/test_models.py`
- Create: `apps/workspaces/tests/test_backfill.py`
- Modify: `apps/organizacoes/models.py`
- Create: `apps/organizacoes/migrations/0004_vinculo_current_workspace.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `Workspace`, `VinculoWorkspace` e `Vinculo.current_workspace`.
- Produces: tabelas `workspace` e `workspace_vinculo`, consumidas pela policy RLS das Tasks 3 e 7.
- Migration order: `workspaces.0001` depende de `organizacoes.0003`; `organizacoes.0004` depende de `workspaces.0001`; `workspaces.0002` depende de `organizacoes.0004`.

- [ ] **Step 1: Gerar o app pelo comando do projeto e preservar somente os arquivos previstos**

Run:

```bash
uv run python manage.py start_api_app workspaces
```

Expected: `apps/workspaces/` existe e `"apps.workspaces"` aparece alfabeticamente antes de `"apps.usuarios"` em `BUSINESS_APPS`.

- [ ] **Step 2: Escrever os testes de schema e invariantes**

Add to `apps/workspaces/tests/test_models.py`:

```python
from django.db import IntegrityError

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_workspace_e_unico_por_slug_ativo_na_organizacao():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")

    with pytest.raises(IntegrityError):
        Workspace.objects.create(organizacao=organizacao, nome="Outra", slug="matriz")


def test_vinculo_workspace_e_unico_enquanto_nao_excluido():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)

    with pytest.raises(IntegrityError):
        VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)


def test_current_workspace_comeca_nulo():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    assert vinculo.current_workspace is None
```

```bash
uv run pytest apps/workspaces/tests/test_models.py -q --reuse-db
```

Expected: FAIL during import because `apps.workspaces.models` does not define the models yet.

- [ ] **Step 3: Implementar os models do control plane**

Create the following public shape in `apps/workspaces/models.py`:

```python
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import BaseTenantless
from utils.logs import register


class Workspace(BaseTenantless):
    api_scope_resource = "workspaces"

    organizacao = models.ForeignKey(
        "organizacoes.Organizacao",
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="workspaces",
        help_text=_("Organização proprietária do Workspace."),
        db_comment="Organização proprietária do Workspace.",
    )
    nome = models.CharField(_("nome"), max_length=150, help_text=_("Nome do Workspace."), db_comment="Nome do Workspace.")
    slug = models.SlugField(
        _("slug"),
        max_length=60,
        help_text=_("Identificador público dentro da Organização."),
        db_comment="Identificador público dentro da Organização.",
    )

    def __str__(self):
        return f"{self.nome} @ {self.organizacao}"

    class Meta:
        db_table = "workspace"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizacao", "slug"],
                condition=models.Q(is_deleted=False),
                name="workspace_organizacao_slug_unico_nao_excluido",
            ),
        ]


class VinculoWorkspace(BaseTenantless):
    api_scope_resource = "workspace_memberships"

    vinculo = models.ForeignKey(
        "organizacoes.Vinculo",
        on_delete=models.CASCADE,
        related_name="workspaces",
        help_text=_("Vínculo organizacional que recebe acesso."),
        db_comment="Vínculo organizacional que recebe acesso.",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="vinculos",
        help_text=_("Workspace acessível pelo vínculo."),
        db_comment="Workspace acessível pelo vínculo.",
    )
    selected_for_view = models.BooleanField(
        _("selecionado para visualização"),
        default=True,
        help_text=_("Inclui o Workspace nas consultas automáticas."),
        db_comment="Inclui o Workspace nas consultas automáticas.",
    )

    def __str__(self):
        return f"{self.vinculo} @ {self.workspace}"

    class Meta:
        db_table = "workspace_vinculo"
        constraints = [
            models.UniqueConstraint(
                fields=["vinculo", "workspace"],
                condition=models.Q(is_deleted=False),
                name="workspace_vinculo_unico_nao_excluido",
            ),
        ]
        indexes = [
            models.Index(fields=["vinculo", "is_active", "selected_for_view"], name="wv_vinculo_ativo_sel_idx"),
            models.Index(fields=["workspace", "is_active"], name="wv_workspace_ativo_idx"),
        ]


register(Workspace)
register(VinculoWorkspace)
```

Add this field to `Vinculo`:

```python
current_workspace = models.ForeignKey(
    "workspaces.Workspace",
    verbose_name=_("workspace atual"),
    on_delete=models.SET_NULL,
    related_name="vinculos_atuais",
    null=True,
    blank=True,
    help_text=_("Workspace operacional atual deste vínculo."),
    db_comment="Workspace operacional atual deste vínculo.",
)
```

Register both Workspace models in `apps/workspaces/admin.py` with Organização, activity and selection visible but no direct admin mutation of mandatory access.

- [ ] **Step 4: Gerar e ordenar as migrations sem ciclo**

Run:

```bash
uv run python manage.py makemigrations workspaces organizacoes
uv run python manage.py showmigrations workspaces organizacoes
```

Expected: initial Workspace tables precede the FK from `Vinculo`, with no `CircularDependencyError`. Rename migrations to the filenames listed in this task and adjust dependencies explicitly.

- [ ] **Step 5: Escrever e testar o backfill reversível**

In `0002_backfill_workspaces_iniciais.py`, use historical models and this algorithm:

```python
def criar_workspaces_iniciais(apps, schema_editor):
    Organizacao = apps.get_model("organizacoes", "Organizacao")
    Vinculo = apps.get_model("organizacoes", "Vinculo")
    Workspace = apps.get_model("workspaces", "Workspace")
    VinculoWorkspace = apps.get_model("workspaces", "VinculoWorkspace")
    using = schema_editor.connection.alias

    for organizacao in Organizacao.objects.using(using).filter(is_deleted=False).iterator():
        workspace = Workspace.objects.using(using).create(
            organizacao_id=organizacao.pk,
            nome="Principal",
            slug="principal",
        )
        vinculos = Vinculo.objects.using(using).filter(
            organizacao_id=organizacao.pk,
            is_active=True,
            is_deleted=False,
        )
        acessos = [VinculoWorkspace(vinculo_id=vinculo.pk, workspace_id=workspace.pk) for vinculo in vinculos]
        VinculoWorkspace.objects.using(using).bulk_create(acessos)
        vinculos.update(current_workspace_id=workspace.pk)


def remover_workspaces_iniciais(apps, schema_editor):
    Vinculo = apps.get_model("organizacoes", "Vinculo")
    Workspace = apps.get_model("workspaces", "Workspace")
    using = schema_editor.connection.alias
    Vinculo.objects.using(using).update(current_workspace_id=None)
    Workspace.objects.using(using).filter(slug="principal").delete()
```

The forward migration deliberately grants the initial Workspace to every active legacy Vínculo so rollout preserves existing access. Add a `MigrationExecutor` test in `test_backfill.py` proving current/access creation and reverse cleanup.

- [ ] **Step 6: Rodar testes, checks de migration e commitar**

Run:

```bash
uv run pytest apps/workspaces/tests/test_models.py apps/workspaces/tests/test_backfill.py -q --reuse-db
uv run python manage.py makemigrations --check --dry-run
```

Expected: PASS and `No changes detected`.

Commit:

```bash
git add api/settings.py apps/workspaces apps/organizacoes/models.py apps/organizacoes/migrations/0004_vinculo_current_workspace.py
git commit -m "feat: criar control plane de workspaces"
```

### Task 2: Implementar ciclo de vida, acessos obrigatórios e onboarding

**Files:**
- Create: `apps/workspaces/errors.py`
- Create: `apps/workspaces/workspaces.py`
- Create: `apps/workspaces/accesses.py`
- Create: `apps/workspaces/tests/test_workspaces.py`
- Create: `apps/workspaces/tests/test_accesses.py`
- Modify: `apps/organizacoes/onboarding.py`
- Modify: `apps/organizacoes/memberships.py`
- Modify: `apps/organizacoes/tests/test_onboarding.py`
- Modify: `apps/organizacoes/tests/test_memberships.py`

**Interfaces:**
- Produces: `Workspaces.criar()`, `Workspaces.criar_inicial()` e `Workspaces.inativar()`.
- Produces: `AcessosWorkspace.garantir_obrigatorios()`, `conceder()`, `revogar()`, `definir_atual()`, `selecionar_visualizacao()` e `resolver_atual_valido()`.
- Consumes: models e constraints da Task 1.

- [ ] **Step 1: Fixar os erros estáveis e escrever testes de serviço**

Create `WorkspaceErrorCode` with these exact values:

```python
class WorkspaceErrorCode(models.TextChoices):
    CURRENT_REQUIRED = "workspaces.current_required", _("Selecione um Workspace atual.")
    ACCESS_REQUIRED = "workspaces.access_required", _("Acesso ativo ao Workspace obrigatório.")
    WORKSPACE_INACTIVE = "workspaces.inactive", _("Este Workspace está inativo.")
    ORGANIZATION_MISMATCH = "workspaces.organization_mismatch", _("Workspace pertence a outra Organização.")
    MANDATORY_ACCESS = "workspaces.mandatory_access", _("Este acesso é obrigatório para o Papel atual.")
    INVALID_SELECTION = "workspaces.invalid_selection", _("Seleção de Workspaces inválida.")
```

Add tests that cover: creation links every active Administrator/Owner; promotion fills existing Workspaces; demotion preserves links; mandatory revoke returns `MANDATORY_ACCESS`; removing current from selection returns `INVALID_SELECTION` without partial updates.

Run:

```bash
uv run pytest apps/workspaces/tests/test_workspaces.py apps/workspaces/tests/test_accesses.py -q --reuse-db
```

Expected: FAIL because the services do not exist.

- [ ] **Step 2: Implementar as assinaturas públicas dos serviços**

Use these exact public signatures:

```text
Workspaces.criar(*, organizacao: Organizacao, nome: str, slug: str, ator: Usuario | None = None, validar_papel_ator: bool = True) -> Workspace
Workspaces.criar_inicial(*, organizacao: Organizacao, proprietario: Vinculo) -> Workspace
Workspaces.inativar(workspace: Workspace, *, ator: Usuario | None = None, validar_papel_ator: bool = True) -> None
AcessosWorkspace.garantir_obrigatorios(vinculo: Vinculo, *, using: str = "default") -> list[VinculoWorkspace]
AcessosWorkspace.conceder(*, vinculo: Vinculo, workspace: Workspace, ator: Usuario | None = None, validar_papel_ator: bool = True) -> VinculoWorkspace
AcessosWorkspace.revogar(acesso: VinculoWorkspace, *, ator: Usuario | None = None, validar_papel_ator: bool = True) -> None
AcessosWorkspace.definir_atual(*, vinculo: Vinculo, workspace: Workspace) -> Vinculo
AcessosWorkspace.selecionar_visualizacao(*, vinculo: Vinculo, workspace_ids: set[int]) -> list[VinculoWorkspace]
AcessosWorkspace.resolver_atual_valido(vinculo: Vinculo, *, using: str = "default") -> Workspace | None
```

Implementations lock in this order: Organização, affected Vínculos ordered by PK, Workspaces ordered by PK, Vínculos de Workspace ordered by PK. Revalidate `is_active`/`is_deleted` under the locks. Reactivate a soft-deleted pair through `all_objects` after locking it; otherwise create the single active pair.
`revogar()` clears `current_workspace` in the same transaction when it revokes the current access. `resolver_atual_valido()` returns `None` and clears a stale current reference when Workspace, access or Vínculo is inactive/deleted.

- [ ] **Step 3: Preservar obrigatoriedade sob promoção e concorrência**

After persisting `papel_pretendido` inside `Vinculos.atualizar_vinculo`, call the service locally to avoid an import cycle:

```python
if papel_pretendido >= Papel.ADMINISTRADOR and vinculo_bloqueado.is_active and not vinculo_bloqueado.is_deleted:
    from apps.workspaces.accesses import AcessosWorkspace

    AcessosWorkspace.garantir_obrigatorios(vinculo_bloqueado, using=using)
```

Add a transaction test with two database connections/threads: one creates a Workspace while the other promotes a member. Assert after both commits that the promoted Vínculo has exactly one active access for every active Workspace.

- [ ] **Step 4: Integrar o Workspace inicial ao onboarding atômico**

Change `OrganizationOnboarding.criar()` to retain the owner Vínculo and create the initial Workspace before the subscription:

```python
organizacao = Organizacoes.criar(nome=nome, slug=slug, proprietario=conta)
proprietario = Vinculos.criar_proprietario(organizacao, conta)

from apps.workspaces.workspaces import Workspaces

Workspaces.criar_inicial(organizacao=organizacao, proprietario=proprietario)
```

Extend the rollback parametrization with `"workspace"`. Assert the successful flow creates `Principal`, one selected owner access and `proprietario.current_workspace_id == workspace.pk`.

- [ ] **Step 5: Testar inativação e a corrida da Review Focus**

Add tests that prove `Workspaces.inativar()`:

```python
Workspaces.inativar(workspace, ator=administrador)

workspace.refresh_from_db()
vinculo.refresh_from_db()
assert workspace.is_active is False
assert vinculo.current_workspace_id is None
```

In a concurrent test, pause a grant or write after resolving the Workspace, inactivate it in another transaction, then continue. The operation must raise `APIError` with `workspaces.inactive` or fail its final database check; no target row may be created.

- [ ] **Step 6: Rodar a suíte focada e commitar**

Run:

```bash
uv run pytest apps/workspaces/tests/test_workspaces.py apps/workspaces/tests/test_accesses.py apps/organizacoes/tests/test_onboarding.py apps/organizacoes/tests/test_memberships.py -q --reuse-db
```

Expected: PASS.

Commit:

```bash
git add apps/workspaces apps/organizacoes/onboarding.py apps/organizacoes/memberships.py apps/organizacoes/tests
git commit -m "feat: gerenciar ciclo de vida de workspaces"
```

### Task 3: Adicionar o campo abstrato e a policy RLS restritiva

**Files:**
- Create: `apps/workspaces/policies.py`
- Create: `apps/workspaces/tests/test_policy_sql.py`
- Modify: `apps/api/base/models.py`
- Modify: `apps/api/base/model_checks.py`
- Modify: `apps/api/base/tests/test_model_checks.py`
- Modify: `apps/api/metadata/handlers.py`
- Modify: `apps/api/metadata/tests/test_aplicar_metadata.py`
- Create: migrations geradas em `apps/api/metadata/migrations/`, `apps/assinaturas/migrations/` e `apps/assinaturas/subapps/faturamento/migrations/`

**Interfaces:**
- Produces: `WorkspacePolicy`, `Base.workspace` e `Base.workspace_required`.
- Produces: system check `base.W003` para models obrigatórios sem constraint.
- Consumes: tabelas `workspace`, `workspace_vinculo` e `vinculo` da Task 1.

- [ ] **Step 1: Escrever testes unitários da expressão e da herança**

Test that `WorkspacePolicy(permissive=False)` contains all four modes and that every concrete subclass of `Base` exposes `workspace` and inherits both policy names:

```python
def test_base_combina_tenant_e_workspace_com_policy_restritiva():
    policies = {policy.name: policy for policy in Metadata._rls_policies}

    assert set(policies) == {"isolamento_organizacao", "isolamento_workspace"}
    assert policies["isolamento_workspace"].permissive is False
    assert Metadata._meta.get_field("workspace").null is True
```

Run:

```bash
uv run pytest apps/workspaces/tests/test_policy_sql.py apps/api/base/tests/test_model_checks.py -q --reuse-db
```

Expected: FAIL because the second policy and field are absent.

- [ ] **Step 2: Implementar `WorkspacePolicy` com modos fechados**

`apps/workspaces/policies.py` must subclass `django_rls.policies.BasePolicy`. Its `get_sql_expression()` returns one parenthesized expression with these branches:

```sql
(SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'system'
OR (
    (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'control'
    AND workspace_id IS NULL
)
OR (
    (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'api_key'
    AND (
        workspace_id IS NULL
        OR workspace_id IN (
            SELECT w.id FROM workspace w
            WHERE w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
              AND w.is_active AND NOT w.is_deleted
        )
    )
    AND EXISTS (
        SELECT 1 FROM workspace w
        WHERE w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
          AND w.is_active AND NOT w.is_deleted
    )
)
OR (
    (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'membership'
    AND (
        (
            workspace_id IS NULL
            AND EXISTS (
                SELECT 1
                FROM workspace_vinculo vw
                JOIN workspace w ON w.id = vw.workspace_id
                JOIN vinculo v ON v.id = vw.vinculo_id
                WHERE vw.vinculo_id = (SELECT NULLIF(current_setting('rls.membership_id', true), '')::bigint)
                  AND vw.is_active AND NOT vw.is_deleted
                  AND w.is_active AND NOT w.is_deleted
                  AND v.is_active AND NOT v.is_deleted
                  AND w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
                  AND v.organizacao_id = w.organizacao_id
            )
        )
        OR workspace_id IN (
            SELECT vw.workspace_id
            FROM workspace_vinculo vw
            JOIN workspace w ON w.id = vw.workspace_id
            JOIN vinculo v ON v.id = vw.vinculo_id
            WHERE vw.vinculo_id = (SELECT NULLIF(current_setting('rls.membership_id', true), '')::bigint)
              AND vw.is_active AND NOT vw.is_deleted AND vw.selected_for_view
              AND w.is_active AND NOT w.is_deleted
              AND v.is_active AND NOT v.is_deleted
              AND w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
              AND v.organizacao_id = w.organizacao_id
        )
    )
)
```

Do not interpolate request data or IDs into this SQL. Only fixed table/column names and PostgreSQL `current_setting` are allowed.

- [ ] **Step 3: Acrescentar `WorkspaceMixin` e declarar obrigatoriedade**

In `apps/api/base/models.py`:

```python
from django_rls.policies import TenantPolicy

from apps.workspaces.policies import WorkspacePolicy


class WorkspaceMixin(models.Model):
    workspace_required = False

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        verbose_name=_("workspace"),
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text=_("Workspace do registro; vazio indica compartilhamento na Organização."),
        db_comment="Workspace do registro; nulo indica compartilhamento na Organização.",
    )

    class Meta:
        abstract = True


class Base(WorkspaceMixin, TenantMixin, BaseTenantless, RLSModel):
    class Meta:
        abstract = True
        rls_policies = [
            TenantPolicy(name="isolamento_organizacao", tenant_field="organizacao"),
            WorkspacePolicy(name="isolamento_workspace", permissive=False),
        ]
```

Do not add `workspace` to forbidden internal fields: clients are allowed to choose it when the endpoint exposes the field.

- [ ] **Step 4: Criar o guardrail para models obrigatórios**

Define `ID_WORKSPACE_OBRIGATORIO_SEM_CONSTRAINT = "base.W003"`. For each concrete `Base` with `workspace_required = True`, require a `CheckConstraint` whose condition rejects `workspace__isnull=True`. Add a test-only model with and without the constraint and assert only the invalid model emits `base.W003`.

- [ ] **Step 5: Gerar migrations para todas as tabelas concretas e propagar Metadata**

Run:

```bash
uv run python manage.py makemigrations metadata assinaturas faturamento
```

Expected fields: `Metadata`; `PropostaComercial`, `AssinaturaOrganizacao`, `AlteracaoAssinatura`; `CheckoutCobranca`, `FaturaAssinatura`, `ReaberturaEventoCobranca`, `SolicitacaoReconciliacaoCobranca`. Every generated migration depends on `workspaces.0002_backfill_workspaces_iniciais`.

Change `aplicar_metadata()` so a new row inherits the target object's Workspace:

```python
if registro is None:
    return Metadata.objects.create(
        content_type=content_type,
        object_id=objeto.pk,
        dados=dados,
        workspace_id=getattr(objeto, "workspace_id", None),
    )
```

Add a test for a workspace-specific object and preserve the shared-object test.

- [ ] **Step 6: Verificar models, migrations e commitar**

Run:

```bash
uv run pytest apps/workspaces/tests/test_policy_sql.py apps/api/base/tests/test_model_checks.py apps/api/metadata/tests -q --reuse-db
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

Expected: PASS and no pending migrations.

Commit:

```bash
git add apps/workspaces/policies.py apps/workspaces/tests/test_policy_sql.py apps/api/base apps/api/metadata apps/assinaturas
git commit -m "feat: aplicar isolamento rls por workspace"
```

### Task 4: Publicar contexto RLS e exigir Workspace atual nas rotas de negócio

**Files:**
- Create: `apps/workspaces/context.py`
- Create: `apps/workspaces/permissions.py`
- Create: `apps/workspaces/tests/test_context.py`
- Create: `apps/workspaces/tests/test_permissions.py`
- Modify: `apps/api/core/route_markers.py`
- Modify: `apps/api/core/tests/test_route_markers.py`
- Modify: `apps/organizacoes/context.py`
- Modify: `apps/organizacoes/middleware.py`
- Modify: `apps/organizacoes/tests/test_tenant_context.py`
- Modify: `api/settings.py`

**Interfaces:**
- Produces: `definir_contexto_workspace_membership()`, `definir_contexto_workspace_api_key()`, `definir_contexto_workspace_control()` and `definir_contexto_workspace_system()`.
- Produces: route decorator `no_workspace` and global `WorkspacePermission`.
- Consumes: `Vinculo.current_workspace`, `VinculoWorkspace` and error codes from Tasks 1–2.

- [ ] **Step 1: Escrever testes do contexto, limpeza e permissão**

Cover exact context maps:

```python
def test_contexto_humano_publica_vinculo_e_workspace_atual():
    with transaction.atomic():
        definir_contexto_workspace_membership(membership_id=11, current_workspace_id=23)
        assert get_active_rls_context() == {
            "membership_id": "11",
            "current_workspace_id": "23",
            "workspace_mode": "membership",
        }
```

Also assert cleanup after normal response and exception, `control` for `@no_workspace`, `api_key` without membership/current, and `WorkspacePermission` raising `workspaces.current_required` only for human business routes.

Run:

```bash
uv run pytest apps/workspaces/tests/test_context.py apps/workspaces/tests/test_permissions.py apps/api/core/tests/test_route_markers.py -q --reuse-db
```

Expected: FAIL because the modules and marker are absent.

- [ ] **Step 2: Implementar chaves, modos e limpeza única**

In `apps/workspaces/context.py` define:

```python
CHAVE_MEMBERSHIP = "membership_id"
CHAVE_CURRENT_WORKSPACE = "current_workspace_id"
CHAVE_WORKSPACE_MODE = "workspace_mode"
CHAVES_WORKSPACE = {CHAVE_MEMBERSHIP, CHAVE_CURRENT_WORKSPACE, CHAVE_WORKSPACE_MODE}

MODO_MEMBERSHIP = "membership"
MODO_API_KEY = "api_key"
MODO_CONTROL = "control"
MODO_SYSTEM = "system"
```

Each setter calls `set_rls_context(key, value, is_local=True)` and clears keys not applicable to its mode before setting the mode. Add these three keys to `DJANGO_RLS["REGISTERED_CONTEXT_KEYS"]` beside `billing_ingress`.

Change `organizacao_atual()` and `organizacao_atual_privilegiada()` to publish `MODO_SYSTEM`, preserving existing background jobs inside one Organization. Their `finally` blocks clear `{CHAVE_TENANT, *CHAVES_WORKSPACE}`.

- [ ] **Step 3: Criar o marcador e a permissão global**

Add `MARCADOR_SEM_WORKSPACE = "_rota_sem_workspace"` and:

```python
def no_workspace(view):
    """Dispensa Workspace atual, mas mantém Organização e acesso comercial."""
    return _marcar(view, MARCADOR_SEM_WORKSPACE)
```

Add `apps.workspaces.permissions.WorkspacePermission` after `TenantPermission` in `DEFAULT_PERMISSION_CLASSES`. Its logic is:

```python
if getattr(request, "tenant_required", True) is False:
    return True
if getattr(request, "workspace_required", True) is False:
    return True
if getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY:
    return True
if getattr(request, "current_workspace", None) is None:
    raise APIError(WorkspaceErrorCode.CURRENT_REQUIRED, status_code=409)
return True
```

- [ ] **Step 4: Integrar o contexto no middleware antes da política comercial**

Initialize `request.current_workspace = None` and `request.workspace_required = True` at the start of every middleware call. Load `current_workspace` with the Vínculo query. Before `resolver_contexto_comercial()`:

```python
if rota_tem_marcador(request.path_info, request.method, MARCADOR_SEM_WORKSPACE):
    definir_contexto_workspace_control()
    request.workspace_required = False
elif vinculo is None:
    definir_contexto_workspace_api_key()
else:
    workspace_atual = AcessosWorkspace.resolver_atual_valido(vinculo)
    definir_contexto_workspace_membership(
        membership_id=vinculo.pk,
        current_workspace_id=workspace_atual.pk if workspace_atual is not None else None,
    )
    request.current_workspace = workspace_atual
```

For API keys use `api_key`, except `@no_workspace` routes which use `control`. In every exit path clear tenant and Workspace keys after the transaction ends. Preserve the existing guarantee that RLS context cannot switch Organization mid-request.

- [ ] **Step 5: Testar integração e commitar**

Run:

```bash
uv run pytest apps/workspaces/tests/test_context.py apps/workspaces/tests/test_permissions.py apps/api/core/tests/test_route_markers.py apps/organizacoes/tests/test_tenant_context.py apps/organizacoes/tests/test_celery_tenancy.py -q --reuse-db
```

Expected: PASS.

Commit:

```bash
git add api/settings.py apps/workspaces/context.py apps/workspaces/permissions.py apps/workspaces/tests apps/api/core/route_markers.py apps/api/core/tests/test_route_markers.py apps/organizacoes/context.py apps/organizacoes/middleware.py apps/organizacoes/tests
git commit -m "feat: resolver contexto atual de workspace"
```

### Task 5: Centralizar assignment e validação de Workspace nas escritas

**Files:**
- Modify: `apps/api/base/serializers.py`
- Create: `apps/api/base/tests/test_workspace_serializer.py`
- Modify: `apps/workspaces/accesses.py`
- Modify: `apps/workspaces/tests/test_accesses.py`

**Interfaces:**
- Produces: `WorkspaceAssignmentSerializerMixin` as first write-aware mixin in `BaseModelSerializer`.
- Produces: `AcessosWorkspace.exigir_workspace_gravavel(*, request, workspace)`.
- Consumes: `request.current_workspace`, `Base.workspace_required` and RLS context from Tasks 3–4.

- [ ] **Step 1: Escrever serializers e models concretos somente para teste**

In `test_workspace_serializer.py`, define one optional and one required model inheriting `Base`; the required model includes:

```python
workspace_required = True

class Meta:
    app_label = "base"
    constraints = [
        models.CheckConstraint(
            condition=models.Q(workspace__isnull=False),
            name="registro_obrigatorio_workspace_not_null",
        ),
    ]
```

Create `BaseModelSerializer` subclasses exposing `id`, `nome` and `workspace`. Test all six cases: optional omitted→`NULL`, optional explicit null→`NULL`, optional explicit authorized Workspace, required omitted→current, required explicit null→validation error, required omitted with API key/current absent→`workspaces.current_required`.

Add Review Focus tests for cross-Organization ID and inactive Workspace using the same public error as an inaccessible Workspace.

- [ ] **Step 2: Rodar os testes para observar as falhas**

Run:

```bash
uv run pytest apps/api/base/tests/test_workspace_serializer.py -q --reuse-db
```

Expected: FAIL because omission is not interpreted and explicit Workspaces are not centrally authorized.

- [ ] **Step 3: Implementar o mixin antes dos mixins de política de campo**

In `WorkspaceAssignmentSerializerMixin.__init__()`, replace the generated Workspace field queryset with `AcessosWorkspace.queryset_gravavel(request)` whenever the serializer exposes `workspace`. This makes missing, foreign, inactive and unauthorized primary keys produce the same DRF `does_not_exist` response without revealing whether the row exists. Use local imports inside the mixin methods so `apps.api.base` does not create an import cycle with `apps.workspaces`.

Add this control flow to `WorkspaceAssignmentSerializerMixin.to_internal_value()`:

```python
workspace_was_sent = isinstance(data, Mapping) and "workspace" in data
validated_data = super().to_internal_value(data)
model = self.Meta.model

if not issubclass(model, Base) or self.instance is not None and not workspace_was_sent:
    return validated_data

if workspace_was_sent:
    workspace = validated_data.get("workspace")
    if workspace is None and model.workspace_required:
        raise serializers.ValidationError({"workspace": "Este campo não pode ser nulo."}, code="null")
    if workspace is not None:
        validated_data["workspace"] = AcessosWorkspace.exigir_workspace_gravavel(
            request=self.context["request"],
            workspace=workspace,
        )
    return validated_data

if model.workspace_required and self.instance is None:
    workspace = getattr(self.context["request"], "current_workspace", None)
    if workspace is None:
        raise APIError(WorkspaceErrorCode.CURRENT_REQUIRED, status_code=409, field="workspace")
    validated_data["workspace"] = workspace

return validated_data
```

The API-key branch in `queryset_gravavel()` and `exigir_workspace_gravavel()` accepts any active Workspace in `request.organizacao_id`; the human branch requires active `VinculoWorkspace` and `selected_for_view=True`, matching `WITH CHECK`. Use one generic denial for unknown, foreign, inactive and unauthorized IDs.

- [ ] **Step 4: Provar a constraint fora do serializer**

Use `schema_editor` to create the required test model's table and assert direct ORM `create(workspace_id=None)` and `bulk_create()` both raise `IntegrityError`. Task 7 separately proves the RLS behavior under a non-owner PostgreSQL role.

- [ ] **Step 5: Rodar testes e commitar**

Run:

```bash
uv run pytest apps/api/base/tests/test_workspace_serializer.py apps/api/base/tests/test_serializer_policies.py apps/workspaces/tests/test_accesses.py -q --reuse-db
```

Expected: PASS.

Commit:

```bash
git add apps/api/base/serializers.py apps/api/base/tests/test_workspace_serializer.py apps/workspaces/accesses.py apps/workspaces/tests/test_accesses.py
git commit -m "feat: atribuir workspace nas escritas de model"
```

### Task 6: Expor Workspace atual, visualização e administração pela API

**Files:**
- Create: `apps/workspaces/serializers.py`
- Create: `apps/workspaces/views.py`
- Create: `apps/workspaces/urls.py`
- Create: `apps/workspaces/schema.py`
- Create: `apps/workspaces/tests/test_api.py`
- Modify: `apps/organizacoes/serializers.py`
- Modify: `apps/organizacoes/tests/test_api.py`

**Interfaces:**
- Produces: `/workspaces/`, `/workspaces/{id}/atual/`, `/workspaces/visualizacao/` and `/vinculos-workspaces/`.
- Produces: `current_workspace` in each item returned by `/organizacoes/` for a human session.
- Consumes: lifecycle services from Task 2 and `@no_workspace` from Task 4.

- [ ] **Step 1: Escrever testes HTTP do contrato público**

Cover these payloads and statuses:

```python
# POST /workspaces/{id}/atual/
assert response.status_code == 200
assert response.json()["current_workspace"] == workspace.id

# PUT /workspaces/visualizacao/
response = client.put("/workspaces/visualizacao/", {"workspaces": [workspace_1.id, workspace_2.id]}, format="json")
assert response.status_code == 200
assert {item["id"] for item in response.json()["workspaces"]} == {workspace_1.id, workspace_2.id}

# current cannot be omitted from selection
response = client.put("/workspaces/visualizacao/", {"workspaces": [workspace_2.id]}, format="json")
assert response.status_code == 409
assert response.json()["errors"][0]["code"] == "workspaces.invalid_selection"
```

Also test list hides inaccessible Workspaces, Administrator sees all, API key lists all active, current/visualization actions reject API keys, and access removal rejects Administrator/Owner.

- [ ] **Step 2: Implementar serializers com querysets vazios por padrão**

Define:

```python
class WorkspaceSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()
    selected_for_view = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ["id", "nome", "slug", "is_active", "is_current", "selected_for_view"]
        read_only_fields = ["id", "is_current", "selected_for_view"]


class WorkspaceSelectionSerializer(serializers.Serializer):
    workspaces = serializers.PrimaryKeyRelatedField(many=True, queryset=Workspace.objects.none())


class VinculoWorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = VinculoWorkspace
        fields = ["id", "vinculo", "workspace", "is_active", "selected_for_view"]
        read_only_fields = ["id", "selected_for_view"]
```

Populate each dynamic queryset from `request.organizacao_id`; human selection is further limited to active accesses. Do not use a client-supplied Organization field.

- [ ] **Step 3: Implementar ViewSets e ações sem exigir current**

Decorate both control-plane ViewSets with `@no_workspace`. `WorkspaceViewSet` uses `TenantPermission`, `TokenScopePermission` and `PapelMinimoPermission`; roles are Viewer for list/retrieve and Administrator for create/update/destroy. Implement actions:

```python
@action(detail=True, methods=["post"], url_path="atual")
def atual(self, request, pk=None):
    workspace = self.get_object()
    vinculo = AcessosWorkspace.definir_atual(vinculo=request.vinculo, workspace=workspace)
    return Response({"current_workspace": vinculo.current_workspace_id})


@action(detail=False, methods=["put"], url_path="visualizacao")
def visualizacao(self, request):
    serializer = WorkspaceSelectionSerializer(data=request.data, context=self.get_serializer_context())
    serializer.is_valid(raise_exception=True)
    acessos = AcessosWorkspace.selecionar_visualizacao(
        vinculo=request.vinculo,
        workspace_ids={workspace.pk for workspace in serializer.validated_data["workspaces"]},
    )
    return Response({"workspaces": WorkspaceSerializer([acesso.workspace for acesso in acessos], many=True, context={"request": request}).data})
```

Mark `atual` and `visualizacao` as session-only through `session_only_actions`; API keys manage/read Workspaces only through their declared scopes.

- [ ] **Step 4: Registrar rotas e incluir o estado atual nas Organizações**

Register `workspaces` and `vinculos-workspaces` in `apps/workspaces/urls.py`. Extend `OrganizacaoSerializer` with a nested compact Workspace field derived from `vinculos_por_organizacao[obj.id].current_workspace`, returning `null` when selection is required. Add `.select_related("current_workspace")` when `OrganizacaoViewSet` builds `vinculos_por_organizacao`, and keep API-key organization responses with `current_workspace = null`.

- [ ] **Step 5: Rodar API e schema tests, depois commitar**

Run:

```bash
uv run pytest apps/workspaces/tests/test_api.py apps/organizacoes/tests/test_api.py apps/organizacoes/tests/test_schema.py -q --reuse-db
uv run python manage.py spectacular --file .tmp-workspaces-schema.yml --validate
```

Expected: PASS and a valid schema. Remove `.tmp-workspaces-schema.yml` after validation.

Commit:

```bash
git add apps/workspaces apps/organizacoes/serializers.py apps/organizacoes/tests
git commit -m "feat: expor gestao e selecao de workspaces"
```

### Task 7: Provar o isolamento real e os quatro modos RLS

**Files:**
- Create: `apps/workspaces/tests/test_rls.py`
- Create: `apps/workspaces/tests/urls_required_model.py`
- Modify: `apps/organizacoes/tests/test_rls.py`
- Modify: `apps/workspaces/tests/test_api.py`

**Interfaces:**
- Consumes: schema, policy, contexto, middleware and APIs from Tasks 1–6.
- Produces: evidence against a real PostgreSQL role that cannot bypass RLS.

- [ ] **Step 1: Criar fixture de role comum e model concreto de prova**

Follow the existing `apps/organizacoes/tests/test_rls.py` fixture. Define:

```python
class RegistroWorkspaceRLS(Base):
    nome = models.CharField(max_length=50)

    class Meta:
        app_label = "workspaces"
        db_table = "registro_workspace_rls_teste"
```

Create its table with `schema_editor`, call `RegistroWorkspaceRLS.enable_rls()`, and grant the test role `SELECT, INSERT, UPDATE, DELETE` on the record table, `USAGE, SELECT` on its ID sequence, plus `SELECT` on `workspace`, `workspace_vinculo` and `vinculo` so policy subqueries can execute.

- [ ] **Step 2: Provar leitura humana sem filtros ORM**

Build two Organizations and three Workspaces in the first. Give a human active access to 1 and 2, select only 1, and create shared, Workspace 1, Workspace 2, Workspace 3 and foreign-Organization rows. Under `tenant_id + membership_id + workspace_mode=membership`, execute plain SQL:

```sql
SELECT nome FROM registro_workspace_rls_teste ORDER BY nome
```

Expected: only shared and Workspace 1. After selecting Workspace 2 in the control plane, repeat in a fresh transaction and expect shared, Workspace 1 and Workspace 2 without changing the SQL.

- [ ] **Step 3: Provar os modos API key, control e system**

Assertions:

```python
assert consultar(modo="api_key") == ["compartilhado", "workspace-1", "workspace-2", "workspace-3"]
assert consultar(modo="control") == ["compartilhado"]
assert consultar(modo="system") == ["compartilhado", "workspace-1", "workspace-2", "workspace-3"]
```

Every mode remains limited to `tenant_id`; the foreign-Organization row never appears. Inactivate Workspace 3 and assert API key no longer sees its row while `system` still does for operational recovery.

- [ ] **Step 4: Provar `WITH CHECK`, estado concorrente e API key obrigatória**

As the common role, assert cross-Organization and unselected inserts fail with `psycopg2.errors.InsufficientPrivilege`. Mount a test-only `ModelViewSet` for the required test model under `override_settings(ROOT_URLCONF="apps.workspaces.tests.urls_required_model")`, then assert an API key:

- gets `409 workspaces.current_required` when a required model omits Workspace;
- creates successfully when it sends an active Workspace of its Organization;
- receives the same denial for inactive, missing and foreign Workspace IDs.

Repeat one write after inactivating the selected access in another committed transaction; the resumed write must fail and leave the table unchanged.

- [ ] **Step 5: Verificar plano de consulta e executar a suíte RLS**

Insert at least 5,000 access rows, run `ANALYZE workspace_vinculo`, set `enable_seqscan=off` only inside the test transaction and use `EXPLAIN (COSTS OFF)` for the policy query. Assert the plan can use `wv_vinculo_ativo_sel_idx` or another equivalent index; do not assert full plan text. This proves index viability without depending on machine-specific cost estimates.

Run:

```bash
uv run pytest apps/workspaces/tests/test_rls.py apps/organizacoes/tests/test_rls.py apps/workspaces/tests/test_api.py -q
```

Expected: PASS using PostgreSQL, never SQLite.

- [ ] **Step 6: Commitar a prova de isolamento**

```bash
git add apps/workspaces/tests apps/organizacoes/tests/test_rls.py
git commit -m "test: provar isolamento rls de workspaces"
```

### Task 8: Documentar, validar e entregar

**Files:**
- Create: `docs/adr/0009-workspaces-como-subtenancy.md`
- Modify: `docs/adr/index.md`
- Modify: `docs/reference/glossario.md`
- Modify: `docs/reference/estrutura-de-diretorios.md`
- Modify: `docs/reference/api.md`
- Modify: `docs/explanation/arquitetura.md`
- Modify: `mkdocs.yml` if navigation is explicit

**Interfaces:**
- Consumes: final public API and names from Tasks 1–7.
- Produces: published contract and ADR; no runtime interface.

- [ ] **Step 1: Escrever a documentação do contrato implementado**

Document these exact facts:

- Organização isola tenants; Workspace isola subsets inside one tenant.
- `current_workspace` lives on the Vínculo and is shared across sessions/devices.
- Workspace selection is persisted and automatically applied by RLS.
- `NULL` means shared; omission differs between optional and required models.
- API keys see all active Workspaces but must send Workspace for required writes.
- `@no_workspace` is reserved for control-plane routes and sees only shared business rows.
- system context crosses Workspaces only inside one explicit Organization context.

The ADR records the rejected alternatives: reusing `Time`, turning branches into Organizations, and relying on queryset filters.

- [ ] **Step 2: Rodar formatação e checks focados**

Run:

```bash
uv run ruff format apps/workspaces apps/organizacoes apps/api/base apps/api/metadata
uv run ruff check apps/workspaces apps/organizacoes apps/api/base apps/api/metadata api/settings.py
uv run python manage.py check --deploy
uv run python manage.py makemigrations --check --dry-run
uv run mkdocs build --strict
```

Expected: all commands exit 0 and migrations report `No changes detected`.

- [ ] **Step 3: Rodar as suítes afetadas**

Run:

```bash
uv run pytest apps/workspaces apps/organizacoes apps/api/base apps/api/metadata apps/assinaturas -q
```

Expected: PASS with PostgreSQL and Redis available.

- [ ] **Step 4: Rodar a suíte completa e conferir cobertura**

Run:

```bash
make test
```

Expected: all tests pass; new code reaches at least 80% coverage. If the full suite finds a regression, add the smallest reproducing test to the owning app before changing production code.

- [ ] **Step 5: Verificar diff e commitar documentação/ajustes finais**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files remain modified.

Commit:

```bash
git add docs mkdocs.yml
git commit -m "docs: documentar workspaces como subtenancy"
```

## Evidência de base (2026-09-22)

- `apps.workspaces` ainda não existe em `BUSINESS_APPS` nem no filesystem.
- `Base` contém somente `organizacao` e uma `TenantPolicy` permissiva.
- `OrganizacaoMiddleware` publica apenas `tenant_id` e limpa somente essa chave.
- `Vinculo` não possui `current_workspace`; `Time` é apenas agrupamento ManyToMany.
- `DJANGO_RLS.REGISTERED_CONTEXT_KEYS` contém somente `billing_ingress`.
- `OrganizationOnboarding` cria Organização, Proprietário e assinatura, sem unidade interna de dados.
- A suíte RLS atual prova isolamento entre Organizações, mas não entre subdivisões da mesma Organização.
