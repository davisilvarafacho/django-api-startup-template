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
    environment.pop("TEST_DATABASE_NAME", None)
    if override is not None:
        environment["TEST_DATABASE_NAME"] = override

    return _read_setting("settings.DATABASES['default']['TEST']['NAME']", environment)


def test_default_test_database_name_is_isolated():
    assert _read_test_database_name(None) == "base_test"


def test_test_database_name_honors_environment_override():
    assert _read_test_database_name("test_parallel_worker_1") == "test_parallel_worker_1"


def test_rls_connection_context_reset_is_disabled_during_tests():
    assert settings.DJANGO_RLS["RESET_CONTEXT_ON_CONNECT"] is False


def test_alias_migration_exige_credenciais_completas():
    base = {**os.environ, "DJANGO_SETTINGS_MODULE": "api.settings", "DJANGO_SECRET_KEY": "test"}
    for key in ("BILLING_MIGRATION_DATABASE_USER", "BILLING_MIGRATION_DATABASE_PASSWORD"):
        base.pop(key, None)
    assert _read_setting("'billing_migration' in settings.DATABASES", base) == "False"
    assert _read_setting("'billing_migration' in settings.DATABASES", {**base, "BILLING_MIGRATION_DATABASE_USER": "migrator"}) == "False"
    completa = {**base, "BILLING_MIGRATION_DATABASE_USER": "migrator", "BILLING_MIGRATION_DATABASE_PASSWORD": "secret"}
    assert _read_setting("settings.DATABASES['billing_migration']['USER']", completa) == "migrator"


def test_make_billing_migrate_falha_antes_de_executar_sem_credenciais():
    recipe = (settings.BASE_DIR / "Makefile").read_text()
    alvo = recipe.split("billing-migrate:", 1)[1].split("\n\n", 1)[0]
    assert 'test -n "$$BILLING_MIGRATION_DATABASE_USER" -a -n "$$BILLING_MIGRATION_DATABASE_PASSWORD"' in alvo
    assert alvo.index("test -n") < alvo.index("manage.py migrate")


def test_swapped_third_party_schemas_are_owned_by_local_models():
    loader = MigrationLoader(None, ignore_no_migrations=True)

    third_party_apps_with_local_models = {"auditlog", "knox"}
    migrated_apps = {app_label for app_label, _migration_name in loader.disk_migrations}

    assert third_party_apps_with_local_models.isdisjoint(migrated_apps)


def test_rls_connection_context_reset_remains_enabled_in_production():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "production",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    reset_context_on_connect = _read_setting("settings.DJANGO_RLS['RESET_CONTEXT_ON_CONNECT']", environment)

    assert reset_context_on_connect == "True"
