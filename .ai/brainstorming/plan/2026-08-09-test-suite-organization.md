# Test Suite Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganizar a suíte por responsabilidade, impedir coleta fora do projeto principal e remover `factory-boy` sem alterar comportamento de produção.

**Architecture:** Testes continuam colocados junto do app ou framework que possui a interface exercitada. Verificações de todo o repositório e helpers compartilhados passam para o pacote raiz `tests`; markers descrevem dependências de execução, enquanto subpastas descrevem features.

**Tech Stack:** Python 3.12, Django 5.2, Django REST Framework, pytest, pytest-django, uv, Ruff, MkDocs, Git.

## Global Constraints

- Preservar todas as alterações locais preexistentes e nunca incluí-las acidentalmente nos commits.
- Usar `uv` para dependências e execução.
- Usar Conventional Commits e produzir um commit estreito por tarefa.
- Não alterar regras de negócio nem reescrever assertions dos testes movidos.
- `.examples` não faz parte da suíte principal nem de varreduras arquiteturais.

---

### Task 1: Delimitar coleta e separar a verificação arquitetural

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/architecture/__init__.py`
- Create: `tests/architecture/test_authorization_writes.py`
- Modify: `internal_frameworks/permission_cache/tests/test_mutations.py`
- Modify: `pyproject.toml:194-205`

**Interfaces:**
- Consumes: raízes de produção `api`, `apps`, `internal_frameworks`, `utils`, `scripts` e arquivos Python da raiz.
- Produces: coleta pytest limitada a `api/tests`, `apps`, `internal_frameworks`, `utils` e `tests`; marker `architecture`.

- [ ] **Step 1: Registrar a coleta atual sem `.examples`**

Run:

```bash
uv run --group test pytest --collect-only -q --no-cov --nomigrations apps internal_frameworks utils
```

Expected: exit code 0 e nenhum node ID contendo `.examples`.

- [ ] **Step 2: Criar o pacote de testes arquiteturais**

Criar `tests/__init__.py` e `tests/architecture/__init__.py` vazios. Mover de `test_mutations.py` para `tests/architecture/test_authorization_writes.py` o scanner iniciado em `REPOSITORY_ROOT`, seus testes unitários e a verificação de código de produção.

Como o novo arquivo está dois diretórios abaixo da raiz, definir:

```python
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
```

Substituir o `rglob` irrestrito por raízes explícitas:

```python
PRODUCTION_ROOTS = ("api", "apps", "internal_frameworks", "utils", "scripts")
ROOT_PYTHON_FILES = ("conftest.py", "gunicorn.conf.py", "manage.py")


def production_python_files() -> list[Path]:
    files = [
        path
        for root in PRODUCTION_ROOTS
        for path in (REPOSITORY_ROOT / root).rglob("*.py")
        if not any(part in {"migrations", "tests"} for part in path.relative_to(REPOSITORY_ROOT).parts)
    ]
    files.extend(REPOSITORY_ROOT / name for name in ROOT_PYTHON_FILES if (REPOSITORY_ROOT / name).exists())
    excluded = REPOSITORY_ROOT / "internal_frameworks/permission_cache/mutations.py"
    return sorted(path for path in files if path != excluded)
