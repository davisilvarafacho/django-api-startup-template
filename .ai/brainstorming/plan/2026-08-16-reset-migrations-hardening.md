# Reset Migrations Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar as lacunas confirmadas do reset de migrations sem executar outro reset destrutivo nem alterar a baseline já consolidada.

**Architecture:** O contrato configurável do banco de teste será provado em subprocessos isolados. O reset continuará dividido entre um management command fino e um módulo de domínio interno, agora alinhado às convenções do repositório, com preflight do grafo, descrição única das etapas e erros contextuais depois do ponto irreversível. O MCP PostgreSQL será reparado somente no ambiente local e continuará protegido pelo PostgreSQL e pelo modo read-only do DBHub.

**Tech Stack:** Python 3.12, Django 5.2, pytest/pytest-django, PostgreSQL 16, Ruff, MkDocs, Codex MCP, Node.js/npx e `@bytebase/dbhub@1.2.0`.

## Global Constraints

- Não executar `reset_migrations --apply` neste plano; a baseline atual já está aplicada e validada.
- Não alterar arquivos em `apps/**/migrations/` nem a migration manual `apps/api/core/migrations/0001_schedule_access_log_cleanup.py`.
- Não editar nem criar specs; `.ai/brainstorming/spec/2026-08-15-migration-reset-and-test-database.md` permanece como registro histórico.
- Preservar todos os arquivos não rastreados do usuário.
- O banco destrutível permitido continua sendo literalmente `base`, somente em `DJANGO_ENVIRONMENT=development`.
- `TEST_DATABASE_NAME` continua configurável; `base_test` é somente o default.
- Testes de integração usam migrations reais; não reintroduzir `--nomigrations` em Makefile, CI ou documentação normativa.
- Não adicionar dependências Python ou Node ao repositório.
- O DBHub permanece fixado em `@bytebase/dbhub@1.2.0`, via STDIO, com `readonly = true` e `max_rows = 500`.
- Nunca imprimir, copiar para o repositório ou incluir em comandos versionados `DBHUB_POSTGRES_PASSWORD`.
- Alterações de código, comentários e documentação são escritas em português; nomes de módulos Python não canônicos ficam em inglês e no plural.

---

## File Map

| Arquivo/estado | Responsabilidade após o plano |
|---|---|
| `api/tests/test_database_settings.py` | Provar separadamente o default `base_test` e o override `TEST_DATABASE_NAME`. |
| `apps/api/core/migration_resets.py` | Planejar, validar e executar resets; substitui o módulo singular `migration_reset.py`. |
| `apps/api/core/management/commands/reset_migrations.py` | Expor CLI/dry-run e delegar toda regra ao módulo interno. |
| `apps/api/core/tests/management_commands/test_reset_migrations.py` | Cobrir preflight, etapas exibidas, falhas irreversíveis e salvaguardas. |
| `CLAUDE.md` | Atuar apenas como router para o contrato vivo de testes. |
| `docs/how-to/fluxo-de-desenvolvimento.md` | Documentar migrations reais e banco configurável na rotina de desenvolvimento. |
| `docs/how-to/resetar-migrations.md` | Documentar etapas e recuperação após falha contextualizada. |
| `docs/research/2026-08-15-complete-codebase-audit.md` | Registrar o estado final dos achados P2.28/P2.30 depois da validação. |
| `/home/rafacho/.codex/config.toml` | Manter o registro MCP e a senha mascarada; não é versionado. |
| `/home/rafacho/.codex/dbhub-postgres.toml` | Manter source/tools DBHub sem segredo, modo `0600`; não é versionado. |
| Papel PostgreSQL `codex_readonly` | Permitir apenas conexão, uso do schema e leitura de tabelas, sem TEMP ou escrita. |

## Task 1: Tornar o teste do banco compatível com `TEST_DATABASE_NAME`

**Files:**
- Modify: `api/tests/test_database_settings.py:1-46`

**Interfaces:**
- Consumes: `api.settings.DATABASES["default"]["TEST"]["NAME"]` e a variável opcional `TEST_DATABASE_NAME`.
- Produces: helper privado `_read_test_database_name(override: str | None) -> str` e dois testes independentes do ambiente do processo pai.

