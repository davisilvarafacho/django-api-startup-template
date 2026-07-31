"""Tasks operacionais do domínio de autenticação."""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError
from django.utils import timezone

from celery import shared_task

from .mfa import _new_otp, _otp_digest
from .mfa_backends import get_sms_backend
from .models import MFAChallenge, MFAChallengeDeliveryStatus, MFAFactorType
from .token_cleanup import cleanup_expired_tokens as cleanup_expired_tokens_service

logger = logging.getLogger(__name__)


@shared_task(
    name="autenticacao.cleanup_expired_tokens",
    ignore_result=True,
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


@shared_task(name="autenticacao.deliver_mfa_otp", ignore_result=True)
def deliver_mfa_otp(challenge_pk: int):
    """Gera e entrega OTP a partir do ID, sem código no payload Celery."""
    challenge = MFAChallenge.objects.select_related("user", "factor").filter(pk=challenge_pk, consumed_at__isnull=True).first()
    if not challenge or challenge.is_expired:
        return None
    code = _new_otp()
    challenge.otp_digest = _otp_digest(code)
    try:
        if challenge.factor.type == MFAFactorType.SMS:
            get_sms_backend().send_otp(destination=challenge.user.phone_number, code=code, context=challenge.purpose)
        else:
            send_mail("Código de autenticação", f"Seu código é: {code}", settings.DEFAULT_FROM_EMAIL, [challenge.user.email])
        challenge.delivery_status = MFAChallengeDeliveryStatus.SENT
        challenge.delivered_at = timezone.now()
    except Exception:
        logger.exception("Falha ao entregar OTP MFA", extra={"challenge_id": challenge.pk})
        challenge.delivery_status = MFAChallengeDeliveryStatus.FAILED
    challenge.save(update_fields=["otp_digest", "delivery_status", "delivered_at"])
    return code
