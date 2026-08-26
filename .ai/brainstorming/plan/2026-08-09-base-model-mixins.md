# Base Model Mixins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompor os modelos base em mixins coesos no mesmo `models.py`, expondo `BaseTenantless` e `Base` tenant-scoped com atualização seletiva de campos.

**Architecture:** `BaseTenantless` compõe os comportamentos comuns e substitui `BaseGlobal`; `Base` acrescenta tenancy/RLS por `TenantMixin`. Querysets e managers permanecem no mesmo módulo, mas são compostos por mixins de manager para visibilidade, soft delete e campos deferred. A implementação de metadata é uma dependência externa: esta mudança apenas usa o import local canônico já estabelecido por ela.

**Tech Stack:** Python 3.12, Django 5, django-rls, django-auditlog, pytest, pytest-django, Ruff.

## Global Constraints

- Todo código de modelos, mixins, querysets e managers permanece em `apps/api/base/models.py`.
- A API pública final tem somente `BaseTenantless` e `Base`; `BaseGlobal` é removida.
- `Base` continua tenant-scoped; `BaseTenantless` não contém FK ou policy RLS de organização.
- `apps/api/metadata/**` não é alterado por este plano.
- Antes da execução, o plano `2026-08-09-importacoes-entre-modulos.md` deve estar integrado e `uv run python manage.py check` deve passar.
- `MetadataMixin` importa `Metadata` localmente de `apps.api.metadata.models`.
- `bulk_create`, `bulk_update` e `QuerySet.update()` não recebem rastreamento automático nesta entrega.
- Chamadas explícitas a `save(update_fields=...)` preservam exatamente os campos fornecidos.
- Use TDD: cada comportamento novo começa por teste que falha por sua ausência.
- Não modificar ou incluir no commit alterações preexistentes que não pertençam a esta entrega.

---

### Task 1: Criar as duas bases públicas e migrar os consumidores tenantless

**Files:**
- Modify: `apps/api/base/models.py`
- Modify: `apps/api/base/tests/test_auditlog.py`
- Modify: `apps/organizacoes/models.py`
- Modify: `apps/usuarios/models.py`
- Modify: `internal_frameworks/sensitive_fields/tests/models.py`
- Test: `apps/api/base/tests/test_auditlog.py`

**Interfaces:**
- Consumes: `CreationAuditMixin` e os contratos de campos já usados pelos modelos.
- Produces: `BaseTenantless`, `Base`, e a ausência de `BaseGlobal` como API pública.

- [ ] **Step 1: Escrever os testes de contrato para as duas bases**

Em `apps/api/base/tests/test_auditlog.py`, troque o import e os dois testes de base por:

```python
import apps.api.base.models as base_models

from apps.api.base.models import Base, BaseTenantless


def test_base_tenantless_define_campos_comuns_sem_organizacao():
    assert BaseTenantless._meta.get_field("created_by").remote_field.on_delete is models.PROTECT
    assert BaseTenantless._meta.get_field("created_at").auto_now_add is True
    assert BaseTenantless._meta.get_field("last_modified_at").auto_now is True
    with pytest.raises(FieldDoesNotExist):
        BaseTenantless._meta.get_field("organizacao")


def test_base_adiciona_organizacao_ao_contrato_tenantless():
    assert Base._meta.get_field("organizacao").remote_field.model._meta.label == "organizacoes.Organizacao"
    assert not hasattr(base_models, "BaseGlobal")
```

Atualize `test_internal_e_read_only_fields_usam_os_novos_nomes` para chamar `BaseTenantless`.

- [ ] **Step 2: Executar os testes e confirmar a falha esperada**

Run:

```bash
uv run pytest apps/api/base/tests/test_auditlog.py -q --no-cov
```

Expected: FAIL de importação para `BaseTenantless` e referências ainda existentes a `BaseGlobal`.

- [ ] **Step 3: Extrair os mixins de campos comuns no mesmo arquivo**

Em `apps/api/base/models.py`, substitua o corpo monolítico de `BaseGlobal` por mixins abstratos, mantendo os campos e valores atuais:

```python
class TimestampMixin(models.Model):
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    last_modified_at = models.DateTimeField(_("última alteração em"), auto_now=True)

    class Meta:
        abstract = True


class CreatedByMixin(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("criado por"),
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
        null=True,
    )

    def save(self, *args, **kwargs):
        if self._state.adding and self.created_by_id is None:
            current_user = get_current_user()
            if current_user and current_user.is_authenticated:
                self.created_by = current_user
        return super().save(*args, **kwargs)

    class Meta:
        abstract = True


class ActivityMixin(models.Model):
    is_active = models.BooleanField(_("ativo"), default=True)

    class Meta:
        abstract = True
```

