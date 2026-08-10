# Camada de API do metadata — plano de implementação

> **Para agentes:** SUB-SKILL OBRIGATÓRIA: use superpowers:subagent-driven-development (recomendado) ou superpowers:executing-plans para executar tarefa a tarefa. Os passos usam checkbox (`- [ ]`).

**Objetivo:** dar superfície HTTP ao app `apps/api/metadata`, permitindo que o consumidor da API anexe chaves próprias a qualquer registro através de `GET`/`PATCH /<recurso>/<id>/metadata/`.

**Arquitetura:** a action nasce em um mixin herdado por `BaseModelViewSet`, ligada por padrão e desligável por atributo. Toda escrita passa por um service layer em `apps/api/metadata/handlers.py`, o único ponto do sistema autorizado a criar linha de `Metadata`. O `MetadataMixin` dos models vira somente leitura.

**Stack:** Django 5.2, Django REST Framework, PostgreSQL com `django-rls`, pytest.

**Spec:** [docs/superpowers/specs/2026-08-09-metadata-api-design.md](../specs/2026-08-09-metadata-api-design.md)

## Restrições globais

- Código, comentários, docstrings e mensagens de erro em **português**. Docstrings no padrão Google. Codenames de permissão e nomes de migração em inglês, como manda o Django.
- Um arquivo por módulo (`models`, `serializers`, `handlers`, `views`); `tests/` é pacote.
- Dependências e execução sempre via `uv`, nunca `pip`/`python` direto.
- A suíte exige Postgres e Redis no ar: rode `make up` antes de qualquer teste com banco.
- Testes rodam com `--nomigrations`. Logo, a migration escrita na Tarefa 4 **não** é exercitada pela suíte; ela é conferida por `makemigrations --check --dry-run`.
- Limites: `METADATA_MAX_KEYS = 50`, `METADATA_MAX_KEY_LENGTH = 256`, `METADATA_MAX_VALUE_LENGTH = 1024`.
- Nomes fixados pela spec, use exatamente estes: `MetadataAlteracaoSerializer`, `mesclar_dados`, `aplicar_metadata`, `get_content_type`, `raw_metadata`, `metadata_habilitado`.
- Ler metadata continua exigindo contexto de tenant, porque `Metadata` herda de `Base` e está sob RLS. Isso é intencional: **não** capture `RLSContextRequiredError` para devolver `{}`. O que a Tarefa 3 corrige é a leitura **gravar**, não a leitura exigir contexto.
- Commits seguem Conventional Commits (validado por commitlint no hook `commit-msg`).

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---------|------------------|
| `utils/env.py` | ganha `get_int_from_env` e as três chaves em `ENVS` |
| `api/settings.py` | os três limites |
| `.env.example` | as três variáveis, documentadas |
| `apps/api/metadata/handlers.py` | **novo** — `mesclar_dados` (puro) e `aplicar_metadata` (persistência) |
| `apps/api/metadata/serializers.py` | **novo** — `MetadataAlteracaoSerializer`, valida formato |
| `apps/api/metadata/models.py` | constraint por organização, `object_id` maior |
| `apps/api/metadata/migrations/0002_*.py` | **novo** |
| `apps/api/base/models.py` | `MetadataMixin` somente leitura |
| `apps/api/base/views.py` | `MetadataViewSetMixin` e o opt-out |
| `docs/reference/metadata.md` | **novo**, com entrada no `mkdocs.yml` |
| `docs/ROADMAP.md` | Batch 8 fechado |

Cadeia de imports resultante: `base/views → metadata/{serializers,handlers} → metadata/models → base/models`. Sem ciclo, conforme ADR 0006.

---

### Tarefa 1: Limites configuráveis

**Arquivos:**
- Modificar: `utils/env.py`
- Modificar: `api/settings.py` (após o bloco `# MFA`, por volta da linha 507)
- Modificar: `.env.example`
- Teste: `utils/tests/test_env.py` (verifique se o arquivo já existe; se não, crie o pacote `utils/tests/` com `__init__.py`)

**Interfaces:**
- Produz: `get_int_from_env(key, default_value) -> int`; os settings `METADATA_MAX_KEYS`, `METADATA_MAX_KEY_LENGTH`, `METADATA_MAX_VALUE_LENGTH`.

- [ ] **Passo 1: Localizar os testes de env existentes**

```bash
ls utils/tests/ 2>/dev/null || find . -path ./.venv -prune -o -name "test_env*.py" -print
```

Se já houver testes de `get_bool_from_env`, siga o estilo deles e adicione os novos no mesmo arquivo.

- [ ] **Passo 2: Escrever os testes que falham**

```python
import pytest

from utils.env import get_int_from_env


def test_get_int_from_env_devolve_o_default_quando_a_variavel_nao_existe(monkeypatch):
    monkeypatch.delenv("METADATA_MAX_KEYS", raising=False)

    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 50


def test_get_int_from_env_converte_o_valor_do_ambiente(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "7")

    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 7


def test_get_int_from_env_trata_string_vazia_como_ausencia(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "")

    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 50


def test_get_int_from_env_rejeita_valor_nao_numerico(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "muitas")

    with pytest.raises(ValueError, match="METADATA_MAX_KEYS"):
        get_int_from_env("METADATA_MAX_KEYS", 50)
```

- [ ] **Passo 3: Rodar e confirmar a falha**

Run: `uv run --group test pytest utils/tests/test_env.py -q --nomigrations`
Esperado: FAIL com `ImportError: cannot import name 'get_int_from_env'`

