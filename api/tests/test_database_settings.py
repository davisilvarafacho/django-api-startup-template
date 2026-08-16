import os
import subprocess
import sys

from django.conf import settings
from django.db.migrations.loader import MigrationLoader


def test_default_test_database_name_is_isolated():
    assert settings.DATABASES["default"]["TEST"]["NAME"] == "base_test"


def test_rls_connection_context_reset_is_disabled_during_tests():
    assert settings.DJANGO_RLS["RESET_CONTEXT_ON_CONNECT"] is False


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
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print(settings.DJANGO_RLS['RESET_CONTEXT_ON_CONNECT'])",
        ],
        check=True,
        capture_output=True,
        cwd=settings.BASE_DIR,
        env=environment,
        text=True,
    )

    assert result.stdout.strip() == "True"
