from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.utils import timezone

from rest_framework import status

import pyotp
import pytest

from apps.api.autenticacao.mfa import _otp_digest, confirm_enrollment, consume_totp, start_enrollment
from apps.api.autenticacao.mfa_backends import InMemorySMSBackend
from apps.api.autenticacao.models import (
    MFAChallenge,
    MFAChallengeDeliveryStatus,
    MFAChallengePurpose,
    MFAFactor,
    MFAFactorType,
    MFARecoveryCode,
    TokenType,
)
from apps.api.autenticacao.services import issue_token
from apps.api.autenticacao.tasks import deliver_mfa_otp


@pytest.mark.django_db
def test_totp_so_ativa_depois_da_confirmacao(usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)

    assert enrollment.factor.confirmed_at is None
    assert enrollment.uri.startswith("otpauth://totp/")

    result = confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    assert result.factor.confirmed_at is not None
    assert len(result.recovery_codes) == 10
    assert MFARecoveryCode.objects.filter(user=usuario).count() == 10
    assert not any(code.digest == result.recovery_codes[0] for code in MFARecoveryCode.objects.filter(user=usuario))
    assert any(check_password(result.recovery_codes[0], code.digest) for code in MFARecoveryCode.objects.filter(user=usuario))


@pytest.mark.django_db
def test_confirmar_outro_fator_nao_regenera_recovery_codes(usuario, monkeypatch):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    first = confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    monkeypatch.setattr("apps.api.autenticacao.tasks.deliver_mfa_otp.delay", lambda *args: None)
    email = start_enrollment(usuario, MFAFactorType.EMAIL)
    monkeypatch.setattr("apps.api.autenticacao.tasks.send_mail", lambda *args, **kwargs: 1)
    result = confirm_enrollment(usuario, MFAFactorType.EMAIL, deliver_mfa_otp(MFAChallenge.objects.get(factor=email.factor).pk))

    assert first.recovery_codes
    assert result.recovery_codes == []
    assert MFARecoveryCode.objects.filter(user=usuario).count() == 10


def test_backend_sms_em_memoria_retem_entrega_para_testes():
    InMemorySMSBackend.sent_messages.clear()
    InMemorySMSBackend().send_otp(destination="+5511999999999", code="123456", context="enrollment")

    assert InMemorySMSBackend.sent_messages == [{"destination": "+5511999999999", "code": "123456", "context": "enrollment"}]


@pytest.mark.django_db
def test_totp_rejeita_reuso_do_mesmo_contador(usuario):
    now = timezone.now().replace(microsecond=0)
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.TOTP, secret=pyotp.random_base32())
    code = pyotp.TOTP(factor.secret).at(now)

    assert consume_totp(factor, code, now=now) is True
    assert consume_totp(factor, code, now=now) is False


@pytest.mark.django_db
def test_task_marca_falha_sem_persistir_otp(usuario, monkeypatch):
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)
    challenge = MFAChallenge.objects.create(
        user=usuario,
        factor=factor,
        purpose=MFAChallengePurpose.ENROLLMENT,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    monkeypatch.setattr("apps.api.autenticacao.tasks.send_mail", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("indisponível")))

    deliver_mfa_otp(challenge.pk)

    challenge.refresh_from_db()
    assert challenge.delivery_status == MFAChallengeDeliveryStatus.FAILED
    assert challenge.otp_digest == ""


@pytest.mark.django_db
def test_enrollment_recusa_reenvio_antes_do_cooldown(usuario, monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.tasks.deliver_mfa_otp.delay", lambda *args: None)
    start_enrollment(usuario, MFAFactorType.EMAIL)

    with pytest.raises(ValueError, match="cooldown"):
        start_enrollment(usuario, MFAFactorType.EMAIL)


@pytest.mark.django_db
def test_confirmacao_recusa_desafio_com_entrega_falha(usuario):
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)
    MFAChallenge.objects.create(
        user=usuario,
        factor=factor,
        purpose=MFAChallengePurpose.ENROLLMENT,
        otp_digest=_otp_digest("123456"),
        delivery_status=MFAChallengeDeliveryStatus.FAILED,
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    with pytest.raises(ValueError, match="inválido"):
        confirm_enrollment(usuario, MFAFactorType.EMAIL, "123456")


@pytest.mark.django_db
def test_api_configura_e_confirma_totp_reautenticado(api_client, usuario):
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, created_by=usuario, expiry=None, metadata_input={})
    issued.instance.metadata.reauthenticated_at = timezone.now()
    issued.instance.metadata.save(update_fields=["reauthenticated_at"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    setup = api_client.post("/auth/mfa/factors/totp/setup/")

    assert setup.status_code == status.HTTP_201_CREATED
    code = pyotp.TOTP(setup.data["secret"]).now()
    confirmed = api_client.post("/auth/mfa/factors/totp/confirm/", {"code": code})
    assert confirmed.status_code == status.HTTP_200_OK
    assert len(confirmed.data["recovery_codes"]) == 10