- [ ] **Step 1: Substituir o teste literal por duas reproduções em subprocesso**

Manter os imports atuais e adicionar o helper abaixo. Ele remove qualquer override herdado antes de testar o default e define explicitamente o override no segundo caso:

```python
def _read_test_database_name(override: str | None) -> str:
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    environment.pop("TEST_DATABASE_NAME", None)
    if override is not None:
        environment["TEST_DATABASE_NAME"] = override

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print(settings.DATABASES['default']['TEST']['NAME'])",
        ],
        check=True,
        capture_output=True,
        cwd=settings.BASE_DIR,
        env=environment,
        text=True,
    )
    return result.stdout.strip()


def test_default_test_database_name_is_isolated():
    assert _read_test_database_name(None) == "base_test"


def test_test_database_name_honors_environment_override():
    assert _read_test_database_name("test_parallel_worker_1") == "test_parallel_worker_1"
```

Remover o teste antigo que compara as settings já carregadas diretamente a `base_test`. Não alterar os outros três testes do arquivo.

- [ ] **Step 2: Executar com um override externo para provar que a regressão fechou**

Run:

```bash
TEST_DATABASE_NAME=test_verify_override \
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run --group test pytest api/tests/test_database_settings.py -q --no-cov
```

Expected: `5 passed`; o processo pai pode usar qualquer nome sem quebrar o teste do default.

- [ ] **Step 3: Executar o contrato relacionado do permission cache**

Run:

```bash
TEST_DATABASE_NAME=test_verify_override \
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run --group test pytest \
  api/tests/test_database_settings.py \
  internal_frameworks/permission_cache/tests/test_config.py \
  -q --no-cov
```

Expected: todos os testes aprovados; ambos os pacotes reconhecem `test_verify_override`.

- [ ] **Step 4: Commit**

```bash
git add api/tests/test_database_settings.py
git commit -m "test: respeitar nome configuravel do banco de testes"
```

## Task 2: Alinhar o módulo de reset às convenções do repositório

**Files:**
- Rename: `apps/api/core/migration_reset.py` → `apps/api/core/migration_resets.py`
- Modify: `apps/api/core/management/commands/reset_migrations.py:1-10`
- Modify: `apps/api/core/tests/management_commands/test_reset_migrations.py:14-309`

**Interfaces:**
- Consumes: as assinaturas públicas atuais `build_migration_reset_plan()` e `apply_migration_reset(plan, *, confirmed_database)`.
- Produces: as mesmas assinaturas em `apps.api.core.migration_resets`, sem shim/reexport no caminho singular.

- [ ] **Step 1: Registrar a baseline dos testes antes do rename**

Run:

```bash
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -q --no-cov
```

Expected: `20 passed` antes da alteração.

- [ ] **Step 2: Renomear o módulo e atualizar todos os imports**

Run:

```bash
git mv apps/api/core/migration_reset.py apps/api/core/migration_resets.py
```

No management command, usar:

```python
from apps.api.core.migration_resets import (
    MigrationResetError,
    apply_migration_reset,
    build_migration_reset_plan,
)
```

Nos testes, substituir o alias singular por:

```python
from apps.api.core import migration_resets
```

Atualizar todas as referências `migration_reset.` para `migration_resets.`. Os patches de mock do management command continuam apontando para `apps.api.core.management.commands.reset_migrations`.

- [ ] **Step 3: Traduzir docstrings e centralizar o banco destrutível**

No início de `apps/api/core/migration_resets.py`, usar:

```python
"""Planeja e executa o reset destrutivo das migrations dos apps próprios."""
```

Na exceção:

```python
class MigrationResetError(Exception):
    """Indica que o reset não pode continuar com segurança."""
```

Adicionar junto às constantes:

```python
RESETTABLE_DATABASE_NAME = "base"
```

Substituir as três comparações literais relevantes em `apply_migration_reset()` pela constante:

