"""Traces distribuídos com OpenTelemetry.

Desligado por padrão e ativado por `OTEL_ENABLED`. As bibliotecas vivem no grupo
opcional `observability` do `pyproject.toml`, então **todo import fica dentro das
funções**: um import no topo quebraria o boot de qualquer ambiente que não instale
o grupo.

O que é instrumentado: request HTTP (Django), queries (psycopg2), tasks (Celery),
cache/broker (Redis) e chamadas de saída (requests). Os spans saem via OTLP/HTTP
para o Tempo (ver docker-compose.observability.yml).

A correlação com o log é feita pelo `ContextFilter` de `api/logging_config.py`,
que lê o span corrente e escreve `trace_id`/`span_id` em cada linha.
"""

import logging

logger = logging.getLogger(__name__)

_configurada = False


def setup_telemetry():
    """Inicializa o tracer global uma única vez.

    Chamada pelo `ready()` da AppConfig do core. É idempotente porque o `ready()`
    pode rodar mais de uma vez (autoreload do runserver, fork de workers).
    """
    global _configurada

    from django.conf import settings

    if _configurada or not settings.OTEL_ENABLED:
        return

    try:
        instrumentar(settings)
    except ImportError:
        logger.warning("OTEL_ENABLED=True mas as libs não estão instaladas. Rode `uv sync --group observability` ou desligue a telemetria.")
        return

    _configurada = True
    logger.info("OpenTelemetry ativo, exportando para %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)


def instrumentar(settings):
    """Configura o provider e liga as instrumentações automáticas."""
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    recurso = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME,
            "deployment.environment": settings.ENVIROMENT or "development",
        }
    )

    provider = TracerProvider(resource=recurso)

    exportador = OTLPSpanExporter(endpoint=f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces")

    # Em lote: exportar span a span colocaria uma chamada de rede no caminho de
    # cada request.
    provider.add_span_processor(BatchSpanProcessor(exportador))

    trace.set_tracer_provider(provider)

    _instrumentar_bibliotecas()


def _instrumentar_bibliotecas():
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    DjangoInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()
    CeleryInstrumentor().instrument()
    RedisInstrumentor().instrument()
    RequestsInstrumentor().instrument()
