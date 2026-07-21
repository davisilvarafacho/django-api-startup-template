import os
import pathlib
import sys
import warnings

from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

import sentry_sdk

from api.configure_enviroment import configure_enviroment
from libs.serpy.mp import *
from utils.env import get_bool_from_env, get_env_var, get_list_from_env

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

SECRET_KEY = get_env_var("DJANGO_SECRET_KEY")

DEBUG = get_bool_from_env("DJANGO_DEBUG", True)

ENVIROMENT = get_env_var("DJANGO_ENVIRONMENT")

IN_DEVELOPMENT = ENVIROMENT == "development"

IN_PRODUCTION = ENVIROMENT == "production"

EXECUTION = get_env_var("DJANGO_EXECUTION_MODE")

# Ambiente efetivo usado para carregar apps/middlewares/storages específicos.
# Sempre resolve para um valor suportado por `configure_enviroment`.
TESTING = "pytest" in sys.modules or "test" in sys.argv

if TESTING:
    CONFIG_ENVIRONMENT = "test"
elif IN_PRODUCTION:
    CONFIG_ENVIRONMENT = "production"
else:
    CONFIG_ENVIRONMENT = "development"

ENV_MIDDLEWARES, ENV_APPS, ENV_STORAGES = configure_enviroment(CONFIG_ENVIRONMENT)


if not SECRET_KEY and not IN_PRODUCTION:
    warnings.warn("'SECRET_KEY' não foi configurada, using a random temporary key.", stacklevel=2)
    SECRET_KEY = get_random_secret_key()


if IN_PRODUCTION:

    def before_send(event, hint):
        # Nunca enviar o token de autenticação para o Sentry.
        headers = event.get("request", {}).get("headers")
        if headers:
            headers.pop("Authorization", None)

        return event

    sentry_sdk.init(
        dsn=get_env_var("SENTRY_DSN"),
        environment=ENVIROMENT,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=True,
        before_send=before_send,
    )


ALLOWED_HOSTS = get_list_from_env("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost"])

CSRF_TRUSTED_ORIGINS = get_list_from_env("DJANGO_CSRF_TRUSTED_ORIGINS", ["http://127.0.0.1:8000", "http://localhost:8000"])

INTERNAL_IPS = [
    "127.0.0.1",
]


SITE_ID = 1

ADMINS = [("Davi Silva Rafacho", "rafacho@zettabyte.tech")]

MANAGERS = ADMINS

ADMINS_EMAILS = [admin[1] for admin in ADMINS]


DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

# Apps agnósticas de ambiente. As específicas de dev/prod/test são adicionadas
# por `configure_enviroment` (ver api/configure_enviroment.py).
LIBS_APPS = [
    "auditlog",
    "corsheaders",
    "django_celery_beat",
    "django_filters",
    "drf_spectacular",
    "django_scalar",
    "knox",
    "rest_framework",
]

BASE_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.usuarios",
]

INSTALLED_APPS = LIBS_APPS + DJANGO_APPS + BASE_APPS + ENV_APPS


# Middlewares agnósticos de ambiente. Os específicos são adicionados ao final
# por `configure_enviroment` (ver api/configure_enviroment.py).
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "threadlocals.middleware.ThreadLocalMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
] + ENV_MIDDLEWARES


ROOT_URLCONF = "api.urls"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "api.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": get_env_var("DATABASE_HOST"),
        "NAME": get_env_var("DATABASE_NAME"),
        "USER": get_env_var("DATABASE_USER"),
        "PASSWORD": get_env_var("DATABASE_PASSWORD"),
        "PORT": get_env_var("DATABASE_PORT"),
        "CONN_MAX_AGE": 60 * 60 * 3,  # 3 horas
        "CONN_HEALTH_CHECKS": True,
    },
    "logging": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "logging_db.sqlite3"),
    },
}


# `staticfiles` é agnóstico; `default` e `dbbackup` vêm de `configure_enviroment`.
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    **ENV_STORAGES,
}


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

AUTH_USER_MODEL = "usuarios.Usuario"


LOGIN_REDIRECT_URL = "/admin/"

LOGIN_URL = "/admin/"

LOGOUT_REDIRECT_URL = "/admin/"


DATE_FORMAT = "d/m/Y"


DECIMAL_SEPARATOR = ","