```python
if plan.database_name != RESETTABLE_DATABASE_NAME:
    raise MigrationResetError(f"reset_migrations só pode operar no banco '{RESETTABLE_DATABASE_NAME}'.")
if confirmed_database != RESETTABLE_DATABASE_NAME:
    raise MigrationResetError(
        f"confirmação inválida; informe exatamente '{RESETTABLE_DATABASE_NAME}'."
    )
if connection_settings.get("NAME") != RESETTABLE_DATABASE_NAME or not str(
    connection_settings.get("ENGINE", "")
).endswith(".postgresql"):
    raise MigrationResetError(
        f"a conexão efetiva deve ser PostgreSQL no banco '{RESETTABLE_DATABASE_NAME}'."
    )
```

No management command, usar a docstring:

```python
"""Expõe o reset protegido de migrations próprias como comando Django."""
```

- [ ] **Step 4: Confirmar que o caminho singular desapareceu e os testes continuam verdes**

Run:

```bash
rg "apps\.api\.core\.migration_reset|from apps\.api\.core import migration_reset" apps api tests
uv run ruff check \
  apps/api/core/migration_resets.py \
  apps/api/core/management/commands/reset_migrations.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -q --no-cov
```

Expected: `rg` não encontra o caminho singular, Ruff passa e os 20 testes continuam aprovados.

- [ ] **Step 5: Commit**

```bash
git add \
  apps/api/core/migration_resets.py \
  apps/api/core/management/commands/reset_migrations.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py
git commit -m "refactor: alinhar modulo de reset de migrations"
```

## Task 3: Adicionar preflight explícito de conflitos no grafo próprio

**Files:**
- Modify: `apps/api/core/migration_resets.py`
- Modify: `apps/api/core/tests/management_commands/test_reset_migrations.py`

**Interfaces:**
- Consumes: `MigrationLoader.detect_conflicts()` e os labels resolvidos de `BUSINESS_APPS`.
- Produces: `_ensure_no_first_party_migration_conflicts(app_labels: tuple[str, ...] | list[str]) -> None`.

- [ ] **Step 1: Escrever os testes que distinguem conflito próprio de conflito externo**

Adicionar aos testes:

```python
def test_plano_recusa_conflito_em_migration_de_app_proprio(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    loader = MagicMock()
    loader.detect_conflicts.return_value = {
        "autenticacao": ["0002_branch_a", "0002_branch_b"],
    }
    monkeypatch.setattr(migration_resets, "MigrationLoader", lambda *_args, **_kwargs: loader)

    with pytest.raises(migration_resets.MigrationResetError, match="autenticacao"):
        migration_resets.build_migration_reset_plan()


def test_plano_ignora_conflito_de_app_externo(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    loader = MagicMock()
    loader.detect_conflicts.return_value = {
        "third_party": ["0002_branch_a", "0002_branch_b"],
    }
    monkeypatch.setattr(migration_resets, "MigrationLoader", lambda *_args, **_kwargs: loader)

    plan = migration_resets.build_migration_reset_plan()

    assert plan.app_labels == ("autenticacao",)
```

- [ ] **Step 2: Executar os dois testes para comprovar o RED**

Run:

```bash
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -k "conflito" -q --no-cov
```

Expected: FAIL porque `MigrationLoader` e a validação ainda não existem no módulo.

- [ ] **Step 3: Implementar o preflight antes de retornar o plano**

Adicionar o import:

```python
from django.db.migrations.loader import MigrationLoader
```

Adicionar a função:

```python
def _ensure_no_first_party_migration_conflicts(app_labels) -> None:
    conflicts = MigrationLoader(None, ignore_no_migrations=True).detect_conflicts()
    first_party_conflicts = {
        label: tuple(names)
        for label, names in conflicts.items()
        if label in app_labels
    }
    if not first_party_conflicts:
        return

    details = "; ".join(
        f"{label}: {', '.join(names)}"
        for label, names in sorted(first_party_conflicts.items())
    )
    raise MigrationResetError(f"conflitos no grafo de migrations próprias: {details}")
```

Em `build_migration_reset_plan()`, imediatamente depois de concluir o loop e antes do `return`, chamar:

```python
_ensure_no_first_party_migration_conflicts(labels)
```

Essa checagem cobre conflitos presentes no grafo antes da primeira mutação. Falhas na geração da nova baseline continuam protegidas pelo snapshot/restauração já existente.

- [ ] **Step 4: Executar testes focados e suíte do comando**

Run:

