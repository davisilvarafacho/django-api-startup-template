import pyotp
import pytest
from cryptography.fernet import Fernet
from django.contrib.auth.hashers import check_password

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.mfa_backends import InMemorySMSBackend
from apps.api.autenticacao.models import MFAFactorType, MFARecoveryCode


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())


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
def test_confirmar_outro_fator_nao_regenera_recovery_codes(usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    first = confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    email = start_enrollment(usuario, MFAFactorType.EMAIL)
    result = confirm_enrollment(usuario, MFAFactorType.EMAIL, email.plain_code)

    assert first.recovery_codes
    assert result.recovery_codes == []
    assert MFARecoveryCode.objects.filter(user=usuario).count() == 10


def test_backend_sms_em_memoria_retem_entrega_para_testes():
    InMemorySMSBackend.sent_messages.clear()
    InMemorySMSBackend().send_otp(destination="+5511999999999", code="123456", context="enrollment")

    assert InMemorySMSBackend.sent_messages == [{"destination": "+5511999999999", "code": "123456", "context": "enrollment"}]
