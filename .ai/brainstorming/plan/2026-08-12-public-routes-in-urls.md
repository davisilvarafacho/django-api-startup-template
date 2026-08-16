# Rotas públicas declaradas em `urls.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preservar a descoberta automática de rotas públicas, declarando `PUBLIC_ROUTES` no `urls.py` de cada app em vez de em `public_routes.py`.

**Architecture:** `RouteRegistry` continua sendo o coletor único de prefixos e o middleware continua consumindo `routes_registry.matches(path)`. Apenas a configuração da instância pública muda para importar `<app>.urls.PUBLIC_ROUTES`; `tenant_free_registry` mantém sua convenção e ciclo de boot atuais.

**Tech Stack:** Python 3.12, Django, Django REST Framework, pytest-django, Ruff, MkDocs.

## Global Constraints

- Preserve a descoberta durante o boot em `CoreConfig.ready()` e a comparação por `str.startswith`.
- Preserve os defaults públicos: `/admin/`, `/health/` e `/metrics`.
- Preserve a validação atual: apenas `list` ou `tuple` de strings são aceitos.
- Não altere `tenant_free_registry`, `tenant_free_routes.py` ou `OrganizacoesConfig.ready()` nesta etapa.
- Não toque nos arquivos preexistentes modificados que não pertencem a esta mudança.

---

## File structure

- `apps/api/core/routes_registry.py`: define o collector genérico e configura o registry público para descobrir a constante em `urls`.
- `apps/api/core/apps.py`: documenta no boot a convenção correta da descoberta pública.
- `apps/api/autenticacao/urls.py`: passa a ser a fonte da lista pública do app de autenticação.
- `apps/api/autenticacao/public_routes.py`: deixa de existir, pois sua única responsabilidade migra para `urls.py`.
- `apps/api/core/tests/test_routes_registry.py`: exercita a descoberta usando módulos `<app>.urls` artificiais.
- `apps/organizacoes/permissions.py`, `CLAUDE.md`, `docs/explanation/autenticacao.md`: descrevem a nova convenção sem alterar o comportamento de tenancy.

### Task 1: Mudar a convenção pública e preservar seu contrato

**Files:**
- Modify: `apps/api/core/tests/test_routes_registry.py:10-30`
- Modify: `apps/api/core/routes_registry.py:7-13,83-85`
- Modify: `apps/api/core/apps.py:23-24`
- Modify: `apps/api/autenticacao/urls.py:31-53`
- Delete: `apps/api/autenticacao/public_routes.py`

**Interfaces:**
- Consumes: `settings.BUSINESS_APPS`, `RouteRegistry.discover()`, `RouteRegistry.matches(path)`.
- Produces: cada app pode exportar `PUBLIC_ROUTES: list[str] | tuple[str, ...]` de seu módulo `urls`; `routes_registry` importa `<app>.urls.PUBLIC_ROUTES` no boot.

- [ ] **Step 1: Escrever o teste que fixa a configuração pública**

Em `apps/api/core/tests/test_routes_registry.py`, troque a constante e a descrição da fixture para que os módulos falsos representem o novo contrato:

```python
FILE_NAME = "urls"
ATTR_NAME = "PUBLIC_ROUTES"


@pytest.fixture
def registrar_app(monkeypatch):
    """Cria um módulo `<app>.urls` importável e devolve o nome do app."""
```

Altere o import para `from apps.api.core.routes_registry import RouteRegistry, routes_registry` e acrescente:

```python
def test_registry_publico_descobre_public_routes_no_modulo_urls():
    assert routes_registry.file_name == "urls"
    assert routes_registry.attr_name == "PUBLIC_ROUTES"
```

Mantenha todos os cenários existentes — defaults, agregação, prefixo, módulo sem constante, tipos inválidos e idempotência — usando a fixture com `FILE_NAME = "urls"`.

- [ ] **Step 2: Executar o teste para confirmar a falha esperada**

Run:

```bash
uv run pytest apps/api/core/tests/test_routes_registry.py -q
```

Expected: FAIL em `test_registry_publico_descobre_public_routes_no_modulo_urls`, porque `routes_registry.file_name` ainda é `"public_routes"`.

- [ ] **Step 3: Implementar a mudança mínima de convenção**

Em `apps/api/core/routes_registry.py`, mantenha `RouteRegistry` sem mudanças funcionais e atualize apenas a documentação e a configuração da instância:

```python
# `routes_registry` — rotas públicas (dispensam token). Cada app declara
# `PUBLIC_ROUTES` no respectivo `urls.py`.

routes_registry = RouteRegistry(
    file_name="urls",
    attr_name="PUBLIC_ROUTES",
    defaults={"/admin/", "/health/", "/metrics"},
)
```