- [ ] **Passo 4: Implementar o helper**

Em `utils/env.py`, adicione ao lado de `get_bool_from_env`:

```python
def get_int_from_env(key: EnviromentVar, default_value):
    """Lê uma env var como inteiro.

    Args:
        key: Nome da variável, declarado em `ENVS`.
        default_value: Valor usado quando a variável está ausente ou vazia.

    Returns:
        O inteiro lido do ambiente ou o default.

    Raises:
        ValueError: Quando o valor presente não é um inteiro.
    """
    value = os.environ.get(key)
    if not value:
        return default_value

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"'{value}' não é um valor válido para '{key}'") from exc
```

- [ ] **Passo 5: Declarar as três chaves em `ENVS`**

`ENVS` é uma tupla fechada usada para tipar `EnviromentVar`; variável não declarada ali não deve ser lida. Adicione, junto de um comentário de seção como os já existentes:

```python
    # metadata
    "METADATA_MAX_KEYS",
    "METADATA_MAX_KEY_LENGTH",
    "METADATA_MAX_VALUE_LENGTH",
```

- [ ] **Passo 6: Rodar e confirmar que passa**

Run: `uv run --group test pytest utils/tests/test_env.py -q --nomigrations`
Esperado: PASS

- [ ] **Passo 7: Declarar os settings**

Em `api/settings.py`, logo após o bloco `# MFA`, e acrescentando `get_int_from_env` ao import já existente de `utils.env`:

```python
# metadata genérico (JSON key-value por objeto)
# Limites por objeto; ver docs/reference/metadata.md.
METADATA_MAX_KEYS = get_int_from_env("METADATA_MAX_KEYS", 50)
METADATA_MAX_KEY_LENGTH = get_int_from_env("METADATA_MAX_KEY_LENGTH", 256)
METADATA_MAX_VALUE_LENGTH = get_int_from_env("METADATA_MAX_VALUE_LENGTH", 1024)
```

- [ ] **Passo 8: Documentar no `.env.example`**

Adicione após o bloco `# --- MFA ---`:

```bash
# --- Metadata genérico ---
# Limites por objeto do JSON key-value anexado pelos consumidores da API.
METADATA_MAX_KEYS=50
METADATA_MAX_KEY_LENGTH=256
METADATA_MAX_VALUE_LENGTH=1024
```

- [ ] **Passo 9: Conferir que o Django ainda sobe**

Run: `uv run python manage.py check`
Esperado: nenhum erro novo (avisos pré-existentes podem aparecer).

- [ ] **Passo 10: Lint e commit**

```bash
uv run ruff format utils/env.py api/settings.py
uv run ruff check utils/env.py api/settings.py
git add utils/env.py api/settings.py .env.example utils/tests/
git commit -m "feat(metadata): adicionar limites configuráveis por settings"
```

---

### Tarefa 2: Merge puro em `handlers.py`

Regra de negócio isolada, sem ORM: dado o documento atual e as alterações, devolve o documento resultante ou levanta erro de validação. Testável sem banco, que é a preferência declarada em `.ai/CONVENTIONS.md`.

**Arquivos:**
- Criar: `apps/api/metadata/handlers.py`
- Teste: `apps/api/metadata/tests/test_handlers.py`

**Interfaces:**
- Consome: os settings da Tarefa 1.
- Produz: `mesclar_dados(atuais: dict, alteracoes: dict) -> dict`, que levanta `rest_framework.serializers.ValidationError`.

- [ ] **Passo 1: Escrever os testes que falham**

```python
from django.test import override_settings

import pytest
from rest_framework.serializers import ValidationError

from apps.api.metadata.handlers import mesclar_dados


def test_mescla_preservando_chaves_ausentes_do_payload():
    resultado = mesclar_dados({"erp_id": "X-1", "nota": "urgente"}, {"nota": "normal"})

    assert resultado == {"erp_id": "X-1", "nota": "normal"}


def test_valor_nulo_remove_a_chave():
    resultado = mesclar_dados({"erp_id": "X-1", "nota": "urgente"}, {"nota": None})

    assert resultado == {"erp_id": "X-1"}


def test_remover_chave_inexistente_nao_falha():
    resultado = mesclar_dados({"erp_id": "X-1"}, {"ausente": None})

    assert resultado == {"erp_id": "X-1"}


def test_nao_altera_o_dicionario_recebido():
    atuais = {"erp_id": "X-1"}

    mesclar_dados(atuais, {"nota": "urgente"})

    assert atuais == {"erp_id": "X-1"}


@override_settings(METADATA_MAX_KEYS=2)
def test_rejeita_quando_excede_o_total_de_chaves():
    with pytest.raises(ValidationError, match="2 chaves"):
        mesclar_dados({"a": "1", "b": "2"}, {"c": "3"})


@override_settings(METADATA_MAX_KEYS=2)
def test_remocao_nao_conta_para_o_limite_de_chaves():
    resultado = mesclar_dados({"a": "1", "b": "2"}, {"b": None, "c": "3"})

    assert resultado == {"a": "1", "c": "3"}


@override_settings(METADATA_MAX_KEY_LENGTH=5)
def test_rejeita_chave_longa_demais():
    with pytest.raises(ValidationError, match="5 caracteres"):
        mesclar_dados({}, {"chave-enorme": "1"})


@override_settings(METADATA_MAX_VALUE_LENGTH=5)
def test_rejeita_valor_longo_demais():
    with pytest.raises(ValidationError, match="5 caracteres"):
        mesclar_dados({}, {"nota": "valor-enorme"})


def test_rejeita_chave_vazia():
    with pytest.raises(ValidationError, match="vazia"):
        mesclar_dados({}, {"": "1"})
```

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/metadata/tests/test_handlers.py -q --nomigrations`
Esperado: FAIL com `ModuleNotFoundError: No module named 'apps.api.metadata.handlers'`

- [ ] **Passo 3: Implementar `mesclar_dados`**

```python
"""Service layer do metadata genérico.

`aplicar_metadata` é o único ponto do sistema que cria linha de `Metadata`;
`mesclar_dados` concentra as regras, sem tocar no banco.
"""

