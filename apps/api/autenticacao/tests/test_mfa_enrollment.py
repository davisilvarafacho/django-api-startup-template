from django.contrib.auth.hashers import check_password
from django.utils import timezone

from rest_framework import status

import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.mfa_backends import InMemorySMSBackend
from apps.api.autenticacao.models import MFAChallenge, MFAFactorType, MFARecoveryCode, TokenType
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
def test_api_configura_e_confirma_totp_reautenticado(api_client, usuario):
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, expiry=None, metadata_input={})
    issued.instance.metadata.reauthenticated_at = timezone.now()
    issued.instance.metadata.save(update_fields=["reauthenticated_at"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    setup = api_client.post("/auth/mfa/factors/totp/setup/")

    assert setup.status_code == status.HTTP_201_CREATED
    code = pyotp.TOTP(setup.data["secret"]).now()
    confirmed = api_client.post("/auth/mfa/factors/totp/confirm/", {"code": code})
    assert confirmed.status_code == status.HTTP_200_OK
    assert len(confirmed.data["recovery_codes"]) == 10
