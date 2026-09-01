"""Tarefas Celery do faturamento."""

from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

from celery import shared_task
from django_checkouts import get_checkout_gateway

from .processing import processar_evento, reconciliar_janela


@shared_task(name="faturamento.processar_evento_cobranca", ignore_result=True)
def processar_evento_cobranca(evento_id: int, variante: str, organizacao_id: int | None = None):
    """Processa um evento já roteado; o tenant explícito evita contexto ambíguo."""
    if organizacao_id is None:
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE billing_functions_owner")
            cursor.execute("SELECT organizacao_id FROM evento_cobranca WHERE id=%s AND organizacao_id IS NOT NULL", [evento_id])
            linha = cursor.fetchone()
            cursor.execute("RESET ROLE")
        if linha is None:
            return False
        organizacao_id = linha[0]
    return processar_evento(evento_id, organizacao_id, variante, client=get_checkout_gateway(variante))


@shared_task(name="faturamento.recuperar_eventos_cobranca", ignore_result=True)
def recuperar_eventos_cobranca(limite: int = 100):
    """Pagina eventos abandonados/vencidos pelo caminho operacional global."""
    agora = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute("SET ROLE billing_functions_owner")
        cursor.execute(
            """SELECT id,variante,organizacao_id FROM evento_cobranca
               WHERE organizacao_id IS NOT NULL AND status IN (20,30)
                 AND (proxima_tentativa_em IS NULL OR proxima_tentativa_em <= %s)
               ORDER BY id LIMIT %s""",
            [agora, limite],
        )
        eventos = cursor.fetchall()
        cursor.execute("RESET ROLE")
    for evento_id, variante, organizacao_id in eventos:
        processar_evento_cobranca.delay(evento_id, variante, organizacao_id)
    return len(eventos)


@shared_task(name="faturamento.reconciliar_eventos_stripe", ignore_result=True)
def reconciliar_eventos_stripe():
    """Reconcilia uma janela curta com overlap; dedupe fica na ingestão comum."""
    fim = timezone.now()
    inicio = fim - timedelta(minutes=settings.BILLING_RECONCILIATION_WINDOW_MINUTES)
    client = get_checkout_gateway("stripe")
    return reconciliar_janela(variante="stripe", inicio=inicio, fim=fim, client=client)