LANGUAGE_CODE = "pt-br"

TIME_ZONE = "America/Sao_Paulo"

USE_TZ = True

USE_I18N = True

LANGUAGES = [
    ("pt-br", _("Português (Brasil)")),
    ("en", _("Inglês")),
    ("es", _("Espanhol")),
]

LOCALE_PATHS = [
    os.path.join(BASE_DIR, "locale"),
]


STATIC_URL = "/static/"

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = "/media/"

MEDIA_ROOT = os.path.join(BASE_DIR, "mediafiles")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


RESEND_API_KEY = get_env_var("RESEND_API_KEY")

DEFAULT_FROM_EMAIL = get_env_var("RESEND_FROM_EMAIL") or "nao-responda@base.com.br"

EMAIL_BACKEND = "apps.api.core.email_backends.ResendEmailBackend"


LOGGING_ROOT = os.path.join(BASE_DIR, "logs/")

# LOGGING = {
#     "version": 1,
#     "disable_existing_loggers": False,
#     "formatters": {
#         "api_formatter": {
#             "format": "[%(asctime)s] %(name)s [%(levelname)s] %(message)s",
#             "datefmt": "%d/%b/%Y %H:%M:%S",
#         },
#         "cloud_formatter": {
#             "format": "[%(asctime)s] base %(name)s: [%(levelname)s] %(message)s",
#             "datefmt": "%Y-%m-%dT%H:%M:%S",
#         },
#     },
#     "filters": {
#         "warnings_filter": {
#             "()": "django.utils.log.CallbackFilter",
#             "callback": lambda record: record.levelno == logging.WARNING,
#         },
#         "api_filter": {
#             "()": "django.utils.log.CallbackFilter",
#             "callback": lambda record: record.levelno >= logging.INFO,
#         },
#         "error_filter": {
#             "()": "django.utils.log.CallbackFilter",
#             "callback": lambda record: record.levelno >= logging.ERROR,
#         },
#     },
#     "handlers": {
#         "console": {
#             "level": "DEBUG",
#             "class": "logging.StreamHandler",
#             "formatter": "api_formatter",
#         },
#         "api_activity": {
#             "level": "DEBUG",
#             "class": "logging.handlers.RotatingFileHandler",
#             "filename": os.path.join(LOGGING_ROOT, "api_activity.log"),
#             "maxBytes": 1024 * 1024 * 5,
#             "backupCount": 10,
#             "formatter": "api_formatter",
#             "filters": ["api_filter"],
#         },
#         "api_warnings": {
#             "level": "WARNING",
#             "class": "logging.handlers.RotatingFileHandler",
#             "filename": os.path.join(LOGGING_ROOT, "warnings.log"),
#             "maxBytes": 1024 * 1024 * 10,
#             "backupCount": 2,
#             "formatter": "api_formatter",
#             "filters": ["warnings_filter"],
#         },
#         "api_errors": {
#             "level": "ERROR",
#             "class": "logging.handlers.RotatingFileHandler",
#             "filename": os.path.join(LOGGING_ROOT, "errors.log"),
#             "maxBytes": 1024 * 1024 * 50,
#             "backupCount": 10,
#             "formatter": "api_formatter",
#             "filters": ["error_filter"],
#         },
#         "api_errors_mail": {
#             "level": "ERROR",
#             "class": "django.utils.log.AdminEmailHandler",
#             "formatter": "api_formatter",
#             "filters": ["error_filter"],
#         },
#         "api_cloud_log": {
#             "level": "DEBUG",
#             "class": "logging.handlers.SysLogHandler",
#             "formatter": "cloud_formatter",
#             "address": ("logs.papertrailapp.com", 17562),
#         },
#     },
#     "loggers": {
#         "django": {
#             "handlers": ["console"],
#             "level": "INFO",
#             "propagate": False,
#         },
#         "pika": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "httpx": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "httpcore": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "botocore": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "urllib3": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "twilio": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#         "django_lifecycle": {
#             "handlers": [],
#             "level": "WARNING",
#             "propagate": False,
#         },
#     },
#     "root": {
#         "handlers": ["console", "api_activity", "api_errors"],
#         "level": "INFO",
#         "propagate": True,
#         "formatter": "simple",
#     },
# }

# if IN_PRODUCTION:
#     LOGGING["root"]["handlers"] += ["api_cloud_log", "api_errors_mail"]


