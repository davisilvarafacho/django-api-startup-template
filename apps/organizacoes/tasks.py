"""Tasks operacionais do ciclo de organizações."""

import logging
import time

from django.conf import settings
from django.db import OperationalError

from celery import shared_task

from apps.organizacoes.organizations import AssinaturasEncerramento, Organizacoes

logger = logging.getLogger(__name__)


def _carregar_assinaturas() -> type[AssinaturasEncerramento]:
    """Importa o encerrador tenantizado real somente depois da Task 9."""
    from apps.assinaturas.subscriptions import Assinaturas

    return Assinaturas


@shared_task(
    name="organizacoes.efetivar_encerramentos_vencidos",
    ignore_result=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def efetivar_encerramentos_vencidos():
    """Efetiva um lote e registra somente duração e contagem."""
    started_at = time.monotonic()
    processadas = Organizacoes.efetivar_encerramentos_vencidos(
        assinaturas=_carregar_assinaturas(),
        batch_size=settings.ORGANIZATION_CLOSURE_BATCH_SIZE,
    )
    logger.info(
        "Encerramento de organizações vencidas concluído.",
        extra={
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "processed_count": processadas,
        },
    )