Crie também `AuditHistoryMixin`, `SoftDeleteMixin`, `FieldIntrospectionMixin`,
`FieldPolicyMixin`, `CloneMixin`, `CapabilityMixin`, `ApiScopeMixin` e
`MetadataMixin` no mesmo módulo. Migre literalmente os métodos e atributos
públicos atuais para o mixin de uma única responsabilidade; não altere nomes,
defaults ou retorno desses contratos nesta tarefa.

Declare a composição pública nesta ordem:

```python
class BaseTenantless(
    MetadataMixin,
    ApiScopeMixin,
    CapabilityMixin,
    CloneMixin,
    FieldPolicyMixin,
    FieldIntrospectionMixin,
    SoftDeleteMixin,
    ActivityMixin,
    AuditHistoryMixin,
    CreatedByMixin,
    TimestampMixin,
):
    class Meta:
        abstract = True
```

`MetadataMixin.metadata` deve usar somente import local:

```python
@property
def metadata(self):
    from apps.api.metadata.models import Metadata

    if not hasattr(self, "_metadata"):
        self._metadata = Metadata.objects.get_or_create(content_type=self.content_type, object_id=self.pk)[0]
    return self._metadata
```

- [ ] **Step 4: Declarar `Base` como base tenant-scoped e migrar imports**

Crie `TenantMixin` com a FK e o `Meta.rls_policies` atuais. Faça `Base` herdar
de `TenantMixin`, `BaseTenantless` e `RLSModel`, preservando a policy:

```python
class TenantMixin(models.Model):
    organizacao = models.ForeignKey(
        "organizacoes.Organizacao",
        verbose_name=_("organização"),
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class Base(TenantMixin, BaseTenantless, RLSModel):
    class Meta:
        abstract = True
        rls_policies = [TenantPolicy(name="isolamento_organizacao", tenant_field="organizacao")]
```

Troque apenas as referências a `BaseGlobal` por `BaseTenantless` nestes locais:

```python
# apps/organizacoes/models.py
from apps.api.base.models import BaseTenantless
# Trocar somente a base da declaração existente de Organizacao para BaseTenantless.

# apps/usuarios/models.py
from apps.api.base.models import BaseQuerySet, BaseTenantless
# Trocar somente a base da declaração existente de Usuario para BaseTenantless.

# internal_frameworks/sensitive_fields/tests/models.py
from apps.api.base.models import BaseTenantless
# Trocar somente a base da declaração existente de RegistroSensivel para BaseTenantless.
```

- [ ] **Step 5: Executar contratos e checks de migração**

Run:

```bash
uv run pytest apps/api/base/tests/test_auditlog.py internal_frameworks/sensitive_fields/tests -q --no-cov
uv run python manage.py makemigrations --check --dry-run
```

Expected: testes passam e Django informa `No changes detected`.

- [ ] **Step 6: Commit da composição sem mudança funcional adicional**

```bash
git add apps/api/base/models.py apps/api/base/tests/test_auditlog.py apps/organizacoes/models.py apps/usuarios/models.py internal_frameworks/sensitive_fields/tests/models.py
git commit -m "refactor: compose base models with mixins"
```

### Task 2: Compor querysets e managers por políticas ortogonais

**Files:**
- Modify: `apps/api/base/models.py`
- Create: `apps/api/base/tests/test_model_managers.py`
- Test: `apps/api/base/tests/test_model_managers.py`

**Interfaces:**
- Consumes: `BaseTenantless.get_queryset_deferred_fields()` e os campos `is_active`/`is_deleted` de Task 1.
- Produces: `objects`, `all_objects` e `ativos` com contratos preservados.

- [ ] **Step 1: Escrever os testes de visibilidade dos managers**

Crie `apps/api/base/tests/test_model_managers.py`:

