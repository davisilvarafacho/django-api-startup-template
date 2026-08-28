"""Tasks operacionais do ciclo de contas."""

import logging
import time

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError
from django.utils import timezone

from celery import shared_task

from .accounts import anonimizar_contas_vencidas as anonimizar_contas_vencidas_service
from .emails import ACCOUNT_REACTIVATION_PURPOSE, carregar_token_email, normalizar_email
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
def send_account_reactivation(token: str):
    """Entrega o link somente enquanto a conta ainda pode ser reativada."""
    signed_token = carregar_token_email(token, purpose=ACCOUNT_REACTIVATION_PURPOSE)
    if signed_token is None:
        return

    conta = Usuario.objects.filter(pk=signed_token.usuario_id, is_active=False).first()
    if (
        conta is None
        or normalizar_email(conta.email) != signed_token.email
        or (conta.exclusao_agendada_para is not None and conta.exclusao_agendada_para <= timezone.now())
    ):
        return

    try:
        link = f"{settings.ACCOUNT_REACTIVATION_FRONTEND_URL}?token={token}"
        send_mail(
            "Reative sua conta",
            f"Para reativar sua conta, acesse: {link}",
            settings.DEFAULT_FROM_EMAIL,
            [signed_token.email],
        )
    except Exception:
        logger.exception("Falha ao enviar reativação de conta", extra={"user_id": signed_token.usuario_id})
