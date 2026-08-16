"""Planeja e executa o reset destrutivo das migrations dos apps próprios."""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connections, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.utils import DatabaseError

MIGRATION_FILENAME = re.compile(r"^\d{4}_[a-z0-9_]+\.py$")
PRESERVED_MIGRATIONS = {
    "core": frozenset({"0001_schedule_access_log_cleanup.py"}),
}
RESETTABLE_DATABASE_NAME = "base"


class MigrationResetError(Exception):
    """Indica que o reset não pode continuar com segurança."""


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


def _ensure_no_first_party_migration_conflicts(app_labels: tuple[str, ...] | list[str]) -> None:
    conflicts = MigrationLoader(None, ignore_no_migrations=True).detect_conflicts()
    first_party_conflicts = {label: tuple(names) for label, names in conflicts.items() if label in app_labels}
    if not first_party_conflicts:
        return

    details = "; ".join(f"{label}: {', '.join(names)}" for label, names in sorted(first_party_conflicts.items()))
    raise MigrationResetError(f"conflitos no grafo de migrations próprias: {details}")


def build_migration_reset_plan() -> MigrationResetPlan:
    base_dir = Path(settings.BASE_DIR).resolve()
    apps_root = base_dir / "apps"
    database = settings.DATABASES["default"]
    engine = database["ENGINE"]
    if not str(engine).endswith(".postgresql"):
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

    _ensure_no_first_party_migration_conflicts(labels)

    return MigrationResetPlan(
        base_dir=base_dir,
        database_name=database_name,
        app_labels=tuple(labels),
        migration_directories=tuple(directories),
        remove=tuple(remove),
        preserve=tuple(sorted(preserve)),
    )


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
    try:
        with transaction.atomic(using="default"):
            with connection.cursor() as cursor:
                cursor.execute("DROP SCHEMA public CASCADE")
                cursor.execute("CREATE SCHEMA public AUTHORIZATION CURRENT_USER")
                cursor.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    finally:
        connection.close()


def apply_migration_reset(plan: MigrationResetPlan, *, confirmed_database: str | None) -> None:
    if settings.IN_PRODUCTION:
        raise MigrationResetError("reset_migrations é bloqueado em produção.")
    if not settings.IN_DEVELOPMENT:
        raise MigrationResetError("reset_migrations só pode ser aplicado em desenvolvimento.")
    if plan.database_name != RESETTABLE_DATABASE_NAME:
        raise MigrationResetError(f"reset_migrations só pode operar no banco '{RESETTABLE_DATABASE_NAME}'.")
    if confirmed_database != RESETTABLE_DATABASE_NAME:
        raise MigrationResetError(f"confirmação inválida; informe exatamente '{RESETTABLE_DATABASE_NAME}'.")
    connection_settings = connections["default"].settings_dict
    if connection_settings.get("NAME") != RESETTABLE_DATABASE_NAME or not str(connection_settings.get("ENGINE", "")).endswith(".postgresql"):
        raise MigrationResetError(f"a conexão efetiva deve ser PostgreSQL no banco '{RESETTABLE_DATABASE_NAME}'.")

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
        except KeyboardInterrupt:
            _restore_snapshot(plan, backup_root)
            raise
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