```python
import pytest

from apps.organizacoes.models import Organizacao


@pytest.mark.django_db
def test_objects_oculta_soft_deletados_e_all_objects_os_inclui():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    organizacao.delete()

    assert not Organizacao.objects.filter(pk=organizacao.pk).exists()
    assert Organizacao.all_objects.filter(pk=organizacao.pk).exists()


@pytest.mark.django_db
def test_ativos_combina_registro_ativo_e_nao_excluido():
    ativa = Organizacao.objects.create(nome="Ativa", slug="ativa")
    inativa = Organizacao.objects.create(nome="Inativa", slug="inativa", is_active=False)
    excluida = Organizacao.objects.create(nome="Excluida", slug="excluida")
    excluida.delete()

    assert list(Organizacao.ativos.values_list("pk", flat=True)) == [ativa.pk]
    assert inativa.pk not in Organizacao.ativos.values_list("pk", flat=True)
```

- [ ] **Step 2: Executar os testes antes da separação dos managers**

Run:

```bash
uv run pytest apps/api/base/tests/test_model_managers.py -q --no-cov
```

Expected: PASS no comportamento legado; isso cria a rede de segurança para a refatoração interna.

- [ ] **Step 3: Separar as políticas de queryset e manager sem mudar a API**

Em `apps/api/base/models.py`, mantenha `BaseQuerySet` como o queryset RLS e
extraia a exclusão lógica para:

```python
class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        queryset = self.filter(is_deleted=False)
        deleted_count = queryset.update(is_deleted=True)
        return deleted_count, {self.model._meta.label: deleted_count}


class BaseQuerySet(SoftDeleteQuerySet, RLSQuerySet):
    pass
```

Componha managers por `get_queryset()` cooperativo:

```python
class DeferredFieldsManagerMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        fields = self.model.get_queryset_deferred_fields()
        return queryset.defer(*fields) if fields else queryset


class ExcludeDeletedManagerMixin:
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ActiveManagerMixin:
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


BaseQuerySetManager = models.Manager.from_queryset(BaseQuerySet)

class ObjectsManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass

class AllObjectsManager(DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass

class ActiveObjectsManager(ActiveManagerMixin, ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass
```

Conecte-os em `BaseTenantless` como `objects`, `all_objects` e `ativos`.

- [ ] **Step 4: Executar testes do manager e regressões de soft delete**

Run:

```bash
uv run pytest apps/api/base/tests/test_model_managers.py apps/organizacoes/tests/test_soft_delete.py -q --no-cov
```

Expected: todos passam.

- [ ] **Step 5: Commit dos managers compostos**

```bash
git add apps/api/base/models.py apps/api/base/tests/test_model_managers.py
git commit -m "refactor: compose base model managers"
```

### Task 3: Implementar rastreamento confiável de campos alterados

**Files:**
- Modify: `apps/api/base/models.py`
- Create: `apps/api/base/tests/test_change_tracking.py`
- Test: `apps/api/base/tests/test_change_tracking.py`

**Interfaces:**
- Consumes: `BaseTenantless`, `last_modified_at` e `get_queryset_deferred_fields()`.
- Produces: `ChangeTrackingMixin` cooperativo para `save()` e `refresh_from_db()`.

- [ ] **Step 1: Escrever testes de alteração final, update vazio e update explícito**

Crie `apps/api/base/tests/test_change_tracking.py` usando `CaptureQueriesContext`:

```python
from django.test.utils import CaptureQueriesContext
from django.db import connection

import pytest

from apps.organizacoes.models import Organizacao


def updates(execute):
    with CaptureQueriesContext(connection) as queries:
        execute()
    return [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith("UPDATE")]


@pytest.mark.django_db
def test_save_atualiza_somente_campo_modificado_e_timestamp():
    organizacao = Organizacao.objects.create(nome="Antes", slug="antes")
    organizacao.nome = "Depois"

    sql = updates(organizacao.save)

    assert len(sql) == 1
    assert '"nome"' in sql[0]
    assert '"last_modified_at"' in sql[0]
    assert '"slug"' not in sql[0]


@pytest.mark.django_db
def test_save_sem_mudanca_nao_executa_update():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    assert updates(organizacao.save) == []


@pytest.mark.django_db
def test_save_com_update_fields_preserva_contrato_explicito():
    organizacao = Organizacao.objects.create(nome="Antes", slug="antes")
    organizacao.nome = "Depois"

    sql = updates(lambda: organizacao.save(update_fields=["nome"]))

    assert len(sql) == 1
    assert '"nome"' in sql[0]
    assert '"last_modified_at"' not in sql[0]
```

Adicione casos análogos para: reverter `nome` ao valor original antes do save;
instância carregada com `.defer("nome")` sem acesso a `nome`; e mutação in-place
de `Metadata.dados` depois que o plano de metadata estiver integrado.

- [ ] **Step 2: Executar os testes e confirmar que a atualização vazia falha**

