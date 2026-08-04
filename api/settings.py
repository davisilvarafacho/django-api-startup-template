import os
import pathlib
import sys
import warnings
from datetime import timedelta

from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

import sentry_sdk
from celery.schedules import crontab

from api.configure_enviroment import configure_enviroment
from api.logging_config import build_logging
from utils.env import get_bool_from_env, get_env_var, get_list_from_env

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

SECRET_KEY = get_env_var("DJANGO_SECRET_KEY")

DEBUG = get_bool_from_env("DJANGO_DEBUG", True)

ENVIROMENT = get_env_var("DJANGO_ENVIRONMENT")

IN_DEVELOPMENT = ENVIROMENT == "development"

IN_PRODUCTION = ENVIROMENT == "production"

EXECUTION = get_env_var("DJANGO_EXECUTION_MODE")

# ambiente efetivo usado para carregar apps/middlewares/storages específicos.
# sempre resolve para um valor suportado por `configure_enviroment`.
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

# A API sempre roda atrás do nginx (`docker/nginx/`), que sobrescreve os
# `X-Forwarded-*` — o valor que o cliente mandar é descartado antes de chegar
# aqui. Sem isto o Django enxerga a request como http na porta do gunicorn e
# monta URLs absolutas (redirects, links do DRF, `build_absolute_uri`) erradas.
#
# Desligue apenas se o gunicorn for exposto direto, sem proxy: confiar nesses
# headers com a porta aberta permitiria forjar `X-Forwarded-Proto: https` e
# burlar as checagens de conexão segura.
BEHIND_PROXY = get_bool_from_env("DJANGO_BEHIND_PROXY", True)

if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    USE_X_FORWARDED_PORT = True

INTERNAL_IPS = [
    "127.0.0.1",
]


SITE_ID = 1

ADMINS = []

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
    "anymail",
    "axes",
    "corsheaders",
    "django_celery_beat",
    "django_filters",
    "django_prometheus",
    "django_rls",
    "drf_spectacular",
    "django_scalar",
    "guardian",
    "knox",
    "rest_framework",
    "rules.apps.AutodiscoverRulesConfig",
    "waffle",
]

BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.logs",
    "apps.organizacoes",
    "apps.usuarios",
]

INSTALLED_APPS = LIBS_APPS + DJANGO_APPS + BUSINESS_APPS + ENV_APPS


MIDDLEWARE = (
    [
        # Primeiro de todos: mede a request inteira, inclusive o tempo gasto pelos
        # demais middlewares.
        "django_prometheus.middleware.PrometheusBeforeMiddleware",
        "django.middleware.security.SecurityMiddleware",
        # Logo após o SecurityMiddleware para que todo log emitido daqui em diante
        # já carregue o correlation id.
        "apps.api.core.request_id.RequestIDMiddleware",
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
        # Resolve o token antes dos middlewares que dependem de `request.user`
        # (auditlog, PostHog, tenancy). Precisa vir depois do ThreadLocalMiddleware.
        "apps.api.autenticacao.middleware.AuthenticationMiddleware",
        "auditlog.middleware.AuditlogMiddleware",
        "posthog.integrations.django.PosthogContextMiddleware",
        # Deve ser o mais interno possível: abre a transação que envolve a request
        # (necessária para o `SET LOCAL` do RLS).
        "apps.organizacoes.middleware.OrganizacaoMiddleware",
        "waffle.middleware.WaffleMiddleware",
        # Par do PrometheusBeforeMiddleware; fecha a medição da request.
        "django_prometheus.middleware.PrometheusAfterMiddleware",
    ]
    + ENV_MIDDLEWARES
    + [
        # Troca a resposta por 429 na volta da request quando o bloqueio dispara.
        # Fica por último para que todos os demais middlewares vejam a resposta final.
        "axes.middleware.AxesMiddleware",
    ]
)


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
        # Backend do django-rls: envolve o do Postgres e adiciona o suporte a
        # ROW LEVEL SECURITY. Com o engine padrão as policies são silenciosamente
        # ignoradas (apenas um warning) e o isolamento entre organizações NÃO acontece.
        "ENGINE": "django_rls.backends.postgresql",
        "HOST": get_env_var("DATABASE_HOST"),
        "NAME": get_env_var("DATABASE_NAME"),
        "USER": get_env_var("DATABASE_USER"),
        "PASSWORD": get_env_var("DATABASE_PASSWORD"),
        "PORT": get_env_var("DATABASE_PORT"),
        "TEST": {
            "NAME": get_env_var("TEST_DATABASE_NAME", "test_base_permission_cache"),
        },
        "CONN_MAX_AGE": 60 * 60 * 3,  # 3 horas
        "CONN_HEALTH_CHECKS": True,
    },
    "logging": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "logging_db.sqlite3"),
    },
}


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
    {
        "NAME": "apps.usuarios.password_validation.PwnedPasswordValidator",
    },
]

