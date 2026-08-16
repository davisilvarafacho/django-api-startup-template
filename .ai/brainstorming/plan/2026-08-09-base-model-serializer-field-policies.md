# Base Model Serializer Field Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompor o `BaseModelSerializer` em mixins coesos e impedir silenciosamente qualquer escrita, pelo payload ou por `save()`, nos campos críticos controlados pela aplicação.

**Architecture:** `FieldPolicyMixin` permanece como fonte única das políticas. Três mixins cooperativos preservam as regras de internal/read-only/write-only no `__init__`; um quarto mixin filtra forbidden antes da validação e novamente na fronteira de persistência. Funções privadas de módulo resolvem nomes reais, aliases com `source` e `attname` de FKs sem duplicar lógica.

**Tech Stack:** Python 3.12, Django 5, Django REST Framework, pytest, pytest-django, Ruff.

## Global Constraints

- Todo o código dos serializers e de seus mixins permanece em `apps/api/base/serializers.py`.
- `FieldPolicyMixin` em `apps/api/base/models.py` continua sendo a fonte única da política forbidden.
- A lista forbidden padrão contém `created_at`, `created_by`, `last_modified_at` e `organizacao`.
- `extra_forbidden_internal_write_fields` é sempre incorporado automaticamente.
- Campos forbidden são descartados silenciosamente; não geram erro de validação.
- Não existe opção no serializer capaz de ignorar ou reduzir a política forbidden.
- Nomes reais, aliases com `source` e `attname` de relações, como `created_by_id` e `organizacao_id`, obedecem à mesma trava.
- O payload original não é mutado.
- A representação de saída não muda.
- Preservar `ignore_internal`, `additional_internal`, `ignore_read_only`, `additional_read_only` e `additional_write_only`.
- `BaseModelSerpySerializer` não é alterado.
- A proteção é da fronteira de serializers; escritas diretas no model continuam disponíveis aos hooks automáticos da aplicação.
- Não há alteração de schema ou criação de migration.
- Use TDD: cada comportamento novo começa por um teste que falha por sua ausência.
- Use `tests.support.usuarios.criar_usuario` para criar usuários nos testes.
- O worktree pode conter mudanças do usuário: revisar `git status` antes de cada commit e adicionar somente os arquivos e hunks desta entrega.

---

### Task 1: Completar o contrato forbidden do model

**Files:**
- Modify: `apps/api/base/models.py`
- Create: `apps/api/base/tests/test_serializer_policies.py`
- Test: `apps/api/base/tests/test_serializer_policies.py`

**Interfaces:**
- Consumes: `FieldPolicyMixin.forbidden_internal_write_fields` e `extra_forbidden_internal_write_fields`.
- Produces: `get_forbidden_internal_write_fields()` com timestamp de criação e extensões do model.

- [ ] **Step 1: Escrever os testes do contrato do model**

Crie `apps/api/base/tests/test_serializer_policies.py` com:

```python
from apps.usuarios.models import Usuario


def test_model_protege_todos_os_campos_controlados_pela_aplicacao():
    assert Usuario.get_forbidden_internal_write_fields() == [
        "created_at",
        "created_by",
        "last_modified_at",
        "organizacao",
    ]


def test_model_inclui_campos_forbidden_extras(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])

    assert Usuario.get_forbidden_internal_write_fields() == [
        "created_at",
        "created_by",
        "last_modified_at",
        "organizacao",
        "first_name",
    ]
```

- [ ] **Step 2: Executar os testes e confirmar a falha esperada**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
```

Expected: o primeiro teste falha porque `created_at` ainda não faz parte da lista padrão.

- [ ] **Step 3: Adicionar `created_at` à política padrão**

Em `FieldPolicyMixin`, altere somente a lista:

```python
forbidden_internal_write_fields = ["created_at", "created_by", "last_modified_at", "organizacao"]
```

Mantenha o getter atual, que já cria a união sem mutar as listas da classe:

```python
@classmethod
def get_forbidden_internal_write_fields(cls):
    return cls.forbidden_internal_write_fields + cls.extra_forbidden_internal_write_fields
```

- [ ] **Step 4: Reexecutar o teste e verificar migrations**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
uv run python manage.py makemigrations --check --dry-run
```

Expected: testes passam e Django informa `No changes detected`.

- [ ] **Step 5: Commit isolado do contrato**

```bash
git add apps/api/base/models.py apps/api/base/tests/test_serializer_policies.py
git diff --cached --check
git commit -m "feat: protect application-managed model fields"
```

