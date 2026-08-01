"""Testes da troca de senha por usuário autenticado.

A regra central: trocar a senha derruba todas as credenciais de sessão,
inclusive a que fez a troca. Se a troca aconteceu porque a conta pode estar
comprometida, manter viva a credencial que a fez anularia o motivo dela.
"""

from datetime import timedelta

from django.core import mail
from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.models import (
    MFAFactor,
    MFAFactorType,
    MFARecoveryCode,
    TokenMetaData,
    TokenType,
    TrustedDevice,
)
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()

URL = "/auth/password/change/"
SENHA_NOVA = "NovaSenha456!"


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def client_com_sessao(usuario, *, reauthenticated=True):
    instance, token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(
        token=instance,
        reauthenticated_at=timezone.now() if reauthenticated else None,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client, instance


def payload(senha=SENHA_NOVA, confirmacao=None):
    return {"new_password": senha, "new_password_confirmation": confirmacao or senha}


def test_troca_a_senha(usuario):
    client, _ = client_com_sessao(usuario)

    resposta = client.post(URL, payload(), format="json")

    assert resposta.status_code == 204
    usuario.refresh_from_db()
    assert usuario.check_password(SENHA_NOVA)


def test_revoga_inclusive_a_sessao_atual(usuario):
    client, sessao_atual = client_com_sessao(usuario)
    outra, _ = client_com_sessao(usuario)

    assert client.post(URL, payload(), format="json").status_code == 204

    assert not AuthToken.objects.filter(
        responsavel=usuario,
        type__in=[TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD],
        revoked_at__isnull=True,
    ).exists()
    sessao_atual.refresh_from_db()
    assert sessao_atual.revoked_at is not None


def test_sessao_revogada_nao_serve_mais(usuario):
    client, _ = client_com_sessao(usuario)
    client.post(URL, payload(), format="json")

    resposta = client.get("/auth/sessions/")

    assert resposta.status_code == 401


def test_exige_reautenticacao_recente(usuario):
    client, _ = client_com_sessao(usuario, reauthenticated=False)

    resposta = client.post(URL, payload(), format="json")

    assert resposta.status_code == 401
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


def test_reautenticacao_expirada_nao_vale(usuario):
    client, instance = client_com_sessao(usuario)
    instance.metadata.reauthenticated_at = timezone.now() - timedelta(hours=1)
    instance.metadata.save(update_fields=["reauthenticated_at"])

    resposta = client.post(URL, payload(), format="json")

    assert resposta.status_code == 401
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


def test_exige_autenticacao():
    resposta = APIClient().post(URL, payload(), format="json")

    assert resposta.status_code == 401


def test_recusa_confirmacao_divergente(usuario):
    client, _ = client_com_sessao(usuario)

    resposta = client.post(URL, payload(confirmacao="OutraCoisa789!"), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


def test_aplica_a_politica_de_senha(usuario):
    client, _ = client_com_sessao(usuario)

    resposta = client.post(URL, payload(senha="123"), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert usuario.check_password("Senha123!")


def test_senha_recusada_nao_revoga_nada(usuario):
    """A revogação e a gravação vivem na mesma transação."""
    client, sessao = client_com_sessao(usuario)

    client.post(URL, payload(senha="123"), format="json")

    sessao.refresh_from_db()
    assert sessao.revoked_at is None


def test_revoga_dispositivos_confiaveis(usuario):
    from apps.api.autenticacao.mfa import create_trusted_device

    client, _ = client_com_sessao(usuario)
    create_trusted_device(usuario, {})

    client.post(URL, payload(), format="json")

    assert not TrustedDevice.objects.filter(user=usuario, revoked_at__isnull=True).exists()


def test_preserva_fatores_mfa_e_recovery_codes(usuario):
    """Trocar a senha não é motivo para desmontar o segundo fator."""
    client, _ = client_com_sessao(usuario)
    MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL, confirmed_at=timezone.now())
    MFARecoveryCode.objects.create(user=usuario, digest="a" * 128)

    client.post(URL, payload(), format="json")

    assert MFAFactor.objects.filter(user=usuario, confirmed_at__isnull=False).exists()
    assert MFARecoveryCode.objects.filter(user=usuario).exists()


def test_preserva_api_keys(usuario):
    """API key pertence à integração, não à sessão humana: derrubá-la quebraria produção."""
    client, _ = client_com_sessao(usuario)
    organizacao = Organizacao.objects.create(nome="Org", slug="org-troca-senha")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    api_key, _plain = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
    )
    TokenMetaData.objects.create(token=api_key)

    client.post(URL, payload(), format="json")

    api_key.refresh_from_db()
    assert api_key.revoked_at is None


def test_avisa_o_dono_da_conta(usuario, django_capture_on_commit_callbacks):
    """O aviso sai por `on_commit`: transação que der rollback não manda e-mail."""
    client, _ = client_com_sessao(usuario)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        client.post(URL, payload(), format="json")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [usuario.email]


def test_nao_avisa_quando_a_troca_falha(usuario, django_capture_on_commit_callbacks):
    client, _ = client_com_sessao(usuario)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        client.post(URL, payload(senha="123"), format="json")

    assert mail.outbox == []


def test_nao_afeta_outro_usuario(usuario):
    client, _ = client_com_sessao(usuario)
    outro = UsuarioFactory(password="Senha123!")
    _, sessao_do_outro = client_com_sessao(outro)

    client.post(URL, payload(), format="json")

    sessao_do_outro.refresh_from_db()
    assert sessao_do_outro.revoked_at is None
    outro.refresh_from_db()
    assert outro.check_password("Senha123!")


def test_nao_exige_organizacao(usuario):
    """Trocar a senha é ato de conta, não de tenant: não pode exigir X-Organization."""
    client, _ = client_com_sessao(usuario)

    resposta = client.post(URL, payload(), format="json")

    assert resposta.status_code == 204