from django.conf import settings

from rest_framework import serializers


def mesclar_dados(atuais, alteracoes):
    """Funde as alterações no documento atual, aplicando os limites.

    Args:
        atuais: Documento já persistido, no formato chave/valor de texto.
        alteracoes: Chaves a gravar; valor `None` remove a chave.

    Returns:
        O documento resultante, sem alterar o dicionário recebido.

    Raises:
        rest_framework.serializers.ValidationError: Quando uma chave é vazia ou
            algum limite de `settings` é violado.
    """
    for chave, valor in alteracoes.items():
        if not chave:
            raise serializers.ValidationError("A chave de metadata não pode ser vazia.")

        if len(chave) > settings.METADATA_MAX_KEY_LENGTH:
            raise serializers.ValidationError(
                f"A chave de metadata não pode ter mais de {settings.METADATA_MAX_KEY_LENGTH} caracteres."
            )

        if valor is not None and len(valor) > settings.METADATA_MAX_VALUE_LENGTH:
            raise serializers.ValidationError(
                f"O valor da chave '{chave}' não pode ter mais de {settings.METADATA_MAX_VALUE_LENGTH} caracteres."
            )

    resultado = dict(atuais)
    for chave, valor in alteracoes.items():
        if valor is None:
            resultado.pop(chave, None)
        else:
            resultado[chave] = valor

    if len(resultado) > settings.METADATA_MAX_KEYS:
        raise serializers.ValidationError(f"O objeto não pode ter mais de {settings.METADATA_MAX_KEYS} chaves de metadata.")

    return resultado
```

A validação de tamanho vem antes do merge, e o total de chaves só depois — é o que faz uma remoção abrir espaço para uma inclusão na mesma requisição.

- [ ] **Passo 4: Rodar e confirmar que passa**

Run: `uv run --group test pytest apps/api/metadata/tests/test_handlers.py -q --nomigrations`
Esperado: PASS, 9 testes.

- [ ] **Passo 5: Lint e commit**

```bash
uv run ruff format apps/api/metadata/
uv run ruff check apps/api/metadata/
git add apps/api/metadata/handlers.py apps/api/metadata/tests/test_handlers.py
git commit -m "feat(metadata): adicionar merge de documentos com limites"
```

---

### Tarefa 3: `MetadataMixin` somente leitura

Hoje o mixin grava a partir de uma property de leitura. Uma listagem de 30 itens que toque `raw_metadata` executa até 30 `INSERT`, cada um registrado pelo `AuditlogHistoryField` do `Base` e cada um herdando a organização do contexto RLS vigente.

**Arquivos:**
- Modificar: `apps/api/base/models.py:289-307` (classe `MetadataMixin`)
- Teste: `apps/api/base/tests/test_metadata_mixin.py`

**Interfaces:**
- Produz: `Model.get_content_type() -> ContentType` (classmethod), `instancia.raw_metadata -> dict`, e a `GenericRelation` `metadata_registros`.
- Consumido por: `aplicar_metadata` (Tarefa 5) e a action (Tarefa 6).

- [ ] **Passo 1: Escrever os testes que falham**

`Usuario` serve de alvo por ser um model concreto e sem RLS próprio; a linha de `Metadata`, essa sim, exige contexto de tenant.

```python
from django.contrib.contenttypes.models import ContentType

import pytest

from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_get_content_type_devolve_o_content_type_do_model():
    assert Usuario.get_content_type() == ContentType.objects.get_for_model(Usuario)


def test_raw_metadata_devolve_dicionario_vazio_sem_registro():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        assert usuario.raw_metadata == {}


def test_raw_metadata_nao_cria_registro():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        usuario.raw_metadata

        assert Metadata.all_objects.count() == 0


def test_raw_metadata_devolve_os_dados_persistidos():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        Metadata.objects.create(
            content_type=Usuario.get_content_type(),
            object_id=usuario.pk,
            dados={"erp_id": "X-1"},
        )

        assert usuario.raw_metadata == {"erp_id": "X-1"}
```

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/base/tests/test_metadata_mixin.py -q --nomigrations`
Esperado: FAIL — `get_content_type` não existe e `test_raw_metadata_nao_cria_registro` encontra uma linha criada pelo `get_or_create` atual.

- [ ] **Passo 3: Reescrever o mixin**

Substitua a classe `MetadataMixin` inteira por:

```python
class MetadataMixin(models.Model):
    """Acesso de leitura ao metadata genérico do objeto.

    Escrita é responsabilidade de `apps.api.metadata.handlers.aplicar_metadata`,
    o único ponto autorizado a criar linha de `Metadata`.
    """

    metadata_registros = GenericRelation(
        "metadata.Metadata",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="%(app_label)s_%(class)s",
    )

    @classmethod
    def get_content_type(cls):
        """`ContentType` deste model, resolvido pelo cache do Django."""
        return ContentType.objects.get_for_model(cls)

    @property
    def raw_metadata(self):
        """Documento de metadata do objeto, ou `{}` quando não houver.

        Leitura pura: não cria registro. Como `Metadata` está sob RLS, exige
        contexto de organização, igual a qualquer leitura de model de negócio.
        """
        registros = list(self.metadata_registros.all())
        return registros[0].dados if registros else {}

    class Meta:
        abstract = True
```

