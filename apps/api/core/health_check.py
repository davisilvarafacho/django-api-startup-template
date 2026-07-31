"""Health checks de liveness e readiness.

Separar os dois é o que evita o pior modo de falha em orquestrador: se o
endpoint que decide "matar o container" tocar o banco, uma indisponibilidade
momentânea do Postgres derruba **toda** a frota em vez de apenas tirá-la do
balanceador.

- `/health/` — *liveness*: o processo responde? Sem I/O, sempre 200.
- `/health/ready/` — *readiness*: as dependências respondem? 200 ou 503.

Ambas são públicas (ver o registry em `apps.api.core.routes_registry`): o
orquestrador não tem token.
"""

import logging
import time

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import connection
from django.http import JsonResponse

from rest_framework import status

logger = logging.getLogger(__name__)

CHAVE_CACHE_HEALTH = "health-check"


def health_check(request):
    """Liveness: responde sem tocar em nenhuma dependência externa."""
    return JsonResponse({"ok": True}, status=status.HTTP_200_OK)


def readiness_check(request):
    """Readiness: verifica banco, cache, broker do Celery e storage."""
    resultados = {
        "banco": _executar(_checar_banco),
        "cache": _executar(_checar_cache),
        "broker": _executar(_checar_broker),
        "storage": _executar(_checar_storage),
    }

    ok = all(resultado["ok"] for resultado in resultados.values())

    if not ok:
        falhas = [nome for nome, resultado in resultados.items() if not resultado["ok"]]
        logger.error("Readiness falhou: %s", ", ".join(falhas), extra={"checks": resultados})

    codigo = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE

    return JsonResponse({"ok": ok, "checks": resultados}, status=codigo)


def _executar(checagem):
    """Roda uma checagem isolando a falha e medindo a duração.

    Nenhuma checagem pode derrubar o endpoint: o valor de um readiness está em
    dizer *qual* dependência caiu, não em propagar a exceção.
    """
    iniciada_em = time.monotonic()

    try:
        checagem()
    except Exception as exc:
        return {
            "ok": False,
            "erro": f"{type(exc).__name__}: {exc}",
            "duracao_ms": _duracao_ms(iniciada_em),
        }

    return {"ok": True, "duracao_ms": _duracao_ms(iniciada_em)}


def _duracao_ms(iniciada_em):
    return round((time.monotonic() - iniciada_em) * 1000, 2)


def _checar_banco():
    connection.ensure_connection()

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _checar_cache():
    # Escreve e lê de volta: um Redis em modo somente-leitura (failover a meio
    # caminho) responde ao PING mas não serve como cache.
    cache.set(CHAVE_CACHE_HEALTH, "ok", timeout=30)

    if cache.get(CHAVE_CACHE_HEALTH) != "ok":
        raise RuntimeError("o cache não devolveu o valor gravado")


def _checar_broker():
    """Verifica a conexão com o broker, não os workers.

    `control.ping()` responderia sobre os workers, mas custa um round-trip com
    timeout em toda checagem e transforma "nenhum worker no ar" em "API fora",
    que são incidentes diferentes.
    """
    from api.celery import app

    conexao = app.connection()
    try:
        conexao.ensure_connection(max_retries=0, timeout=3)
    finally:
        conexao.release()


def _checar_storage():
    # `exists("")` consulta o backend sem escrever nada; no B2 isso vale um
    # round-trip autenticado, que é exatamente o que se quer validar.
    default_storage.exists("")
