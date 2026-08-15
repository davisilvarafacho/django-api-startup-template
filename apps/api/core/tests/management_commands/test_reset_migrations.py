from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace

from django.db import connections

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
    monkeypatch.setitem(connections["default"].settings_dict, "ENGINE", "django_rls.backends.postgresql")
    monkeypatch.setitem(connections["default"].settings_dict, "NAME", "base")
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


def test_apply_recusa_plano_de_banco_diferente_de_base_antes_de_mutar(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    settings.IN_PRODUCTION = False
    settings.DATABASES["default"]["NAME"] = "outro"
    plan = migration_reset.build_migration_reset_plan()
    monkeypatch.setattr(migration_reset, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))
    monkeypatch.setattr(migration_reset, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_reset.MigrationResetError, match="base"):
        migration_reset.apply_migration_reset(plan, confirmed_database="outro")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"


def test_apply_recusa_conexao_efetiva_diferente_do_plano_antes_de_mutar(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    settings.IN_PRODUCTION = False
    plan = migration_reset.build_migration_reset_plan()
    monkeypatch.setitem(connections["default"].settings_dict, "NAME", "outro")
    monkeypatch.setitem(connections["default"].settings_dict, "ENGINE", "django.db.backends.sqlite3")
    monkeypatch.setattr(migration_reset, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))
    monkeypatch.setattr(migration_reset, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_reset.MigrationResetError, match="conexão"):
        migration_reset.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"


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