### Task 2: Extrair as políticas existentes para mixins cooperativos

**Files:**
- Modify: `apps/api/base/serializers.py`
- Modify: `apps/api/base/tests/test_serializer_policies.py`
- Test: `apps/api/base/tests/test_serializer_policies.py`
- Test: `internal_frameworks/sensitive_fields/tests/test_fields.py`

**Interfaces:**
- Consumes: getters `get_internal_fields()`, `get_read_only_fields()` e `get_write_only_fields()` do model.
- Produces: `InternalFieldsSerializerMixin`, `ReadOnlyFieldsSerializerMixin`, `WriteOnlyFieldsSerializerMixin` e um `BaseModelSerializer` composto.

- [ ] **Step 1: Escrever os testes de preservação das três políticas**

Acrescente ao início dos imports de `test_serializer_policies.py`:

```python
from rest_framework import serializers

from apps.api.base.serializers import (
    BaseModelSerializer,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
)
```

Acrescente o serializer de teste e os contratos:

```python
class UsuarioFieldPolicySerializer(BaseModelSerializer):
    created_at = serializers.DateTimeField(required=False)

    class Meta:
        model = Usuario
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "password",
            "created_at",
            "last_modified_at",
        ]


def test_base_serializer_compoe_os_mixins_de_politica():
    assert issubclass(BaseModelSerializer, InternalFieldsSerializerMixin)
    assert issubclass(BaseModelSerializer, ReadOnlyFieldsSerializerMixin)
    assert issubclass(BaseModelSerializer, WriteOnlyFieldsSerializerMixin)


def test_mixins_aplicam_internal_read_only_e_write_only():
    serializer = UsuarioFieldPolicySerializer()

    assert "last_modified_at" not in serializer.fields
    assert serializer.fields["created_at"].read_only is True
    assert serializer.fields["password"].write_only is True


def test_mixins_preservam_opcoes_ignore_existentes():
    serializer = UsuarioFieldPolicySerializer(
        ignore_internal=["last_modified_at"],
        ignore_read_only=["created_at"],
    )

    assert "last_modified_at" in serializer.fields
    assert serializer.fields["created_at"].read_only is False


def test_mixins_preservam_opcoes_additional_existentes():
    serializer = UsuarioFieldPolicySerializer(
        additional_internal=["last_name"],
        additional_read_only=["email"],
        additional_write_only=["first_name"],
    )

    assert "last_name" not in serializer.fields
    assert serializer.fields["email"].read_only is True
    assert serializer.fields["first_name"].write_only is True
```

- [ ] **Step 2: Executar os testes e confirmar a falha estrutural**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py internal_frameworks/sensitive_fields/tests/test_fields.py -q --no-cov
```

Expected: erro de importação porque os três mixins ainda não existem.

- [ ] **Step 3: Extrair um `__init__` cooperativo por política**

Em `apps/api/base/serializers.py`, substitua o `BaseModelSerializer` monolítico por:

```python
class InternalFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        ignore_internal = kwargs.pop("ignore_internal", ())
        additional_internal = kwargs.pop("additional_internal", ())
        super().__init__(*args, **kwargs)

        internal_fields = [*self.Meta.model.get_internal_fields(), *additional_internal]
        for field_name in internal_fields:
            if field_name in self.fields and field_name not in ignore_internal:
                self.fields.pop(field_name)


class ReadOnlyFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        ignore_read_only = kwargs.pop("ignore_read_only", ())
        additional_read_only = kwargs.pop("additional_read_only", ())
        super().__init__(*args, **kwargs)

        read_only_fields = [*self.Meta.model.get_read_only_fields(), *additional_read_only]
        for field_name in read_only_fields:
            if field_name in self.fields and field_name not in ignore_read_only:
                self.fields[field_name].read_only = True


class WriteOnlyFieldsSerializerMixin:
    def __init__(self, *args, **kwargs):
        additional_write_only = kwargs.pop("additional_write_only", ())
        super().__init__(*args, **kwargs)

        write_only_fields = [*self.Meta.model.get_write_only_fields(), *additional_write_only]
        for field_name in write_only_fields:
            if field_name in self.fields:
                self.fields[field_name].write_only = True


class BaseModelSerializer(
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
    serializers.ModelSerializer,
):
    pass
