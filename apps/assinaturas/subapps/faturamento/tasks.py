"""Tarefas Celery do faturamento."""

from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from celery import shared_task
from django_checkouts import get_checkout_gateway

from .processing import processar_evento, reconciliar_duravel


@shared_task(name="faturamento.processar_evento_cobranca", ignore_result=True)
def processar_evento_cobranca(evento_id: int, variante: str, organizacao_id: int | None = None):
    """Processa um evento já roteado; o tenant explícito evita contexto ambíguo."""
    if organizacao_id is None:
        return False
    return processar_evento(evento_id, organizacao_id, variante, client=get_checkout_gateway(variante))


@shared_task(name="faturamento.recuperar_eventos_cobranca", ignore_result=True)
def recuperar_eventos_cobranca(limite: int = 100):
    """Pagina eventos abandonados/vencidos pelo caminho operacional global."""
    agora = timezone.now()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
        cursor.execute("SELECT set_config('rls.tenant_id','0',true)")
        cursor.execute("SELECT set_config('rls.billing_ingress','1',true)")
        cursor.execute(
            "SELECT evento_id,variante,organizacao_id FROM faturamento_claim_recovery(%s,%s)",
            [limite, agora],
        )
        eventos = cursor.fetchall()
    for evento_id, variante, organizacao_id in eventos:
        processar_evento_cobranca.delay(evento_id, variante, organizacao_id)
    return len(eventos)


@shared_task(name="faturamento.reconciliar_eventos_stripe", ignore_result=True)
def reconciliar_eventos_stripe():
    """Reconcilia uma janela curta com overlap; dedupe fica na ingestão comum."""
    client = get_checkout_gateway("stripe")
    return reconciliar_duravel(
        variante="stripe",
        client=client,
        janela=timedelta(minutes=settings.BILLING_RECONCILIATION_WINDOW_MINUTES),
        overlap=timedelta(minutes=settings.BILLING_RECONCILIATION_OVERLAP_MINUTES),
    )
