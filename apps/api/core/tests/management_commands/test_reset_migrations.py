from io import StringIO
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.utils import DatabaseError

import pytest

from apps.api.core import migration_resets


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
    settings.IN_DEVELOPMENT = True
    databases = settings.DATABASES.copy()
    databases["default"] = {
        "ENGINE": "django_rls.backends.postgresql",
        "NAME": "base",
    }
    monkeypatch.setattr(migration_resets.settings, "DATABASES", databases)
    monkeypatch.setitem(connections["default"].settings_dict, "ENGINE", "django_rls.backends.postgresql")
    monkeypatch.setitem(connections["default"].settings_dict, "NAME", "base")
    monkeypatch.setattr(migration_resets, "_installed_app_configs", lambda: configs)


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


def test_plano_remove_so_migrations_dos_business_apps(settings, monkeypatch, tmp_path):
    autenticacao = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py", "0002_legacy.py")
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [autenticacao, core])

    plan = migration_resets.build_migration_reset_plan()

    assert plan.database_name == "base"
    assert plan.app_labels == ("autenticacao", "core")
    assert [path.name for path in plan.remove] == ["0001_initial.py", "0002_legacy.py"]
    assert [path.name for path in plan.preserve] == ["0001_schedule_access_log_cleanup.py"]
    assert all(path.name != "__init__.py" for path in (*plan.remove, *plan.preserve))


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


def test_plano_recusa_app_fora_de_apps(settings, monkeypatch, tmp_path):
    externo = criar_app(tmp_path, "vendor.externo", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [externo])

    with pytest.raises(migration_resets.MigrationResetError, match="fora de apps"):
        migration_resets.build_migration_reset_plan()


def test_plano_recusa_backend_nao_postgresql(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.DATABASES["default"]["ENGINE"] = "django.db.backends.sqlite3"

    with pytest.raises(migration_resets.MigrationResetError, match="PostgreSQL"):
        migration_resets.build_migration_reset_plan()


def test_plano_recusa_backend_postgresql_sem_prefixo(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.DATABASES["default"]["ENGINE"] = "postgresql"

    with pytest.raises(migration_resets.MigrationResetError, match="PostgreSQL"):
        migration_resets.build_migration_reset_plan()


def test_plano_recusa_migration_manual_ausente(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core")
    configurar_plano(settings, monkeypatch, tmp_path, [core])

    with pytest.raises(migration_resets.MigrationResetError, match="schedule_access_log_cleanup"):
        migration_resets.build_migration_reset_plan()


def test_plano_recusa_diretorio_de_migrations_simbolico(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    migration_dir = Path(auth.path) / "migrations"
    outside = tmp_path / "outside-migrations"
    migration_dir.rename(outside)
    migration_dir.symlink_to(outside, target_is_directory=True)
    configurar_plano(settings, monkeypatch, tmp_path, [auth])

    with pytest.raises(migration_resets.MigrationResetError, match="fora do app"):
        migration_resets.build_migration_reset_plan()


def test_apply_bloqueia_producao_antes_de_mutar(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = True
    plan = migration_resets.build_migration_reset_plan()
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))

    with pytest.raises(migration_resets.MigrationResetError, match="produção"):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")


def test_apply_bloqueia_ambiente_que_nao_e_desenvolvimento_antes_de_mutar(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = False
    settings.IN_DEVELOPMENT = False
    plan = migration_resets.build_migration_reset_plan()
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))

    with pytest.raises(migration_resets.MigrationResetError, match="desenvolvimento"):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")


def test_apply_exige_nome_exato_do_banco(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()

    with pytest.raises(migration_resets.MigrationResetError, match="confirmação"):
        migration_resets.apply_migration_reset(plan, confirmed_database="outro")


def test_apply_recusa_plano_de_banco_diferente_de_base_antes_de_mutar(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    settings.IN_PRODUCTION = False
    settings.DATABASES["default"]["NAME"] = "outro"
    plan = migration_resets.build_migration_reset_plan()
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_resets.MigrationResetError, match="base"):
        migration_resets.apply_migration_reset(plan, confirmed_database="outro")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"


def test_apply_recusa_conexao_efetiva_diferente_do_plano_antes_de_mutar(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()
    monkeypatch.setitem(connections["default"].settings_dict, "NAME", "outro")
    monkeypatch.setitem(connections["default"].settings_dict, "ENGINE", "django.db.backends.sqlite3")
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_resets.MigrationResetError, match="conexão"):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"


def test_apply_recusa_backend_postgresql_sem_prefixo_antes_de_mutar(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()
    monkeypatch.setitem(connections["default"].settings_dict, "ENGINE", "postgresql")
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: pytest.fail("não deveria executar"))
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_resets.MigrationResetError, match="conexão"):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"


def test_apply_restaura_migrations_quando_makemigrations_falha(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth, core])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()

    def falhar(*args):
        (Path(auth.path) / "migrations" / "0001_generated.py").write_text("# generated\n", encoding="utf-8")
        raise CalledProcessError(1, args)

    monkeypatch.setattr(migration_resets, "_run_manage_py", falhar)
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(migration_resets.MigrationResetError, match="makemigrations"):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"
    assert not (Path(auth.path) / "migrations" / "0001_generated.py").exists()


def test_apply_restaura_migrations_quando_makemigrations_e_interrompido(settings, monkeypatch, tmp_path):
    auth = criar_app(tmp_path, "apps.api.autenticacao", "0001_initial.py")
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [auth, core])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()

    def interromper(*_args):
        (Path(auth.path) / "migrations" / "0001_generated.py").write_text("# generated\n", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(migration_resets, "_run_manage_py", interromper)
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: pytest.fail("schema não deveria mudar"))

    with pytest.raises(KeyboardInterrupt):
        migration_resets.apply_migration_reset(plan, confirmed_database="base")

    assert (Path(auth.path) / "migrations" / "0001_initial.py").read_text(encoding="utf-8") == "# migration\n"
    assert not (Path(auth.path) / "migrations" / "0001_generated.py").exists()