```

Cada mixin remove apenas seus próprios kwargs antes de delegar. Na volta da cadeia cooperativa, write-only, read-only e internal são aplicados; internal remove por último os campos que não devem ser expostos.

Não altere `BaseModelSerpySerializer`.

- [ ] **Step 4: Reexecutar testes e Ruff**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py internal_frameworks/sensitive_fields/tests/test_fields.py -q --no-cov
uv run ruff check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
uv run ruff format --check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
```

Expected: testes e checks passam; o teste de sensitive fields confirma a compatibilidade de `extra_write_only_fields`.

- [ ] **Step 5: Commit isolado da decomposição**

```bash
git add apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
git diff --cached --check
git commit -m "refactor: compose base serializer field policies"
```

### Task 3: Descartar forbidden do payload antes da validação

**Files:**
- Modify: `apps/api/base/serializers.py`
- Modify: `apps/api/base/tests/test_serializer_policies.py`
- Test: `apps/api/base/tests/test_serializer_policies.py`

**Interfaces:**
- Consumes: `Meta.model.get_forbidden_internal_write_fields()` e os campos vinculados em `self.fields`.
- Produces: `ForbiddenInternalWriteFieldsSerializerMixin.to_internal_value()` com descarte silencioso e cópia defensiva.

- [ ] **Step 1: Declarar serializers de teste graváveis e alias**

Acrescente aos imports de `test_serializer_policies.py`:

```python
from datetime import UTC, datetime

import pytest

from tests.support.usuarios import criar_usuario
```

Acrescente:

```python
class UsuarioForbiddenSerializer(BaseModelSerializer):
    created_at = serializers.DateTimeField(required=False)
    created_by = serializers.PrimaryKeyRelatedField(
        queryset=Usuario.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name", "last_name", "created_at", "created_by"]


class UsuarioForbiddenAliasSerializer(BaseModelSerializer):
    autor = serializers.PrimaryKeyRelatedField(
        source="created_by",
        queryset=Usuario.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name", "last_name", "autor"]
```

- [ ] **Step 2: Testar create silencioso e ausência de mutação**

Acrescente:

```python
@pytest.mark.django_db
def test_create_descarta_forbidden_sem_mutar_payload():
    atacante = criar_usuario()
    instante_forjado = datetime(2000, 1, 1, tzinfo=UTC)
    payload = {
        "email": f"novo-{atacante.pk}@exemplo.com",
        "first_name": "Nome",
        "last_name": "Criado",
        "created_at": instante_forjado.isoformat(),
        "created_by": atacante.pk,
    }
    payload_original = payload.copy()
    serializer = UsuarioForbiddenSerializer(
        data=payload,
        ignore_read_only=["created_at", "created_by"],
    )

    assert serializer.is_valid(), serializer.errors
    assert "created_at" not in serializer.validated_data
    assert "created_by" not in serializer.validated_data

    usuario = serializer.save()

    assert usuario.created_at != instante_forjado
    assert usuario.created_by_id is None
    assert payload == payload_original
```

- [ ] **Step 3: Testar update completo e parcial**

Acrescente:

```python
@pytest.mark.django_db
@pytest.mark.parametrize("partial", [False, True])
def test_update_descarta_forbidden_e_preserva_campos_normais(partial):
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    payload = {
        "email": usuario.email,
        "first_name": "Nome alterado",
        "last_name": usuario.last_name,
        "created_at": datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
        "created_by": atacante.pk,
    }
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data=payload,
        partial=partial,
        ignore_read_only=["created_at", "created_by"],
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save()
    usuario.refresh_from_db()

    assert usuario.first_name == "Nome alterado"
    assert usuario.created_at == created_at_original
    assert usuario.created_by_id == criador.pk
```

- [ ] **Step 4: Testar aliases e extras declarados pelo model**

Acrescente:

```python
@pytest.mark.django_db
def test_payload_descarta_alias_com_source_forbidden():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    serializer = UsuarioForbiddenAliasSerializer(
        usuario,
        data={"autor": atacante.pk, "first_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert "created_by" not in serializer.validated_data

    serializer.save()
    usuario.refresh_from_db()

    assert usuario.created_by_id == criador.pk
    assert usuario.first_name == "Permitido"


@pytest.mark.django_db
def test_payload_descarta_extra_forbidden_do_model(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])
    usuario = criar_usuario(first_name="Original", last_name="Original")
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"first_name": "Bloqueado", "last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert "first_name" not in serializer.validated_data
    assert serializer.validated_data["last_name"] == "Permitido"

    serializer.save()
    usuario.refresh_from_db()

    assert usuario.first_name == "Original"
    assert usuario.last_name == "Permitido"
```

