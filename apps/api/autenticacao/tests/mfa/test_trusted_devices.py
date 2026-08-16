import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, create_trusted_device, start_enrollment
from apps.api.autenticacao.models import MFAFactorType
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db
def test_trusted_device_pula_mfa_e_rotaciona(api_client, usuario, monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.views.posthog.tag", lambda *args, **kwargs: None)
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    trusted = create_trusted_device(usuario, {})

    response = api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!", "trusted_device_token": trusted.plain_token})

    assert response.status_code == 200
    assert response.data["trusted_device_token"] != trusted.plain_token


@pytest.mark.django_db(transaction=True)
def test_alteracao_de_telefone_revoga_dispositivos_confiaveis(usuario):
    trusted = create_trusted_device(usuario, {})

    usuario.phone_number = "+5511999999999"
    usuario.save(update_fields=["phone_number"])

    trusted.instance.refresh_from_db()
    assert trusted.instance.revoked_at is not None


@pytest.mark.django_db
def test_confirmacao_de_fator_revoga_dispositivos_confiaveis(usuario):
    trusted = create_trusted_device(usuario, {})
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)

    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    trusted.instance.refresh_from_db()
    assert trusted.instance.revoked_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize("flags", [{"is_active": False}, {"is_deleted": True}])
def test_dispositivo_confiavel_nao_e_criado_para_conta_inativa_ou_excluida(flags):
    usuario = criar_usuario()
    Usuario.all_objects.filter(pk=usuario.pk).update(**flags)

    assert create_trusted_device(usuario, {}) is None
