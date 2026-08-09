# Política de importações entre módulos — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir o carregamento de `apps.api.metadata` sem facades de reexportação e tornar normativa a política global de imports diretos, resolução localizada de ciclos e referências textuais em campos relacionais Django.

**Architecture:** Imports entre módulos permanecem livres e apontam para o módulo canônico que declara o objeto. Quando existir um ciclo concreto, somente uma aresta será adiada por import local; relações Django usam strings e type hints usam `TYPE_CHECKING`. O caso metadata aplica a regra removendo os dois `shared.py`, mantendo `Metadata -> Base` no topo e tornando `Base -> Metadata` um import local direto.

**Tech Stack:** Python 3.12, Django, pytest, pytest-django, Ruff, MkDocs Material.

## Global Constraints

- O caminho canônico do app é `apps.api.metadata`.
- Imports diretos entre quaisquer apps e tipos de módulo são permitidos.
- `shared.py` não pode existir apenas para reexportar objetos.
- Somente ciclos efetivamente reproduzidos recebem resolução lazy localizada.
- Campos relacionais Django referenciam models por string.
- Não introduzir `apps.get_model()`, facade lazy, registry de imports ou import hook.
- Não corrigir neste plano os demais aspectos funcionais do framework de metadata.
- Preservar todas as mudanças existentes no worktree; não criar commit sem autorização explícita do usuário.

---

### Task 1: Carregar metadata pelo caminho canônico sem ciclo

**Files:**
- Create: `apps/api/metadata/tests/test_imports.py`
- Modify: `api/settings.py`
- Modify: `apps/api/metadata/apps.py`
- Modify: `apps/api/metadata/admin.py`
- Modify: `apps/api/metadata/models.py`
- Modify: `apps/api/base/models.py`
- Delete: `apps/api/metadata/shared.py`
- Delete: `apps/api/base/shared.py`
- Test: `apps/api/metadata/tests/test_imports.py`
- Test: `apps/api/metadata/tests/test_model_contract.py`

**Interfaces:**
- Consumes: `Base` de `apps.api.base.models` e `Metadata` de `apps.api.metadata.models`.
- Produces: app Django registrado como `apps.api.metadata`; `Metadata` como subclasse de `Base`; propriedade `BaseGlobal.metadata` com import local canônico.

- [ ] **Step 1: Escrever o teste de regressão de imports**

```python
from apps.api.base.models import Base
from apps.api.metadata.models import Metadata


def test_metadata_importa_base_pelo_modulo_canonico():
    assert issubclass(Metadata, Base)
```

- [ ] **Step 2: Executar o teste e confirmar a falha de bootstrap**

Run:

```bash
uv run pytest apps/api/metadata/tests/test_imports.py -q --no-cov
```

Expected: FAIL durante o setup com `ModuleNotFoundError` para `apps.metadata` ou erro de importação causado pelas facades atuais.

- [ ] **Step 3: Corrigir o caminho canônico do app**

Em `api/settings.py`, substituir a entrada antiga:

```python
BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.api.metadata",
    "apps.logs",
    "apps.organizacoes",
    "apps.usuarios",
]
```

Em `apps/api/metadata/apps.py`:

```python
from django.apps import AppConfig


class MetadataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api.metadata"
```

- [ ] **Step 4: Substituir as facades por imports canônicos**

Em `apps/api/metadata/models.py`:

```python
from apps.api.base.models import Base
```

Em `BaseGlobal.metadata`, dentro de `apps/api/base/models.py`:

```python
@property
def metadata(self):
    from apps.api.metadata.models import Metadata

    if not hasattr(self, "_metadata"):
        self._metadata = Metadata.objects.get_or_create(content_type=self.content_type, object_id=self.pk)[0]
    return self._metadata
```

Remover `apps/api/metadata/shared.py` e `apps/api/base/shared.py`; ambos apenas reexportam classes de `models.py`.

Como o app ainda não registra models no admin, substituir o scaffold com import
não utilizado em `apps/api/metadata/admin.py` por um módulo vazio. Isso permite
validar o app inteiro com Ruff sem ampliar sua superfície administrativa.

- [ ] **Step 5: Executar os testes direcionados**

Run:

```bash
uv run pytest apps/api/metadata/tests/test_imports.py apps/api/metadata/tests/test_model_contract.py -q --no-cov
```

Expected: `2 passed`.

- [ ] **Step 6: Confirmar o carregamento completo do Django**

Run:

```bash
uv run python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 7: Validar lint e formatação do código alterado**

Run:

```bash
uv run ruff check api/settings.py apps/api/base/models.py apps/api/metadata
uv run ruff format --check api/settings.py apps/api/base/models.py apps/api/metadata
```

Expected: Ruff sem erros e todos os arquivos formatados.

### Task 2: Registrar a política global de imports

**Files:**
- Create: `docs/adr/0006-importacoes-entre-modulos.md`
- Modify: `mkdocs.yml`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: decisão aprovada em `docs/superpowers/specs/2026-08-09-importacoes-entre-modulos-design.md`.
- Produces: ADR 0006 publicado no portal e regras operacionais equivalentes para agentes de código.

- [ ] **Step 1: Criar o ADR 0006**

Criar `docs/adr/0006-importacoes-entre-modulos.md`:

```markdown
# 0006 — Importações entre módulos

- Status: Aceito
- Data: 2026-08-09

## Contexto