- [ ] **Step 5: Executar os testes e confirmar as falhas esperadas**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
```

Expected: os valores forbidden aparecem em `validated_data` e alteram updates porque o novo mixin ainda não existe.

- [ ] **Step 6: Implementar resolução centralizada de nomes e cópia filtrada**

Adicione os imports:

```python
from collections.abc import Mapping

from django.core.exceptions import FieldDoesNotExist
```

Antes dos mixins, adicione:

```python
def _forbidden_model_write_names(serializer):
    model = serializer.Meta.model
    configured_names = set(model.get_forbidden_internal_write_fields())
    write_names = set(configured_names)

    for field_name in configured_names:
        try:
            write_names.add(model._meta.get_field(field_name).attname)
        except FieldDoesNotExist:
            continue

    return write_names


def _forbidden_input_names(serializer):
    forbidden_sources = _forbidden_model_write_names(serializer)
    input_names = set(forbidden_sources)

    for field_name, field in serializer.fields.items():
        source_root = field.source.split(".", 1)[0]
        if source_root in forbidden_sources:
            input_names.add(field_name)

    return input_names


def _without_fields(data, field_names):
    if not isinstance(data, Mapping):
        return data

    present_fields = set(data).intersection(field_names)
    if not present_fields:
        return data

    filtered_data = data.copy()
    for field_name in present_fields:
        filtered_data.pop(field_name, None)
    return filtered_data
```

`FieldDoesNotExist` é necessário porque bases tenantless herdam a política padrão de `organizacao`, mas não possuem esse campo. O uso do `attname` impede bypass por `created_by_id`/`organizacao_id`.

- [ ] **Step 7: Implementar o primeiro ponto de entrada do mixin forbidden**

Antes de `InternalFieldsSerializerMixin`, adicione:

```python
class ForbiddenInternalWriteFieldsSerializerMixin:
    def to_internal_value(self, data):
        filtered_data = _without_fields(data, _forbidden_input_names(self))
        validated_data = super().to_internal_value(filtered_data)
        return _without_fields(validated_data, _forbidden_model_write_names(self))
```

Atualize a composição:

```python
class BaseModelSerializer(
    ForbiddenInternalWriteFieldsSerializerMixin,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
    serializers.ModelSerializer,
):
    pass
```

A segunda filtragem remove os nomes produzidos por `source` antes dos validators do DRF. Não adicione kwargs de bypass forbidden.

- [ ] **Step 8: Reexecutar testes e lint**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
uv run ruff check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
uv run ruff format --check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
```

Expected: testes de create, update, partial update, alias, extras e payload imutável passam.

- [ ] **Step 9: Commit isolado do filtro de payload**

```bash
git add apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
git diff --cached --check
git commit -m "feat: ignore forbidden serializer payload fields"
```

### Task 4: Fechar a entrada programática de `save()`

**Files:**
- Modify: `apps/api/base/serializers.py`
- Modify: `apps/api/base/tests/test_serializer_policies.py`
- Test: `apps/api/base/tests/test_serializer_policies.py`

**Interfaces:**
- Consumes: `_forbidden_model_write_names()`, `_forbidden_input_names()` e `_without_fields()`.
- Produces: `ForbiddenInternalWriteFieldsSerializerMixin.save()` sem bypass por kwargs ou por alteração tardia de `validated_data`.

- [ ] **Step 1: Testar kwargs forbidden em `save()`**

Acrescente:

```python
@pytest.mark.django_db
def test_save_descarta_kwargs_forbidden_inclusive_attname():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save(
        created_by=atacante,
        created_by_id=atacante.pk,
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    usuario.refresh_from_db()

    assert usuario.last_name == "Permitido"
    assert usuario.created_by_id == criador.pk
    assert usuario.created_at == created_at_original
```

- [ ] **Step 2: Testar extras forbidden em kwargs**

Acrescente:

```python
@pytest.mark.django_db
def test_save_descarta_kwarg_extra_forbidden(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])
    usuario = criar_usuario(first_name="Original")
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save(first_name="Bloqueado")
    usuario.refresh_from_db()

    assert usuario.first_name == "Original"
    assert usuario.last_name == "Permitido"
```

- [ ] **Step 3: Testar defesa contra injeção tardia em `validate()`**

Acrescente:

