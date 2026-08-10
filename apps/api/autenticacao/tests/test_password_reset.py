"""Testes do reset de senha para usuário deslogado.

O fluxo tem duas metades: `request` (anônimo, não pode revelar se o e-mail
existe) e `confirm` (troca a senha e derruba as credenciais antigas).
"""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.utils import timezone

from rest_framework.throttling import ScopedRateThrottle

import pytest
from knox.models import get_token_model

from apps.api.autenticacao.models import MFAFactor, MFAFactorType, TokenType
from tests.support.usuarios import criar_usuario

AuthToken = get_token_model()

URL_REQUEST = "/auth/password/reset/request/"
URL_CONFIRM = "/auth/password/reset/confirm/"

SENHA_NOVA = "NovaSenha456!"


def extrair_token(mensagem):
    """Lê o token do link enviado por e-mail."""
    for palavra in mensagem.body.split():
        if "token=" in palavra:
            return parse_qs(urlparse(palavra.strip()).query)["token"][0]
    raise AssertionError(f"nenhum link com token no corpo: {mensagem.body!r}")


def payload_confirm(token, senha=SENHA_NOVA):
    return {"token": token, "new_password": senha, "new_password_confirmation": senha}


@pytest.fixture
def reset_token(api_client, usuario):
    """Dispara um reset real e devolve o token puro entregue por e-mail."""
    mail.outbox.clear()
    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")
    return extrair_token(mail.outbox[-1])


@pytest.mark.django_db
def test_request_nao_enumera_conta(api_client, usuario):
    existente = api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")
    inexistente = api_client.post(URL_REQUEST, {"email": "ninguem@example.com"}, format="json")

    assert existente.status_code == inexistente.status_code == 202
    assert existente.json() == inexistente.json()


@pytest.mark.django_db
def test_request_so_envia_email_para_conta_existente(api_client, usuario):
    mail.outbox.clear()
    api_client.post(URL_REQUEST, {"email": "ninguem@example.com"}, format="json")
    assert mail.outbox == []

    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [usuario.email]


@pytest.mark.django_db
def test_request_ignora_usuario_inativo_sem_denunciar(api_client, usuario):
    usuario.is_active = False
    usuario.save(update_fields=["is_active"])
    mail.outbox.clear()

    resposta = api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")

    assert resposta.status_code == 202
    assert mail.outbox == []
    assert not AuthToken.objects.filter(responsavel=usuario, type=TokenType.RESET_PASSWORD).exists()


@pytest.mark.django_db
def test_request_e_case_insensitive_no_email(api_client, usuario):
    mail.outbox.clear()

    resposta = api_client.post(URL_REQUEST, {"email": usuario.email.upper()}, format="json")

    assert resposta.status_code == 202
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_request_revoga_reset_anterior(api_client, usuario):
    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")
    primeiro = AuthToken.objects.get(responsavel=usuario, type=TokenType.RESET_PASSWORD)

    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")

    primeiro.refresh_from_db()
    assert primeiro.revoked_at is not None
    ativos = AuthToken.objects.filter(responsavel=usuario, type=TokenType.RESET_PASSWORD, revoked_at__isnull=True)
    assert ativos.count() == 1


@pytest.mark.django_db
def test_request_emite_token_com_ttl_de_30_minutos(api_client, usuario, settings):
    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")

    token = AuthToken.objects.get(responsavel=usuario, type=TokenType.RESET_PASSWORD)
    esperado = timezone.now() + timedelta(minutes=settings.PASSWORD_RESET_TIMEOUT_MINUTES)
    assert abs((token.expiry - esperado).total_seconds()) < 60


@pytest.mark.django_db
def test_request_nao_coloca_token_puro_no_payload_da_task(api_client, usuario, monkeypatch):
    """O broker não pode transportar credencial: a task recebe só o ID."""
    from apps.api.autenticacao import tasks

    capturado = {}

    def fake_delay(*args, **kwargs):
        capturado.update(args=args, kwargs=kwargs)

    monkeypatch.setattr(tasks.send_password_reset, "delay", fake_delay)

    api_client.post(URL_REQUEST, {"email": usuario.email}, format="json")

    assert capturado["args"] == (usuario.pk,)
    assert capturado["kwargs"] == {}


@pytest.mark.django_db
def test_confirm_troca_a_senha(api_client, usuario, reset_token):
    resposta = api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    assert resposta.status_code == 204
    usuario.refresh_from_db()
    assert usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_consumo_unico(api_client, reset_token):
    assert api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json").status_code == 204
    assert api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json").status_code == 422