def test_reset_public_schema_executa_operacoes_em_transacao(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(migration_resets, "connections", {"default": connection})

    with patch("django.db.transaction.atomic") as atomic:
        migration_resets._reset_public_schema()

    atomic.assert_called_once_with(using="default")
    cursor.execute.assert_has_calls(
        [
            call("DROP SCHEMA public CASCADE"),
            call("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"),
            call("GRANT USAGE ON SCHEMA public TO PUBLIC"),
        ]
    )
    assert connection.close.call_count == 2


def test_reset_public_schema_fecha_conexao_quando_operacao_intermediaria_falha(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = [None, DatabaseError("falha ao recriar schema")]
    monkeypatch.setattr(migration_resets, "connections", {"default": connection})

    with patch("django.db.transaction.atomic") as atomic:
        with pytest.raises(DatabaseError, match="falha ao recriar schema"):
            migration_resets._reset_public_schema()

    atomic.assert_called_once_with(using="default")
    assert connection.close.call_count == 2


def test_apply_reseta_schema_depois_de_validar_migrations(settings, monkeypatch, tmp_path):
    core = criar_app(tmp_path, "apps.api.core", "0001_schedule_access_log_cleanup.py")
    configurar_plano(settings, monkeypatch, tmp_path, [core])
    settings.IN_PRODUCTION = False
    plan = migration_resets.build_migration_reset_plan()
    events = []
    monkeypatch.setattr(migration_resets, "_run_manage_py", lambda *args: events.append(args))
    monkeypatch.setattr(migration_resets, "_reset_public_schema", lambda: events.append(("reset-schema",)))

    migration_resets.apply_migration_reset(plan, confirmed_database="base")

    assert events == [
        ("makemigrations", "core"),
        ("makemigrations", "--check", "--dry-run"),
        ("reset-schema",),
        ("migrate",),
        ("makemigrations", "--check", "--dry-run"),
        ("showmigrations", "--plan"),
    ]
