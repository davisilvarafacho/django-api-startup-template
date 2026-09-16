import os
import pathlib
import sys
import warnings
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

import sentry_sdk
from celery.schedules import crontab

from api.configure_enviroment import configure_enviroment
from api.logging_config import build_logging
from utils.env import get_bool_from_env, get_env_var, get_int_from_env, get_list_from_env

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

SECRET_KEY = get_env_var("DJANGO_SECRET_KEY")

DEBUG = get_bool_from_env("DJANGO_DEBUG", True)

ENVIROMENT = get_env_var("DJANGO_ENVIRONMENT")

IN_DEVELOPMENT = ENVIROMENT == "development"

IN_PRODUCTION = ENVIROMENT == "production"

IN_TEST = ENVIROMENT == "test"

EXECUTION = get_env_var("DJANGO_EXECUTION_MODE")

BILLING_DATABASE_MODE = get_env_var("BILLING_DATABASE_MODE", "web")
if BILLING_DATABASE_MODE not in {"web", "ingress", "migration"}:
    raise ImproperlyConfigured("BILLING_DATABASE_MODE deve ser 'web', 'ingress' ou 'migration'.")

# ambiente efetivo usado para carregar apps/middlewares/storages específicos.
# sempre resolve para um valor suportado por `configure_enviroment`.
TESTING = IN_TEST or "pytest" in sys.modules or "test" in sys.argv

if TESTING:
    CONFIG_ENVIRONMENT = "test"
elif IN_PRODUCTION:
    CONFIG_ENVIRONMENT = "production"
else:
    CONFIG_ENVIRONMENT = "development"

ENV_MIDDLEWARES, ENV_APPS, ENV_STORAGES = configure_enviroment(CONFIG_ENVIRONMENT)
if BILLING_DATABASE_MODE == "ingress":
    # O ingresso público não carrega ferramentas de debug/profiling/log de
    # request, que poderiam persistir o corpo e a assinatura do webhook.
    ENV_MIDDLEWARES = []
    ENV_APPS = []


if not SECRET_KEY and not IN_PRODUCTION:
    warnings.warn("'SECRET_KEY' não foi configurada, using a random temporary key.", stacklevel=2)
    SECRET_KEY = get_random_secret_key()


def SENTRY_BEFORE_SEND(event, hint):
    """Descarta eventos de webhook e remove autenticação dos demais eventos."""
    del hint
    request = event.get("request") or {}
    url = str(request.get("url") or request.get("path") or "")
    if "/faturamento/webhooks/" in url:
        return None
    headers = request.get("headers") or {}
    for header in tuple(headers):
        if header.casefold() in {"authorization", "stripe-signature"}:
            headers.pop(header, None)
    return event


if IN_PRODUCTION:
    sentry_sdk.init(
        dsn=get_env_var("SENTRY_DSN"),
        environment=ENVIROMENT,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=True,
        before_send=SENTRY_BEFORE_SEND,
    )


ALLOWED_HOSTS = get_list_from_env("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost"])

CSRF_TRUSTED_ORIGINS = get_list_from_env("DJANGO_CSRF_TRUSTED_ORIGINS", ["http://127.0.0.1:8000", "http://localhost:8000"])

MCP_SERVER_URL = get_env_var("MCP_SERVER_URL")
MCP_AUTH_ISSUER_URL = get_env_var("MCP_AUTH_ISSUER_URL")
MCP_AUTH_AUDIENCE = get_env_var("MCP_AUTH_AUDIENCE")
MCP_AUTH_JWKS_URL = get_env_var("MCP_AUTH_JWKS_URL")
MCP_AUTH_ALGORITHMS = get_list_from_env("MCP_AUTH_ALGORITHMS", ["RS256"])
MCP_ALLOWED_HOSTS = get_list_from_env("MCP_ALLOWED_HOSTS")
MCP_ALLOWED_ORIGINS = get_list_from_env("MCP_ALLOWED_ORIGINS")

GOOGLE_OAUTH_CLIENT_IDS = get_list_from_env("GOOGLE_OAUTH_CLIENT_IDS")

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

