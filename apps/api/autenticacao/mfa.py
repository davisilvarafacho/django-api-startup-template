"""Serviços transacionais para enrollment e verificação MFA."""

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

import pyotp
from knox.settings import knox_settings

from .models import AuthToken, MFAChallenge, MFAChallengePurpose, MFAFactor, MFAFactorType, MFARecoveryCode, MFAResetAudit, TokenType, TrustedDevice
from .services import issue_token, lock_eligible_responsible, lock_responsible, revoke_all_sessions

OTP_LIFETIME = timedelta(minutes=5)
OTP_COOLDOWN = timedelta(seconds=60)
RECOVERY_CODE_COUNT = 10
PRE_AUTH_LIFETIME = timedelta(minutes=5)
TRUSTED_DEVICE_LIFETIME = timedelta(days=30)


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


@dataclass(frozen=True)
class LoginResult:
    token: str
    instance: AuthToken
    trusted_device_token: str = ""


@dataclass(frozen=True)
class PlainTrustedDevice:
    instance: TrustedDevice
    plain_token: str


def _otp_digest(code: str) -> str:
    return salted_hmac("mfa-otp", code, secret=settings.SECRET_KEY).hexdigest()


def _new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _trusted_digest(token: str) -> str:
    return salted_hmac("trusted-device", token, secret=settings.SECRET_KEY).hexdigest()


def consume_totp(factor: MFAFactor, code: str, *, now=None) -> bool:
    """Consome um contador TOTP novo, preservando uma janela de um período."""
    if not factor.secret:
        return False
    now = now or timezone.now()
    totp = pyotp.TOTP(factor.secret, digits=factor.totp_digits, interval=factor.totp_period)
    current_counter = totp.timecode(now)
    last_counter = factor.totp_last_counter if factor.totp_last_counter is not None else -1
    for counter in range(max(0, current_counter - 1), current_counter + 2):
        if counter > last_counter and hmac.compare_digest(totp.generate_otp(counter), code):
            factor.totp_last_counter = counter
            factor.last_used_at = now
            factor.save(update_fields=["totp_last_counter", "last_used_at"])
            return True
    return False


def schedule_otp_delivery(challenge: MFAChallenge) -> None:
    """Agenda a entrega somente depois que o desafio for persistido."""
    from .tasks import deliver_mfa_otp

    transaction.on_commit(lambda: deliver_mfa_otp.delay(challenge.pk))


def active_factors(user):
    database_alias = user._state.db or "default"
    return MFAFactor.objects.using(database_alias).filter(
        user=user,
        confirmed_at__isnull=False,
        enabled_at__isnull=False,
        disabled_at__isnull=True,
    )


def available_methods(user) -> list[str]:
    return [
        *active_factors(user).values_list("type", flat=True),
        *(["recovery"] if MFARecoveryCode.objects.filter(user=user, consumed_at__isnull=True).exists() else []),
    ]


def _lock_eligible_user(user):
    """Primeiro lock dos fluxos MFA que podem disputar com exclusão/reset."""
    conta = lock_user_account(user)
    if conta.is_deleted or not conta.is_active:
        raise ValueError("Conta inativa ou excluída.")
    return conta


