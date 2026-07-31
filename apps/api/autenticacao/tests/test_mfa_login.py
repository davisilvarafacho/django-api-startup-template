import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import AuthToken, MFAFactorType, TokenType


@pytest.mark.django_db
def test_login_com_totp_emite_pre_auth_e_conclui(api_client, usuario, monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.views.posthog.tag", lambda *args, **kwargs: None)
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    login = api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!"})

    assert login.status_code == 202
    assert login.data["methods"] == ["totp", "recovery"]
    assert AuthToken.objects.get(responsavel=usuario).type == TokenType.PRE_AUTH

    api_client.credentials(HTTP_AUTHORIZATION=f"PreAuth {login.data['pre_auth_token']}")
    assert api_client.post("/auth/mfa/challenge/start/", {"type": "totp"}).status_code == 201
    verify = api_client.post("/auth/mfa/challenge/verify/", {"type": "totp", "code": pyotp.TOTP(enrollment.plain_secret).now(), "trust_device": True})
    assert verify.status_code == 200, verify.content
    assert verify.data["token"]
    assert verify.data["trusted_device_token"]


@pytest.mark.django_db
def test_pre_auth_nao_acessa_rota_normal(api_client, usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    login = api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!"})
    api_client.credentials(HTTP_AUTHORIZATION=f"PreAuth {login.data['pre_auth_token']}")

    assert api_client.get("/auth/tokens/").status_code == 401