INTERNAL_IPS = get_list_from_env(
    "DJANGO_INTERNAL_IPS",
    [
        "127.0.0.1",
        "::1",
    ],
)


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
    "django_checkouts",
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
    "apps.api.mcp_server",
    "apps.api.metadata",
    "apps.assinaturas",
    "apps.assinaturas.subapps.faturamento",
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
        # Publica a request e limpa todas as ContextVars na saída, depois de os
        # middlewares internos restaurarem seus próprios tokens.
        "apps.api.core.context.RequestContextMiddleware",
        # Logo após o middleware de contexto para que todo log emitido daqui em
        # diante já carregue o correlation id.
        "apps.api.core.request_id.RequestIDMiddleware",
        "django.middleware.gzip.GZipMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        "corsheaders.middleware.CorsMiddleware",
        # Resolve o token antes dos middlewares que dependem de `request.user`
        # (auditlog, PostHog, tenancy).
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

CORS_EXPOSE_HEADERS = ["Deprecation", "Sunset", "Link"]


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
            "NAME": get_env_var("TEST_DATABASE_NAME", "base_test"),
        },
        "CONN_MAX_AGE": 60 * 60 * 3,  # 3 horas
        "CONN_HEALTH_CHECKS": True,
    },
    "logging": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "logging_db.sqlite3"),
    },
}

# As migrations de faturamento transferem funções SECURITY DEFINER para uma
# role NOLOGIN dedicada. O alias só existe no processo de migration, impedindo
# que web/worker sequer tenham a credencial DDL disponível.
_billing_migration_user = get_env_var("BILLING_MIGRATION_DATABASE_USER")
_billing_migration_password = get_env_var("BILLING_MIGRATION_DATABASE_PASSWORD")
if BILLING_DATABASE_MODE == "migration" and _billing_migration_user and _billing_migration_password:
    DATABASES["billing_migration"] = {
        **DATABASES["default"],
        "USER": _billing_migration_user,
        "PASSWORD": _billing_migration_password,
        "TEST": {"MIRROR": "default"},
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

# Verificação e troca de e-mail usam tokens assinados, sem tabela ou e-mail
# pendente persistido. Os dois propósitos têm salts e tempos de vida distintos.
EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS = int(get_env_var("EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS", 60 * 60))
EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS = int(get_env_var("EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS", 60 * 60))
EMAIL_VERIFICATION_FRONTEND_URL = get_env_var("EMAIL_VERIFICATION_FRONTEND_URL", "http://localhost:3000/verificar-email")
EMAIL_CHANGE_FRONTEND_URL = get_env_var("EMAIL_CHANGE_FRONTEND_URL", "http://localhost:3000/confirmar-troca-email")

# Ciclo da conta: o pedido permanece reversível durante a carência; só a task
# periódica torna a exclusão definitiva por anonimização.
ACCOUNT_DELETION_GRACE_DAYS = get_int_from_env("ACCOUNT_DELETION_GRACE_DAYS", 7)
ACCOUNT_DELETION_BATCH_SIZE = get_int_from_env("ACCOUNT_DELETION_BATCH_SIZE", 100)
ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS = get_int_from_env("ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS", 60 * 60)
ACCOUNT_REACTIVATION_FRONTEND_URL = get_env_var("ACCOUNT_REACTIVATION_FRONTEND_URL", "http://localhost:3000/reativar-conta")

# O catálogo e o contrato real são ligados ao orquestrador de onboarding na
# Task 9. Até lá, estes valores alimentam apenas a seam tipada do caso de uso.
ASSINATURAS_ONBOARDING_MODO = get_env_var("ASSINATURAS_ONBOARDING_MODO", "gratuito")
ASSINATURAS_ONBOARDING_PLANO = get_env_var("ASSINATURAS_ONBOARDING_PLANO", "gratuito")
ASSINATURAS_ONBOARDING_PERIODICIDADE = get_env_var("ASSINATURAS_ONBOARDING_PERIODICIDADE", "mensal")
ORGANIZATION_CLOSURE_BATCH_SIZE = get_int_from_env("ORGANIZATION_CLOSURE_BATCH_SIZE", 100)
SUBSCRIPTION_TASK_BATCH_SIZE = get_int_from_env("SUBSCRIPTION_TASK_BATCH_SIZE", 100)

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
# auth.W004: `Usuario.email` é o USERNAME_FIELD e não tem `unique=True` — de
# propósito. A unicidade é parcial (`usuario_email_unico_nao_excluido`), para o
# e-mail voltar a ficar livre após a exclusão lógica; como o `_default_manager`
# esconde os excluídos, `get_by_natural_key()` continua enxergando um único
# registro por e-mail.
SILENCED_SYSTEM_CHECKS = ["guardian.W001", "auth.W004"]
if CONFIG_ENVIRONMENT in {"development", "test"}:
    # O quickstart local pode subir antes de o Stripe ser configurado. A
    # integração continua indisponível em runtime; produção nunca recebe esta
    # exceção e permanece fail-closed.
    SILENCED_SYSTEM_CHECKS.append("django_checkouts.E001")
if CONFIG_ENVIRONMENT == "test":
    # Os gates determinísticos não fazem I/O com o gateway.
    SILENCED_SYSTEM_CHECKS.append("faturamento.E001")

# Não criar o usuário anônimo do guardian (o modelo de usuário usa e-mail como
# username e o isolamento por organização torna esse registro desnecessário).
ANONYMOUS_USER_NAME = None

# Proteção contra força bruta no login. A chave de bloqueio é o par
# usuário + IP (E lógico): bloquear só por usuário permitiria que qualquer um
# trancasse a conta alheia, e bloquear só por IP puniria clientes atrás de NAT.
# Ver .ai/brainstorming/spec/2026-07-30-django-axes-design.md.
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
    # Evita reabrir a conexão de teste durante o teardown; em produção permanece ativo.
    "RESET_CONTEXT_ON_CONNECT": not TESTING,
    "REGISTERED_CONTEXT_KEYS": ("billing_ingress",),
}