Em `apps/api/core/apps.py`, atualize o comentário imediatamente antes de `routes_registry.discover()` para mencionar `urls.PUBLIC_ROUTES`.

Em `apps/api/autenticacao/urls.py`, declare a constante ao lado de `urlpatterns`:

```python
PUBLIC_ROUTES = [
    "/auth/login/",
]
```

Remova `apps/api/autenticacao/public_routes.py`, pois a lista agora possui uma única fonte de verdade no módulo de URLs.

- [ ] **Step 4: Executar os testes unitários da mudança**

Run:

```bash
uv run pytest apps/api/core/tests/test_routes_registry.py -q
```

Expected: PASS, incluindo a asserção de que a instância pública procura `urls.PUBLIC_ROUTES`.

- [ ] **Step 5: Verificar o boot e o contrato de autenticação**

Run:

```bash
uv run pytest apps/api/autenticacao/tests/test_passthrough.py apps/api/core/tests/test_routes_registry.py -q
```

Expected: PASS; uma rota marcada pelo registry continua sendo resolvida como pública e nenhuma autenticação é chamada nesse caso.

- [ ] **Step 6: Commitar a alteração funcional isolada**

```bash
git add apps/api/core/routes_registry.py apps/api/core/apps.py apps/api/core/tests/test_routes_registry.py apps/api/autenticacao/urls.py apps/api/autenticacao/public_routes.py
git commit -m "refactor: discover public routes from urls"
```

### Task 2: Alinhar a documentação com a nova fonte de verdade

**Files:**
- Modify: `apps/organizacoes/permissions.py:3-6`
- Modify: `CLAUDE.md:119-124`
- Modify: `docs/explanation/autenticacao.md:18-30,240-245`

**Interfaces:**
- Consumes: o contrato produzido na Task 1 — `<app>.urls.PUBLIC_ROUTES`.
- Produces: documentação interna e de usuário que instrui corretamente onde declarar rotas públicas; a convenção de `TENANT_FREE_ROUTES` permanece explícita e inalterada.

- [ ] **Step 1: Atualizar a descrição da permissão de tenancy**

Em `apps/organizacoes/permissions.py`, ajuste somente o docstring inicial para dizer que rotas sem token são listadas em `urls.py` via `PUBLIC_ROUTES`, enquanto rotas autenticadas sem organização continuam em `tenant_free_routes.py` via `TENANT_FREE_ROUTES`.

- [ ] **Step 2: Atualizar a orientação de arquitetura do repositório**

Em `CLAUDE.md`, substitua a linha:

```markdown
- `public_routes.py` → `PUBLIC_ROUTES`: rotas **sem token**.
```

por:

```markdown
- `urls.py` → `PUBLIC_ROUTES`: rotas **sem token**.
```

Não altere a linha sobre `tenant_free_routes.py`.

- [ ] **Step 3: Atualizar a explicação de autenticação e seu exemplo**

Em `docs/explanation/autenticacao.md`:

```markdown
| `PUBLIC_ROUTES` | `<app>/urls.py` | Rotas que dispensam token |
```

Altere o fluxo para informar que `CoreConfig.ready()` procura `PUBLIC_ROUTES` em `urls`, e troque o cabeçalho do exemplo para:

```python
# apps/meu_app/urls.py
PUBLIC_ROUTES = [
    "/v1/meu-endpoint/publico/",
]
```

Mantenha a explicação de que `AllowAny` não substitui a inclusão em `PUBLIC_ROUTES`.

- [ ] **Step 4: Validar estilo, documentação e referências obsoletas ativas**

Run:

```bash
uv run ruff check apps/api/core/routes_registry.py apps/api/core/apps.py apps/api/core/tests/test_routes_registry.py apps/api/autenticacao/urls.py apps/organizacoes/permissions.py
make docs
rg -n 'public_routes\.py|módulo `public_routes`|public_routes\.PUBLIC_ROUTES' apps docs/explanation CLAUDE.md
```

Expected: Ruff e a validação MkDocs passam; a busca não retorna referência ativa à antiga convenção. Referências históricas em `.ai/brainstorming/plan/` e `.ai/brainstorming/spec/` não são alteradas nesta tarefa.

- [ ] **Step 5: Executar a verificação final focada**

Run:

```bash
uv run pytest apps/api/core/tests/test_routes_registry.py apps/api/autenticacao/tests/test_passthrough.py -q
git diff --check
git status --short
```

Expected: testes passam e `git diff --check` não produz saída. `git status --short` só lista os arquivos desta mudança e quaisquer alterações preexistentes já presentes antes da execução.

- [ ] **Step 6: Commitar a documentação isoladamente**

```bash
git add apps/organizacoes/permissions.py CLAUDE.md docs/explanation/autenticacao.md
git commit -m "docs: document public routes in urls"
```