```bash
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -q --no-cov
```

Expected: 22 testes aprovados.

- [ ] **Step 5: Commit**

```bash
git add \
  apps/api/core/migration_resets.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py
git commit -m "fix: validar conflitos antes do reset de migrations"
```

## Task 4: Exibir todas as etapas e contextualizar falhas irreversíveis

**Files:**
- Modify: `apps/api/core/migration_resets.py`
- Modify: `apps/api/core/management/commands/reset_migrations.py`
- Modify: `apps/api/core/tests/management_commands/test_reset_migrations.py`

**Interfaces:**
- Consumes: `_reset_public_schema()`, `_run_manage_py(*arguments)` e `MigrationResetError`.
- Produces: `MIGRATION_RESET_STEPS: tuple[str, ...]` e `_run_irreversible_step(stage, operation, *args) -> None`.

- [ ] **Step 1: Escrever teste do dry-run completo**

No teste `test_command_default_e_dry_run`, substituir a asserção única de etapa por:

```python
output = stdout.getvalue()
assert output.count("makemigrations --check --dry-run") == 2
assert "recriar schema public" in output
assert "migrate" in output
assert "showmigrations --plan" in output
```

- [ ] **Step 2: Escrever testes de erro contextual por etapa**

Adicionar exatamente:

```python
@pytest.mark.parametrize(
    ("stage", "exception"),
    [
        ("recriar schema public", DatabaseError("schema")),
        ("aplicar migrations", CalledProcessError(1, ["migrate"])),
        ("validar models e migrations", OSError("check")),
        ("exibir plano aplicado", KeyboardInterrupt()),
    ],
)
def test_etapa_irreversivel_informa_nome_ao_falhar(stage, exception):
    def fail():
        raise exception

    with pytest.raises(migration_resets.MigrationResetError, match=stage):
        migration_resets._run_irreversible_step(stage, fail)
```

- [ ] **Step 3: Executar os testes para comprovar o RED**

Run:

```bash
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -k "dry_run or irreversivel" -q --no-cov
```

Expected: FAIL porque o dry-run contém uma única ocorrência do check e `_run_irreversible_step` ainda não existe.

- [ ] **Step 4: Definir a descrição única das etapas**

Em `apps/api/core/migration_resets.py`, adicionar:

```python
MIGRATION_RESET_STEPS = (
    "gerar nova baseline com makemigrations",
    "validar baseline com makemigrations --check --dry-run",
    "recriar schema public",
    "aplicar migrations com migrate",
    "validar models e migrations com makemigrations --check --dry-run",
    "exibir plano aplicado com showmigrations --plan",
)
```

Importar a constante no management command e substituir a linha `ETAPAS ...` por:

```python
self.stdout.write("ETAPAS:")
for index, step in enumerate(MIGRATION_RESET_STEPS, start=1):
    self.stdout.write(f"{index}. {step}")
```

- [ ] **Step 5: Implementar o wrapper de etapa irreversível**

Adicionar imports:

```python
from collections.abc import Callable
from typing import Any
```

Adicionar:

```python
def _run_irreversible_step(
    stage: str,
    operation: Callable[..., None],
    *args: Any,
) -> None:
    try:
        operation(*args)
    except KeyboardInterrupt as exc:
        raise MigrationResetError(
            f"reset interrompido na etapa irreversível '{stage}'."
        ) from exc
    except (OSError, subprocess.CalledProcessError, DatabaseError) as exc:
        raise MigrationResetError(
            f"falha na etapa irreversível '{stage}'."
        ) from exc
```

Substituir o segundo `try/except` de `apply_migration_reset()` por chamadas explícitas:

```python
_run_irreversible_step("recriar schema public", _reset_public_schema)
_run_irreversible_step("aplicar migrations", _run_manage_py, "migrate")
_run_irreversible_step(
    "validar models e migrations",
    _run_manage_py,
    "makemigrations",
    "--check",
    "--dry-run",
)
_run_irreversible_step(
    "exibir plano aplicado",
    _run_manage_py,
    "showmigrations",
    "--plan",
)
```

- [ ] **Step 6: Executar a suíte do comando**

Run:

```bash
uv run --group test pytest \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  -q --no-cov
```

