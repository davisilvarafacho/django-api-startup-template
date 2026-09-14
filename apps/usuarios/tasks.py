"""Tasks operacionais do ciclo de contas."""

import logging
import time

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError
from django.utils import timezone

from celery import shared_task

from .accounts import anonimizar_contas_vencidas as anonimizar_contas_vencidas_service
from .emails import emitir_token_reativacao
from .models import Usuario

logger = logging.getLogger(__name__)


@shared_task(
    name="usuarios.anonimizar_contas_vencidas",
    ignore_result=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def anonimizar_contas_vencidas():
    """Anonimiza um lote vencido e registra apenas duração e contagem."""
    started_at = time.monotonic()
    processadas = anonimizar_contas_vencidas_service(batch_size=settings.ACCOUNT_DELETION_BATCH_SIZE)
    logger.info(
        "Anonimização de contas vencidas concluída.",
        extra={
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "processed_count": processadas,
        },
    )


@shared_task(name="usuarios.send_account_reactivation", ignore_result=True)
def send_account_reactivation(user_id: int):
    """Relê a conta e só então gera e entrega o segredo de reativação."""
    conta = Usuario.objects.filter(pk=user_id, is_active=False).first()
    if conta is None or (conta.exclusao_agendada_para is not None and conta.exclusao_agendada_para <= timezone.now()):
        return

    try:
        token = emitir_token_reativacao(conta)
        link = f"{settings.ACCOUNT_REACTIVATION_FRONTEND_URL}?token={token}"
        send_mail(
            "Reative sua conta",
            f"Para reativar sua conta, acesse: {link}",
            settings.DEFAULT_FROM_EMAIL,
            [conta.email],
        )
    except Exception:
        # Exceções de backends de e-mail podem incluir recipient ou conteúdo da
        # mensagem. Não anexe traceback/exception: o link assinado nunca pode
        # chegar ao log.
        logger.error("Falha ao enviar reativação de conta", extra={"user_id": user_id})