@transaction.atomic
def start_enrollment(user, factor_type: MFAFactorType) -> EnrollmentResult:
    """Provisiona um fator inativo e o material secreto exibido uma única vez."""
    user = _lock_eligible_user(user)
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

    if factor_type == MFAFactorType.SMS and (not user.phone_number or not user.phone_verified_at):
        raise ValueError("Um telefone verificado é necessário para habilitar SMS.")

    previous = (
        MFAChallenge.objects.select_for_update()
        .filter(user=user, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if previous and previous.created_at > timezone.now() - OTP_COOLDOWN:
        raise ValueError("OTP em cooldown.")
    if previous:
        previous.consumed_at = timezone.now()
        previous.save(update_fields=["consumed_at"])
    challenge = MFAChallenge.objects.create(
        user=user,
        factor=factor,
        purpose=MFAChallengePurpose.ENROLLMENT,
        expires_at=timezone.now() + OTP_LIFETIME,
    )
    schedule_otp_delivery(challenge)
    return EnrollmentResult(factor=factor)


def confirm_enrollment(user, factor_type: MFAFactorType, code: str) -> ConfirmResult:
    """Confirma um fator e cria recovery codes somente no primeiro enrollment."""
    database_alias = user._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_user = lock_eligible_responsible(user, database_alias)
        revoke_trusted_devices(locked_user)
        factor = MFAFactor.objects.using(database_alias).select_for_update().get(user=locked_user, type=factor_type)
        if factor_type == MFAFactorType.TOTP:
            valid = consume_totp(factor, code)
        else:
            challenge = (
                MFAChallenge.objects.using(database_alias)
                .select_for_update()
                .filter(user=locked_user, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT, consumed_at__isnull=True)
                .order_by("-created_at")
                .first()
            )
            valid = (
                bool(challenge)
                and not challenge.is_expired
                and challenge.delivery_status == "sent"
                and hmac.compare_digest(challenge.otp_digest, _otp_digest(code))
            )
            if valid:
                challenge.consumed_at = timezone.now()
                challenge.save(update_fields=["consumed_at"])

        if not valid:
            raise ValueError("Código MFA inválido ou expirado.")

        had_confirmed_factor = (
            MFAFactor.objects.using(database_alias)
            .filter(user=locked_user, confirmed_at__isnull=False, disabled_at__isnull=True)
            .exclude(pk=factor.pk)
            .exists()
        )
        factor.confirmed_at = timezone.now()
        factor.enabled_at = factor.confirmed_at
        factor.disabled_at = None
        factor.save(update_fields=["confirmed_at", "enabled_at", "disabled_at"])

        if had_confirmed_factor:
            return ConfirmResult(factor=factor, recovery_codes=[])

        recovery_codes = regenerate_recovery_codes(locked_user)
        return ConfirmResult(factor=factor, recovery_codes=recovery_codes)


def regenerate_recovery_codes(user) -> list[str]:
    """Substitui o lote anterior por códigos de recuperação de uso único."""
    database_alias = user._state.db or "default"
    codes = [secrets.token_urlsafe(9) for _ in range(RECOVERY_CODE_COUNT)]
    MFARecoveryCode.objects.using(database_alias).filter(user=user).delete()
    MFARecoveryCode.objects.using(database_alias).bulk_create([MFARecoveryCode(user=user, digest=make_password(code)) for code in codes])
    return codes


def remove_factor(user, factor_type: MFAFactorType) -> None:
    database_alias = user._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_user = lock_eligible_responsible(user, database_alias)
        revoke_trusted_devices(locked_user)
        factor = MFAFactor.objects.using(database_alias).select_for_update().get(user=locked_user, type=factor_type)
        factor.delete()


def create_trusted_device(user, metadata: dict) -> PlainTrustedDevice:
    database_alias = user._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_user = lock_eligible_responsible(user, database_alias)
        plain_token = secrets.token_urlsafe(32)
        device = TrustedDevice.objects.using(database_alias).create(
            user=locked_user,
            digest=_trusted_digest(plain_token),
            name=metadata.get("device_name", ""),
            user_agent=metadata.get("user_agent", ""),
            ip_address=metadata.get("ip_address"),
            expires_at=timezone.now() + TRUSTED_DEVICE_LIFETIME,
        )
    return PlainTrustedDevice(instance=device, plain_token=plain_token)


def consume_trusted_device(user, plain_token: str) -> PlainTrustedDevice | None:
    database_alias = user._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_user = lock_eligible_responsible(user, database_alias)
        device = (
            TrustedDevice.objects.using(database_alias)
            .select_for_update()
            .filter(user=locked_user, digest=_trusted_digest(plain_token), revoked_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if not device:
            return None
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at"])
        return create_trusted_device(
            locked_user,
            {"device_name": device.name, "user_agent": device.user_agent, "ip_address": device.ip_address},
        )


def revoke_trusted_devices(user, *, using=None, database_alias=None) -> int:
    """Revoga dispositivos no mesmo banco da operação chamadora."""
    effective_alias = database_alias or using or user._state.db or "default"
    return TrustedDevice.objects.using(effective_alias).filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


@transaction.atomic
def start_login_challenge(pre_auth: AuthToken, factor_type: str) -> MFAChallenge:
    user = _lock_eligible_user(pre_auth.responsavel)
    factor_type = MFAFactorType(factor_type)
    factor = active_factors(user).select_for_update().get(type=factor_type)
    if factor_type == MFAFactorType.TOTP:
        return MFAChallenge.objects.create(
            user=user, factor=factor, token=pre_auth, purpose=MFAChallengePurpose.LOGIN, expires_at=timezone.now() + OTP_LIFETIME
        )
    return create_otp_challenge(user, factor, pre_auth, MFAChallengePurpose.LOGIN)


def _consume_recovery_code(user, code: str) -> bool:
    database_alias = user._state.db or "default"
    for recovery in MFARecoveryCode.objects.using(database_alias).select_for_update().filter(user=user, consumed_at__isnull=True):
        if check_password(code, recovery.digest):
            recovery.consumed_at = timezone.now()
            recovery.save(update_fields=["consumed_at"])
            return True
    return False


def verify_login_challenge(pre_auth: AuthToken, code: str, factor_type: str, *, trust_device: bool, metadata: dict) -> LoginResult:
    user = pre_auth.responsavel
    database_alias = pre_auth._state.db or user._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_user = lock_eligible_responsible(user, database_alias)
        try:
            pre_auth = AuthToken.objects.using(database_alias).select_for_update().get(pk=pre_auth.pk, responsavel=locked_user)
        except AuthToken.DoesNotExist as exc:
            raise ValueError("Pré-autenticação inválida ou expirada.") from exc
        if pre_auth.type != TokenType.PRE_AUTH or pre_auth.is_expired:
            raise ValueError("Pré-autenticação inválida ou expirada.")

        valid = False
        if factor_type == "recovery":
            valid = _consume_recovery_code(locked_user, code)
        else:
            factor = active_factors(locked_user).select_for_update().get(type=MFAFactorType(factor_type))
            challenge = (
                MFAChallenge.objects.using(database_alias)
                .select_for_update()
                .filter(token=pre_auth, factor=factor, purpose=MFAChallengePurpose.LOGIN, consumed_at__isnull=True)
                .order_by("-created_at")
                .first()
            )
            if challenge and not challenge.is_expired and challenge.attempts < 5:
                valid = (
                    consume_totp(factor, code) if factor.type == MFAFactorType.TOTP else hmac.compare_digest(challenge.otp_digest, _otp_digest(code))
                )
                challenge.attempts += 1
                if valid:
                    challenge.consumed_at = timezone.now()
                    factor.last_used_at = timezone.now()
                    factor.save(update_fields=["last_used_at"])
                elif challenge.attempts >= 5:
                    challenge.consumed_at = timezone.now()
                challenge.save(update_fields=["attempts", "consumed_at"])
        if not valid:
            raise ValueError("Código MFA inválido ou expirado.")
        issued = issue_token(
            responsavel=locked_user,
            token_type=TokenType.TOKEN,
            created_by=locked_user,
            expiry=knox_settings.TOKEN_TTL,
            metadata_input={**metadata, "reauthenticated_at": timezone.now()},
        )
        pre_auth.delete(using=database_alias)
        trusted = create_trusted_device(locked_user, metadata) if trust_device else None
        return LoginResult(issued.plain_token, issued.instance, trusted.plain_token if trusted else "")


@transaction.atomic
def start_reauthentication(session: AuthToken, factor_type: str) -> MFAChallenge:
    if session.type != TokenType.TOKEN:
        raise ValueError("Sessão inválida.")
    user = _lock_eligible_user(session.responsavel)
    return start_login_challenge_for(session, factor_type, MFAChallengePurpose.REAUTHENTICATION, user=user)


def start_login_challenge_for(token: AuthToken, factor_type: str, purpose: str, *, user=None) -> MFAChallenge:
    user = user or _lock_eligible_user(token.responsavel)
    factor_type = MFAFactorType(factor_type)
    factor = active_factors(user).select_for_update().get(type=factor_type)
    if factor_type != MFAFactorType.TOTP:
        return create_otp_challenge(user, factor, token, purpose)
    return MFAChallenge.objects.create(
        user=user,
        factor=factor,
        token=token,
        purpose=purpose,
        expires_at=timezone.now() + OTP_LIFETIME,
    )


def create_otp_challenge(user, factor: MFAFactor, token: AuthToken, purpose: str) -> MFAChallenge:
    challenge = MFAChallenge.objects.create(user=user, factor=factor, token=token, purpose=purpose, expires_at=timezone.now() + OTP_LIFETIME)
    schedule_otp_delivery(challenge)
    return challenge


@transaction.atomic
def verify_reauthentication(session: AuthToken, code: str, factor_type: str) -> None:
    if session.type != TokenType.TOKEN:
        raise ValueError("Sessão inválida.")
    user = _lock_eligible_user(session.responsavel)
    valid = _consume_recovery_code(user, code) if factor_type == "recovery" else False
    if factor_type != "recovery":
        factor = active_factors(user).select_for_update().get(type=MFAFactorType(factor_type))
        challenge = (
            MFAChallenge.objects.select_for_update()
            .filter(token=session, factor=factor, purpose=MFAChallengePurpose.REAUTHENTICATION, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        valid = (
            bool(challenge)
            and not challenge.is_expired
            and challenge.attempts < 5
            and (factor.type == MFAFactorType.TOTP or challenge.delivery_status == "sent")
            and (consume_totp(factor, code) if factor.type == MFAFactorType.TOTP else hmac.compare_digest(challenge.otp_digest, _otp_digest(code)))
        )
        if challenge:
            challenge.attempts += 1
            challenge.consumed_at = timezone.now() if valid or challenge.attempts >= 5 else None
            challenge.save(update_fields=["attempts", "consumed_at"])
    if not valid:
        raise ValueError("Código MFA inválido ou expirado.")
    session.metadata.reauthenticated_at = timezone.now()
    session.metadata.save(update_fields=["reauthenticated_at"])


def reset_user_mfa(*, target, actor, reason: str) -> None:
    if not reason.strip():
        raise ValueError("A justificativa é obrigatória.")
    database_alias = target._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked_target = lock_responsible(target, database_alias)
        # Revogação lógica: o registro fica para auditoria. Os `PRE_AUTH`
        # pendentes são efêmeros e não podem sobreviver ao reset.
        revoke_all_sessions(locked_target, actor=actor)
        AuthToken.objects.using(database_alias).filter(responsavel=locked_target, type=TokenType.PRE_AUTH).delete()
        TrustedDevice.objects.using(database_alias).filter(user=locked_target).update(revoked_at=timezone.now())
        MFAFactor.objects.using(database_alias).select_for_update().filter(user=locked_target).delete()
        MFARecoveryCode.objects.using(database_alias).filter(user=locked_target).delete()
        MFAResetAudit.objects.using(database_alias).create(actor=actor, target=locked_target, reason=reason.strip())