Expected: 26 testes aprovados — 22 anteriores e quatro casos parametrizados novos.

- [ ] **Step 7: Commit**

```bash
git add \
  apps/api/core/migration_resets.py \
  apps/api/core/management/commands/reset_migrations.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py
git commit -m "fix: identificar etapa interrompida no reset"
```

## Task 5: Remover instruções obsoletas e documentar o contrato final

**Files:**
- Modify: `CLAUDE.md:32-45`
- Modify: `docs/how-to/fluxo-de-desenvolvimento.md:46-54`
- Modify: `docs/how-to/resetar-migrations.md:8-37`

**Interfaces:**
- Consumes: comportamento comprovado nas Tasks 1–4.
- Produces: uma única orientação normativa para migrations reais, banco configurável e mensagens de recuperação.

- [ ] **Step 1: Transformar `CLAUDE.md` em router**

Substituir o bloco que exige `--nomigrations` por:

```markdown
Para executar testes, siga `docs/how-to/fluxo-de-desenvolvimento.md` e
`CONTRIBUTING.md`. A suíte normal aplica migrations reais; use os defaults do
Makefile ou declare `DATABASE_*` e `TEST_DATABASE_NAME` explicitamente quando
rodar pytest fora dele.
```

Não duplicar exemplos de comandos nesse router.

- [ ] **Step 2: Documentar override seguro no fluxo de desenvolvimento**

Depois do parágrafo que descreve `base_test`, adicionar:

```markdown
Execuções paralelas devem fornecer nomes distintos por `TEST_DATABASE_NAME`.
O valor muda somente o banco efêmero de testes; o banco de desenvolvimento
continua vindo de `DATABASE_NAME`.
```

- [ ] **Step 3: Atualizar o how-to do reset com etapas e falhas**

Na seção de inspeção, declarar que a saída lista seis etapas, incluindo os dois checks. Na seção final, substituir o texto genérico por:

```markdown
Se a geração falhar antes do reset do schema, os arquivos antigos são
restaurados. Depois que o schema for removido, o erro informa uma destas etapas:
`recriar schema public`, `aplicar migrations`, `validar models e migrations` ou
`exibir plano aplicado`. Corrija a causa indicada e continue com `make migrate`;
as migrations novas permanecem no working tree.
```

- [ ] **Step 4: Validar busca de instruções conflitantes e build**

Run:

```bash
rg -n "falha em massa|pytest --nomigrations|use.*--nomigrations" \
  CLAUDE.md CONTRIBUTING.md README.md docs/how-to docs/ROADMAP.md
uv run mkdocs build --strict
```

Expected: a busca não encontra instrução normativa que mande usar `--nomigrations`; MkDocs termina com exit 0.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/how-to/fluxo-de-desenvolvimento.md docs/how-to/resetar-migrations.md
git commit -m "docs: alinhar contrato do reset de migrations"
```

## Task 6: Restaurar o MCP DBHub e remover o privilégio TEMP

**Files:**
- Modify local state: `/home/rafacho/.npm/_npx/4d95a661cec87209`
- Preserve: `/home/rafacho/.codex/config.toml`
- Preserve: `/home/rafacho/.codex/dbhub-postgres.toml`
- Modify database privileges: database `base`, grantee `PUBLIC`

**Interfaces:**
- Consumes: MCP `postgres_best_django_api_template`, role `codex_readonly` e DBHub `1.2.0` já configurados.
- Produces: DBHub inicializável e `has_database_privilege('codex_readonly', 'base', 'TEMPORARY') = false`.

Esta task é operacional e local; não gera commit Git.

- [ ] **Step 1: Confirmar o erro e validar o diretório exato antes de mover**

Run:

```bash
test -f /home/rafacho/.npm/_npx/4d95a661cec87209/node_modules/@bytebase/dbhub/package.json
DBHUB_POSTGRES_PASSWORD=validation-only timeout 10s \
  npx -y @bytebase/dbhub@1.2.0 \
  --transport stdio \
  --config /home/rafacho/.codex/dbhub-postgres.toml