```

Marcar o módulo com:

```python
pytestmark = pytest.mark.architecture
```

- [ ] **Step 3: Declarar as fronteiras no pytest**

Adicionar ao `[tool.pytest.ini_options]`:

```toml
testpaths = ["api/tests", "apps", "internal_frameworks", "utils", "tests"]
markers = [
  "architecture: verifica invariantes estáticos no código do repositório",
  "integration: requer infraestrutura externa ou coordenação entre processos",
  "redis: requer uma instância Redis real",
]
```

- [ ] **Step 4: Verificar coleta e scanner**

Run:

```bash
uv run --group test pytest tests/architecture/test_authorization_writes.py internal_frameworks/permission_cache/tests/test_mutations.py --no-cov --nomigrations -q
uv run --group test pytest --collect-only -q --no-cov --nomigrations
```

Expected: ambos terminam com exit code 0; nenhum node ID contém `.examples`.

- [ ] **Step 5: Commit**

```bash
git add tests/architecture internal_frameworks/permission_cache/tests/test_mutations.py
git add -p pyproject.toml
git commit -m "test: delimitar a coleta da suíte"
```

### Task 2: Substituir factory-boy por suporte local de testes

**Files:**
- Create: `tests/support/__init__.py`
- Create: `tests/support/usuarios.py`
- Create: `tests/support/test_usuarios.py`
- Create: `tests/README.md`
- Delete: `apps/usuarios/factories.py`
- Modify: `conftest.py`
- Modify: todos os testes que importam `apps.usuarios.factories.UsuarioFactory`
- Modify: `pyproject.toml:218-225`
- Modify: `uv.lock`
- Modify: `CLAUDE.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: `Usuario.objects._create_user(..., validate=False)`.
- Produces: `criar_usuario(**campos) -> Usuario`, helper compartilhado e exclusivo de testes.

- [ ] **Step 1: Escrever testes do helper antes de removê-lo**

Criar `tests/support/test_usuarios.py`:

```python
import pytest

from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_criar_usuario_gera_email_unico_e_senha_com_hash():
    primeiro = criar_usuario(password="Senha123!")
    segundo = criar_usuario(password="Senha123!")

    assert primeiro.email != segundo.email
    assert primeiro.check_password("Senha123!")


def test_criar_usuario_aceita_campos_sobrescritos():
    usuario = criar_usuario(email="ada@example.com", first_name="Ada", is_active=False)

    assert usuario.email == "ada@example.com"
    assert usuario.first_name == "Ada"
    assert usuario.is_active is False
```

- [ ] **Step 2: Confirmar que o novo helper ainda não existe**

Run:

```bash
uv run --group test pytest tests/support/test_usuarios.py --no-cov --nomigrations -q
```

Expected: FAIL na importação de `tests.support.usuarios`.

- [ ] **Step 3: Implementar o helper determinístico**

Criar `tests/support/usuarios.py`:

```python
from itertools import count
from typing import Any

from apps.usuarios.models import Usuario

_sequencia = count()


def criar_usuario(**campos: Any) -> Usuario:
    """Cria um usuário persistido para testes, sem aplicar a política de senha."""
    indice = next(_sequencia)
    password = campos.pop("password", "senha-de-teste")
    defaults = {
        "first_name": "Usuário",
        "last_name": str(indice),
        "email": f"usuario{indice}@exemplo.com",
    }
    defaults.update(campos)
    return Usuario.objects._create_user(password=password, validate=False, **defaults)
```

- [ ] **Step 4: Migrar os consumidores e remover a dependência**

Trocar imports por:

```python
from tests.support.usuarios import criar_usuario
```

Trocar chamadas `UsuarioFactory(...)` por `criar_usuario(...)`, remover `apps/usuarios/factories.py` e executar:

```bash
uv remove --group test factory-boy
```

- [ ] **Step 5: Documentar a infraestrutura de teste**

Criar `tests/README.md` explicando que `tests/support` não é interface de produção e atualizar as menções ativas em `CLAUDE.md` e `docs/ROADMAP.md`.

- [ ] **Step 6: Verificar helper e consumidores**

Run:

```bash
uv run --group test pytest tests/support/test_usuarios.py apps/usuarios/tests apps/api/autenticacao/tests internal_frameworks/permission_cache/tests --no-cov --nomigrations -q
uv run ruff check tests conftest.py apps internal_frameworks
```

Expected: exit code 0 nos dois comandos e nenhuma ocorrência de `factory-boy`, `factory_boy`, `import factory` ou `UsuarioFactory` no código ativo.

- [ ] **Step 7: Commit**

```bash
git add tests/support tests/README.md apps/usuarios/factories.py conftest.py CLAUDE.md docs/ROADMAP.md
git add -p apps internal_frameworks
git add -p pyproject.toml uv.lock
git commit -m "test: substituir factory-boy por helper local"
```

Não adicionar por inteiro arquivos de teste modificados ou não rastreados antes desta tarefa. Neles, aplicar a troca de import necessária para o workspace continuar executável, mas adicionar ao commit somente hunks rastreados que pertençam à migração.

