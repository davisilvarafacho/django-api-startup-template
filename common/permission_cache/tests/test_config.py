import os

from django.core.cache import caches
from django.db import connection
from django.test import override_settings

from common.permission_cache.config import get_authorization_cache_config


def test_django_uses_isolated_permission_cache_test_database():
    expected_name = os.environ.get("TEST_DATABASE_NAME", "test_base_permission_cache")
    assert connection.settings_dict["TEST"]["NAME"] == expected_name


def test_default_authorization_cache_contract(settings):
    config = get_authorization_cache_config()

    assert config.enabled is True
    assert config.alias == "permissions"
    assert config.timeout == 1800
    assert config.key_prefix.startswith("authz:v1:")
    assert caches["permissions"] is not None


@override_settings(
    AUTHORIZATION_CACHE={
        "ENABLED": False,
        "ALIAS": "default",
        "TIMEOUT": 60,
        "KEY_PREFIX": "authz:test",
        "MAX_RETRIES": 1,
    }
)
def test_config_reads_override_without_process_cache():
    config = get_authorization_cache_config()

    assert config.enabled is False
    assert config.alias == "default"
    assert config.timeout == 60
    assert config.max_retries == 1
