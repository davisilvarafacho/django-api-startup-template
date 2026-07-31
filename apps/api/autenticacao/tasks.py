"""Tasks operacionais do domínio de autenticação."""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError
from django.utils import timezone

from celery import shared_task

from .token_cleanup import cleanup_expired_tokens as cleanup_expired_tokens_service

logger = logging.getLogger(__name__)


@shared_task(
    name="autenticacao.cleanup_expired_tokens",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def cleanup_expired_tokens():
    """Executa a limpeza diária e registra apenas métricas não sensíveis."""
    started_at = time.monotonic()
    result = cleanup_expired_tokens_service(
        now=timezone.now(),
        batch_size=settings.AUTH_TOKEN_CLEANUP_BATCH_SIZE,
        session_retention=timedelta(days=settings.AUTH_TOKEN_SESSION_RETENTION_DAYS),
    )
    logger.info(
        "Limpeza de tokens expirados concluída.",
        extra={
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "examined_by_type": result.examined,
            "deleted_by_type": result.deleted,
        },
    )
    return result