### Task 3: Alinhar testes de configuração e management commands aos proprietários

**Files:**
- Create: `api/tests/__init__.py`
- Move: `apps/api/core/tests/test_logging_config.py` to `api/tests/test_logging_config.py`
- Move: `apps/api/core/tests/test_email_backends.py` to `api/tests/test_email_backends.py`
- Create: `apps/api/core/tests/management_commands/__init__.py`
- Move: `test_start_api_app.py`, `test_seed_demo.py`, `test_migrate_storage_command.py` and `test_permission_cache_command.py` into `apps/api/core/tests/management_commands/`
- Modify: `pyproject.toml` Ruff per-file ignores for moved legacy tests.

**Interfaces:**
- Consumes: imports existentes; nenhum import de produção muda.
- Produces: caminhos de teste que refletem o módulo proprietário.

- [ ] **Step 1: Mover arquivos sem alterar assertions**

Usar `mkdir` apenas para diretórios e `mv` para preservar o conteúdo; criar os `__init__.py` com `apply_patch`.

- [ ] **Step 2: Atualizar configuração Ruff**

Trocar os caminhos antigos de `test_email_backends.py` e qualquer outro override afetado pelos novos caminhos.

- [ ] **Step 3: Verificar os grupos movidos**

Run:

```bash
uv run --group test pytest api/tests apps/api/core/tests/management_commands --no-cov --nomigrations -q
uv run ruff check api/tests apps/api/core/tests/management_commands
```

Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add api/tests apps/api/core/tests
git add -p pyproject.toml
git commit -m "test: alinhar testes aos módulos proprietários"
```

### Task 4: Agrupar testes de autenticação por feature

**Files:**
- Create: `apps/api/autenticacao/tests/{api_keys,login,mfa,passwords,tokens}/__init__.py`
- Move: arquivos existentes de autenticação para as cinco subpastas conforme a feature.
- Move: `recent_auth_urls.py` para `passwords/recent_auth_urls.py`.
- Modify: referências a `recent_auth_urls`.

**Interfaces:**
- Consumes: o `conftest.py` na raiz de `autenticacao/tests` continua visível aos descendentes.
- Produces: navegação por `api_keys`, `login`, `mfa`, `passwords` e `tokens` sem mudar fixtures ou comportamento.

- [ ] **Step 1: Registrar a coleta atual de autenticação**

Run:

```bash
uv run --group test pytest apps/api/autenticacao/tests --collect-only -q --no-cov --nomigrations
```

Expected: exit code 0; guardar a contagem coletada.

- [ ] **Step 2: Criar subpacotes e mover arquivos**

Mapeamento:

```text
api_keys: test_api_keys, test_api_key_tenancy, test_token_audit
login: test_login, test_axes_login, test_axes_politica, test_axes_resposta, test_client_ip
mfa: test_mfa_admin_reset, test_mfa_checks, test_mfa_enrollment, test_mfa_login,
     test_mfa_models, test_mfa_reauthentication, test_trusted_devices
passwords: test_password_change, test_password_reset, test_recent_auth, recent_auth_urls
tokens: test_sessions_api, test_token_cleanup, test_token_model, test_token_services,
        test_token_types