Apps Django compartilham models, serializers, validators, choices, services e
outros objetos. Uma hierarquia universal de camadas evitaria alguns ciclos, mas
restringiria dependências legítimas. Arquivos `shared.py` que apenas reexportam
objetos também não resolvem o problema: eles mudam o caminho, mas preservam o
mesmo grafo de imports.

O Python carrega cada módulo sequencialmente. Dois módulos não podem depender,
no topo, de símbolos um do outro antes que ambos terminem de inicializar. Alguma
aresta de um ciclo concreto precisa ser adiada ou removida.

## Decisão

Imports diretos entre quaisquer apps e tipos de módulo são permitidos. Cada
objeto deve ser importado do módulo que o declara; `shared.py` não será usado
apenas como facade de reexportação.

Não haverá uma ordem global obrigatória entre models, services, serializers,
validators, views ou outros módulos. Quando um ciclo for reproduzido, somente
uma aresta será tornada lazy, no menor escopo possível:

1. Campos relacionais Django devem referenciar models por string, como
   `models.ForeignKey("organizacoes.Organizacao", ...)`.
2. Imports usados somente para type hints devem ficar sob `TYPE_CHECKING`, com
   annotations adiadas.
3. Dependências executadas em runtime devem usar import local dentro do método,
   propriedade ou função que as utiliza.
4. Contratos só devem ser extraídos para um módulo independente quando forem
   abstrações compartilhadas reais, não para esconder um ciclo.

`apps.get_model()`, facades lazy, registries de imports e import hooks globais
não fazem parte do padrão inicial.

## Alternativas rejeitadas

**Hierarquia global obrigatória.** Evitaria parte dos ciclos, mas impediria
colaborações legítimas entre tipos de módulo e imporia refatorações sem benefício
quando nenhum ciclo existe.

**`shared.py` como facade universal.** Reexportar uma classe importa o módulo que
a declara e, portanto, mantém a dependência circular.

**Registry ou import hook lazy global.** Permitiria mais imports bidirecionais no
topo, mas adicionaria magia, indireção e uma superfície de manutenção incompatível
com o problema atual.

## Consequências

- Módulos continuam livres para importar dependências legítimas diretamente.
- Somente ciclos concretos pagam o custo de um import lazy.
- Imports locais são uma exceção arquitetural deliberada e devem ficar no menor
  escopo que usa a dependência.
- Relações Django ficam desacopladas da ordem de carregamento dos models.
- A existência de um `shared.py` precisa ser justificada por abstrações próprias,
  nunca somente por conveniência de reexportação.
```

- [ ] **Step 2: Publicar o ADR na navegação do MkDocs**

Adicionar após o ADR 0005 em `mkdocs.yml`:

```yaml
      - '0006 — Importações entre módulos': adr/0006-importacoes-entre-modulos.md
```

- [ ] **Step 3: Adicionar a regra ao AGENTS.md**

Adicionar depois de `Project Structure & Module Organization`:

```markdown
## Import Architecture

Import objects directly from the module that declares them; do not create a
`shared.py` solely to re-export symbols. Imports between apps and module types
are otherwise unrestricted. When a concrete circular import occurs, defer only
one edge with a local import in the smallest runtime scope. Keep type-only
imports under `TYPE_CHECKING`. Django relational fields must reference models
with strings, such as `"organizacoes.Organizacao"`, so model loading does not
depend on import order.
```

- [ ] **Step 4: Adicionar a regra ao CLAUDE.md**

Adicionar em `## Convenções obrigatórias`:

```markdown
### Imports entre módulos

Importe cada objeto diretamente do módulo que o declara; não crie `shared.py`
somente para reexportar símbolos. Imports entre apps e tipos de módulo são
livres. Quando houver um ciclo concreto, adie apenas uma aresta com import local
no menor escopo de runtime. Imports exclusivos de tipagem ficam sob
`TYPE_CHECKING`. Campos relacionais Django referenciam models obrigatoriamente
por string, como `"organizacoes.Organizacao"`, para desacoplar o carregamento da
ordem de imports. Consulte o ADR 0006.
```

- [ ] **Step 5: Validar a documentação**

Run:

```bash
uv run mkdocs build --strict
```

Expected: build concluído sem erro.

- [ ] **Step 6: Validar whitespace e revisar somente os arquivos do plano**

Run:

```bash
git diff --check -- AGENTS.md CLAUDE.md api/settings.py apps/api/base/models.py apps/api/metadata docs/adr/0006-importacoes-entre-modulos.md mkdocs.yml
git status --short
```

Expected: nenhum erro de whitespace; o status preserva mudanças preexistentes e mostra apenas os arquivos esperados adicionais deste plano.

### Task 3: Verificação integrada

**Files:**
- Verify only: todos os arquivos listados nas Tasks 1 e 2.

**Interfaces:**
- Consumes: app metadata carregável e documentação normativa publicada.
- Produces: evidência final de que código, teste e documentação permanecem consistentes.

- [ ] **Step 1: Executar checks integrados sem banco**

Run:

```bash
uv run python manage.py check
uv run pytest apps/api/metadata/tests -q --no-cov
uv run ruff check api/settings.py apps/api/base/models.py apps/api/metadata
uv run ruff format --check api/settings.py apps/api/base/models.py apps/api/metadata
uv run mkdocs build --strict
```

Expected: Django sem issues, testes de metadata passando, Ruff limpo e documentação construída.

- [ ] **Step 2: Conferir o escopo final**

Run:

```bash
git status --short
git diff --stat
```

Expected: nenhuma alteração alheia foi removida ou sobrescrita; não há commit automático.