# Checagem de senha vazada (HaveIBeenPwned). Desligada em teste para que a suíte
# nunca dependa de rede. A consulta é k-anonymous — só o prefixo de cinco
# caracteres do SHA-1 sai da aplicação — e falha aberta: indisponibilidade da API
# não pode impedir alguém de trocar a própria senha.
HIBP_PASSWORD_CHECK_ENABLED = get_bool_from_env("HIBP_PASSWORD_CHECK_ENABLED", CONFIG_ENVIRONMENT != "test")
HIBP_PASSWORDS_URL = get_env_var("HIBP_PASSWORDS_URL", "https://api.pwnedpasswords.com/range")
HIBP_TIMEOUT_SECONDS = float(get_env_var("HIBP_TIMEOUT_SECONDS", 2))

# Redefinição de senha. O TTL é curto de propósito: o link chega por e-mail, um
# canal que a aplicação não controla, então a janela em que uma caixa de entrada
# comprometida ainda serve precisa ser pequena.
PASSWORD_RESET_TIMEOUT_MINUTES = int(get_env_var("PASSWORD_RESET_TIMEOUT_MINUTES", 30))
PASSWORD_RESET_FRONTEND_URL = get_env_var("PASSWORD_RESET_FRONTEND_URL", "http://localhost:3000/redefinir-senha")

AUTH_USER_MODEL = "usuarios.Usuario"


# Autorização em camadas: `rules` (predicados por objeto, ex.: papel na
# organização), o backend padrão do Django (permissions/groups) e o `guardian`
# (permissões por objeto persistidas no banco).
AUTHENTICATION_BACKENDS = [
    # Só verifica bloqueio e devolve `None`, delegando a autenticação real aos
    # backends seguintes. Precisa ser o primeiro para interromper antes deles.
    "axes.backends.AxesStandaloneBackend",
    "rules.permissions.ObjectPermissionBackend",
    "internal_frameworks.permission_cache.backends.CachedModelBackend",
    "internal_frameworks.permission_cache.backends.CachedObjectPermissionBackend",
]

# CachedObjectPermissionBackend subclasses and compatibility-tests Guardian's backend.
SILENCED_SYSTEM_CHECKS = ["guardian.W001"]

# Não criar o usuário anônimo do guardian (o modelo de usuário usa e-mail como
# username e o isolamento por organização torna esse registro desnecessário).
ANONYMOUS_USER_NAME = None

# Proteção contra força bruta no login. A chave de bloqueio é o par
# usuário + IP (E lógico): bloquear só por usuário permitiria que qualquer um
# trancasse a conta alheia, e bloquear só por IP puniria clientes atrás de NAT.
# Ver docs/superpowers/specs/2026-07-30-django-axes-design.md.
AXES_ENABLED = get_bool_from_env("AXES_ENABLED", CONFIG_ENVIRONMENT != "test")
# Sem isso o axes usa `USERNAME_FIELD` do model ("email") como chave nas
# credenciais. O `AuthTokenSerializer` padrão do DRF sempre chama
# `authenticate()` com a chave literal "username", então o valor nunca seria
# encontrado e todo `AccessAttempt` seria gravado com `username=None`.
AXES_USERNAME_FORM_FIELD = "username"
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_FAILURE_LIMIT = int(get_env_var("AXES_FAILURE_LIMIT", 5))
AXES_COOLOFF_TIME = timedelta(minutes=int(get_env_var("AXES_COOLOFF_MINUTES", 30)))
AXES_RESET_ON_SUCCESS = True
# Sem isso, um cliente que faz retry automático (app com credencial salva
# desatualizada) reinicia o cooloff a cada tentativa e nunca sai do bloqueio.
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_HANDLER = "axes.handlers.database.AxesDatabaseHandler"
AXES_CLIENT_IP_CALLABLE = "apps.api.autenticacao.utils.get_client_ip"
AXES_LOCKOUT_CALLABLE = "apps.api.autenticacao.handlers.resposta_de_bloqueio"
AXES_HTTP_RESPONSE_CODE = 429
# O admin do axes é a única via de desbloqueio manual antes do fim do cooloff.
AXES_ENABLE_ADMIN = True
# Mantido porque o `TokenMetaData` só registra login de API; sem o AccessLog o
# login bem-sucedido no /admin/ não deixaria trilha nenhuma.
AXES_DISABLE_ACCESS_LOG = False
AXES_ENABLE_ACCESS_FAILURE_LOG = False


# Row Level Security. O contexto é aplicado por `apps.organizacoes` com
# `SET LOCAL`, seguro sob connection pool em transaction mode.
DJANGO_RLS = {
    # Falha alto ao consultar um modelo isolado sem contexto de organização,
    # em vez de silenciosamente retornar zero linhas.
    "REQUIRE_CONTEXT": True,
    "AUDIT_LOG": IN_PRODUCTION,
}


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