```

`test_passthrough.py`, `test_permission_cache.py`, `test_schema.py`,
`test_scope_delegation.py` e `test_token_scopes.py` permanecem na raiz por serem
transversais.

- [ ] **Step 3: Corrigir a referência ao URLConf auxiliar**

Atualizar o dotted path para:

```python
"apps.api.autenticacao.tests.passwords.recent_auth_urls"
```

- [ ] **Step 4: Verificar coleta e execução**

Run:

```bash
uv run --group test pytest apps/api/autenticacao/tests --collect-only -q --no-cov --nomigrations
uv run --group test pytest apps/api/autenticacao/tests --no-cov --nomigrations -q
uv run ruff check apps/api/autenticacao/tests
```

Expected: a contagem coletada é igual à registrada no passo 1 e todos os comandos terminam com exit code 0.

- [ ] **Step 5: Commit**

```bash
git add apps/api/autenticacao/tests
git commit -m "test: organizar autenticação por feature"
```

### Task 5: Espelhar resolvers e signals do permission cache e localizar Redis

**Files:**
- Create: `internal_frameworks/permission_cache/tests/conftest.py`
- Create: `internal_frameworks/permission_cache/tests/resolvers/__init__.py`
- Create: `internal_frameworks/permission_cache/tests/signals/__init__.py`
- Move: `test_guardian_resolver.py` and `test_tenant_resolver.py` to `resolvers/`
- Move: `test_django_signals.py`, `test_guardian_signals.py` and `test_tenant_signals.py` to `signals/`
- Modify: `conftest.py`
- Modify: `internal_frameworks/permission_cache/tests/test_redis_integration.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: fixtures Redis hoje definidas no `conftest.py` raiz.
- Produces: fixtures Redis locais; markers `integration` e `redis`; comandos `test-fast`, `test-integration` e `test-redis`.

- [ ] **Step 1: Mover as fixtures Redis sem mudar sua implementação**

Mover `_redis_permission_cache_url` e `redis_permission_cache`, junto de seus imports exclusivos, para o novo `conftest.py` de `permission_cache/tests`.

- [ ] **Step 2: Mover resolvers e signals**

Criar os subpacotes e mover somente os cinco arquivos definidos nesta tarefa.

- [ ] **Step 3: Marcar a integração Redis**

Em `test_redis_integration.py`, definir:

```python
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.integration,
    pytest.mark.redis,
]
```

- [ ] **Step 4: Adicionar comandos Make**

Adicionar os targets à lista `.PHONY` e implementar:

```make
test-fast: ## Roda a suíte sem testes de integração
	uv run --group test pytest --nomigrations -m "not integration"

test-integration: ## Roda somente testes de integração
	uv run --group test pytest --nomigrations -m integration

test-redis: ## Roda testes que exigem Redis real
	uv run --group test pytest --nomigrations -m redis
```

- [ ] **Step 5: Verificar estrutura e seleção**

Run:

```bash
uv run --group test pytest internal_frameworks/permission_cache/tests --collect-only -q --no-cov --nomigrations
uv run --group test pytest internal_frameworks/permission_cache/tests -m "not integration" -q --no-cov --nomigrations
uv run ruff check conftest.py internal_frameworks/permission_cache/tests
```

Expected: exit code 0; a coleta Redis mostra os markers registrados; testes sem integração não consultam Redis real.

- [ ] **Step 6: Commit**

```bash
git add conftest.py internal_frameworks/permission_cache/tests Makefile
git commit -m "test: localizar integrações do cache de permissões"
```

### Task 6: Validar e documentar o resultado final

**Files:**
- Modify: `tests/README.md` somente se a estrutura final divergir do mapa documentado.

**Interfaces:**
- Consumes: todos os commits anteriores.
- Produces: evidência de coleta, lint, testes, migrações e documentação válidos.

- [ ] **Step 1: Confirmar que `.examples` está fora da coleta e do scanner**

Run:

```bash
uv run --group test pytest --collect-only -q --no-cov --nomigrations | tee /tmp/test-suite-collection.txt
! rg '\.examples/' /tmp/test-suite-collection.txt
```

Expected: coleta com exit code 0 e nenhuma ocorrência de `.examples`.

- [ ] **Step 2: Executar verificações completas**

Run:

```bash
uv run ruff check .
make test
uv run python manage.py makemigrations --check --dry-run
make docs
```

Expected: todos os comandos terminam com exit code 0.

- [ ] **Step 3: Auditar escopo dos commits e estado do workspace**

Run:

```bash
git status --short
git log --oneline -7
git diff --check
```

Expected: alterações preexistentes do usuário continuam presentes e nenhum commit da reorganização inclui hunks alheios ao plano.

- [ ] **Step 4: Commit documental, se necessário**

```bash
git add tests/README.md
git commit -m "docs: documentar infraestrutura de testes"
```

Pular este commit se `tests/README.md` já estiver correto e commitado na Task 2.
