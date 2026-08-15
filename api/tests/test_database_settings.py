from django.conf import settings


def test_default_test_database_name_is_isolated():
    assert settings.DATABASES["default"]["TEST"]["NAME"] == "base_test"


def test_rls_connection_context_reset_is_disabled_during_tests():
    assert settings.DJANGO_RLS["RESET_CONTEXT_ON_CONNECT"] is False