```python
class UsuarioInjectingForbiddenSerializer(UsuarioForbiddenSerializer):
    def validate(self, attrs):
        attrs["created_by"] = self.context["atacante"]
        attrs["created_at"] = datetime(2000, 1, 1, tzinfo=UTC)
        return attrs


@pytest.mark.django_db
def test_save_remove_forbidden_injetado_por_validacao_customizada():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    serializer = UsuarioInjectingForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
        context={"atacante": atacante},
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save()
    usuario.refresh_from_db()

    assert usuario.created_by_id == criador.pk
    assert usuario.created_at == created_at_original
```

Esse teste garante a regra absoluta mesmo quando um serializer concreto adiciona dados depois de `to_internal_value()`.

- [ ] **Step 4: Executar os testes e confirmar as falhas esperadas**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
```

Expected: kwargs e valores injetados ainda chegam ao `ModelSerializer.update()` porque `save()` não foi protegido.

- [ ] **Step 5: Implementar o segundo e último método do mixin forbidden**

Complete `ForbiddenInternalWriteFieldsSerializerMixin`:

```python
class ForbiddenInternalWriteFieldsSerializerMixin:
    def to_internal_value(self, data):
        filtered_data = _without_fields(data, _forbidden_input_names(self))
        validated_data = super().to_internal_value(filtered_data)
        return _without_fields(validated_data, _forbidden_model_write_names(self))

    def save(self, **kwargs):
        forbidden_model_names = _forbidden_model_write_names(self)
        forbidden_kwarg_names = forbidden_model_names | _forbidden_input_names(self)
        filtered_kwargs = _without_fields(kwargs, forbidden_kwarg_names)

        if hasattr(self, "_validated_data"):
            self._validated_data = _without_fields(self._validated_data, forbidden_model_names)

        return super().save(**filtered_kwargs)
```

O `hasattr` preserva as assertions nativas do DRF quando `save()` é chamado antes de `is_valid()`. A cópia de `_validated_data` bloqueia alterações tardias sem mutar o dicionário anteriormente produzido.

- [ ] **Step 6: Reexecutar testes e commit**

Run:

```bash
uv run pytest apps/api/base/tests/test_serializer_policies.py -q --no-cov
uv run ruff check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
uv run ruff format --check apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
```

Expected: todos os testes passam.

```bash
git add apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
git diff --cached --check
git commit -m "feat: block forbidden serializer save overrides"
```

### Task 5: Executar regressão integrada e checks finais

**Files:**
- Verify: `apps/api/base/models.py`
- Verify: `apps/api/base/serializers.py`
- Verify: `apps/api/base/tests/test_serializer_policies.py`
- Verify: `apps/api/base/tests/test_view_actions.py`
- Verify: `apps/api/base/tests/test_bulk_update.py`
- Verify: `internal_frameworks/sensitive_fields/tests/test_fields.py`

**Interfaces:**
- Consumes: serializer base final e consumidores atuais.
- Produces: evidência de compatibilidade, lint limpo, configuração Django válida e ausência de migrations.

- [ ] **Step 1: Executar a suíte focada de regressão**

Com PostgreSQL e Redis locais disponíveis, execute:

```bash
uv run pytest \
  apps/api/base/tests/test_serializer_policies.py \
  apps/api/base/tests/test_view_actions.py \
  apps/api/base/tests/test_bulk_update.py \
  internal_frameworks/sensitive_fields/tests/test_fields.py \
  -q --no-cov
```

Expected: todos os testes passam. Se a infraestrutura local estiver parada, rode `make up` e repita; não altere testes para contornar serviços obrigatórios.

- [ ] **Step 2: Executar checks estáticos e do Django**

```bash
uv run ruff check apps/api/base/models.py apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
uv run ruff format --check apps/api/base/models.py apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

Expected: Ruff e Django passam; nenhuma migration é detectada.

- [ ] **Step 3: Revisar o diff final e a granularidade dos commits**

```bash
git status --short
git diff HEAD~4 -- apps/api/base/models.py apps/api/base/serializers.py apps/api/base/tests/test_serializer_policies.py
git log -4 --oneline
```

Confirme:

- os três mixins de configuração têm somente `__init__`;
- o mixin forbidden tem somente `to_internal_value()` e `save()`;
- não há bypass forbidden;
- `extra_forbidden_internal_write_fields` é lido em todas as entradas;
- `BaseModelSerpySerializer` permanece idêntico ao início da execução;
- nenhum arquivo preexistente do usuário entrou nos quatro commits.

- [ ] **Step 4: Registrar somente correções finais, se necessárias**

Se a regressão exigir ajuste, comece por um teste que reproduza o problema e faça um commit estreito com Conventional Commit. Se tudo estiver verde, não crie commit vazio.