Run:

```bash
uv run pytest apps/api/base/tests/test_change_tracking.py -q --no-cov
```

Expected: FAIL em `test_save_sem_mudanca_nao_executa_update`, pois o Django atual ainda grava `last_modified_at`.

- [ ] **Step 3: Implementar `ChangeTrackingMixin` por snapshot de valores carregados**

Em `apps/api/base/models.py`, importe `copy` e implemente:

```python
class ChangeTrackingMixin(models.Model):
    _original_field_values: dict[str, object]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._reset_original_field_values()

    def _loaded_concrete_fields(self):
        deferred = self.get_deferred_fields()
        return [field for field in self._meta.concrete_fields if field.name not in deferred]

    def _reset_original_field_values(self):
        self._original_field_values = {
            field.name: copy.deepcopy(getattr(self, field.attname)) for field in self._loaded_concrete_fields()
        }

    def _changed_loaded_field_names(self):
        return [
            field.name
            for field in self._loaded_concrete_fields()
            if field.name in self._original_field_values
            and self._original_field_values[field.name] != getattr(self, field.attname)
        ]
```

No `save()`, para `self._state.adding` delegue sem `update_fields`; para update
sem `update_fields`, use `_changed_loaded_field_names()`. Se a lista estiver
vazia, retorne sem escrever. Se não estiver, acrescente todos os campos
concretos com `auto_now=True`, atribua a lista resultante a
`kwargs["update_fields"]` e chame `super().save(*args, **kwargs)`
e só então reconstrua o snapshot. Para `update_fields` explícito, delegue sem
modificá-lo e reconstrua o snapshot após sucesso.

Implemente também:

```python
def refresh_from_db(self, using=None, fields=None, from_queryset=None):
    result = super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
    self._reset_original_field_values()
    return result
```

Inclua `ChangeTrackingMixin` na composição de `BaseTenantless` antes dos mixins
que fazem `save()`, para que ele decida o `update_fields` e depois delegue a
`CreatedByMixin`/Django.

- [ ] **Step 4: Executar testes de rastreamento e regressões de usuário**

Run:

```bash
uv run pytest apps/api/base/tests/test_change_tracking.py apps/api/base/tests/test_auditlog.py apps/api/autenticacao/tests/test_mfa_models.py -q --no-cov
```

Expected: todos passam.

- [ ] **Step 5: Commit do rastreamento centralizado**

```bash
git add apps/api/base/models.py apps/api/base/tests/test_change_tracking.py
git commit -m "feat: track changed base model fields"
```

### Task 4: Preencher organização de novos modelos `Base` a partir do contexto RLS

**Files:**
- Modify: `apps/api/base/models.py`
- Modify: `apps/organizacoes/tests/test_rls.py`
- Test: `apps/organizacoes/tests/test_rls.py`

**Interfaces:**
- Consumes: `TenantMixin.organizacao`, `apps.organizacoes.context.organizacao_atual_privilegiada` e `django_rls.context.get_active_rls_context`.
- Produces: criação de modelo tenant-scoped sem repetição de `organizacao=` quando há contexto ativo.

- [ ] **Step 1: Escrever os testes de atribuição e ausência de contexto**

Em `apps/organizacoes/tests/test_rls.py`, adicione:

```python
@pytest.mark.django_db(transaction=True)
def test_base_preenche_organizacao_do_contexto_rls(ambiente_rls):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-auto")

    with organizacao_atual_privilegiada(organizacao.pk):
        registro = RegistroRLS.objects.create(descricao="automático")

    assert registro.organizacao_id == organizacao.pk


@pytest.mark.django_db(transaction=True)
def test_base_sem_contexto_nao_escolhe_organizacao(ambiente_rls):
    from django_rls.exceptions import RLSError

    with pytest.raises(RLSError):
        RegistroRLS.objects.create(descricao="sem tenant")
```

Mantenha os testes existentes que passam `organizacao` explicitamente: eles
provam que um valor informado não é substituído pela base.

- [ ] **Step 2: Executar os testes e confirmar a falha da criação automática**

Run:

```bash
uv run pytest apps/organizacoes/tests/test_rls.py -q --no-cov
```

Expected: FAIL em `test_base_preenche_organizacao_do_contexto_rls` por violação de `NOT NULL` ou `WITH CHECK`.

- [ ] **Step 3: Implementar a atribuição no `TenantMixin.save()`**

Em `apps/api/base/models.py`, acrescente ao `TenantMixin`:

