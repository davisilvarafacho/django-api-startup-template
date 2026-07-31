"""Serviços transacionais para enrollment e verificação MFA."""

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import MFAChallenge, MFAChallengePurpose, MFAFactor, MFAFactorType, MFARecoveryCode


OTP_LIFETIME = timedelta(minutes=5)
RECOVERY_CODE_COUNT = 10


@dataclass(frozen=True)
class EnrollmentResult:
    factor: MFAFactor
    plain_secret: str = ""
    plain_code: str = ""
    uri: str = ""


@dataclass(frozen=True)
class ConfirmResult:
    factor: MFAFactor
    recovery_codes: list[str]


def _otp_digest(code: str) -> str:
    return salted_hmac("mfa-otp", code, secret=settings.SECRET_KEY).hexdigest()


def _new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@transaction.atomic
def start_enrollment(user, factor_type: MFAFactorType) -> EnrollmentResult:
    """Provisiona um fator inativo e o material secreto exibido uma única vez."""
    if factor_type == MFAFactorType.TOTP:
        secret = pyotp.random_base32()
        factor, _ = MFAFactor.objects.select_for_update().get_or_create(user=user, type=factor_type, defaults={"secret": secret})
        factor.secret = secret
        factor.confirmed_at = None
        factor.enabled_at = None
        factor.disabled_at = None
        factor.save(update_fields=["secret", "confirmed_at", "enabled_at", "disabled_at"])
        return EnrollmentResult(
            factor=factor,
            plain_secret=secret,
            uri=pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="DRF Base API"),
        )

    factor, _ = MFAFactor.objects.select_for_update().get_or_create(user=user, type=factor_type)

    if factor_type == MFAFactorType.SMS and not user.phone_number:
        raise ValueError("Um telefone verificado é necessário para habilitar SMS.")

    code = _new_otp()
    MFAChallenge.objects.filter(user=user, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT, consumed_at__isnull=True).update(consumed_at=timezone.now())
    MFAChallenge.objects.create(
        user=user,
        factor=factor,
        purpose=MFAChallengePurpose.ENROLLMENT,
        otp_digest=_otp_digest(code),
        expires_at=timezone.now() + OTP_LIFETIME,
    )
    return EnrollmentResult(factor=factor, plain_code=code)


@transaction.atomic
def confirm_enrollment(user, factor_type: MFAFactorType, code: str) -> ConfirmResult:
    """Confirma um fator e cria recovery codes somente no primeiro enrollment."""
    factor = MFAFactor.objects.select_for_update().get(user=user, type=factor_type)
    if factor_type == MFAFactorType.TOTP:
        valid = bool(factor.secret) and pyotp.TOTP(factor.secret).verify(code, valid_window=1)
    else:
        challenge = (
            MFAChallenge.objects.select_for_update()
            .filter(user=user, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        valid = bool(challenge) and not challenge.is_expired and hmac.compare_digest(challenge.otp_digest, _otp_digest(code))
        if valid:
            challenge.consumed_at = timezone.now()
            challenge.save(update_fields=["consumed_at"])

    if not valid:
        raise ValueError("Código MFA inválido ou expirado.")

    had_confirmed_factor = MFAFactor.objects.filter(user=user, confirmed_at__isnull=False, disabled_at__isnull=True).exclude(pk=factor.pk).exists()
    factor.confirmed_at = timezone.now()
    factor.enabled_at = factor.confirmed_at
    factor.disabled_at = None
    factor.save(update_fields=["confirmed_at", "enabled_at", "disabled_at"])

    if had_confirmed_factor:
        return ConfirmResult(factor=factor, recovery_codes=[])

    recovery_codes = regenerate_recovery_codes(user)
    return ConfirmResult(factor=factor, recovery_codes=recovery_codes)


def regenerate_recovery_codes(user) -> list[str]:
    """Substitui o lote anterior por códigos de recuperação de uso único."""
    codes = [secrets.token_urlsafe(9) for _ in range(RECOVERY_CODE_COUNT)]
    MFARecoveryCode.objects.filter(user=user).delete()
    MFARecoveryCode.objects.bulk_create([MFARecoveryCode(user=user, digest=make_password(code)) for code in codes])
    return codes
