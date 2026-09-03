"""Tarefas Celery do faturamento."""

from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from celery import shared_task
from django_checkouts import get_checkout_gateway

from apps.organizacoes.context import organizacao_atual_privilegiada

from .models import SolicitacaoReconciliacaoCobranca
from .processing import (
    _destino_reconcile,
    processar_evento,
    reabrir_evento_reconciliado,
    reconciliar_duravel,
    reconciliar_janela,
)


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


@shared_task(name="faturamento.reconciliar_evento_cobranca", ignore_result=True)
def reconciliar_evento_cobranca(evento_id: int, variante: str, organizacao_id: int | None = None):
    """Reabre um RECONCILE_FIRST pelo destino autenticado e pelo processador comum."""
    if organizacao_id is None or variante != "stripe":
        return False
    destino = _destino_reconcile(evento_id)
    if destino != organizacao_id:
        return False
    return reabrir_evento_reconciliado(evento_id=evento_id, organizacao_id=organizacao_id) is not None


def _reivindicar_solicitacao(solicitacao_id: int, organizacao_id: int):
    with organizacao_atual_privilegiada(organizacao_id):
        solicitacao = (
            SolicitacaoReconciliacaoCobranca.objects.select_for_update()
            .filter(
                pk=solicitacao_id,
                organizacao_id=organizacao_id,
            )
            .first()
        )
        if solicitacao is None:
            return None
        if solicitacao.resultado == "concluida":
            return int(solicitacao.parametros.get("eventos_ingeridos", 0))
        if solicitacao.resultado != "agendada":
            return None
        solicitacao.resultado = "executando"
        solicitacao.save(update_fields=["resultado", "last_modified_at"])
        return (
            solicitacao.variante,
            solicitacao.janela_inicio,
            solicitacao.janela_fim,
        )


def _finalizar_solicitacao(solicitacao_id: int, organizacao_id: int, *, resultado: str, complemento: dict[str, object]) -> None:
    with organizacao_atual_privilegiada(organizacao_id):
        solicitacao = SolicitacaoReconciliacaoCobranca.objects.select_for_update().get(
            pk=solicitacao_id,
            organizacao_id=organizacao_id,
        )
        parametros = dict(solicitacao.parametros)
        parametros.update(complemento)
        solicitacao.parametros = parametros
        solicitacao.resultado = resultado
        solicitacao.save(update_fields=["parametros", "resultado", "last_modified_at"])


@shared_task(name="faturamento.executar_reconciliacao_operacional", ignore_result=True)
def executar_reconciliacao_operacional(solicitacao_id: int, organizacao_id: int):
    """Executa exatamente a janela auditada e persiste o desfecho sem dados remotos."""
    reivindicacao = _reivindicar_solicitacao(solicitacao_id, organizacao_id)
    if reivindicacao is None or isinstance(reivindicacao, int):
        return reivindicacao if isinstance(reivindicacao, int) else False
    variante, inicio, fim = reivindicacao
    try:
        client = get_checkout_gateway(variante)
        total = reconciliar_janela(variante=variante, inicio=inicio, fim=fim, client=client)
    except Exception as exc:
        _finalizar_solicitacao(
            solicitacao_id,
            organizacao_id,
            resultado="falhou",
            complemento={"erro_codigo": type(exc).__name__},
        )
        raise
    _finalizar_solicitacao(
        solicitacao_id,
        organizacao_id,
        resultado="concluida",
        complemento={"eventos_ingeridos": total},
    )
    return total


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
