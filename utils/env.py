import ast
import os
from typing import Literal

EnviromentVar = Literal[
    # api
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ENVIRONMENT",
    "DJANGO_EXECUTION_MODE",
    "DJANGO_LOG_LEVEL",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "TEST_DATABASE_NAME",
    "REDIS_HOST",
    "REDIS_PORT",
    "AUTHORIZATION_CACHE_ENABLED",
    "AUTHORIZATION_CACHE_KEY_PREFIX",
    "AUTHORIZATION_REDIS_URL",
    "EMAIL_PASSWORD",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    "RABBITMQ_CONNECTION_STRING",
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
    "HIBP_PASSWORDS_URL",
    "HIBP_TIMEOUT_SECONDS",
    "PASSWORD_RESET_TIMEOUT_MINUTES",
    "PASSWORD_RESET_FRONTEND_URL",
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
    # sentry
    "SENTRY_DSN",
    "SENSITIVE_FIELD_KEYS",
    # metadata
    "METADATA_MAX_KEYS",
    "METADATA_MAX_KEY_LENGTH",
    "METADATA_MAX_VALUE_LENGTH",
]


def get_env_var(key: EnviromentVar, default=None):
    return os.environ.get(key, default)


def get_bool_from_env(key: EnviromentVar, default_value):
    if key in os.environ:
        value = os.environ[key]
        try:
            return ast.literal_eval(value)
        except ValueError as exc:
            raise ValueError(f"'{value}' não é um valor válido para '{key}'") from exc
    return default_value


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
