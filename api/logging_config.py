"""Configuração de logging da API.

Mesma ideia de `api/configure_enviroment.py`: o `settings.py` continua sendo o
único ponto de entrada, mas a montagem fica isolada aqui — um `LOGGING` completo
inline tomaria mais de cem linhas no meio das settings.

Formato por ambiente:

- **desenvolvimento/teste**: texto legível no console.
- **produção**: JSON em uma linha por evento (stdout + arquivo rotativo). O
  arquivo é o que o Promtail lê para mandar ao Loki; o stdout é o que orquestrador
  (Docker/Kubernetes) coleta.

Todo evento carrega `request_id` e, quando a telemetria está ligada, `trace_id` /
`span_id` — é o que amarra log ↔ Sentry ↔ trace no Tempo.
"""
import logging
import os


class ContextFilter(logging.Filter):
    """Injeta o contexto da request corrente em todo registro de log.

    É um filtro (e não um formatter) porque precisa valer para qualquer handler,
    inclusive o console de desenvolvimento.
    """

    def filter(self, record):
        record.request_id = self._get_request_id()
        record.trace_id, record.span_id = self._get_trace()

        usuario_id, organizacao = self._get_request_context()
        record.usuario_id = usuario_id
        record.organizacao = organizacao

        return True

    def _get_request_id(self):
        from apps.api.core.request_id import get_request_id

        return get_request_id()

    def _get_trace(self):
        """Lê o trace corrente do OpenTelemetry, se a telemetria estiver ativa.

        Import local: `opentelemetry` está no grupo opcional `observability` e
        pode não estar instalado.
        """
        try:
            from opentelemetry import trace
        except ImportError:
            return None, None

        span = trace.get_current_span()
        contexto = span.get_span_context()

        if not contexto.is_valid:
            return None, None

        return format(contexto.trace_id, "032x"), format(contexto.span_id, "016x")

    def _get_request_context(self):
        """Lê usuário e organização da request corrente.

        Reaproveita o `ThreadLocalMiddleware` que a base já usa. A leitura é
        preguiçosa de propósito: com autenticação por token o `request.user` só
        é resolvido no dispatch da view, então fixar o valor em um middleware
        pegaria sempre um usuário anônimo.
        """
        from threadlocals.threadlocals import get_current_request

        request = get_current_request()
        if request is None:
            return None, None

        usuario = getattr(request, "user", None)
        usuario_id = usuario.pk if usuario is not None and usuario.is_authenticated else None

        return usuario_id, getattr(request, "organizacao_slug", None)


# Bibliotecas que logam em DEBUG/INFO a cada chamada HTTP interna e afogam o
# log da aplicação sem agregar nada. Continuam em WARNING (e não silenciadas por
# completo): um retry esgotado do urllib3 ou uma falha de envio do PostHog são
# exatamente o tipo de coisa que se quer ver.
LOGGERS_RUIDOSOS = [
    "asyncio",
    "b2sdk",
    "botocore",
    "httpcore",
    "httpx",
    "posthog",
    "urllib3",
]

# Campos do `LogRecord` expostos no JSON. `request_id`, `trace_id`, `span_id`,
# `usuario_id` e `organizacao` vêm do `ContextFilter`; o que for passado em
# `extra=` entra automaticamente.
FORMATO_JSON = " ".join(
    [
        "%(asctime)s",
        "%(levelname)s",
        "%(name)s",
        "%(message)s",
        "%(module)s",
        "%(funcName)s",
        "%(lineno)d",
        "%(process)d",
        "%(request_id)s",
        "%(trace_id)s",
        "%(span_id)s",
        "%(usuario_id)s",
        "%(organizacao)s",
    ]
)


def build_logging(environment, level, log_root):
    """Monta o dicionário de `LOGGING` para o ambiente informado.

    Args:
        environment: `development`, `production` ou `test`.
        level: nível dos loggers da aplicação (ex.: `INFO`).
        log_root: diretório onde o arquivo de log JSON é escrito.

    Returns:
        dict: configuração no formato `logging.config.dictConfig`.
    """
    em_producao = environment == "production"
    em_teste = environment == "test"

    formatter_padrao = "json" if em_producao else "console"

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": formatter_padrao,
            "filters": ["contexto"],
        },
    }

    handlers_ativos = ["console"]

    if em_producao:
        handlers["arquivo"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(log_root, "api.jsonl"),
            "maxBytes": 1024 * 1024 * 50,  # 50 MB
            "backupCount": 5,
            "formatter": "json",
            "filters": ["contexto"],
        }
        handlers_ativos.append("arquivo")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "contexto": {
                "()": "api.logging_config.ContextFilter",
            },
        },
        "formatters": {
            "console": {
                "format": "[%(asctime)s] %(levelname)s %(name)s [%(request_id)s] %(message)s",
                "datefmt": "%d/%m/%Y %H:%M:%S",
            },
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "format": FORMATO_JSON,
                "rename_fields": {"asctime": "timestamp", "levelname": "level", "name": "logger"},
                "timestamp": False,
            },
        },
        "handlers": handlers,
        "loggers": {
            "django": {
                "handlers": handlers_ativos,
                "level": "INFO",
                "propagate": False,
            },
            # O log de acesso do runserver duplica o `api.access`, que é mais rico.
            "django.server": {
                "handlers": [],
                "level": "WARNING",
                "propagate": False,
            },
            "api": {
                "handlers": handlers_ativos,
                # Em teste o log de acesso a cada request só polui a saída do pytest.
                "level": "WARNING" if em_teste else level,
                "propagate": False,
            },
            "apps": {
                "handlers": handlers_ativos,
                "level": level,
                "propagate": False,
            },
            "celery": {
                "handlers": handlers_ativos,
                "level": "INFO",
                "propagate": False,
            },
            **{
                nome: {"handlers": handlers_ativos, "level": "WARNING", "propagate": False}
                for nome in LOGGERS_RUIDOSOS
            },
        },
        "root": {
            "handlers": handlers_ativos,
            "level": level,
        },
    }
