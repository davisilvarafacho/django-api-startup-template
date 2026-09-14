import os
import subprocess
import sys

from django.conf import settings
from django.db.migrations.loader import MigrationLoader

import pytest


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


def test_explicit_test_environment_selects_test_profile_and_silences_missing_billing_credentials():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    for key in ("STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "DJC_STRIPE_API_KEY"):
        environment.pop(key, None)

    result = _read_setting(
        "(settings.CONFIG_ENVIRONMENT, settings.TESTING, "
        "'django_checkouts.E001' in settings.SILENCED_SYSTEM_CHECKS, "
        "'faturamento.E001' in settings.SILENCED_SYSTEM_CHECKS)",
        environment,
    )

    assert result == "('test', True, True, True)"


def test_production_does_not_silence_missing_billing_credentials():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "production",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }

    result = _read_setting(
        "('django_checkouts.E001' in settings.SILENCED_SYSTEM_CHECKS, 'faturamento.E001' in settings.SILENCED_SYSTEM_CHECKS)",
        environment,
    )

    assert result == "(False, False)"


def test_development_permite_subir_sem_credenciais_stripe():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "development",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
    }
    for key in ("STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "DJC_STRIPE_API_KEY"):
        environment.pop(key, None)

    result = _read_setting(
        "(settings.CONFIG_ENVIRONMENT, "
        "'django_checkouts.E001' in settings.SILENCED_SYSTEM_CHECKS, "
        "'faturamento.E001' in settings.SILENCED_SYSTEM_CHECKS)",
        environment,
    )

    assert result == "('development', True, False)"


def test_modo_ingress_restringe_urlconf_ao_processo_de_webhook():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
        "BILLING_DATABASE_MODE": "ingress",
    }

    assert _read_setting("settings.ROOT_URLCONF", environment) == "api.billing_ingress_urls"


def test_modo_de_banco_invalido_falha_antes_de_carregar_urlconf():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "test",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
        "BILLING_DATABASE_MODE": "ingres",
    }

    with pytest.raises(subprocess.CalledProcessError) as erro:
        _read_setting("settings.ROOT_URLCONF", environment)

    assert "BILLING_DATABASE_MODE deve ser" in erro.value.stderr


def test_ingress_de_desenvolvimento_remove_apps_e_middlewares_que_capturam_request():
    environment = {
        **os.environ,
        "DJANGO_ENVIRONMENT": "development",
        "DJANGO_SECRET_KEY": "test-secret",
        "DJANGO_SETTINGS_MODULE": "api.settings",
        "BILLING_DATABASE_MODE": "ingress",
    }

    result = _read_setting(
        "(settings.ROOT_URLCONF, 'silk' in settings.INSTALLED_APPS, "
        "'drf_api_logger' in settings.INSTALLED_APPS, "
        "any('SilkyMiddleware' in item or 'APILoggerMiddleware' in item for item in settings.MIDDLEWARE))",
        environment,
    )

    assert result == "('api.billing_ingress_urls', False, False, False)"


def test_sentry_descarta_evento_do_webhook_com_body_e_assinatura():
    event = {
        "request": {
            "url": "https://api.example.com/faturamento/webhooks/stripe/",
            "data": "payload-sensitive",
            "headers": {"Stripe-Signature": "secret", "Authorization": "Bearer secret"},
        }
    }

    assert settings.SENTRY_BEFORE_SEND(event, {}) is None


def test_filas_celery_separam_ingresso_de_processamento_tenantizado():
    ingresso = {
        "faturamento.recuperar_eventos_cobranca",
        "faturamento.reconciliar_eventos_stripe",
        "faturamento.reconciliar_evento_cobranca",
        "faturamento.executar_reconciliacao_operacional",
    }

    assert {nome for nome, rota in settings.CELERY_TASK_ROUTES.items() if rota["queue"] == "billing_ingress"} == ingresso
    assert settings.CELERY_TASK_ROUTES["faturamento.processar_evento_cobranca"] == {"queue": "celery"}
    assert settings.CELERY_BEAT_SCHEDULE["recover-billing-events"]["options"] == {"queue": "billing_ingress"}
    assert settings.CELERY_BEAT_SCHEDULE["reconcile-stripe-events"]["options"] == {"queue": "billing_ingress"}


def test_alias_migration_exige_credenciais_completas():
    base = {**os.environ, "DJANGO_SETTINGS_MODULE": "api.settings", "DJANGO_SECRET_KEY": "test"}
    for key in ("BILLING_MIGRATION_DATABASE_USER", "BILLING_MIGRATION_DATABASE_PASSWORD"):
        base.pop(key, None)
    assert _read_setting("'billing_migration' in settings.DATABASES", base) == "False"
    assert _read_setting("'billing_migration' in settings.DATABASES", {**base, "BILLING_MIGRATION_DATABASE_USER": "migrator"}) == "False"
    completa = {**base, "BILLING_MIGRATION_DATABASE_USER": "migrator", "BILLING_MIGRATION_DATABASE_PASSWORD": "secret"}
    assert _read_setting("'billing_migration' in settings.DATABASES", completa) == "False"
    completa["BILLING_DATABASE_MODE"] = "migration"
    assert _read_setting("settings.DATABASES['billing_migration']['USER']", completa) == "migrator"


def test_make_billing_migrate_falha_antes_de_executar_sem_credenciais():
    recipe = (settings.BASE_DIR / "Makefile").read_text()
    alvo = recipe.split("billing-migrate:", 1)[1].split("\n\n", 1)[0]
    assert 'test -n "$$BILLING_MIGRATION_DATABASE_USER" -a -n "$$BILLING_MIGRATION_DATABASE_PASSWORD"' in alvo
    assert alvo.index("test -n") < alvo.index("manage.py migrate")


def test_make_escopa_credenciais_privilegiadas_aos_alvos_dedicados():
    makefile = (settings.BASE_DIR / "Makefile").read_text()
    exports_globais = [linha for linha in makefile.splitlines() if linha.startswith("export ")]

    assert all("BILLING_MIGRATION_DATABASE" not in linha for linha in exports_globais)
    assert all("BILLING_INGRESS_WORKER_DATABASE" not in linha for linha in exports_globais)
    assert "billing-migrate: export BILLING_MIGRATION_DATABASE_USER" in makefile
    assert "billing-migrate: export BILLING_MIGRATION_DATABASE_PASSWORD" in makefile
    assert "billing-ingress-worker: export BILLING_INGRESS_WORKER_DATABASE_USER" in makefile
    assert "billing-ingress-worker: export BILLING_INGRESS_WORKER_DATABASE_PASSWORD" in makefile


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
