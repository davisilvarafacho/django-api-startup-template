"""Tasks operacionais das transicoes locais de assinatura."""

import logging
import time

from django.conf import settings
from django.db import OperationalError
from django.utils import timezone

from celery import shared_task

from apps.assinaturas.models import PoliticaTrial, StatusAssinatura
from apps.assinaturas.subscriptions import Assinaturas, ConflitoRevisaoAssinatura, FallbackTrialGratuito, MotivoFallbackTrial
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao

logger = logging.getLogger(__name__)


def _validar_lote(batch_size: int) -> None:
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError("O tamanho do lote precisa ser um inteiro positivo.")


@shared_task(
    name="assinaturas.encerrar_trials_vencidos",
    ignore_result=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def encerrar_trials_vencidos():
    """Converte em gratuito um lote de trials locais vencidos."""
    batch_size = settings.SUBSCRIPTION_TASK_BATCH_SIZE
    _validar_lote(batch_size)
    started_at = time.monotonic()
    agora = timezone.now()
    processadas = 0

    organizacoes = Organizacao.objects.order_by("pk").only("pk").iterator(chunk_size=batch_size)
    for organizacao in organizacoes:
        if processadas >= batch_size:
            break
        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = Assinaturas.obter_corrente(organizacao)
            if (
                assinatura is None
                or assinatura.status != StatusAssinatura.EM_TRIAL
                or assinatura.politica_trial != PoliticaTrial.SEM_FORMA_PAGAMENTO
                or assinatura.trial_termina_em is None
                or assinatura.trial_termina_em > agora
            ):
                continue
            ocupacao = Vinculos.calcular_ocupacao(organizacao, Assinaturas.papeis_isentos_seat(assinatura))
            revisao_anterior = assinatura.revisao
            try:
                resultado = Assinaturas.encerrar_trial(
                    assinatura,
                    resultado=FallbackTrialGratuito(MotivoFallbackTrial.TRIAL_LOCAL_ENCERRADO),
                    ocupacao=ocupacao,
                    agora=agora,
                )
            except ConflitoRevisaoAssinatura:
                # Um evento pode ter encerrado o mesmo trial depois da leitura.
                # O contrato nominalmente bloqueado decide o vencedor da corrida.
                continue
            processadas += int(resultado.assinatura.revisao != revisao_anterior)

    logger.info(
        "Trials locais vencidos processados.",
        extra={
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "processed_count": processadas,
        },
    )
    return processadas


@shared_task(
    name="assinaturas.reconciliar_carencias_seats",
    ignore_result=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def reconciliar_carencias_seats():
    """Reconcilia o excesso real de seats de todos os contratos ativos."""
    batch_size = settings.SUBSCRIPTION_TASK_BATCH_SIZE
    _validar_lote(batch_size)
    started_at = time.monotonic()
    agora = timezone.now()
    processadas = 0

    organizacoes = Organizacao.objects.order_by("pk").only("pk").iterator(chunk_size=batch_size)
    for organizacao in organizacoes:
        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = Assinaturas.obter_corrente(organizacao)
            if assinatura is None or assinatura.status != StatusAssinatura.ATIVA:
                continue
            ocupacao = Vinculos.calcular_ocupacao(organizacao, Assinaturas.papeis_isentos_seat(assinatura))
            revisao_anterior = assinatura.revisao
            reconciliada = Assinaturas.reconciliar_carencia_seats(assinatura, ocupacao, agora=agora)
            processadas += int(reconciliada.revisao != revisao_anterior)

    logger.info(
        "Carencias de excesso de seats reconciliadas.",
        extra={
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "processed_count": processadas,
        },
    )
    return processadas
