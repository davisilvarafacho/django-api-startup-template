import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, create_trusted_device, start_enrollment
from apps.api.autenticacao.models import MFAFactorType


@pytest.mark.django_db
def test_trusted_device_pula_mfa_e_rotaciona(api_client, usuario, monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.views.posthog.tag", lambda *args, **kwargs: None)
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    trusted = create_trusted_device(usuario, {})

    response = api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!", "trusted_device_token": trusted.plain_token})

    assert response.status_code == 200
    assert response.data["trusted_device_token"] != trusted.plain_token