@pytest.mark.django_db
def test_confirm_recusa_token_expirado(api_client, usuario, reset_token):
    AuthToken.objects.filter(responsavel=usuario, type=TokenType.RESET_PASSWORD).update(expiry=timezone.now() - timedelta(minutes=1))

    resposta = api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_recusa_token_invalido(api_client, usuario):
    resposta = api_client.post(URL_CONFIRM, payload_confirm("token-que-nunca-existiu"), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_recusa_sessao_como_token_de_reset(api_client, usuario):
    """Uma sessão válida não pode ser usada para trocar a senha sem saber a antiga."""
    from apps.api.autenticacao.services import issue_token

    sessao = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        created_by=usuario,
        expiry=timedelta(days=1),
        metadata_input={},
    )

    resposta = api_client.post(URL_CONFIRM, payload_confirm(sessao.plain_token), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_recusa_confirmacao_divergente(api_client, usuario, reset_token):
    resposta = api_client.post(
        URL_CONFIRM,
        {"token": reset_token, "new_password": SENHA_NOVA, "new_password_confirmation": "OutraCoisa789!"},
        format="json",
    )

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_aplica_a_politica_de_senha(api_client, usuario, reset_token):
    resposta = api_client.post(URL_CONFIRM, payload_confirm(reset_token, senha="123"), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert usuario.check_password("Senha123!")


@pytest.mark.django_db
def test_confirm_recusa_usuario_inativado_depois_da_emissao(api_client, usuario, reset_token):
    usuario.is_active = False
    usuario.save(update_fields=["is_active"])

    resposta = api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    assert resposta.status_code == 422
    usuario.refresh_from_db()
    assert not usuario.check_password(SENHA_NOVA)


@pytest.mark.django_db
def test_confirm_nao_exige_mfa(api_client, usuario, reset_token):
    """Quem perdeu a senha pode ter perdido o segundo fator junto."""
    MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL, confirmed_at=timezone.now())

    resposta = api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    assert resposta.status_code == 204


@pytest.mark.django_db
def test_confirm_revoga_sessoes_ativas(api_client, usuario, reset_token):
    from apps.api.autenticacao.services import issue_token

    sessao = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        created_by=usuario,
        expiry=timedelta(days=1),
        metadata_input={},
    )

    api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    sessao.instance.refresh_from_db()
    assert sessao.instance.revoked_at is not None


@pytest.mark.django_db
def test_confirm_revoga_dispositivos_confiaveis(api_client, usuario, reset_token):
    from apps.api.autenticacao.mfa import create_trusted_device
    from apps.api.autenticacao.models import TrustedDevice

    create_trusted_device(usuario, {})

    api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    assert not TrustedDevice.objects.filter(user=usuario, revoked_at__isnull=True).exists()


@pytest.mark.django_db
def test_request_tem_throttle_proprio(api_client, usuario, monkeypatch):
    """Cada tentativa dispara um e-mail; sem throttle o endpoint vira canhão de spam.

    O rate precisa ser trocado no `THROTTLE_RATES` da classe: o DRF resolve esse
    dicionário no import e um `override_settings` de `REST_FRAMEWORK` não o
    alcança.
    """
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_password_reset", "2/min")

    respostas = [api_client.post(URL_REQUEST, {"email": usuario.email}, format="json").status_code for _ in range(3)]

    assert respostas[:2] == [202, 202]
    assert respostas[-1] == 429


@pytest.mark.django_db
def test_confirm_tem_throttle_proprio(api_client, usuario, reset_token, monkeypatch):
    """Sem throttle aqui, o token de 30 minutos vira alvo de força bruta."""
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_password_reset", "2/min")

    respostas = [api_client.post(URL_CONFIRM, payload_confirm("token-errado"), format="json").status_code for _ in range(3)]

    assert respostas[-1] == 429


@pytest.mark.django_db
def test_rotas_de_reset_sao_publicas(api_client):
    """Sem token e sem X-Organization, ninguém consegue redefinir a senha."""
    resposta = api_client.post(URL_REQUEST, {"email": "qualquer@example.com"}, format="json")

    assert resposta.status_code == 202


@pytest.mark.django_db
def test_reset_de_um_usuario_nao_afeta_o_outro(api_client, usuario, reset_token):
    outro = criar_usuario(password="Senha123!")

    api_client.post(URL_CONFIRM, payload_confirm(reset_token), format="json")

    outro.refresh_from_db()
    assert outro.check_password("Senha123!")
