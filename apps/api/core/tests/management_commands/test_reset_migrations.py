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
