import os
from typing import Literal

EnviromentVar = Literal[
    # api
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ENVIRONMENT",
    "DJANGO_EXECUTION_MODE",
    "DJANGO_LOG_LEVEL",
    "DJANGO_BEHIND_PROXY",
    "DJANGO_INTERNAL_IPS",
    "DJANGO_JSON_LOG_FILE_ENABLED",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_TEST_NAME",
    "REDIS_HOST",
    "REDIS_PORT",
    "AUTHORIZATION_CACHE_ENABLED",
    "AUTHORIZATION_CACHE_KEY_PREFIX",
    "AUTHORIZATION_REDIS_URL",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    # mfa
    "MFA_SMS_ENABLED",
    "MFA_SMS_BACKEND",
    # mcp
    "MCP_SERVER_URL",
    "MCP_AUTH_ISSUER_URL",
    "MCP_AUTH_AUDIENCE",
    "MCP_AUTH_JWKS_URL",
    "MCP_AUTH_ALGORITHMS",
    "MCP_ALLOWED_HOSTS",
    "MCP_ALLOWED_ORIGINS",
    # axes
    "AXES_ENABLED",
    "AXES_FAILURE_LIMIT",
    "AXES_COOLOFF_MINUTES",
    # senha / haveibeenpwned
    "HIBP_PASSWORD_CHECK_ENABLED",
    "HIBP_PASSWORD_URL",
    "HIBP_TIMEOUT_SECONDS",
    "AUTH_PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS",
    "FRONTEND_AUTH_PASSWORD_RESET_URL",
    "AUTH_EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS",
    "AUTH_EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS",
    "FRONTEND_AUTH_EMAIL_VERIFICATION_URL",
    "FRONTEND_AUTH_EMAIL_CHANGE_URL",
    "ACCOUNT_DELETION_GRACE_DAYS",
    "ACCOUNT_DELETION_BATCH_SIZE",
    "ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS",
    "FRONTEND_ACCOUNT_REACTIVATION_URL",
    # Google Identity Services
    "GOOGLE_OAUTH_CLIENT_IDS",
    # back blaze
    "BACKBLAZE_APPLICATION_ID",
    "BACKBLAZE_APPLICATION_KEY",
    "BACKBLAZE_BUCKET_NAME",
    "BACKBLAZE_BUCKET_ID",
    "BACKBLAZE_LOCATION",
    "BACKBLAZE_PUBLIC_BASE_URL",
    # hosts
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    # celery
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "SUBSCRIPTION_TASK_BATCH_SIZE",
    "ORGANIZATION_CLOSURE_BATCH_SIZE",
    # onboarding de organizações
    "SUBSCRIPTION_ONBOARDING_MODE",
    "SUBSCRIPTION_ONBOARDING_PLAN",
    "SUBSCRIPTION_ONBOARDING_BILLING_PERIOD",
    # faturamento
    "STRIPE_API_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_SANDBOX",
    "DATABASE_BILLING_INGRESS_ROLE",
    "DATABASE_BILLING_OWNER_ROLE",
    "DATABASE_BILLING_MIGRATION_USER",
    "DATABASE_BILLING_MIGRATION_PASSWORD",
    "DATABASE_BILLING_MODE",
    "FRONTEND_PAYMENT_SUCCESS_URL",
    "FRONTEND_PAYMENT_CANCEL_URL",
    # sentry
    "SENTRY_DSN",
    # posthog
    "POSTHOG_PROJECT_TOKEN",
    "POSTHOG_HOST",
    "POSTHOG_ENABLED",
    # opentelemetry
    "OTEL_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
    # prometheus
    "PROMETHEUS_METRICS_TOKEN",
    "PROMETHEUS_ALLOWED_NETWORKS",
    "PROMETHEUS_MULTIPROC_DIR",
    # campos sensíveis
    "SENSITIVE_FIELD_KEYS",
    # metadata
    "METADATA_MAX_KEYS",
    "METADATA_MAX_KEY_LENGTH",
    "METADATA_MAX_VALUE_LENGTH",
]