**Não troque isso por `.first()`.** Parece equivalente e não é: `.first()` acrescenta `ORDER BY` e `LIMIT`, o que força uma query nova e **ignora o cache do `prefetch_related`** — o N+1 que a `GenericRelation` existe para resolver voltaria inteiro. `.all()` devolve o cache quando ele existe. A constraint garante no máximo um registro vivo por organização e objeto, então materializar a lista é seguro.

O import de `GenericRelation` entra no topo do arquivo, junto do `GenericForeignKey` que já é importado em outros módulos:

```python
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
```

Confira se `ContentType` já está importado no arquivo antes de duplicar a linha.

- [ ] **Passo 4: Rodar e confirmar que passa**

Run: `uv run --group test pytest apps/api/base/tests/test_metadata_mixin.py -q --nomigrations`
Esperado: PASS, 4 testes.

- [ ] **Passo 5: Confirmar que a `GenericRelation` não pede migration**

`GenericRelation` é campo virtual e não gera coluna, então nada deve aparecer:

```bash
uv run python manage.py makemigrations --check --dry-run
```

Esperado: nenhuma mudança pendente. Se aparecer alguma operação relacionada a `metadata_registros`, pare e reporte — o plano assume que não há.

- [ ] **Passo 6: Rodar a suíte da base inteira**

Run: `uv run --group test pytest apps/api/base -q --nomigrations`
Esperado: PASS. Se algum teste existente dependia do `get_or_create` na leitura, corrija-o para usar `Metadata.objects.create` explicitamente e diga isso no relatório.

- [ ] **Passo 7: Lint e commit**

```bash
uv run ruff format apps/api/base/models.py apps/api/base/tests/test_metadata_mixin.py
uv run ruff check apps/api/base/
git add apps/api/base/models.py apps/api/base/tests/test_metadata_mixin.py
git commit -m "fix(metadata): impedir que a leitura de metadata grave no banco"
```

---

### Tarefa 4: Constraint por organização

A constraint atual, `UniqueConstraint("content_type", "object_id")`, não inclui organização. Como o `MetadataMixin` vive em `BaseTenantless`, `Usuario`, `Organizacao`, `Vinculo` e `Convite` também aceitam metadata — e duas organizações não conseguiriam anotar o mesmo usuário. Pior: o RLS esconde a linha da outra organização, então a segunda escrita recebe `IntegrityError` sobre uma linha invisível, virando 500 e vazando existência entre inquilinos.

Ver [ADR 0007](../../adr/0007-indices-e-constraints-por-organizacao.md) e [ADR 0008](../../adr/0008-unicidade-por-organizacao.md).

**Arquivos:**
- Modificar: `apps/api/metadata/models.py:26-31`
- Modificar: `apps/api/metadata/tests/test_model_contract.py` (o teste atual afirma a constraint antiga por AST e **vai falhar**)
- Criar: `apps/api/metadata/migrations/0002_metadata_constraint_por_organizacao.py`
- Teste: `apps/api/metadata/tests/test_constraints.py`

**Interfaces:**
- Produz: constraint `metadata_organizacao_content_type_object_id_unique`.

- [ ] **Passo 1: Escrever o teste que falha**

```python
import pytest

from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_duas_organizacoes_anotam_o_mesmo_objeto():
    usuario = criar_usuario()
    acme = Organizacao.objects.create(nome="Acme", slug="acme")
    globex = Organizacao.objects.create(nome="Globex", slug="globex")
    content_type = Usuario.get_content_type()

    with organizacao_atual_privilegiada(acme.pk):
        Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={"dono": "acme"})

    with organizacao_atual_privilegiada(globex.pk):
        Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={"dono": "globex"})

    assert Metadata.all_objects.count() == 2


def test_documento_pode_ser_recriado_apos_exclusoes_sucessivas():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    content_type = Usuario.get_content_type()

    with organizacao_atual_privilegiada(organizacao.pk):
        for _ in range(3):
            registro = Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={})
            registro.delete()

        assert Metadata.objects.count() == 0
        assert Metadata.all_objects.count() == 3
```

O segundo teste é o que prova a escolha do índice **parcial** em vez de `is_deleted` na lista de campos: com `is_deleted` como coluna da constraint, só caberia um registro excluído e a segunda exclusão estouraria `IntegrityError`.

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/metadata/tests/test_constraints.py -q --nomigrations`
Esperado: FAIL com `IntegrityError` de violação de `metadata_content_type_object_id_unique` nos dois testes.

- [ ] **Passo 3: Trocar a constraint no model**

Em `apps/api/metadata/models.py`, substitua o bloco `constraints` e amplie `object_id` (os PKs do projeto são `BigAutoField`, e `PositiveIntegerField` para em 2.147.483.647):

```python
    object_id = models.PositiveBigIntegerField(verbose_name=_("object ID"))
```

```python
        constraints = [
            models.UniqueConstraint(
                fields=["organizacao", "content_type", "object_id"],
                condition=models.Q(is_deleted=False),
                name="metadata_organizacao_content_type_object_id_unique",
            ),
        ]
