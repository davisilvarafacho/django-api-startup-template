# Migration Reset and Test Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidar os models atuais em uma baseline nova, oferecer um reset destrutivo protegido e voltar a testar o grafo real de migrations em `base_test`.

**Architecture:** `apps.api.core.migration_reset` será um módulo profundo com duas entradas: construir o plano e aplicá-lo. O management command adapta argumentos e saída; o Makefile apenas encaminha a chamada. A baseline é gerada antes de recriar `public`, com restauração dos arquivos antigos se a geração falhar.

**Tech Stack:** Python 3.12, Django 5.2, PostgreSQL 16, psycopg2, pytest 9, pytest-django, GNU Make, GitHub Actions, MkDocs.

## Global Constraints

- O banco autorizado para a execução integrada é exatamente `base`; seus dados são descartáveis.
- O comando é dry-run por padrão e exige simultaneamente `--apply` e `--confirm-database base` para alterar arquivos ou banco.
- `DJANGO_ENVIRONMENT=production` e backends que não terminem em `.postgresql` são bloqueados.
- Somente apps de `settings.BUSINESS_APPS` sob `BASE_DIR/apps` entram no reset.
- Cada `migrations/__init__.py` e `apps/api/core/migrations/0001_schedule_access_log_cleanup.py` são preservados.
- Migrations de dependências externas nunca são removidas ou reescritas.
- O schema `public` é recriado; o banco, seu owner e os demais aliases de `DATABASES` permanecem.
- O banco de testes padrão é `base_test`, sem `--reuse-db` e sem `--nomigrations`.
- Não corrigir a regressão preexistente em `apps/api/core/deprecation.py` dentro destes commits.
- Preservar `TODO.md` e quaisquer alterações do usuário fora dos arquivos listados.

---

## File map

| Arquivo | Responsabilidade |
|---|---|
| `apps/api/core/migration_reset.py` | Descobrir arquivos, representar o plano, restaurar o snapshot, executar subprocessos Django e recriar `public`. |
| `apps/api/core/management/commands/reset_migrations.py` | Converter CLI em `build_migration_reset_plan()`/`apply_migration_reset()` e renderizar a saída. |
| `apps/api/core/tests/management_commands/test_reset_migrations.py` | Cobrir planejamento, proteções, rollback de arquivos, ordem e adapter CLI sem banco destrutivo. |
| `apps/*/migrations/*.py` | Nova baseline gerada; somente a migration manual do `core` atravessa o reset. |
| `api/settings.py`, `.env.example`, `Makefile` | Nome `base_test`, alvo de reset e testes com migrations reais. |
| `.github/workflows/ci.yml`, `.github/PULL_REQUEST_TEMPLATE.md` | Restaurar o gate e remover a política temporária. |
| `docs/how-to/resetar-migrations.md`, `mkdocs.yml` | Runbook versionado do comando destrutivo. |
| `docs/how-to/fluxo-de-desenvolvimento.md`, `docs/ROADMAP.md`, `docs/relatorio-base-gente-grande.md` | Registrar que o congelamento terminou. |

### Task 1: Planejar o reset sem efeitos colaterais

**Files:**
- Create: `apps/api/core/migration_reset.py`
- Create: `apps/api/core/tests/management_commands/test_reset_migrations.py`

**Interfaces:**
- Consumes: `settings.BASE_DIR`, `settings.BUSINESS_APPS`, `settings.DATABASES["default"]`, registry global `django.apps.apps`.
- Produces: `MigrationResetError`, `MigrationResetPlan` e `build_migration_reset_plan() -> MigrationResetPlan`.

- [ ] **Step 1: Escrever os testes vermelhos do planejamento**

Criar o arquivo de teste com helpers que montem apps falsos sob `tmp_path` e cubram descoberta, preservação e recusa de caminhos/backend:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.api.core import migration_reset


