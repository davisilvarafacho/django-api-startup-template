import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import MFAFactor, MFAFactorType, TokenType
from apps.api.autenticacao.services import issue_token


@pytest.mark.django_db
def test_reauth_com_mfa_so_atualiza_apos_segundo_fator(api_client, usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, expiry=None, metadata_input={})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    started = api_client.post("/auth/reauthenticate/", {"password": "Senha123!"})

    assert started.status_code == 202
    issued.instance.metadata.refresh_from_db()
    assert issued.instance.metadata.reauthenticated_at is None
    assert api_client.post("/auth/reauthenticate/challenge/start/", {"type": "totp"}).status_code == 201
    assert (
        api_client.post("/auth/reauthenticate/challenge/verify/", {"type": "totp", "code": pyotp.TOTP(enrollment.plain_secret).now()}).status_code
        == 204
    )
    issued.instance.metadata.refresh_from_db()
    assert issued.instance.metadata.reauthenticated_at is not None
