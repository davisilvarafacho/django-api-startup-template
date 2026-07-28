import ast
import os
from typing import Literal

ENVS = (
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
    "REDIS_HOST",
    "REDIS_PORT",
    "EMAIL_PASSWORD",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    # back blaze
    "BACKBLAZE_APPLICATION_ID",
    "BACKBLAZE_APPLICATION_KEY",
    "BACKBLAZE_BUCKET_NAME",
    "BACKBLAZE_BUCKET_ID",
    "BACKBLAZE_LOCATION",
    "BACKBLAZE_PUBLIC_BASE_URL",
    # sentry
    "SENTRY_DSN",
    "SENSITIVE_FIELD_KEYS",
    # hosts
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    # celery
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    # ipapi
    "IPAPI_ACCESS_KEY",
)

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
    "REDIS_HOST",
    "REDIS_PORT",
    "EMAIL_PASSWORD",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    "RABBITMQ_CONNECTION_STRING",
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


def get_list_from_env(key: EnviromentVar, default_value=None):
    """Lê uma env var como lista separada por vírgula (ex.: "a,b,c" -> ["a", "b", "c"])."""
    value = os.environ.get(key)
    if not value:
        return list(default_value) if default_value is not None else []
    return [item.strip() for item in value.split(",") if item.strip()]