```

Expected: o primeiro comando passa; o segundo falha citando `safer-buffer/safer.js`. Se o erro for outro ou o diretório não existir, parar e rediagnosticar em vez de mover outro cache.

- [ ] **Step 2: Mover somente o cache corrompido para backup recuperável**

Run:

```bash
test ! -e /home/rafacho/.npm/_npx/4d95a661cec87209.corrupt-20260816
mv \
  /home/rafacho/.npm/_npx/4d95a661cec87209 \
  /home/rafacho/.npm/_npx/4d95a661cec87209.corrupt-20260816
```

Expected: o caminho original deixa de existir e o backup `.corrupt-20260816` permanece disponível para inspeção/recuperação.

- [ ] **Step 3: Forçar reinstalação da versão fixada sem usar a senha real**

Run:

```bash
DBHUB_POSTGRES_PASSWORD=validation-only timeout 10s \
  npx -y @bytebase/dbhub@1.2.0 \
  --transport stdio \
  --config /home/rafacho/.codex/dbhub-postgres.toml
```

Expected: não aparece `MODULE_NOT_FOUND`; a execução espera STDIO até timeout 124 ou falha apenas por autenticação com a senha deliberadamente inválida.

- [ ] **Step 4: Remover TEMP herdado de PUBLIC no banco local**

Como o PostgreSQL ativo pode estar fora do container deste projeto, usar a conexão configurada em vez de `docker compose exec`. Run:

```bash
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
uv run python - <<'PY'
import os

import psycopg2

connection = psycopg2.connect(
    dbname="postgres",
    user=os.environ["DATABASE_USER"],
    password=os.environ["DATABASE_PASSWORD"],
    host=os.environ["DATABASE_HOST"],
    port=os.environ["DATABASE_PORT"],
)
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute('REVOKE TEMPORARY ON DATABASE "base" FROM PUBLIC')
    cursor.execute(
        "SELECT has_database_privilege(%s, %s, %s)",
        ("codex_readonly", "base", "TEMPORARY"),
    )
    print(cursor.fetchone()[0])
connection.close()
PY
```

Expected: o script termina com exit 0 e imprime `False`. O owner/superusuário local `postgres` continua capaz de operar o banco.

- [ ] **Step 5: Provar leitura permitida e escrita permanente/temporária recusada**

Executar em um único processo Python para nunca devolver a senha ao shell:

```bash
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 uv run python - <<'PY'
import os
import tomllib
from pathlib import Path

import psycopg2
from psycopg2.errors import InsufficientPrivilege

config = tomllib.loads(
    Path.home().joinpath(".codex/config.toml").read_text(encoding="utf-8")
)
password = (
    config["mcp_servers"]["postgres_best_django_api_template"]["env"]
    ["DBHUB_POSTGRES_PASSWORD"]
)
connection = psycopg2.connect(
    dbname="base",
    user="codex_readonly",
    password=password,
    host=os.environ["DATABASE_HOST"],
    port=os.environ["DATABASE_PORT"],
)
with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM django_migrations")
    print(f"migrations={cursor.fetchone()[0]}")

    for statement in (
        "DELETE FROM django_migrations WHERE false",
        "CREATE TEMP TABLE codex_mcp_temp_probe (id integer)",
    ):
        try:
            cursor.execute(statement)
        except InsufficientPrivilege:
            connection.rollback()
        else:
            raise AssertionError(f"escrita indevidamente permitida: {statement}")
connection.close()
PY
```

Expected: a primeira linha corresponde a `^migrations=[1-9][0-9]*$` e o processo termina com exit 0; DELETE e CREATE TEMP TABLE chegam aos ramos `InsufficientPrivilege`.

- [ ] **Step 6: Validar inicialização com a credencial real e reiniciar o cliente**

Executar na mesma shell:

```bash
DBHUB_POSTGRES_PASSWORD=$(uv run python - <<'PY'
import tomllib
from pathlib import Path

config = tomllib.loads(
    Path.home().joinpath(".codex/config.toml").read_text(encoding="utf-8")
)
print(
    config["mcp_servers"]["postgres_best_django_api_template"]["env"]
    ["DBHUB_POSTGRES_PASSWORD"]
)
PY
)
export DBHUB_POSTGRES_PASSWORD
timeout 10s npx -y @bytebase/dbhub@1.2.0 \
  --transport stdio \
  --config /home/rafacho/.codex/dbhub-postgres.toml