```python
def save(self, *args, **kwargs):
    if self._state.adding and self.organizacao_id is None:
        from django_rls.context import get_active_rls_context

        organizacao_id = get_active_rls_context().get("tenant_id")
        if organizacao_id is not None:
            self.organizacao_id = organizacao_id
    return super().save(*args, **kwargs)
```

Use a constante `CHAVE_TENANT` de `apps.organizacoes.context` apenas se isso não
criar ciclo de importação; caso contrário, declare uma constante local com o
mesmo valor e cubra-a pelo teste acima. Não adicione fallback de organização
global, query adicional ou bypass de RLS.

- [ ] **Step 4: Executar a suíte RLS e o check de schema**

Run:

```bash
uv run pytest apps/organizacoes/tests/test_rls.py -q --no-cov
uv run python manage.py makemigrations --check --dry-run
```

Expected: testes passam e `No changes detected`.

- [ ] **Step 5: Commit da criação tenant-scoped**

```bash
git add apps/api/base/models.py apps/organizacoes/tests/test_rls.py
git commit -m "feat: infer organization for tenant models"
```

### Task 5: Validar integração completa e documentação operacional

**Files:**
- Modify: `apps/api/base/models.py` somente se Ruff identificar ajuste local de importação/ordem
- Modify: `apps/organizacoes/tests/test_rls.py`
- Test: `apps/api/base/tests/`
- Test: `apps/organizacoes/tests/`

**Interfaces:**
- Consumes: todas as bases, mixins, managers e o caminho canônico de metadata dos tasks anteriores.
- Produces: regressão integrada validada, sem migrations pendentes e sem importação circular.

- [ ] **Step 1: Escrever a asserção de import local de metadata**

Em `apps/organizacoes/tests/test_rls.py`, adicione:

```python
@pytest.mark.django_db(transaction=True)
def test_metadata_mixin_resolve_modelo_pelo_caminho_canonico(ambiente_rls):
    from apps.api.metadata.models import Metadata

    organizacao = Organizacao.objects.create(nome="Meta", slug="meta")
    with organizacao_atual_privilegiada(organizacao.pk):
        registro = RegistroRLS.objects.create(descricao="com metadata")
        metadata = registro.metadata

    assert isinstance(metadata, Metadata)
    assert metadata.content_type == registro.content_type
    assert metadata.object_id == registro.pk
```

- [ ] **Step 2: Executar o teste e confirmar que usa o app canônico**

Run:

```bash
uv run pytest apps/organizacoes/tests/test_rls.py::test_metadata_mixin_resolve_modelo_pelo_caminho_canonico -q --no-cov
```

Expected: PASS; uma falha de importação indica que o plano de metadata ainda não está integrado e bloqueia esta task.

- [ ] **Step 3: Executar a validação completa proporcional ao risco**

Run:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest apps/api/base/tests apps/organizacoes/tests internal_frameworks/sensitive_fields/tests -q --no-cov
uv run ruff check apps/api/base/models.py apps/api/base/tests apps/organizacoes/models.py apps/usuarios/models.py internal_frameworks/sensitive_fields/tests
uv run ruff format --check apps/api/base/models.py apps/api/base/tests apps/organizacoes/models.py apps/usuarios/models.py internal_frameworks/sensitive_fields/tests
```

Expected: checks sem issues, sem migrations pendentes, testes passam e Ruff não reporta diferenças.

- [ ] **Step 4: Executar a suíte do projeto com cobertura**

Run:

```bash
make test
```

Expected: pytest termina com sucesso e a cobertura de código novo atende ao limite do projeto.

- [ ] **Step 5: Commit final de testes e ajustes de integração**

```bash
git add apps/api/base/models.py apps/api/base/tests apps/organizacoes/tests
git commit -m "test: cover base model mixins"
```

## Self-review

- Cobertura do spec: Task 1 cria as duas bases e os mixins comuns; Task 2 cobre managers/querysets; Task 3 cobre rastreamento; Task 4 cobre tenancy; Task 5 valida metadata, imports, schema e suíte completa.
- Restrições: nenhum task modifica `apps/api/metadata/**`; todos mantêm as mudanças no mesmo `apps/api/base/models.py`; operações bulk permanecem fora do rastreamento.
- Consistência: os nomes públicos são `BaseTenantless`, `Base`, `TenantMixin`, `ChangeTrackingMixin`, `ObjectsManager`, `AllObjectsManager` e `ActiveObjectsManager` em todas as tasks.
