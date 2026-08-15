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
