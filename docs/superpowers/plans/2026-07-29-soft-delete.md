# Soft Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement model-rooted soft deletion with distinct `is_active` and `is_deleted` lifecycle flags.

**Architecture:** `BaseGlobal` owns the two flags and its `delete()` implementation; the default manager hides deleted rows while `all_objects` provides internal visibility. Partial database constraints apply uniqueness only to non-deleted records.

**Tech Stack:** Python, Django 5.2, Django REST Framework, pytest, PostgreSQL conditional `UniqueConstraint`.

## Global Constraints

- `is_active` means operational inactivation; deleting must not change it.
- `is_deleted` defaults to `False`, is in `internal_fields`, and is never exposed by standard serializers.
- `BaseGlobal.delete()` and `BaseQuerySet.delete()` only mark records deleted.
- `objects` omits deleted rows; `all_objects` includes them.
- No restore endpoint, restore admin action, or public hard-delete operation is added.
- Existing Portuguese actions `ativar` and `inativar` remain; only their backing field changes.
- Legacy `usuario.ativo=False` must become `usuario.is_active=False` during migration.
- All present unique business keys become conditional on `is_deleted=False`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `apps/api/base/models.py` | Lifecycle model contract, managers, queryset deletion. |
| `apps/organizacoes/models.py` | Conditional organization-domain constraints. |
| `apps/usuarios/models.py` | Filtered authentication manager and user lifecycle fields. |
| `apps/organizacoes/migrations/0002_soft_delete.py` | Rename/add fields and conditional constraints. |
| `apps/usuarios/migrations/0002_soft_delete.py` | Preserve user activation, add deletion, alter email constraint. |
| `apps/api/base/{handlers,admin}.py` | Rename inactivation behavior. |
| `apps/organizacoes/{views,serializers,rules,permissions,admin}.py` | API/authorization field rename and delete flow. |
| `apps/organizacoes/tests/{test_soft_delete,test_api}.py` | ORM, migration, uniqueness, and HTTP regression tests. |

### Task 1: Add the root soft-delete contract

**Files:**

- Modify: `apps/api/base/models.py:43-111`
- Create: `apps/organizacoes/tests/test_soft_delete.py`

**Interfaces:**

- Produces: `BaseGlobal.is_active`, `BaseGlobal.is_deleted`, `BaseGlobal.delete()`, `BaseQuerySet.delete()`, and `all_objects`.

- [ ] **Step 1: Write the failing model tests**

```python
import pytest

from apps.organizacoes.models import Organizacao


pytestmark = pytest.mark.django_db


def test_delete_marca_registro_sem_alterar_o_estado_ativo():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    organizacao.is_active = False
    organizacao.save(update_fields=["is_active"])

    organizacao.delete()

    assert not Organizacao.objects.filter(pk=organizacao.pk).exists()
    excluida = Organizacao.all_objects.get(pk=organizacao.pk)
    assert excluida.is_deleted is True
    assert excluida.is_active is False


def test_delete_do_queryset_marca_registros_em_lote():
    primeira = Organizacao.objects.create(nome="Acme", slug="acme")
    segunda = Organizacao.objects.create(nome="Beta", slug="beta")

    count, details = Organizacao.objects.filter(pk__in=[primeira.pk, segunda.pk]).delete()

    assert count == 2
    assert details == {"organizacoes.Organizacao": 2}
    assert Organizacao.objects.count() == 0
    assert Organizacao.all_objects.filter(is_deleted=True).count() == 2
```

- [ ] **Step 2: Verify the red state**

Run: `uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: FAIL because neither lifecycle field nor `all_objects` exists and deletion is physical.

- [ ] **Step 3: Implement the model and manager contract**

Replace the current manager block in `apps/api/base/models.py` with this code, retaining `RLSQuerySet` so RLS behavior stays intact.

```python
class BaseQuerySet(RLSQuerySet):
    def delete(self):
        queryset = self.filter(is_deleted=False)
        count = queryset.update(is_deleted=True)
        return count, {self.model._meta.label: count}


class BaseManager(models.Manager.from_queryset(BaseQuerySet)):
    include_deleted = False

    def get_queryset(self):
        queryset = super().get_queryset()
        deferred_fields = self.model.get_queryset_deferred_fields()
        if deferred_fields:
            queryset = queryset.defer(*deferred_fields)
        if not self.include_deleted:
            queryset = queryset.filter(is_deleted=False)
        return queryset