# base
BASE_AUDITLOG_EXCLUDE_FIELDS = [
    "data_ultima_alteracao",
    "hora_ultima_alteracao",
    "data_criacao",
    "hora_criacao",
]


# b2 storage
B2_APPLICATION_KEY_ID = get_env_var("BACKBLAZE_APPLICATION_ID")

B2_APPLICATION_KEY = get_env_var("BACKBLAZE_APPLICATION_KEY")

B2_BUCKET_NAME = get_env_var("BACKBLAZE_BUCKET_NAME")

B2_BUCKET_ID = get_env_var("BACKBLAZE_BUCKET_ID")

# Prefixo opcional para todos os arquivos enviados ao bucket (ex.: "media").
B2_LOCATION = get_env_var("BACKBLAZE_LOCATION") or ""

# URL pública opcional (domínio próprio/CDN). Sem ela, o SDK fornece a URL B2.
B2_PUBLIC_BASE_URL = get_env_var("BACKBLAZE_PUBLIC_BASE_URL")


# rest framework
REST_FRAMEWORK = {
    "PAGE_SIZE": 30,
    "DEFAULT_PAGINATION_CLASS": "apps.api.core.pagination.CustomPagination",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "knox.auth.TokenAuthentication",
        "apps.api.autenticacao.authentications.QueryParamTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "apps.api.autenticacao.permissions.CustomDjangoModelPermissions",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
        "auth": "10/min",
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DATE_INPUT_FORMATS": ["%d/%m/%Y"],
}


# OpenAPI / Scalar
SPECTACULAR_SETTINGS = {
    "TITLE": "DRF Base API",
    "DESCRIPTION": "Documentação da API.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SCALAR_OPENAPI_URL = "/api/schema/"
SCALAR_TITLE = "DRF Base API - Referência"
SCALAR_THEME = "purple"


# knox
KNOX_TOKEN_MODEL = "knox.AuthToken"

REST_KNOX = {
    "AUTH_HEADER_PREFIX": "Bearer",
}

# zeal
ZEAL_RAISE = False


# drf api logger
DRF_API_LOGGER_DATABASE = True

DRF_LOGGER_QUEUE_MAX_SIZE = 5000

DRF_LOGGER_INTERVAL = 1

DRF_API_LOGGER_DEFAULT_DATABASE = "logging"

# dbbackup
DBBACKUP_MEDIA_PATH = "backups/"

DBBACKUP_DATE_FORMAT = "%d/%m/%Y_%H%M%S"


# redis
REDIS_HOST = get_env_var("REDIS_HOST") or "127.0.0.1"

REDIS_PORT = get_env_var("REDIS_PORT") or "6379"

REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"


# cache
# Em testes, cache em memória para não exigir Redis rodando.
if TESTING:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "cachalot": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": f"{REDIS_URL}/1",
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        },
        # Cache dedicado do cachalot (ver CACHALOT_CACHE abaixo).
        "cachalot": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": f"{REDIS_URL}/3",
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        },
    }

# cachalot precisa de um cache COMPARTILHADO entre processos (Redis) para
# invalidar corretamente. Com LocMemCache (por-processo), cada worker do
# gunicorn mantém seu próprio cache e a invalidação disparada em um worker não
# limpa os demais — causa clássica de dado "velho" servido a clientes.
# Obs.: escritas fora do ORM (SQL cru, outro serviço no mesmo banco, triggers)
# continuam invisíveis ao cachalot; use CACHALOT_UNCACHABLE_TABLES nesses casos.
CACHALOT_CACHE = "cachalot"


# celery
CELERY_BROKER_URL = get_env_var("CELERY_BROKER_URL") or f"{REDIS_URL}/0"

CELERY_RESULT_BACKEND = get_env_var("CELERY_RESULT_BACKEND") or f"{REDIS_URL}/2"

CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_TRACK_STARTED = True

CELERY_TASK_TIME_LIMIT = 60 * 30  # hard limit: 30 min

CELERY_TASK_SOFT_TIME_LIMIT = 60 * 25  # soft limit: 25 min

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Em testes, executa as tasks de forma síncrona e propaga exceções.
CELERY_TASK_ALWAYS_EAGER = TESTING

CELERY_TASK_EAGER_PROPAGATES = TESTING