```

- [ ] **Passo 4: Atualizar o teste de contrato existente**

`apps/api/metadata/tests/test_model_contract.py` inspeciona a constraint por AST e afirma os campos e o nome antigos. Atualize as duas asserções para os novos valores e acrescente uma terceira, exigindo `condition`:

```python
def test_metadata_has_unique_composite_constraint():
    assignments = _metadata_meta_assignments()
    constraints = assignments["constraints"]
    constraint = constraints.elts[0]

    assert isinstance(constraint.func, ast.Attribute)
    assert constraint.func.attr == "UniqueConstraint"
    assert ast.literal_eval(next(keyword.value for keyword in constraint.keywords if keyword.arg == "fields")) == [
        "organizacao",
        "content_type",
        "object_id",
    ]
    assert (
        ast.literal_eval(next(keyword.value for keyword in constraint.keywords if keyword.arg == "name"))
        == "metadata_organizacao_content_type_object_id_unique"
    )
    assert any(keyword.arg == "condition" for keyword in constraint.keywords)
```

- [ ] **Passo 5: Gerar a migration**

```bash
uv run python manage.py makemigrations metadata --name metadata_constraint_por_organizacao
```

Confira o arquivo gerado: deve conter `AlterField` em `object_id`, `RemoveConstraint` da antiga e `AddConstraint` da nova. Não edite as operações à mão a menos que falte alguma.

- [ ] **Passo 6: Rodar e confirmar que passa**

```bash
uv run --group test pytest apps/api/metadata -q --nomigrations
uv run python manage.py makemigrations --check --dry-run
```

Esperado: testes PASS e nenhuma migration pendente.

- [ ] **Passo 7: Lint e commit**

```bash
uv run ruff format apps/api/metadata/
uv run ruff check apps/api/metadata/
git add apps/api/metadata/
git commit -m "fix(metadata): tornar o documento único por organização"
```

---

### Tarefa 5: `aplicar_metadata`

**Arquivos:**
- Modificar: `apps/api/metadata/handlers.py`
- Teste: `apps/api/metadata/tests/test_aplicar_metadata.py`

**Interfaces:**
- Consome: `mesclar_dados` (Tarefa 2), `get_content_type` (Tarefa 3), a constraint (Tarefa 4).
- Produz: `aplicar_metadata(objeto, alteracoes) -> Metadata`.

- [ ] **Passo 1: Escrever os testes que falham**

```python
import pytest
from rest_framework.serializers import ValidationError

from apps.api.metadata.handlers import aplicar_metadata
from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Acme", slug="acme")