CHECKOUT_VARIANTS = {
    "stripe": (
        "django_checkouts.gateways.stripe.StripeGateway",
        {
            "api_key": get_env_var("STRIPE_API_KEY"),
            "webhook_secret": get_env_var("STRIPE_WEBHOOK_SECRET"),
            "sandbox": get_bool_from_env("STRIPE_SANDBOX", not IN_PRODUCTION),
        },
    )
}
_BILLING_LOCAL_URL = "http://localhost:3000/assinatura/checkout"
BILLING_CHECKOUT_SUCCESS_URL = get_env_var("BILLING_CHECKOUT_SUCCESS_URL", "" if IN_PRODUCTION else f"{_BILLING_LOCAL_URL}/sucesso")
BILLING_CHECKOUT_CANCEL_URL = get_env_var("BILLING_CHECKOUT_CANCEL_URL", "" if IN_PRODUCTION else f"{_BILLING_LOCAL_URL}/cancelado")
BILLING_INGRESS_DATABASE_ROLE = get_env_var("BILLING_INGRESS_DATABASE_ROLE", "billing_ingress_runtime")
BILLING_DATABASE_OWNER_ROLE = get_env_var("BILLING_DATABASE_OWNER_ROLE", "billing_functions_owner")
if BILLING_DATABASE_MODE == "ingress":
    # O processo exposto apenas ao endpoint público de webhook não carrega o
    # restante da superfície HTTP, mesmo se o proxy for contornado na rede.
    ROOT_URLCONF = "api.billing_ingress_urls"


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


LOGGING_ROOT = os.path.join(BASE_DIR, "logs/")

os.makedirs(LOGGING_ROOT, exist_ok=True)

LOG_LEVEL = get_env_var("DJANGO_LOG_LEVEL", "INFO")

JSON_LOG_FILE_ENABLED = get_bool_from_env("DJANGO_JSON_LOG_FILE_ENABLED", False)

# Em produção sai JSON (uma linha por evento, lido pelo Alloy/Loki); nos demais
# ambientes, o arquivo JSON é opcional. Ver api/logging_config.py.
LOGGING = build_logging(
    CONFIG_ENVIRONMENT,
    LOG_LEVEL,
    LOGGING_ROOT,
    write_json_file=JSON_LOG_FILE_ENABLED,
)