def criar_app(root: Path, dotted_path: str, *migrations: str):
    app_path = root.joinpath(*dotted_path.split("."))
    migration_dir = app_path / "migrations"
    migration_dir.mkdir(parents=True)
    (migration_dir / "__init__.py").write_text("", encoding="utf-8")
    for name in migrations:
        (migration_dir / name).write_text("# migration\n", encoding="utf-8")
    return SimpleNamespace(name=dotted_path, label=dotted_path.rsplit(".", 1)[-1], path=str(app_path))


def configurar_plano(settings, monkeypatch, tmp_path, configs):
    settings.BASE_DIR = tmp_path
    settings.BUSINESS_APPS = [config.name for config in configs]
    settings.DATABASES["default"] = {
        "ENGINE": "django_rls.backends.postgresql",
        "NAME": "base",
    }
    monkeypatch.setattr(migration_reset, "_installed_app_configs", lambda: configs)


def test_plano_remove_so_migrations_dos_business_apps(settings, monkeypatch, tmp_path):
    autenticacao = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py", "0002_legacy.py")
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [autenticacao, core])

    plan = migration_reset.build_migration_reset_plan()

    assert plan.database_name == "base"
    assert plan.app_labels == ("autenticacao", "core")
    assert [path.name for path in plan.remove] == ["0001_initial.py", "0002_legacy.py"]
    assert [path.name for path in plan.preserve] == ["0001_schedule_access_log_cleanup.py"]
    assert all(path.name != "__init__.py" for path in (*plan.remove, *plan.preserve))


def test_plano_recusa_app_fora_de_apps(settings, monkeypatch, tmp_path):
    externo = criar_app(tmp_path, "vendor.externo", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [externo])

    with pytest.raises(migration_reset.MigrationResetError, match="fora de apps"):
        migration_reset.build_migration_reset_plan()