class CustomManager(BaseManager):
    pass


class AllObjectsManager(BaseManager):
    include_deleted = True


class AtivosManager(BaseManager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
```

Replace `ativo`, the three manager declarations, `internal_fields`, and `read_only_fields` in `BaseGlobal` with these exact members; add `delete()` beside `save()`.

```python
is_active = models.BooleanField(_("ativo"), default=True)
is_deleted = models.BooleanField(_("excluído"), default=False)

objects = CustomManager()
all_objects = AllObjectsManager()
ativos = AtivosManager()

internal_fields = ["is_deleted", "data_ultima_alteracao", "hora_ultima_alteracao"]
read_only_fields = ["is_active", "is_deleted", "data_criacao", "hora_criacao", "owner"]

def delete(self, using=None, keep_parents=False):
    self.is_deleted = True
    self.save(using=using, update_fields=["is_deleted"])
```

- [ ] **Step 4: Verify the green state**

Run: `uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: the assertions now exercise the intended behavior but the database raises missing-column errors until Task 2 adds the migration.

### Task 2: Migrate organization models and allow key reuse

**Files:**

- Modify: `apps/organizacoes/models.py:44-160`
- Create: `apps/organizacoes/migrations/0002_soft_delete.py`
- Modify: `apps/organizacoes/tests/test_soft_delete.py`

**Interfaces:**

- Consumes: root fields from Task 1.
- Produces: database-backed lifecycle flags and conditional uniqueness for organizations, teams, links, and invitations.

- [ ] **Step 1: Add failing conditional-uniqueness coverage**

Append this test, importing `Convite`, `Papel`, `Time`, `Vinculo`, and `UsuarioFactory`.

```python
def test_chaves_unicas_podem_ser_reutilizadas_apos_exclusao_logica():
    usuario = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    time = Time.objects.create(organizacao=organizacao, nome="Produto")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    convite = Convite.objects.create(organizacao=organizacao, email="a@example.com", token="token-reutilizavel")

    time.delete()
    vinculo.delete()
    convite.delete()
    organizacao.delete()

    nova = Organizacao.objects.create(nome="Nova", slug="acme")
    Time.objects.create(organizacao=nova, nome="Produto")
    Vinculo.objects.create(organizacao=nova, usuario=usuario, papel=Papel.MEMBRO)
    Convite.objects.create(organizacao=nova, email="b@example.com", token="token-reutilizavel")
```

- [ ] **Step 2: Verify the red state**

Run: `uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: FAIL because the current schema lacks `is_deleted` and current unique indexes reject reused keys.

- [ ] **Step 3: Change model constraints and generate migration**

Remove `unique=True` from `Organizacao.slug` and `Convite.token`. Give each `Meta` the corresponding constraint below; use `models.Q` so no new import is needed.

```python
models.UniqueConstraint(fields=["slug"], condition=models.Q(is_deleted=False), name="organizacao_slug_unico_nao_excluido")
models.UniqueConstraint(fields=["organizacao", "nome"], condition=models.Q(is_deleted=False), name="time_unico_por_organizacao_nao_excluido")
models.UniqueConstraint(fields=["organizacao", "usuario"], condition=models.Q(is_deleted=False), name="vinculo_unico_por_organizacao_nao_excluido")
models.UniqueConstraint(fields=["token"], condition=models.Q(is_deleted=False), name="convite_token_unico_nao_excluido")
```

Run `uv run python manage.py makemigrations organizacoes --name soft_delete`. In the generated `0002_soft_delete.py`, retain these operations in order: four `RenameField(... ativo, is_active)`, four `AddField(... is_deleted, default=False)`, `AlterField` for slug/token to remove field uniqueness, remove the original `time_unico_por_organizacao` and `vinculo_unico_por_organizacao` constraints, then add the four constraints above. Import `apps.organizacoes.models` because `Convite.token` keeps the callable default.

- [ ] **Step 4: Verify migration and green state**

Run: `uv run python manage.py migrate && uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: migration applies and all three model tests pass.

- [ ] **Step 5: Commit the root and organization schema work**

```bash
git add apps/api/base/models.py apps/organizacoes/models.py apps/organizacoes/migrations/0002_soft_delete.py apps/organizacoes/tests/test_soft_delete.py
git commit -m "feat: add model soft delete lifecycle"
```

### Task 3: Support deleted users and preserve legacy inactivation

**Files:**

- Modify: `apps/usuarios/models.py:9-58`
- Create: `apps/usuarios/migrations/0002_soft_delete.py`
- Modify: `apps/organizacoes/tests/test_soft_delete.py`

**Interfaces:**

- Consumes: `BaseQuerySet`, `all_objects`, and root lifecycle fields.
- Produces: a filtered `Usuario.objects`, conditional email uniqueness, and a data-preserving migration.

- [ ] **Step 1: Add failing user behavior coverage**

```python
def test_usuario_excluido_fica_fora_do_manager_padrao_e_email_pode_ser_reutilizado():
    usuario = UsuarioFactory(email="pessoa@example.com")

    usuario.delete()

    assert not usuario.__class__.objects.filter(pk=usuario.pk).exists()
    assert usuario.__class__.all_objects.get(pk=usuario.pk).is_deleted is True
    assert UsuarioFactory(email="pessoa@example.com").pk != usuario.pk
```

Add this migration test and imports. It isolates the old and new model states so it verifies the data migration rather than the current application model.

```python
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class UsuarioSoftDeleteMigrationTests(TransactionTestCase):
    reset_sequences = True

    def test_copia_inativacao_legada_para_is_active(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("usuarios", "0001_initial")])
        old_apps = executor.loader.project_state([("usuarios", "0001_initial")]).apps
        UsuarioLegado = old_apps.get_model("usuarios", "Usuario")
        usuario = UsuarioLegado.objects.create(
            email="legado@example.com",
            first_name="Pessoa",
            last_name="Legada",
            password="senha",
            ativo=False,
            is_active=True,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([("usuarios", "0002_soft_delete")])
        new_apps = executor.loader.project_state([("usuarios", "0002_soft_delete")]).apps
        UsuarioMigrado = new_apps.get_model("usuarios", "Usuario")
        migrated = UsuarioMigrado.objects.get(pk=usuario.pk)

        assert migrated.is_active is False
        assert migrated.is_deleted is False
```

- [ ] **Step 2: Verify the red state**

Run: `uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: FAIL because the authentication manager exposes deleted users, email remains field-unique, and the migration is absent.

- [ ] **Step 3: Implement the filtered user manager and migration**

Change the manager declaration to inherit the root queryset and filter deleted rows; preserve the existing `create_user` and `create_superuser` methods.

```python
class UsuarioManager(UserManager.from_queryset(BaseQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)
```

Declare the shared authentication/lifecycle fields exactly once on `Usuario`:

```python
is_active = models.BooleanField(_("ativo"), default=True)
is_deleted = models.BooleanField(_("excluído"), default=False)
```

Generate `usuarios/0002_soft_delete`. Add this `RunPython` operation before removing the legacy field, then remove `ativo`, add `is_deleted`, alter `is_active`, alter email to remove `unique=True`, and add `models.UniqueConstraint(fields=["email"], condition=models.Q(is_deleted=False), name="usuario_email_unico_nao_excluido")`.

```python
def copiar_ativo_legado_para_is_active(apps, schema_editor):
    Usuario = apps.get_model("usuarios", "Usuario")
    Usuario.objects.filter(ativo=False).update(is_active=False)
```

- [ ] **Step 4: Verify user behavior and migration state**

Run: `uv run python manage.py makemigrations --check --dry-run && uv run pytest apps/organizacoes/tests/test_soft_delete.py -q`

Expected: no pending migrations and every model/user test passes.

- [ ] **Step 5: Commit user support**

```bash
git add apps/usuarios/models.py apps/usuarios/migrations/0002_soft_delete.py apps/organizacoes/tests/test_soft_delete.py
git commit -m "feat: support soft deleted users"
```

### Task 4: Route HTTP deletion through the model contract

**Files:**

- Modify: `apps/api/base/{handlers,admin}.py`
- Modify: `apps/organizacoes/{views,serializers,rules,permissions,admin}.py`
- Modify: `apps/organizacoes/tests/test_api.py`

**Interfaces:**

- Consumes: `is_active`, `is_deleted`, and `BaseGlobal.delete()`.
- Produces: soft-deleting DELETE endpoints and separate inactivation behavior.

- [ ] **Step 1: Write failing API tests**

```python
def test_delete_de_time_oculta_registro_sem_remover_linha():
    usuario = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    response = client_autenticado(usuario).delete(f"/times/{time.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Time.objects.filter(pk=time.pk).exists()
    assert Time.all_objects.get(pk=time.pk).is_deleted is True


def test_delete_de_vinculo_oculta_registro_sem_remover_linha():
    usuario = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.ADMINISTRADOR)
    alvo = vincular(UsuarioFactory(), organizacao)

    response = client_autenticado(usuario).delete(f"/vinculos/{alvo.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Vinculo.all_objects.get(pk=alvo.pk).is_deleted is True


def test_delete_de_convite_oculta_registro_sem_remover_linha():
    usuario = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    convite = Convite.objects.create(organizacao=organizacao, email="nova@example.com", convidado_por=usuario)

    response = client_autenticado(usuario).delete(f"/convites/{convite.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Convite.all_objects.get(pk=convite.pk).is_deleted is True


def test_inativacao_muda_apenas_is_active():
    usuario = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.ADMINISTRADOR)
    alvo = vincular(UsuarioFactory(), organizacao)

    response = client_autenticado(usuario).get(f"/vinculos/{alvo.pk}/inativar/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_200_OK
    alvo.refresh_from_db()
    assert alvo.is_active is False
    assert alvo.is_deleted is False
```

- [ ] **Step 2: Verify the red state**

Run: `uv run pytest apps/organizacoes/tests/test_api.py -q`

Expected: FAIL because existing consumers reference `ativo` and the two custom `perform_destroy()` implementations only inactivate records.

- [ ] **Step 3: Rename all active-state consumers and remove delete bypasses**

Apply these replacements across the listed files: `ativo=True` → `is_active=True`; `registro.ativo` → `registro.is_active`; `update_fields=["ativo"]` → `update_fields=["is_active"]`; serializer `"ativo"` → `"is_active"`; admin display `"ativo"` → `"is_active"`. In `BaseModelAdmin`, rename `_model_has_ativo_field` to `_model_has_is_active_field` and use `queryset.update(is_active=True/False)`.

Use this exact handler implementation:

```python
def ativar_registro(registro):
    registro.is_active = True
    registro.save(update_fields=["is_active"])


def inativar_registro(registro):
    registro.is_active = False
    registro.save(update_fields=["is_active"])
```

Remove `perform_destroy()` from `VinculoViewSet` and `ConviteViewSet`. The inherited DRF behavior invokes `instance.delete()`, satisfying the root-model deletion contract.

- [ ] **Step 4: Verify the green state**

Run: `uv run pytest apps/organizacoes/tests/test_api.py -q`

Expected: DELETE returns 204 and sets only `is_deleted`; inactivation sets only `is_active`.

- [ ] **Step 5: Commit API integration**

```bash
git add apps/api/base/handlers.py apps/api/base/admin.py apps/organizacoes/views.py apps/organizacoes/serializers.py apps/organizacoes/rules.py apps/organizacoes/permissions.py apps/organizacoes/admin.py apps/organizacoes/tests/test_api.py
git commit -m "feat: apply soft delete to organization APIs"
```

### Task 5: Verify the complete change

**Files:**

- Modify only files from Tasks 1–4 if a verification command demonstrates a defect.

- [ ] **Step 1: Check migration consistency**

Run: `uv run python manage.py makemigrations --check --dry-run`

Expected: exit code 0 and `No changes detected`.

- [ ] **Step 2: Run focused and complete tests**

Run: `uv run pytest apps/organizacoes/tests/test_soft_delete.py apps/organizacoes/tests/test_api.py -q && uv run pytest -q`

Expected: exit code 0 with all tests passing.

- [ ] **Step 3: Run style verification**

Run: `uv run ruff check apps/api/base apps/organizacoes apps/usuarios`

Expected: exit code 0 with no violations.

- [ ] **Step 4: Inspect the delivered diff**

Run: `git diff --check HEAD~3..HEAD && git status --short`

Expected: no whitespace errors; pre-existing untracked `docker-compose.otel.yml` and `examples/` remain untouched.

## Self-review

- Task 1 implements the root deletion contract, field semantics, default visibility, and internal visibility.
- Task 2 handles all organization-domain migrations and key reuse.
- Task 3 handles authentication, email reuse, and legacy user-state preservation.
- Task 4 updates API, serializer, permission, handler, and admin integrations.
- Task 5 provides fresh migration, test, lint, and diff evidence.