# base
# O auditlog escreve no model apontado por esta setting. Sem ela os signals
# gravariam em `auditlog.LogEntry` e a tabela `log_alteracao` — que a API expõe
# em `GET /<recurso>/<id>/logs/` — ficaria permanentemente vazia. O model
# apontado é excluído do próprio registro pelo auditlog, então não há recursão.
AUDITLOG_LOGENTRY_MODEL = "logs.LogAlteracao"

# `logs.LogAlteracao` e `autenticacao.AuthToken` possuem os schemas locais;
# não crie as tabelas substituídas dos pacotes.
MIGRATION_MODULES = {"auditlog": None, "knox": None}

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
        "auth_email_verification": "5/min",
        "account_reactivation": "5/min",
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    # Nenhuma URL do projeto vive sob namespace ainda, então sem uma versão padrão
    # `determine_version()` devolve `None` e o drf-spectacular descarta *todas* as
    # operações do schema (`paths` sai vazio). A versão só é usada para reverse de
    # URL versionada — que o projeto não faz —, então isto é inerte em runtime.
    "DEFAULT_VERSION": "v1",
    # Subclasse do AutoSchema do drf-spectacular que publica a depreciação
    # declarada por `@api_deprecated` (ver `apps.api.core.deprecation`).
    "DEFAULT_SCHEMA_CLASS": "apps.api.core.deprecation.DeprecationAwareAutoSchema",
    "EXCEPTION_HANDLER": "apps.api.core.errors.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DATE_INPUT_FORMATS": ["%d/%m/%Y"],
}


# openapi / scalar
SPECTACULAR_SETTINGS = {
    "TITLE": "DRF Base API",
    "DESCRIPTION": "Documentação da API.",
    "VERSION": "1.0.0",  # x-release-please-version
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

# metadata genérico (JSON key-value por objeto)
# Limites por objeto; ver docs/reference/metadata.md.
METADATA_MAX_KEYS = get_int_from_env("METADATA_MAX_KEYS", 50)
METADATA_MAX_KEY_LENGTH = get_int_from_env("METADATA_MAX_KEY_LENGTH", 256)
METADATA_MAX_VALUE_LENGTH = get_int_from_env("METADATA_MAX_VALUE_LENGTH", 1024)

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

CELERY_TASK_DEFAULT_QUEUE = "celery"

CELERY_TASK_ROUTES = {
    "faturamento.recuperar_eventos_cobranca": {"queue": "billing_ingress"},
    "faturamento.reconciliar_eventos_stripe": {"queue": "billing_ingress"},
    "faturamento.reconciliar_evento_cobranca": {"queue": "billing_ingress"},
    "faturamento.executar_reconciliacao_operacional": {"queue": "billing_ingress"},
    # O processador usa ORM sob um tenant conhecido e fica no worker web,
    # deliberadamente sem membership na role operacional de ingresso.
    "faturamento.processar_evento_cobranca": {"queue": "celery"},
}

AUTH_TOKEN_SESSION_RETENTION_DAYS = 90

AUTH_TOKEN_CLEANUP_BATCH_SIZE = 500

CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-auth-tokens": {
        "task": "autenticacao.cleanup_expired_tokens",
        "schedule": crontab(hour=0, minute=0),
    },
    "anonymize-expired-accounts": {
        "task": "usuarios.anonimizar_contas_vencidas",
        "schedule": crontab(hour=0, minute=30),
    },
    "close-expired-organizations": {
        "task": "organizacoes.efetivar_encerramentos_vencidos",
        "schedule": crontab(hour=1, minute=0),
    },
    "finish-expired-subscription-trials": {
        "task": "assinaturas.encerrar_trials_vencidos",
        "schedule": crontab(minute=5),
    },
    "reconcile-subscription-seat-graces": {
        "task": "assinaturas.reconciliar_carencias_seats",
        "schedule": crontab(minute="*/15"),
    },
    "recover-billing-events": {
        "task": "faturamento.recuperar_eventos_cobranca",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "billing_ingress"},
    },
    "reconcile-stripe-events": {
        "task": "faturamento.reconciliar_eventos_stripe",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "billing_ingress"},
    },
}

BILLING_RECONCILIATION_WINDOW_MINUTES = 20
BILLING_RECONCILIATION_OVERLAP_MINUTES = 5

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