def test_plano_recusa_backend_nao_postgresql(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.DATABASES["default"]["ENGINE"] = "django.db.backends.sqlite3"

    with pytest.raises(migration_reset.MigrationResetError, match="PostgreSQL"):
        migration_reset.build_migration_reset_plan()


def test_plano_recusa_migration_manual_ausente(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core")
    configurar_plano(settings, monkeypatch, tmp_path, [core])

    with pytest.raises(migration_reset.MigrationResetError, match="schedule_access_log_cleanup"):
        migration_reset.build_migration_reset_plan()


def test_plano_recusa_diretorio_de_migrations_simbolico(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    migration_dir = Path(auth.path) / "migrations"
    outside = tmp_path / "outside-migrations"
    migration_dir.rename(outside)
    migration_dir.symlink_to(outside, target_is_directory=True)
    configurar_plano(settings, monkeypatch, tmp_path, [auth])

    with pytest.raises(migration_reset.MigrationResetError, match="fora do app"):
        migration_reset.build_migration_reset_plan()
```

- [ ] **Step 2: Executar os testes e confirmar o RED**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
```

Expected: collection error `ImportError: cannot import name 'migration_reset' from 'apps.api.core'`.

- [ ] **Step 3: Implementar a representação e descoberta mínimas**

Criar `apps/api/core/migration_reset.py` com esta interface e validação:

```python
"""Plan and execute a destructive first-party migration reset."""

import re
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings

MIGRATION_FILENAME = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")
PRESERVED_MIGRATIONS = {
    "core": frozenset({"0001_schedule_access_log_cleanup.py"}),
}


class MigrationResetError(Exception):
    """Raised before or during a migration reset that cannot continue safely."""


@dataclass(frozen=True)
class MigrationResetPlan:
    base_dir: Path
    database_name: str
    app_labels: tuple[str, ...]
    migration_directories: tuple[Path, ...]
    remove: tuple[Path, ...]
    preserve: tuple[Path, ...]


def _installed_app_configs():
    by_name = {config.name: config for config in django_apps.get_app_configs()}
    try:
        return [by_name[name] for name in settings.BUSINESS_APPS]
    except KeyError as exc:
        raise MigrationResetError(f"BUSINESS_APPS contém app não instalado: {exc.args[0]}") from exc


def build_migration_reset_plan() -> MigrationResetPlan:
    base_dir = Path(settings.BASE_DIR).resolve()
    apps_root = base_dir / "apps"
    database = settings.DATABASES["default"]
    engine = database["ENGINE"]
    if engine.rsplit(".", 1)[-1] != "postgresql":
        raise MigrationResetError("reset_migrations exige um backend PostgreSQL.")

    database_name = str(database.get("NAME") or "")
    if not database_name:
        raise MigrationResetError("DATABASES['default']['NAME'] não pode ser vazio.")

    labels = []
    directories = []
    remove = []
    preserve = []
    for config in _installed_app_configs():
        app_path = Path(config.path).resolve()
        if not app_path.is_relative_to(apps_root):
            raise MigrationResetError(f"app '{config.name}' fica fora de apps/: {app_path}")

        migration_dir = (app_path / "migrations").resolve()
        if not migration_dir.is_relative_to(app_path):
            raise MigrationResetError(f"migrations de {config.label} fica fora do app: {migration_dir}")
        if not (migration_dir / "__init__.py").is_file():
            raise MigrationResetError(f"{config.label} não possui migrations/__init__.py.")

        labels.append(config.label)
        directories.append(migration_dir)
        preserved_names = PRESERVED_MIGRATIONS.get(config.label, frozenset())
        for name in preserved_names:
            path = migration_dir / name
            if not path.is_file():
                raise MigrationResetError(f"migration preservada ausente: {path}")
            preserve.append(path)

        for path in sorted(migration_dir.glob("*.py")):
            if path.name == "__init__.py" or path.name in preserved_names:
                continue
            if not MIGRATION_FILENAME.fullmatch(path.name):
                raise MigrationResetError(f"arquivo inesperado em migrations/: {path}")
            remove.append(path)

    return MigrationResetPlan(
        base_dir=base_dir,
        database_name=database_name,
        app_labels=tuple(labels),
        migration_directories=tuple(directories),
        remove=tuple(remove),
        preserve=tuple(sorted(preserve)),
    )
```

- [ ] **Step 4: Executar testes e Ruff**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py
```

Expected: 5 tests pass; Ruff exits 0.

### Task 2: Aplicar a baseline com rollback de arquivos

**Files:**
- Modify: `apps/api/core/migration_reset.py`
- Modify: `apps/api/core/tests/management_commands/test_reset_migrations.py`

**Interfaces:**
- Consumes: `MigrationResetPlan` from Task 1.
- Produces: `apply_migration_reset(plan: MigrationResetPlan, *, confirmed_database: str | None) -> None`.

- [ ] **Step 1: Adicionar testes vermelhos de proteção e ordem**

Acrescentar testes que patcham somente os seams internos, nunca uma conexão real:

```python
from subprocess import CalledProcessError


def test_apply_bloqueia_producao_antes_de_mutar(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = True
    plan = migration_reset.build_migration_reset_plan()
    monkeypatch.setattr(migration_reset, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))

    with pytest.raises(migration_reset.MigrationResetError, match="produção"):
        migration_reset.apply_migration_reset(plan, confirmed_database="base")


def test_apply_exige_nome_exato_do_banco(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = False
    plan = migration_reset.build_migration_reset_plan()

    with pytest.raises(migration_reset.MigrationResetError, match="confirmação"):
        migration_reset.apply_migration_reset(plan, confirmed_database="outro")


def test_apply_restaura_migrations_quando_makemigrations_falha(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth, core])
    settings.IN_PRODUCTION = False
    plan = migration_reset.build_migration_reset_plan()

    def falhar(*args):
        (Path(auth.path) / "migrations" / "0001_generated.py").write_text("# generated\n", encoding="utf-8")
        raise CalledProcessError(1, args)

    monkeypatch.setattr(migration_reset, "_run_manage_py", falhar)
    monkeypatch.setattr(migration_reset, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_reset.MigrationResetError, match="makemigrations"):
        migration_reset.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"
    assert not (Path(auth.path) / "migrations" / "0001_generated.py").exists()


def test_apply_reseta_schema_depois_de_validar_migrations(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = False
    plan = migration_reset.build_migration_reset_plan()
    events = []
    monkeypatch.setattr(migration_reset, "_run_manage_py", lambda *args: events.append(args))
    monkeypatch.setattr(migration_reset, "_reset_public_schema", lambda: events.append(("reset-schema",)))

    migration_reset.apply_migration_reset(plan, confirmed_database="base")

    assert events == [
        ("makemigrations", "core"),
        ("makemigrations", "--check", "--dry-run"),
        ("reset-schema",),
        ("migrate",),
        ("makemigrations", "--check", "--dry-run"),
        ("showmigrations", "--plan"),
    ]
```

- [ ] **Step 2: Confirmar o RED**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
```

Expected: failures report that `apply_migration_reset`, `_run_manage_py` and `_reset_public_schema` do not exist.

- [ ] **Step 3: Implementar execução, snapshot e schema reset**

Adicionar os imports e funções abaixo em `migration_reset.py`:

```python
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.db import connections
from django.db.utils import DatabaseError


def _run_manage_py(*arguments: str) -> None:
    subprocess.run(
        [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), *arguments],
        cwd=settings.BASE_DIR,
        check=True,
    )


def _current_resettable_files(plan: MigrationResetPlan):
    preserved = set(plan.preserve)
    for migration_dir in plan.migration_directories:
        for path in migration_dir.glob("*.py"):
            if path.name != "__init__.py" and path not in preserved and MIGRATION_FILENAME.fullmatch(path.name):
                yield path


def _restore_snapshot(plan: MigrationResetPlan, backup_root: Path) -> None:
    for path in _current_resettable_files(plan):
        path.unlink()
    for original in plan.remove:
        backup = backup_root / original.relative_to(plan.base_dir)
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)


def _reset_public_schema() -> None:
    connection = connections["default"]
    connection.close()
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public AUTHORIZATION CURRENT_USER")
        cursor.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    connection.close()


def apply_migration_reset(plan: MigrationResetPlan, *, confirmed_database: str | None) -> None:
    if settings.IN_PRODUCTION:
        raise MigrationResetError("reset_migrations é bloqueado em produção.")
    if confirmed_database != plan.database_name:
        raise MigrationResetError(f"confirmação inválida; informe exatamente '{plan.database_name}'.")

    with TemporaryDirectory(prefix="migration-reset-") as temporary:
        backup_root = Path(temporary)
        for original in plan.remove:
            backup = backup_root / original.relative_to(plan.base_dir)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, backup)

        try:
            for original in plan.remove:
                original.unlink()
            _run_manage_py("makemigrations", *plan.app_labels)
            _run_manage_py("makemigrations", "--check", "--dry-run")
        except (OSError, subprocess.CalledProcessError) as exc:
            _restore_snapshot(plan, backup_root)
            raise MigrationResetError("makemigrations falhou; os arquivos originais foram restaurados.") from exc

        try:
            _reset_public_schema()
            _run_manage_py("migrate")
            _run_manage_py("makemigrations", "--check", "--dry-run")
            _run_manage_py("showmigrations", "--plan")
        except (OSError, subprocess.CalledProcessError, DatabaseError) as exc:
            raise MigrationResetError("a baseline foi gerada, mas a reconstrução do banco falhou.") from exc
```

- [ ] **Step 4: Executar a suíte focada e Ruff**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py
```

Expected: 9 tests pass; Ruff exits 0.

### Task 3: Expor CLI e alvo Make sem duplicar lógica

**Files:**
- Create: `apps/api/core/management/commands/reset_migrations.py`
- Modify: `apps/api/core/tests/management_commands/test_reset_migrations.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `build_migration_reset_plan()` and `apply_migration_reset()`.
- Produces: `python manage.py reset_migrations [--apply --confirm-database NAME]` and `make reset-migrations RESET_MIGRATIONS_ARGS="..."`.

- [ ] **Step 1: Escrever testes vermelhos do adapter CLI**

Adicionar imports e testes:

```python
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError


def test_command_default_e_dry_run(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    stdout = StringIO()

    with patch("apps.api.core.management.commands.reset_migrations.apply_migration_reset") as apply:
        call_command("reset_migrations", stdout=stdout)

    assert "DRY-RUN" in stdout.getvalue()
    assert "Ambiente:" in stdout.getvalue()
    assert "base" in stdout.getvalue()
    assert "0001_schedule_access_log_cleanup.py" in stdout.getvalue()
    assert "makemigrations --check --dry-run" in stdout.getvalue()
    apply.assert_not_called()


def test_command_apply_encaminha_confirmacao(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])

    with patch("apps.api.core.management.commands.reset_migrations.apply_migration_reset") as apply:
        call_command("reset_migrations", "--apply", "--confirm-database", "base")

    apply.assert_called_once()
    assert apply.call_args.kwargs == {"confirmed_database": "base"}


def test_command_recusa_confirmacao_sem_apply(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])

    with pytest.raises(CommandError, match="--apply"):
        call_command("reset_migrations", "--confirm-database", "base")
```

- [ ] **Step 2: Confirmar que o comando ainda não existe**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
```

Expected: the three new tests fail with `Unknown command: 'reset_migrations'`.

- [ ] **Step 3: Criar o management command fino**

Criar `reset_migrations.py`:

```python
"""Expose the guarded first-party migration reset as a Django command."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.api.core.migration_reset import (
    MigrationResetError,
    apply_migration_reset,
    build_migration_reset_plan,
)


class Command(BaseCommand):
    help = "Reconstrói as migrations dos apps próprios e o schema public com confirmação explícita."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Executa as alterações exibidas pelo dry-run.")
        parser.add_argument("--confirm-database", help="Nome exato do banco descartável.")

    def handle(self, *args, **options):
        if options["confirm_database"] and not options["apply"]:
            raise CommandError("--confirm-database só pode ser usado com --apply.")

        try:
            plan = build_migration_reset_plan()
        except MigrationResetError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Ambiente: {settings.ENVIROMENT or 'development'}")
        self.stdout.write(f"Banco: {plan.database_name}")
        self.stdout.write(f"Apps: {', '.join(plan.app_labels)}")
        for path in plan.remove:
            self.stdout.write(f"REMOVER {path.relative_to(plan.base_dir)}")
        for path in plan.preserve:
            self.stdout.write(f"PRESERVAR {path.relative_to(plan.base_dir)}")
        self.stdout.write(
            "ETAPAS makemigrations -> makemigrations --check --dry-run -> "
            "reset public -> migrate -> showmigrations --plan"
        )

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("DRY-RUN: nenhuma alteração foi aplicada."))
            return

        try:
            apply_migration_reset(plan, confirmed_database=options["confirm_database"])
        except MigrationResetError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Migrations e schema reconstruídos com sucesso."))
```

- [ ] **Step 4: Adicionar o alvo Make**

Incluir `reset-migrations` em `.PHONY` e, logo depois de `migrate`, adicionar:

```make
reset-migrations: ## Planeja o reset; use RESET_MIGRATIONS_ARGS='--apply --confirm-database base' para executar
	uv run python manage.py reset_migrations $(RESET_MIGRATIONS_ARGS)
```

- [ ] **Step 5: Executar testes, dry-run real e lint**

Run:

```bash
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
make reset-migrations
uv run ruff check apps/api/core/migration_reset.py apps/api/core/management/commands/reset_migrations.py apps/api/core/tests/management_commands/test_reset_migrations.py
```

Expected: 12 tests pass; dry-run lista `base`, os sete business apps, arquivos `REMOVER` e a migration `PRESERVAR`; Ruff exits 0.

- [ ] **Step 6: Commitar o comando protegido**

```bash
git add Makefile apps/api/core/migration_reset.py apps/api/core/management/commands/reset_migrations.py apps/api/core/tests/management_commands/test_reset_migrations.py
git commit -m "feat: adicionar reset seguro de migrations"
```

Expected: commit contém apenas o módulo, o adapter, seus testes e o alvo Make.

### Task 4: Executar o reset autorizado e versionar a baseline

**Files:**
- Replace: `apps/api/autenticacao/migrations/0001_initial.py`
- Delete: `apps/api/autenticacao/migrations/0002_*.py` through `0009_*.py`
- Preserve: `apps/api/core/migrations/0001_schedule_access_log_cleanup.py`
- Replace: `apps/api/metadata/migrations/0001_initial.py`
- Delete: `apps/api/metadata/migrations/0002_*.py`
- Replace: `apps/logs/migrations/0001_initial.py`
- Replace: `apps/organizacoes/migrations/0001_initial.py`
- Delete: `apps/organizacoes/migrations/0002_*.py` and `0003_*.py`
- Replace: `apps/usuarios/migrations/0001_initial.py`
- Delete: `apps/usuarios/migrations/0002_*.py` through `0006_*.py`

**Interfaces:**
- Consumes: `make reset-migrations` from Task 3 and the local Compose PostgreSQL.
- Produces: a fresh, applied migration graph for all first-party models.

- [ ] **Step 1: Reconciliar os containers sem apagar volumes**

Run:

```bash
docker compose up -d --force-recreate db redis
docker compose ps
```

Expected: `db` and `redis` are healthy; PostgreSQL publishes `0.0.0.0:5432->5432/tcp` or `127.0.0.1:5432->5432/tcp`.

- [ ] **Step 2: Confirmar alvo e working tree antes da destruição**

Run:

```bash
git status --short
docker compose exec -T db psql -U postgres -d postgres -Atc "SELECT datname FROM pg_database WHERE datname = 'base';"
make reset-migrations
```

Expected: `TODO.md` pode continuar untracked; nenhum arquivo de migration deve estar modificado antes da execução; SQL prints `base`; dry-run identifies the same database.

- [ ] **Step 3: Executar exatamente o reset autorizado**

Run:

```bash
make reset-migrations RESET_MIGRATIONS_ARGS='--apply --confirm-database base'
```

Expected: command ends with `Migrations e schema reconstruídos com sucesso.`; no prompt appears.

- [ ] **Step 4: Inspecionar o novo grafo e os efeitos manuais**

Run:

```bash
git status --short
git diff --stat
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py showmigrations --plan
docker compose exec -T db psql -U postgres -d base -Atc "SELECT count(*) FROM django_celery_beat_periodictask WHERE task = 'apps.api.core.tasks.limpar_logs_de_acesso_antigos';"
```

Expected: only first-party migration files changed; migration check prints `No changes detected`; all plan entries are `[X]`; SQL prints `1`.

- [ ] **Step 5: Revisar a baseline gerada**

Run:

```bash
rg -n "RunPython|RunSQL|SeparateDatabaseAndState" apps/*/migrations apps/api/*/migrations
rg -n "CreateModel|AddConstraint|AlterModelOptions" apps/*/migrations/0001*.py apps/api/*/migrations/0001*.py
git diff --check
```

Expected: only `core/0001_schedule_access_log_cleanup.py` contains `RunPython`; no legacy Knox reconciliation or timestamp copy remains; initial migrations contain the current models, constraints and custom permissions.

- [ ] **Step 6: Commitar a baseline separadamente**

```bash
git add apps/api/autenticacao/migrations apps/api/base/migrations apps/api/core/migrations apps/api/metadata/migrations apps/logs/migrations apps/organizacoes/migrations apps/usuarios/migrations
git commit -m "chore: consolidar baseline de migrations"
```

Expected: commit contains only migration files; `core/0001_schedule_access_log_cleanup.py` is unchanged.

### Task 5: Ativar migrations reais no banco efêmero de testes

**Files:**
- Create: `api/tests/test_database_settings.py`
- Modify: `api/settings.py:218-226`
- Modify: `.env.example:59-65`
- Modify: `Makefile:82-101`
- Modify: `.github/workflows/ci.yml:26-79`
- Modify: `.github/PULL_REQUEST_TEMPLATE.md:10-18`

**Interfaces:**
- Consumes: the baseline from Task 4.
- Produces: `TEST_DATABASE_NAME=base_test`, pytest with real migrations, and CI migration drift gate.

- [ ] **Step 1: Escrever o teste vermelho do nome padrão**

Criar `api/tests/test_database_settings.py`:

```python
from django.conf import settings


def test_default_test_database_name_is_isolated():
    assert settings.DATABASES["default"]["TEST"]["NAME"] == "base_test"
```

- [ ] **Step 2: Confirmar o RED**

Run:

```bash
uv run --group test pytest --nomigrations api/tests/test_database_settings.py -q --no-cov
```

Expected: FAIL showing `test_base_permission_cache != base_test`.

- [ ] **Step 3: Atualizar settings e configuração local**

Em `api/settings.py`, substituir o default:

```python
"TEST": {
    "NAME": get_env_var("TEST_DATABASE_NAME", "base_test"),
},
```

Em `.env.example`, declarar sem valor real:

```dotenv
DATABASE_PORT=5432
TEST_DATABASE_NAME=base_test
```

No Makefile, declarar e exportar `TEST_DATABASE_NAME`, remover cada `--nomigrations` e atualizar as descrições:

```make
TEST_DATABASE_NAME ?= base_test

export DATABASE_NAME DATABASE_USER DATABASE_PASSWORD DATABASE_HOST DATABASE_PORT TEST_DATABASE_NAME

test: ## Roda a suíte com cobertura e migrations reais
	uv run --group test pytest

test-fast: ## Roda a suíte sem testes de integração
	uv run --group test pytest -m "not integration"

test-integration: ## Roda somente testes de integração
	uv run --group test pytest -m integration

test-redis: ## Roda testes que exigem Redis real
	uv run --group test pytest -m redis
```

- [ ] **Step 4: Restaurar CI e checklist de PR**

No job `lint`, depois de `Versões do pacote e OpenAPI`, adicionar:

```yaml
      - name: Migrations sincronizadas
        run: uv run python manage.py makemigrations --check --dry-run
```

No ambiente do job `test`, adicionar `TEST_DATABASE_NAME: base_test`; substituir a receita de teste por `uv run pytest`. No template de PR, substituir a política temporária por:

```markdown
- [ ] `makemigrations --check --dry-run` sem mudanças pendentes
```

- [ ] **Step 5: Validar o banco efêmero com migrations reais**

Run:

```bash
uv run --group test pytest api/tests/test_database_settings.py apps/api/core/tests/management_commands/test_seed_demo.py -q --no-cov
docker compose exec -T db psql -U postgres -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname = 'base_test';"
make -pn TEST_DATABASE_NAME=custom_test | rg '^TEST_DATABASE_NAME = custom_test$'
```

Expected: tests pass after Django creates/migrates/destroys `base_test`; SQL prints `0`; Make prints the explicit override.

- [ ] **Step 6: Commitar configuração e gate**

```bash
git add api/tests/test_database_settings.py api/settings.py .env.example Makefile .github/workflows/ci.yml .github/PULL_REQUEST_TEMPLATE.md
git commit -m "test: executar suíte com migrations reais"
```

Expected: commit is limited to database test configuration and CI/Make adapters.

### Task 6: Publicar o runbook e encerrar a política temporária

**Files:**
- Create: `docs/how-to/resetar-migrations.md`
- Modify: `mkdocs.yml:23-35`
- Modify: `docs/how-to/fluxo-de-desenvolvimento.md:43-56`
- Modify: `docs/ROADMAP.md:65-70,137-139`
- Modify: `docs/relatorio-base-gente-grande.md:103-109,155-164`

**Interfaces:**
- Consumes: final CLI and test workflow.
- Produces: a discoverable destructive-operation runbook and current project status.

- [ ] **Step 1: Escrever o runbook completo**

Criar `docs/how-to/resetar-migrations.md` com estas seções e comandos exatos:

````markdown
# Resetar migrations locais

Este procedimento descarta o schema `public` do banco configurado e substitui
as migrations dos apps em `BUSINESS_APPS`. Use apenas quando o histórico de
upgrade também for descartável. O comando recusa produção.

## Inspecionar sem alterar

```bash
make reset-migrations
```

Confira o banco, os arquivos marcados como `REMOVER` e a migration manual
marcada como `PRESERVAR`.

## Executar

```bash
make reset-migrations RESET_MIGRATIONS_ARGS='--apply --confirm-database base'
```

Troque `base` somente quando `DATABASE_NAME` tiver outro valor e confirme o
nome exatamente. A operação não cria backup dos dados.

## Verificar

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py showmigrations --plan
make test
```

Se a geração falhar antes do reset do schema, os arquivos antigos são
restaurados. Depois que o schema for removido, corrija a causa indicada e rode
`make migrate`; as migrations novas permanecem no working tree.
````

- [ ] **Step 2: Adicionar o runbook ao MkDocs e atualizar estados**

Em `mkdocs.yml`, incluir:

```yaml
      - Resetar migrations: how-to/resetar-migrations.md
```

logo depois do fluxo de desenvolvimento. Em `fluxo-de-desenvolvimento.md`, substituir os dois parágrafos da política temporária por:

```markdown
Antes de abrir o PR, garanta o que o `CONTRIBUTING.md` exige: `make lint`,
`make test` e `make docs` verdes. Os testes criam `base_test`, aplicam o grafo
real de migrations e destroem o banco ao final.

1. **CI** roda três jobs — `commits` (valida Conventional Commits do range),
   `lint` (ruff, versões, migrations e docs) e `test` (pytest com migrations +
   cobertura Codecov). Todos precisam passar.
```

No ROADMAP, marcar o gate como ativo e substituir a pendência por:

```markdown
- ✅ Baseline de migrations consolidada; CI e testes aplicam o grafo real e o
  gate `makemigrations --check --dry-run` está ativo.
```

No relatório, mudar `Gate de migrations` para `✅ ativo na CI` e remover o reset da lista `Próximas entregas prioritárias`, renumerando os itens restantes.

- [ ] **Step 3: Verificar documentação e buscas residuais**

Run:

```bash
uv run mkdocs build --strict
rg -n "sem migrations|--nomigrations|reset integral pré-lançamento|migrations permanecem congeladas|gate .*suspenso" Makefile .github docs --glob '!docs/superpowers/**'
```

Expected: MkDocs exits 0; the search returns no live-policy references outside historical `.ai/brainstorming/`.

- [ ] **Step 4: Commitar documentação operacional**

```bash
git add docs/how-to/resetar-migrations.md docs/how-to/fluxo-de-desenvolvimento.md docs/ROADMAP.md docs/relatorio-base-gente-grande.md mkdocs.yml
git commit -m "docs: documentar reset de migrations"
```

Expected: commit contains only public documentation and navigation.

### Task 7: Verificação final e registro do bloqueio preexistente

**Files:**
- No planned source changes.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verification evidence and a clean migration state.

- [ ] **Step 1: Executar gates que pertencem a esta entrega**

Run:

```bash
uv run ruff check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run --group test pytest api/tests/test_database_settings.py apps/api/core/tests/management_commands/test_reset_migrations.py apps/api/core/tests/management_commands/test_seed_demo.py -q --no-cov
uv run mkdocs build --strict
git diff --check
```

Expected: every command exits 0; migration check prints `No changes detected`.

- [ ] **Step 2: Executar a suíte completa sem mascarar migrations**

Run:

```bash
make test
```

Expected at the current baseline: collection reaches the preexisting `NameError: name 'extend_schema' is not defined` in `apps/api/core/deprecation.py`. Confirm that the command line contains no `--nomigrations`, preserve the failure evidence, and do not modify that module in this task.

- [ ] **Step 3: Auditar escopo e histórico**

Run:

```bash
git status --short
git log --oneline -6
git diff HEAD~4 -- . ':!TODO.md'
```

Expected: `TODO.md` remains untouched; commits are narrow and conventional; no credential, MCP config or unrelated bug fix appears in the diff.