# PostHog
POSTHOG_PROJECT_TOKEN = get_env_var("POSTHOG_PROJECT_TOKEN")
POSTHOG_HOST = get_env_var("POSTHOG_HOST", "https://us.i.posthog.com")
POSTHOG_DISABLED = get_bool_from_env("POSTHOG_DISABLED", False)


RESEND_API_KEY = get_env_var("RESEND_API_KEY")

ANYMAIL = {
    "RESEND_API_KEY": RESEND_API_KEY,
}

DEFAULT_FROM_EMAIL = get_env_var("RESEND_FROM_EMAIL", "nao-responda@base.com.br")

EMAIL_BACKEND = "anymail.backends.test.EmailBackend" if TESTING else "anymail.backends.resend.EmailBackend"


LOGGING_ROOT = os.path.join(BASE_DIR, "logss/")

os.makedirs(LOGGING_ROOT, exist_ok=True)

LOG_LEVEL = get_env_var("DJANGO_LOG_LEVEL", "INFO")

# Em produção sai JSON (uma linha por evento, lido pelo Promtail/Loki); nos demais
# ambientes, texto legível no console. Ver api/logging_config.py.
LOGGING = build_logging(CONFIG_ENVIRONMENT, LOG_LEVEL, LOGGING_ROOT)


# base
BASE_AUDITLOG_EXCLUDE_FIELDS = [
    "created_at",
    "last_modified_at",
]


# b2 storage
B2_APPLICATION_KEY_ID = get_env_var("BACKBLAZE_APPLICATION_ID")

B2_APPLICATION_KEY = get_env_var("BACKBLAZE_APPLICATION_KEY")

B2_BUCKET_NAME = get_env_var("BACKBLAZE_BUCKET_NAME")

B2_BUCKET_ID = get_env_var("BACKBLAZE_BUCKET_ID")

# Prefixo opcional para todos os arquivos enviados ao bucket (ex.: "media").
B2_LOCATION = get_env_var("BACKBLAZE_LOCATION", "")

B2_PUBLIC_BASE_URL = get_env_var("BACKBLAZE_PUBLIC_BASE_URL")


# rest framework
REST_FRAMEWORK = {
    "PAGE_SIZE": 30,
    "DEFAULT_PAGINATION_CLASS": "apps.api.core.pagination.CustomPagination",
    # A autenticação real acontece no AuthenticationMiddleware; aqui o DRF apenas
    # reaproveita o usuário já resolvido.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.autenticacao.authentications.PassthroughAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        # Fallback global de tenancy. Vem antes das permissões de modelo para
        # que o contexto de RLS já esteja aplicado. Exceções são declarativas:
        # `public_routes.py` (sem token) e `tenant_free_routes.py` (sem organização).
        "apps.organizacoes.permissions.TenantPermission",
        "apps.api.autenticacao.permissions.TokenScopePermission",
        "apps.api.autenticacao.permissions.CustomDjangoModelPermissions",
        # No-op sem `@require_recent_auth` declarado na view/action/método.
        "apps.api.autenticacao.recent_auth.RecentAuthenticationPermission",
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
        "auth_login": "10/min",
        "auth_reauthenticate": "5/min",
        # Mais apertado que o login: cada tentativa dispara um e-mail, então o
        # abuso aqui não é só força bruta, é usar a API como canhão de spam.
        "auth_password_reset": "5/min",
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.core.errors.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DATE_INPUT_FORMATS": ["%d/%m/%Y"],
}


# openapi / scalar
SPECTACULAR_SETTINGS = {
    "TITLE": "DRF Base API",
    "DESCRIPTION": "Documentação da API.",
    "VERSION": "0.1.0",  # x-release-please-version
    "SERVE_INCLUDE_SCHEMA": False,
}

SCALAR_OPENAPI_URL = "/api/schema/"
SCALAR_TITLE = "DRF Base API - Referência"
SCALAR_THEME = "purple"


# knox
KNOX_TOKEN_MODEL = "autenticacao.AuthToken"

# MFA
MFA_SMS_BACKEND = get_env_var("MFA_SMS_BACKEND", "apps.api.autenticacao.mfa_backends.ConsoleSMSBackend")
MFA_SMS_ENABLED = get_bool_from_env("MFA_SMS_ENABLED", False)

REST_KNOX = {
    "AUTH_HEADER_PREFIX": "Bearer",
}

# zeal
ZEAL_RAISE = False


# drf api logger
# Só é carregado em desenvolvimento (ver configure_enviroment), e grava no banco
# `logging` — separado do banco da aplicação de propósito. Em produção o log de
# request é estruturado em arquivo/stdout (ver `apps.api.core.request_id`):
# request log em tabela é o caminho mais rápido para um banco de dezenas de GB.
DRF_API_LOGGER_DATABASE = True