status=$?
unset DBHUB_POSTGRES_PASSWORD
test "$status" -eq 0 || test "$status" -eq 124
```

Expected: DBHub inicia sem `MODULE_NOT_FOUND`; ele encerra limpo ao receber EOF ou permanece aguardando STDIO até o timeout. A shell termina com exit 0. Depois, fechar e reabrir o Codex para que a sessão carregue a tool MCP reparada.

## Task 7: Executar gates finais e atualizar o relatório local de auditoria

**Files:**
- Modify local/untracked: `docs/research/2026-08-15-complete-codebase-audit.md`

**Interfaces:**
- Consumes: entregáveis e evidências das Tasks 1–6.
- Produces: validação final reproduzível e status local atualizado dos achados P2.28/P2.30; nenhum commit adicional.

- [ ] **Step 1: Executar checks estáticos e de migrations**

Run:

```bash
uv run ruff check .
uv run ruff format --check \
  apps/api/core/migration_resets.py \
  apps/api/core/management/commands/reset_migrations.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  api/tests/test_database_settings.py
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run python manage.py makemigrations --check --dry-run
uv run mkdocs build --strict
git diff --check
```

Expected: todos os comandos terminam com exit 0; `makemigrations` imprime `No changes detected`.

- [ ] **Step 2: Executar dry-run real e conferir a lista completa**

Run:

```bash
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=development DJANGO_SECRET_KEY=test-secret \
uv run python manage.py reset_migrations
```

Expected: saída contém `DRY-RUN`, lista os apps/arquivos e exibe seis etapas numeradas, com duas ocorrências de `makemigrations --check --dry-run`. Nenhum arquivo ou schema é alterado.

- [ ] **Step 3: Executar suíte completa com banco exclusivo e migrations reais**

Run:

```bash
TEST_DATABASE_NAME=test_reset_hardening_20260816 \
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
uv run --group test pytest --no-cov -q
```

Expected: todos os testes passam usando o nome exclusivo; o banco é criado, migrado e removido no teardown sem warning de conexão ativa.

- [ ] **Step 4: Atualizar o relatório validado sem reescrever o histórico**

Em `docs/research/2026-08-15-complete-codebase-audit.md`, adicionar uma seção `## Acompanhamento — 16 de agosto de 2026` contendo:

```markdown
### Reset de migrations

- P2.28: resolvido — baseline consolidada, CI e suíte local executam migrations reais.
- P2.30: resolvido — `base_test` permanece o default, overrides por
  `TEST_DATABASE_NAME` são cobertos e a suíte completa passa com nome exclusivo.
- Hardening adicional: preflight de conflitos, etapas irreversíveis identificadas,
  documentação normativa alinhada e MCP PostgreSQL read-only novamente operacional.
```

Incluir, logo abaixo, os comandos finais e suas contagens reais. Não alterar a descrição histórica original dos achados e não adicionar esse relatório interno ao Git.

- [ ] **Step 5: Revisar o range completo e preservar o relatório local**

Run:

```bash
git log --oneline --decorate HEAD~5..HEAD
git diff HEAD~5...HEAD --check
git status --short
```

Expected: cinco commits versionados deste plano, nenhum erro de whitespace e somente o próprio plano, o relatório local e arquivos não rastreados do usuário fora do escopo.

## Completion Criteria

- A suíte passa com `TEST_DATABASE_NAME` arbitrário e migrations reais.
- O caminho singular `apps.api.core.migration_reset` não existe mais.
- Conflitos no grafo de migrations próprias bloqueiam o reset antes de qualquer mutação.
- O dry-run lista as seis etapas, incluindo os dois checks.
- Toda falha depois do ponto irreversível identifica a etapa exata.
- Nenhuma instrução viva manda usar `--nomigrations` na rotina normal.
- O DBHub `1.2.0` inicia via STDIO sem erro de módulo.
- `codex_readonly` lê tabelas, mas não possui DML, CREATE, sequences ou TEMP.
- Ruff, format check dos arquivos tocados, MkDocs, migration check, suíte completa e diff check passam.
- A baseline e a migration manual permanecem inalteradas.
