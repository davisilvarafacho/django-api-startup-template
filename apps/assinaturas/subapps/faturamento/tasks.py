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
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE billing_functions_owner")
            cursor.execute("SELECT organizacao_id FROM evento_cobranca WHERE id=%s AND organizacao_id IS NOT NULL", [evento_id])
            linha = cursor.fetchone()
        if linha is None:
            return False
        organizacao_id = linha[0]
    return processar_evento(evento_id, organizacao_id, variante, client=get_checkout_gateway(variante))


@shared_task(name="faturamento.recuperar_eventos_cobranca", ignore_result=True)
def recuperar_eventos_cobranca(limite: int = 100):
    """Pagina eventos abandonados/vencidos pelo caminho operacional global."""
    agora = timezone.now()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute(
            """SELECT id,variante,organizacao_id,identificador_assinatura,identificador_checkout
               FROM evento_cobranca
               WHERE status IN (10,20,30)
                 AND (proxima_tentativa_em IS NULL OR proxima_tentativa_em <= %s)
               ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s""",
            [agora, limite],
        )
        eventos = cursor.fetchall()
    for evento_id, variante, organizacao_id, assinatura_externa, checkout_externo in eventos:
        if organizacao_id is None:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE billing_functions_owner")
                cursor.execute(
                    """SELECT organizacao_id FROM assinatura_gateway
                       WHERE variante=%s AND identificador_externo=%s AND is_deleted=false
                       UNION ALL
                       SELECT organizacao_id FROM checkout_cobranca
                       WHERE variante=%s AND identificador_externo=%s AND is_deleted=false LIMIT 2""",
                    [variante, assinatura_externa, variante, checkout_externo],
                )
                destinos = {linha[0] for linha in cursor.fetchall()}
            if len(destinos) == 1:
                destino = destinos.pop()
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
                    cursor.execute("SELECT set_config('rls.tenant_id','0',true)")
                    cursor.execute("SELECT set_config('rls.billing_ingress','1',true)")
                    cursor.execute("SELECT faturamento_rotear_evento_destino(%s,%s)", [evento_id, destino])
                processar_evento_cobranca.delay(evento_id, variante, destino)
        else:
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
