"""Tasks operacionais do domínio de autenticação."""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import OperationalError, transaction
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


@shared_task(name="autenticacao.send_password_reset", ignore_result=True)
def send_password_reset(user_pk: int):
    """Emite o token de redefinição e envia o link por e-mail.

    A emissão acontece aqui, e não na view, para que o token puro exista apenas
    dentro desta task: o payload que trafega pelo broker é só o ID do usuário.
    """
    from apps.usuarios.models import Usuario

    from .passwords import issue_password_reset

    user = Usuario.objects.filter(pk=user_pk, is_active=True).first()
    if user is None:
        return

    issued = issue_password_reset(user)
    if issued is None:
        # A conta foi excluída entre o pedido e a emissão: não há link a enviar.
        return

    link = f"{settings.PASSWORD_RESET_FRONTEND_URL}?token={issued.plain_token}"
    minutos = settings.PASSWORD_RESET_TIMEOUT_MINUTES

    corpo = (
        f"Para escolher uma nova senha, acesse: {link}\n\n"
        f"O link vale {minutos} minutos e só pode ser usado uma vez.\n"
        "Se você não pediu isso, ignore este e-mail."
    )

    try:
        send_mail("Redefinição de senha", corpo, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:
        # O token fica revogado: um link que não chegou ao dono não pode
        # continuar valendo à espera de quem intercepte o e-mail depois.
        logger.exception("Falha ao enviar e-mail de redefinição de senha", extra={"user_id": user_pk})
        issued.instance.revoked_at = timezone.now()
        issued.instance.save(update_fields=["revoked_at"])


@shared_task(name="autenticacao.notify_password_changed", ignore_result=True)
def notify_password_changed(user_pk: int):
    """Avisa o dono da conta que a senha mudou.

    É o único sinal que chega a quem teve a conta invadida e não fez a troca.
    """
    from apps.usuarios.models import Usuario

    user = Usuario.objects.filter(pk=user_pk).first()
    if user is None:
        return

    try:
        send_mail(
            "Sua senha foi alterada",
            "A senha da sua conta acabou de ser alterada e todas as sessões foram encerradas.\n\nSe não foi você, redefina a senha imediatamente.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
    except Exception:
        logger.exception("Falha ao avisar sobre troca de senha", extra={"user_id": user_pk})


@shared_task(name="autenticacao.deliver_mfa_otp", ignore_result=True)
def deliver_mfa_otp(challenge_pk: int):
    """Gera e entrega OTP a partir do ID, sem código no payload Celery."""
    with transaction.atomic():
        challenge = (
            MFAChallenge.objects.select_for_update().select_related("user", "factor").filter(pk=challenge_pk, consumed_at__isnull=True).first()
        )
        if not challenge or challenge.is_expired or challenge.delivery_status != MFAChallengeDeliveryStatus.PENDING:
            return None
        code = _new_otp()
        try:
            if challenge.factor.type == MFAFactorType.SMS:
                get_sms_backend().send_otp(destination=challenge.user.phone_number, code=code, context=challenge.purpose)
            else:
                send_mail("Código de autenticação", f"Seu código é: {code}", settings.DEFAULT_FROM_EMAIL, [challenge.user.email])
        except Exception:
            logger.exception("Falha ao entregar OTP MFA", extra={"challenge_id": challenge.pk})
            challenge.delivery_status = MFAChallengeDeliveryStatus.FAILED
            challenge.save(update_fields=["delivery_status"])
            return None
        challenge.otp_digest = _otp_digest(code)
        challenge.delivery_status = MFAChallengeDeliveryStatus.SENT
        challenge.delivered_at = timezone.now()
        challenge.save(update_fields=["otp_digest", "delivery_status", "delivered_at"])
        return code
