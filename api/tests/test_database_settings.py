import os
import subprocess
import sys

from django.conf import settings


def test_default_test_database_name_is_isolated():
    assert settings.DATABASES["default"]["TEST"]["NAME"] == "base_test"


def test_rls_connection_context_reset_is_disabled_during_tests():
    assert settings.DJANGO_RLS["RESET_CONTEXT_ON_CONNECT"] is False


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
