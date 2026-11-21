import os
import subprocess
import sys

from django.conf import settings
from django.db.migrations.loader import MigrationLoader


def _read_setting(expression: str, environment: dict[str, str]) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from django.conf import settings; print({expression})",
        ],
        check=True,
        capture_output=True,
        cwd=settings.BASE_DIR,
        env=environment,
        text=True,
    )
    return result.stdout.strip()


def _read_test_database_name(override: str | None) -> str:
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    environment.pop("DATABASE_TEST_NAME", None)
    if override is not None:
        environment["DATABASE_TEST_NAME"] = override

    return _read_setting("settings.DATABASES['default']['TEST']['NAME']", environment)


def test_default_test_database_name_is_isolated():
    assert _read_test_database_name(None) == "base_test"


def test_test_database_name_honors_environment_override():
    assert _read_test_database_name("test_parallel_worker_1") == "test_parallel_worker_1"


def test_rls_connection_context_reset_is_disabled_during_tests():
    assert settings.DJANGO_RLS["RESET_CONTEXT_ON_CONNECT"] is False


def test_sentry_remove_header_de_autenticacao():
    event = {
        "request": {
            "url": "https://api.example.com/organizacoes/",
            "headers": {"Authorization": "Bearer secret", "Accept": "application/json"},
        }
    }

    resultado = settings.SENTRY_BEFORE_SEND(event, {})

    assert resultado["request"]["headers"] == {"Accept": "application/json"}


def test_make_migrate_usa_credencial_admin_sem_exporta_la_globalmente():
    makefile = (settings.BASE_DIR / "Makefile").read_text()
    exports_globais = [linha for linha in makefile.splitlines() if linha.startswith("export ")]
    alvo = makefile.split("\nmigrate:", 1)[1].split("\n\n", 1)[0]

    assert all("DATABASE_ADMIN" not in linha for linha in exports_globais)
    assert 'DATABASE_USER="$(DATABASE_ADMIN_USER)"' in alvo
    assert "manage.py migrate" in alvo


def test_swapped_third_party_schemas_are_owned_by_local_models():
    loader = MigrationLoader(None, ignore_no_migrations=True)

    third_party_apps_with_local_models = {"auditlog", "knox"}
    migrated_apps = {app_label for app_label, _migration_name in loader.disk_migrations}

    assert third_party_apps_with_local_models.isdisjoint(migrated_apps)


def test_rls_connection_context_reset_remains_enabled_in_production():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "production",
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    reset_context_on_connect = _read_setting("settings.DJANGO_RLS['RESET_CONTEXT_ON_CONNECT']", environment)

    assert reset_context_on_connect == "True"