def test_cria_o_registro_na_primeira_escrita(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        registro = aplicar_metadata(usuario, {"erp_id": "X-1"})

    assert registro.dados == {"erp_id": "X-1"}
    assert registro.organizacao_id == organizacao.pk
    assert Metadata.objects.count() == 1


def test_reaproveita_o_registro_existente(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicar_metadata(usuario, {"erp_id": "X-1"})
        registro = aplicar_metadata(usuario, {"nota": "urgente"})

    assert registro.dados == {"erp_id": "X-1", "nota": "urgente"}
    assert Metadata.objects.count() == 1


def test_valor_nulo_remove_a_chave(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicar_metadata(usuario, {"erp_id": "X-1", "nota": "urgente"})
        registro = aplicar_metadata(usuario, {"nota": None})

    assert registro.dados == {"erp_id": "X-1"}


def test_nao_cria_registro_quando_a_validacao_falha(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(ValidationError):
            aplicar_metadata(usuario, {"": "sem chave"})

        assert Metadata.all_objects.count() == 0
```

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/metadata/tests/test_aplicar_metadata.py -q --nomigrations`
Esperado: FAIL com `ImportError: cannot import name 'aplicar_metadata'`

- [ ] **Passo 3: Implementar o service**

Acrescente ao fim de `apps/api/metadata/handlers.py`, e o import do model no topo:

```python
from apps.api.metadata.models import Metadata
```

```python
def aplicar_metadata(objeto, alteracoes):
    """Aplica alterações ao documento de metadata de um objeto e persiste.

    É o único ponto do sistema que cria linha de `Metadata`. A validação roda
    antes de qualquer escrita, para que um payload inválido não deixe registro
    vazio para trás.

    Args:
        objeto: Instância de qualquer model que herde de `BaseTenantless`.
        alteracoes: Chaves a gravar; valor `None` remove a chave.

    Returns:
        O registro de `Metadata` persistido.

    Raises:
        rest_framework.serializers.ValidationError: Propagada de `mesclar_dados`.
    """
    content_type = objeto.get_content_type()
    registro = Metadata.objects.filter(content_type=content_type, object_id=objeto.pk).first()
    dados = mesclar_dados(registro.dados if registro is not None else {}, alteracoes)

    if registro is None:
        return Metadata.objects.create(content_type=content_type, object_id=objeto.pk, dados=dados)

    registro.dados = dados
    registro.save(update_fields=["dados"])
    return registro
```

A organização não é passada explicitamente: `TenantMixin.save` a preenche a partir do contexto RLS ativo.

- [ ] **Passo 4: Rodar e confirmar que passa**

Run: `uv run --group test pytest apps/api/metadata -q --nomigrations`
Esperado: PASS.

- [ ] **Passo 5: Lint e commit**

```bash
uv run ruff format apps/api/metadata/
uv run ruff check apps/api/metadata/
git add apps/api/metadata/
git commit -m "feat(metadata): adicionar service de escrita de metadata"
```

---

### Tarefa 6: Serializer de alteração

**Arquivos:**
- Criar: `apps/api/metadata/serializers.py`
- Teste: `apps/api/metadata/tests/test_serializers.py`

**Interfaces:**
- Produz: `MetadataAlteracaoSerializer`, com o único campo `dados` (`dict[str, str | None]`).

Divisão de responsabilidade: o serializer valida **formato**, o handler valida **limites**.

- [ ] **Passo 1: Escrever os testes que falham**

```python
from apps.api.metadata.serializers import MetadataAlteracaoSerializer


def test_aceita_valores_texto_e_nulos():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"erp_id": "X-1", "nota": None}})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["dados"] == {"erp_id": "X-1", "nota": None}


def test_aceita_documento_vazio():
    serializer = MetadataAlteracaoSerializer(data={"dados": {}})

    assert serializer.is_valid(), serializer.errors


def test_exige_o_campo_dados():
    serializer = MetadataAlteracaoSerializer(data={})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_valor_numerico():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"tentativas": 3}})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_valor_aninhado():
    serializer = MetadataAlteracaoSerializer(data={"dados": {"erp": {"id": "X-1"}}})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors


def test_rejeita_dados_que_nao_sao_objeto():
    serializer = MetadataAlteracaoSerializer(data={"dados": ["erp_id"]})

    assert not serializer.is_valid()
    assert "dados" in serializer.errors
```

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/metadata/tests/test_serializers.py -q --nomigrations`
Esperado: FAIL com `ModuleNotFoundError`.

- [ ] **Passo 3: Implementar o serializer**

`DictField(child=CharField())` não serve: o `CharField` do DRF converte silenciosamente `3` em `"3"`. A checagem explícita é o que garante o contrato de texto.

```python
"""Serializers do metadata genérico."""

from rest_framework import serializers


class MetadataAlteracaoSerializer(serializers.Serializer):
    """Valida o corpo de `PATCH /<recurso>/<id>/metadata/`.

    Cuida apenas do formato; os limites de tamanho e quantidade são aplicados
    por `apps.api.metadata.handlers.mesclar_dados`.
    """

    dados = serializers.DictField(allow_empty=True)

    def validate_dados(self, value):
        """Exige chaves de texto e valores de texto ou nulos."""
        for chave, valor in value.items():
            if valor is not None and not isinstance(valor, str):
                raise serializers.ValidationError(f"O valor da chave '{chave}' deve ser texto ou nulo.")

        return value
```

- [ ] **Passo 4: Rodar e confirmar que passa**

Run: `uv run --group test pytest apps/api/metadata/tests/test_serializers.py -q --nomigrations`
Esperado: PASS, 6 testes.

- [ ] **Passo 5: Lint e commit**

```bash
uv run ruff format apps/api/metadata/
uv run ruff check apps/api/metadata/
git add apps/api/metadata/serializers.py apps/api/metadata/tests/test_serializers.py
git commit -m "feat(metadata): adicionar serializer de alteração de metadata"
```

---

### Tarefa 7: Action no `BaseModelViewSet`

**Arquivos:**
- Modificar: `apps/api/base/views.py` (novo mixin junto dos demais mixins de action, e `BaseModelViewSet` passando a herdá-lo)
- Teste: `apps/api/base/tests/test_metadata_action.py`

**Interfaces:**
- Consome: `MetadataAlteracaoSerializer` (Tarefa 6), `aplicar_metadata` (Tarefa 5), `raw_metadata` (Tarefa 3).
- Produz: `MetadataViewSetMixin`, com o atributo de classe `metadata_habilitado = True`.

Contrato:

```
GET   /pedidos/42/metadata/  -> 200 {"dados": {"erp_id": "X-1"}}
PATCH /pedidos/42/metadata/  <- {"dados": {"nota": null, "canal": "web"}}
                             -> 200 {"dados": {"erp_id": "X-1", "canal": "web"}}
```

Não há `DELETE`: hoje `CustomDjangoModelPermissions.is_action()` só reconhece uma action quando o path começa com `v1/`, prefixo que nenhuma URL do projeto usa, então vale o mapa por método HTTP — e nele `DELETE` exigiria `delete_<model>`, permissão errada para remover uma chave de anotação. Com `GET` e `PATCH` as permissões caem certas: `view_<model>` e `change_<model>`.

- [ ] **Passo 1: Escrever os testes que falham**

Os testes usam `APIRequestFactory` com um viewset descartável, que é o idioma de `apps/api/base/tests/test_view_actions.py`. Como `Metadata` está sob RLS, as chamadas ficam dentro de `organizacao_atual_privilegiada` — em produção quem abre esse contexto é a `TenantPermission`.

```python
from django.contrib.auth.models import Permission

from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest

from apps.api.autenticacao.permissions import CustomDjangoModelPermissions
from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.views import BaseModelViewSet
from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


class _UsuarioSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email"]


class _UsuarioViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


class _SemMetadataViewSet(_UsuarioViewSet):
    metadata_habilitado = False


class _UsuarioProtegidoViewSet(_UsuarioViewSet):
    """Exercita a permissão real; os demais testes usam `AllowAny` de propósito."""

    permission_classes = [CustomDjangoModelPermissions]


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Acme", slug="acme")


def test_get_devolve_documento_vazio_quando_nao_ha_metadata(organizacao):
    usuario = criar_usuario()
    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/metadata/")
    view = _UsuarioViewSet.as_view({"get": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 200
    assert response.data == {"dados": {}}


def test_patch_grava_e_devolve_o_documento(organizacao):
    usuario = criar_usuario()
    request = APIRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    view = _UsuarioViewSet.as_view({"patch": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

        assert response.status_code == 200
        assert response.data == {"dados": {"erp_id": "X-1"}}
        assert Metadata.objects.get().dados == {"erp_id": "X-1"}


def test_patch_funde_com_o_documento_existente(organizacao):
    usuario = criar_usuario()
    view = _UsuarioViewSet.as_view({"patch": "metadata"})
    factory = APIRequestFactory()

    with organizacao_atual_privilegiada(organizacao.pk):
        view(
            factory.patch("/", {"dados": {"erp_id": "X-1", "nota": "urgente"}}, format="json"),
            pk=usuario.pk,
        )
        response = view(factory.patch("/", {"dados": {"nota": None}}, format="json"), pk=usuario.pk)

    assert response.data == {"dados": {"erp_id": "X-1"}}


def test_patch_invalido_devolve_422(organizacao):
    usuario = criar_usuario()
    request = APIRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"tentativas": 3}},
        format="json",
    )
    view = _UsuarioViewSet.as_view({"patch": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 422
    assert Metadata.all_objects.count() == 0


def test_action_e_registrada_por_padrao():
    nomes = [action.__name__ for action in _UsuarioViewSet.get_extra_actions()]

    assert "metadata" in nomes


def test_opt_out_remove_a_rota():
    nomes = [action.__name__ for action in _SemMetadataViewSet.get_extra_actions()]

    assert "metadata" not in nomes


def test_get_exige_permissao_de_visualizacao(organizacao):
    usuario = criar_usuario()
    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/metadata/")
    force_authenticate(request, user=usuario)
    view = _UsuarioProtegidoViewSet.as_view({"get": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 403


def test_patch_exige_permissao_de_alteracao_e_nao_basta_a_de_leitura(organizacao):
    usuario = criar_usuario()
    usuario.user_permissions.add(Permission.objects.get(codename="view_usuario"))
    autor = Usuario.objects.get(pk=usuario.pk)
    request = APIRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    force_authenticate(request, user=autor)
    view = _UsuarioProtegidoViewSet.as_view({"patch": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 403
    assert Metadata.all_objects.count() == 0


def test_patch_passa_com_permissao_de_alteracao(organizacao):
    usuario = criar_usuario()
    usuario.user_permissions.add(Permission.objects.get(codename="change_usuario"))
    autor = Usuario.objects.get(pk=usuario.pk)
    request = APIRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    force_authenticate(request, user=autor)
    view = _UsuarioProtegidoViewSet.as_view({"patch": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 200


def test_listagem_com_prefetch_nao_cresce_em_queries(organizacao, django_assert_num_queries):
    with organizacao_atual_privilegiada(organizacao.pk):
        for _ in range(3):
            usuario = criar_usuario()
            Metadata.objects.create(
                content_type=Usuario.get_content_type(),
                object_id=usuario.pk,
                dados={"erp_id": "X-1"},
            )

        with django_assert_num_queries(2):
            documentos = [
                instancia.raw_metadata for instancia in Usuario.objects.prefetch_related("metadata_registros")
            ]

    assert documentos == [{"erp_id": "X-1"}] * 3
```

- [ ] **Passo 2: Rodar e confirmar a falha**

Run: `uv run --group test pytest apps/api/base/tests/test_metadata_action.py -q --nomigrations`
Esperado: FAIL — a action `metadata` não existe.

Duas observações sobre estes testes:

- `test_listagem_com_prefetch_nao_cresce_em_queries` espera duas queries, a dos usuários e a do prefetch. Se o número real divergir, ajuste a expectativa **depois** de confirmar no log que ela não cresce com a quantidade de registros — o que o teste protege é a ausência de N+1, não o número exato.
- Os três testes de permissão relêem o usuário do banco (`Usuario.objects.get(pk=...)`) porque o Django memoriza as permissões na instância. O projeto ainda tem um cache de autorização próprio (`internal_frameworks/permission_cache`, ligado por `AUTHORIZATION_CACHE_ENABLED`); se algum desses testes ficar intermitente, limpe o cache entre eles em vez de afrouxar a asserção.

- [ ] **Passo 3: Implementar o mixin**

Em `apps/api/base/views.py`, junto dos outros mixins de action (depois de `ClonarViewSetMixin`, por exemplo):

```python
class MetadataViewSetMixin:
    """Action de metadata genérico do objeto.

    Ligada por padrão em `BaseModelViewSet`. Uma view que não deva aceitar
    escrita livre de chaves declara `metadata_habilitado = False`, e a action
    deixa de ser coletada pelo router — a rota não passa a existir.
    """

    metadata_habilitado = True

    @classmethod
    def get_extra_actions(cls):
        actions = super().get_extra_actions()
        if cls.metadata_habilitado:
            return actions

        return [action for action in actions if action.__name__ != "metadata"]

    @action(methods=["get", "patch"], detail=True)
    def metadata(self, request, *args, **kwargs):
        """Lê ou altera o documento de metadata do objeto.

        `PATCH` funde as chaves enviadas com as existentes; valor `null` remove
        a chave. As permissões vêm do método HTTP: `view_<model>` no `GET` e
        `change_<model>` no `PATCH`.
        """
        instance = self.get_object()

        if request.method == "GET":
            return Response({"dados": instance.raw_metadata})

        serializer = MetadataAlteracaoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registro = aplicar_metadata(instance, serializer.validated_data["dados"])
        return Response({"dados": registro.dados})
```

Imports no topo do arquivo:

```python
from apps.api.metadata.handlers import aplicar_metadata
from apps.api.metadata.serializers import MetadataAlteracaoSerializer
```

- [ ] **Passo 4: Ligar no `BaseModelViewSet`**

```python
class BaseModelViewSet(UtilsViewSetMixin, MetadataViewSetMixin, ModelViewSet):
```

`GenericBaseViewSet` fica como está: sem `get_object` de model concreto, a action não faria sentido lá.

- [ ] **Passo 5: Rodar e confirmar que passa**

Run: `uv run --group test pytest apps/api/base/tests/test_metadata_action.py -q --nomigrations`
Esperado: PASS, 10 testes.

- [ ] **Passo 6: Rodar a suíte inteira**

Run: `make test`
Esperado: PASS. Esta é a primeira tarefa que altera o comportamento de **todos** os viewsets que herdam de `BaseModelViewSet`; qualquer regressão aparece aqui.

- [ ] **Passo 7: Lint e commit**

```bash
uv run ruff format apps/api/base/
uv run ruff check .
git add apps/api/base/views.py apps/api/base/tests/test_metadata_action.py
git commit -m "feat(metadata): expor metadata como action do BaseModelViewSet"
```

---

### Tarefa 8: Documentação

**Arquivos:**
- Criar: `docs/reference/metadata.md`
- Modificar: `mkdocs.yml` (seção `Referência`, após `Deprecação de API`)
- Modificar: `docs/ROADMAP.md`

- [ ] **Passo 1: Escrever a referência**

Crie `docs/reference/metadata.md` com este conteúdo:

````markdown
# Metadata genérico

Todo recurso servido por um `BaseModelViewSet` aceita um documento
chave/valor anexado pelo consumidor da API. Serve para uma integração guardar
dados próprios — o id do registro no ERP, um rótulo de campanha, o estado de
uma sincronização — sem exigir migration no projeto derivado.

O documento é **por organização**: duas organizações anotando o mesmo objeto
mantêm documentos independentes.

## Contrato

```http
GET /pedidos/42/metadata/
```

```json
{"dados": {"erp_id": "X-1", "nota": "urgente"}}
```

`PATCH` funde as chaves enviadas com as existentes. Valor `null` remove a chave.

```http
PATCH /pedidos/42/metadata/
{"dados": {"nota": null, "canal": "web"}}
```

```json
{"dados": {"erp_id": "X-1", "canal": "web"}}
```

Não existe `DELETE`: remover é enviar a chave com `null`.

Valores são **texto**. Números, booleanos, listas e objetos aninhados são
recusados com 422 — serialize do lado do cliente o que precisar de estrutura.

## Permissões

| Método | Permissão exigida |
|--------|-------------------|
| `GET` | `<app_label>.view_<model>` |
| `PATCH` | `<app_label>.change_<model>` |

Quem pode editar o registro pode anotá-lo. Não há codename próprio de metadata.

## Limites

| Setting | Variável de ambiente | Default |
|---------|----------------------|---------|
| `METADATA_MAX_KEYS` | `METADATA_MAX_KEYS` | 50 |
| `METADATA_MAX_KEY_LENGTH` | `METADATA_MAX_KEY_LENGTH` | 256 |
| `METADATA_MAX_VALUE_LENGTH` | `METADATA_MAX_VALUE_LENGTH` | 1024 |

Violar qualquer um deles devolve **422** no envelope de erros padrão. Uma
remoção abre espaço na mesma requisição: enviar `{"a": null, "b": "1"}` com o
limite estourado por `a` funciona.

## Desligando em um recurso

A action é registrada por padrão. Metadata é superfície de escrita livre para o
cliente, então recursos sensíveis devem recusá-la explicitamente:

```python
class VinculoViewSet(BaseModelViewSet):
    metadata_habilitado = False
```

A action deixa de ser coletada pelo router: a rota não existe, e a resposta é
404 — nem `OPTIONS` revela o endpoint.

## Uso a partir do Python

Leitura é uma property de qualquer instância; escrita passa pelo service:

```python
from apps.api.metadata.handlers import aplicar_metadata

pedido.raw_metadata                        # {"erp_id": "X-1"}
aplicar_metadata(pedido, {"erp_id": "X-2"})
```

Em listagem, use `prefetch_related` para não pagar uma query por objeto:

```python
Pedido.objects.prefetch_related("metadata_registros")
```

`aplicar_metadata` é o único ponto do sistema que cria linha de `Metadata`.
Como `Metadata` está sob RLS, tanto a leitura quanto a escrita exigem contexto
de organização — fora do ciclo de request, envolva em `organizacao_atual(id)`.

## Ciclo de vida

Exclusão de objeto é lógica, então o documento permanece e o endpoint passa a
devolver 404 junto com o alvo. Em exclusão física o documento fica órfão: não há
job de expurgo.
````

- [ ] **Passo 2: Registrar no `mkdocs.yml`**

```yaml
      - Metadata genérico: reference/metadata.md
```

- [ ] **Passo 3: Fechar o item no roadmap**

Em `docs/ROADMAP.md`, no Batch 8, troque a linha do metadata framework de `⏳` para `✅`, descrevendo o que foi entregue no mesmo tom das demais linhas concluídas.

- [ ] **Passo 4: Validar a documentação**

Run: `make docs`
Esperado: build sem erro (`mkdocs build --strict`).

- [ ] **Passo 5: Commit**

```bash
git add docs/reference/metadata.md mkdocs.yml docs/ROADMAP.md
git commit -m "docs(metadata): documentar a camada de API do metadata"
```

---

## Verificação final

Rode tudo o que a CI roda, e reporte a saída real de cada comando:

```bash
make lint
make test
make docs
uv run python manage.py makemigrations --check --dry-run
```
