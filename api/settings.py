import os
import pathlib
import warnings

from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

import sentry_sdk
from threadlocals.threadlocals import get_request_variable

from libs.serpy.mp import *
from utils.env import get_bool_from_env, get_env_var

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

SECRET_KEY = get_env_var("DJANGO_SECRET_KEY")

DEBUG = get_bool_from_env("DJANGO_DEBUG", True)

ENVIROMENT = get_env_var("DJANGO_ENVIRONMENT")

IN_DEVELOPMENT = ENVIROMENT == "development"

IN_PRODUCTION = ENVIROMENT == "production"

EXECUTION = get_env_var("DJANGO_EXECUTION_MODE")


if not SECRET_KEY and not IN_PRODUCTION:
    warnings.warn("'SECRET_KEY' não foi configurada, using a random temporary key.", stacklevel=2)
    SECRET_KEY = get_random_secret_key()


if IN_PRODUCTION:

    def before_send(event, hint):
        token = get_request_variable("token")

        if "request" in event and "headers" in event["request"]:
            event["request"]["headers"]["Authorization"] = token

        event.setdefault("extra", {})
        event["extra"]["token"] = token

        return event

    sentry_sdk.init(
        dsn=get_env_var("SENTRY_DSN"),
        environment=ENVIROMENT,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        send_default_pii=True,
        before_send=before_send,
    )


ALLOWED_HOSTS = [
    # development
    "127.0.0.1",
    "localhost",
    "191.101.234.208",
    # production
]

CSRF_TRUSTED_ORIGINS = [
    # development
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    # production
]

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

LIBS_APPS = [
    "auditlog",
    "cachalot",  # PRODUCTION ONLY
    "corsheaders",
    "django_extensions",
    "django_filters",
    "debug_toolbar",  # DEVELOPMENT ONLY
    "drf_api_logger",
    "drf_spectacular",
    "django_scalar",
    "hijack",  # DEVELOPMENT ONLY
    "hijack.contrib.admin",  # DEVELOPMENT ONLY
    "knox",
    "rest_framework",
    "dbbackup",  # PRODUCTION ONLY
    "silk",  # DEVELOPMENT ONLY
    "zeal",  # DEVELOPMENT ONLY
]

BASE_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.usuarios",
]

INSTALLED_APPS = LIBS_APPS + DJANGO_APPS + BASE_APPS


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
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    "zeal.middleware.zeal_middleware",  # DEVELOPMENT ONLY
    "django_cprofile_middleware.middleware.ProfilerMiddleware",  # DEVELOPMENT ONLY
    "silk.middleware.SilkyMiddleware",  # DEVELOPMENT ONLY
    "drf_api_logger.middleware.api_logger_middleware.APILoggerMiddleware",  # DEVELOPMENT ONLY
    "hijack.middleware.HijackUserMiddleware",  # DEVELOPMENT ONLY
]


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
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    },
    "logging": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "logging_db.sqlite3"),
    },
    # postgresql
    # "default": {
    #     "ENGINE": "django.db.backends.postgresql",
    #     "HOST": get_env_var("DATABASE_HOST"),
    #     "NAME": get_env_var("DATABASE_NAME"),
    #     "USER": get_env_var("DATABASE_USER"),
    #     "PASSWORD": get_env_var("DATABASE_PASSWORD"),
    #     "PORT": get_env_var("DATABASE_PORT"),
    #     "CONN_MAX_AGE": 60 * 60 * 3,  # 3 hours
    # },
}


STORAGES = {
    "default": {
        "BACKEND": "apps.api.core.b2_storage.BackblazeB2Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    "dbbackup": {
        "BACKEND": "apps.api.core.b2_storage.BackblazeB2Storage",
    },
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