DRF_LOGGER_QUEUE_MAX_SIZE = 5000

DRF_LOGGER_INTERVAL = 1

DRF_API_LOGGER_DEFAULT_DATABASE = "logging"

# dbbackup
DBBACKUP_MEDIA_PATH = "backups/"

DBBACKUP_DATE_FORMAT = "%d/%m/%Y_%H%M%S"


# redis
REDIS_HOST = get_env_var("REDIS_HOST", "127.0.0.1")

REDIS_PORT = get_env_var("REDIS_PORT", "6379")

REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

AUTHORIZATION_CACHE = {
    "ENABLED": get_bool_from_env("AUTHORIZATION_CACHE_ENABLED", True),
    "ALIAS": "permissions",
    "TIMEOUT": 60 * 30,
    "KEY_PREFIX": get_env_var(
        "AUTHORIZATION_CACHE_KEY_PREFIX",
        f"authz:v1:{ENVIROMENT or CONFIG_ENVIRONMENT}",
    ),
    "MAX_RETRIES": 2,
}

AUTHORIZATION_REDIS_URL = get_env_var("AUTHORIZATION_REDIS_URL") or f"{REDIS_URL}/4"


# cache
# sm testes, cache em memória para não exigir Redis rodando.
if TESTING:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "cachalot": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "permissions": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "KEY_PREFIX": AUTHORIZATION_CACHE["KEY_PREFIX"],
        },
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
        "permissions": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": AUTHORIZATION_REDIS_URL,
            "KEY_PREFIX": AUTHORIZATION_CACHE["KEY_PREFIX"],
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
                "REDIS_CLIENT_KWARGS": {
                    "socket_connect_timeout": 1,
                    "socket_timeout": 1,
                },
            },
        },
    }

# cachalot precisa de um cache COMPARTILHADO entre processos (Redis) para
# invalidar corretamente. Com LocMemCache (por-processo), cada worker do
# gunicorn mantém seu próprio cache e a invalidação disparada em um worker não
# limpa os demais.
# sbs.: escritas fora do ORM (SQL cru, outro serviço no mesmo banco, triggers)
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

AUTH_TOKEN_SESSION_RETENTION_DAYS = 90

AUTH_TOKEN_CLEANUP_BATCH_SIZE = 500

CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-auth-tokens": {
        "task": "autenticacao.cleanup_expired_tokens",
        "schedule": crontab(hour=0, minute=0),
    },
}

# sm testes, executa as tasks de forma síncrona e propaga exceções.
CELERY_TASK_ALWAYS_EAGER = TESTING

CELERY_TASK_EAGER_PROPAGATES = TESTING


# waffle (feature flags operacionais)
# `Flag`/`Switch`/`Sample` vivem no banco e são editáveis pelo admin — servem de
# kill-switch e rollout interno. Rollout de produto por segmento de usuário é
# papel do PostHog (ver docs/adr/0003).
WAFFLE_FLAG_DEFAULT = False

WAFFLE_SWITCH_DEFAULT = False

WAFFLE_SAMPLE_DEFAULT = False

# Uma flag consultada e inexistente deve falhar fechado, não criar linha no banco.
WAFFLE_CREATE_MISSING_FLAGS = False

WAFFLE_CREATE_MISSING_SWITCHES = False


# prometheus
# Atenção: NÃO trocar o ENGINE do banco para `django_prometheus.db.backends.*`.
# O RLS depende de `django_rls.backends.postgresql` (ver DATABASES) e os dois
# backends são mutuamente exclusivos. Métricas de banco vêm da instrumentação
# do OpenTelemetry (psycopg2), não daqui.
PROMETHEUS_EXPORT_MIGRATIONS = False

# Com múltiplos workers do gunicorn, cada processo mantém seu próprio registro
# em memória e o /metrics devolveria só o do worker que atendeu o scrape. O
# diretório compartilhado é o que consolida os números (ver gunicorn.conf.py).
PROMETHEUS_MULTIPROC_DIR = get_env_var("PROMETHEUS_MULTIPROC_DIR")

if PROMETHEUS_MULTIPROC_DIR:
    os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", PROMETHEUS_MULTIPROC_DIR)
    os.makedirs(PROMETHEUS_MULTIPROC_DIR, exist_ok=True)


# opentelemetry (traces distribuídos)
# Desligado por padrão: as libs estão no grupo opcional `observability` e a
# inicialização é feita em `api/telemetry.py`, chamada pelo ready() do core.
OTEL_ENABLED = get_bool_from_env("OTEL_ENABLED", False)

OTEL_EXPORTER_OTLP_ENDPOINT = get_env_var("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

OTEL_SERVICE_NAME = get_env_var("OTEL_SERVICE_NAME", "drf-base-api")