# Nomes anteriores à padronização por prefixo de domínio. Um deles no ambiente
# seria ignorado em silêncio (caindo nos defaults), então o settings recusa o boot.
LEGACY_ENV_VARS = {
    "PASSWORD_RESET_FRONTEND_URL": "FRONTEND_AUTH_PASSWORD_RESET_URL",
    "EMAIL_VERIFICATION_FRONTEND_URL": "FRONTEND_AUTH_EMAIL_VERIFICATION_URL",
    "EMAIL_CHANGE_FRONTEND_URL": "FRONTEND_AUTH_EMAIL_CHANGE_URL",
    "ACCOUNT_REACTIVATION_FRONTEND_URL": "FRONTEND_ACCOUNT_REACTIVATION_URL",
    "BILLING_CHECKOUT_SUCCESS_URL": "FRONTEND_PAYMENT_SUCCESS_URL",
    "BILLING_CHECKOUT_CANCEL_URL": "FRONTEND_PAYMENT_CANCEL_URL",
    "POSTGRES_ADMIN_USER": "DATABASE_ADMIN_USER",
    "POSTGRES_ADMIN_PASSWORD": "DATABASE_ADMIN_PASSWORD",
    "TEST_DATABASE_NAME": "DATABASE_TEST_NAME",
    "BILLING_DATABASE_MODE": "DATABASE_BILLING_MODE",
    "BILLING_DATABASE_OWNER_ROLE": "DATABASE_BILLING_OWNER_ROLE",
    "BILLING_INGRESS_DATABASE_ROLE": "DATABASE_BILLING_INGRESS_ROLE",
    "BILLING_INGRESS_DATABASE_USER": "DATABASE_BILLING_INGRESS_USER",
    "BILLING_INGRESS_DATABASE_PASSWORD": "DATABASE_BILLING_INGRESS_PASSWORD",
    "BILLING_INGRESS_WORKER_DATABASE_USER": "DATABASE_BILLING_INGRESS_WORKER_USER",
    "BILLING_INGRESS_WORKER_DATABASE_PASSWORD": "DATABASE_BILLING_INGRESS_WORKER_PASSWORD",
    "BILLING_MIGRATION_DATABASE_USER": "DATABASE_BILLING_MIGRATION_USER",
    "BILLING_MIGRATION_DATABASE_PASSWORD": "DATABASE_BILLING_MIGRATION_PASSWORD",
    "EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS": "AUTH_EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS",
    "EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS": "AUTH_EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS",
    "NGINX_PORT": "NGINX_HOST_PORT",
    "GRAFANA_PORT": "GRAFANA_HOST_PORT",
    "UPTIME_KUMA_PORT": "UPTIME_KUMA_HOST_PORT",
    "HIBP_PASSWORDS_URL": "HIBP_PASSWORD_URL",
    "ASSINATURAS_ONBOARDING_MODO": "SUBSCRIPTION_ONBOARDING_MODE",
    "ASSINATURAS_ONBOARDING_PLANO": "SUBSCRIPTION_ONBOARDING_PLAN",
    "ASSINATURAS_ONBOARDING_PERIODICIDADE": "SUBSCRIPTION_ONBOARDING_BILLING_PERIOD",
    "PASSWORD_RESET_TIMEOUT_MINUTES": "AUTH_PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS",
    "POSTHOG_DISABLED": "POSTHOG_ENABLED",
}

# Renomeações em que copiar o valor não basta.
LEGACY_ENV_VAR_NOTES = {
    "PASSWORD_RESET_TIMEOUT_MINUTES": "o valor passa de minutos para segundos (30 min = 1800)",
    "POSTHOG_DISABLED": "a polaridade foi invertida (false vira true e vice-versa)",
}


def get_legacy_env_vars() -> dict[str, str]:
    """Devolve as variáveis com nome antigo presentes no ambiente.

    Returns:
        Mapa `nome antigo -> orientação` (novo nome e, quando o valor muda, o que ajustar).
    """
    achadas = {}
    for antigo, novo in LEGACY_ENV_VARS.items():
        if antigo in os.environ:
            nota = LEGACY_ENV_VAR_NOTES.get(antigo)
            achadas[antigo] = f"{novo} ({nota})" if nota else novo
    return achadas


def get_env_var(key: EnviromentVar, default=None):
    return os.environ.get(key, default)


def get_bool_from_env(key: EnviromentVar, default_value):
    """Lê uma env var booleana, aceitando apenas `true`/`false` (sem distinguir caixa).

    Args:
        key: Nome da variável, declarado em `EnviromentVar`.
        default_value: Valor usado quando a variável está ausente ou vazia.

    Returns:
        O booleano lido do ambiente ou o default.

    Raises:
        ValueError: Quando o valor presente não é `true` nem `false`.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        return default_value

    normalized = value.lower()
    if normalized not in ("true", "false"):
        raise ValueError(f"'{value}' não é um valor válido para '{key}': use true ou false")
    return normalized == "true"


def get_int_from_env(key: EnviromentVar, default_value):
    """Lê uma env var como inteiro.

    Args:
        key: Nome da variável, declarado em `EnviromentVar`.
        default_value: Valor usado quando a variável está ausente ou vazia.

    Returns:
        O inteiro lido do ambiente ou o default.

    Raises:
        ValueError: Quando o valor presente não é um inteiro.
    """
    value = os.environ.get(key)
    if not value:
        return default_value

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"'{value}' não é um valor válido para '{key}'") from exc


def get_list_from_env(key: EnviromentVar, default_value=None):
    """Lê uma env var como lista separada por vírgula (ex.: "a,b,c" -> ["a", "b", "c"])."""
    value = os.environ.get(key)
    if not value:
        return list(default_value) if default_value is not None else []
    return [item.strip() for item in value.split(",") if item.strip()]
