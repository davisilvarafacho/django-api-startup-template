"""Contrato HTTP do ciclo de conta."""

import json
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from rest_framework.test import APIClient

import pyotp
import pytest
from celery.signals import before_task_publish

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import AuthToken, MFAFactor, MFAFactorType, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.accounts import Contas
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

DEACTIVATE_URL = "/account/deactivate/"
DELETION_URL = "/account/deletion/"
REACTIVATION_URL = "/account/reactivation/"
REACTIVATION_CONFIRM_URL = "/account/reactivation/confirm/"


@pytest.fixture(autouse=True)
def _clear_reactivation_cache():
    cache.clear()
    yield
    cache.clear()


def _client_with_session(usuario, *, recent=True):
    token, plain_token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now() if recent else None)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    return client


def _token_from(message):
    for word in message.body.split():
        if "token=" in word:
            return parse_qs(urlparse(word.strip()).query)["token"][0]
    raise AssertionError(f"Nenhum token encontrado: {message.body!r}")


@pytest.mark.parametrize("url", [DEACTIVATE_URL, DELETION_URL])
def test_mutacoes_da_conta_exigem_reautenticacao_recente(url):
    usuario = criar_usuario()

    response = _client_with_session(usuario, recent=False).post(url, format="json")

    assert response.status_code == 401
    usuario.refresh_from_db()
    assert usuario.is_active is True


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7)
def test_rotas_autenticadas_desativam_e_agendam_exclusao():
    desativado = criar_usuario(email="desativar-rota@example.com")
    agendado = criar_usuario(email="excluir-rota@example.com")

    deactivate_response = _client_with_session(desativado).post(DEACTIVATE_URL, format="json")
    deletion_response = _client_with_session(agendado).post(DELETION_URL, format="json")

    assert deactivate_response.status_code == 204
    assert deletion_response.status_code == 202
    assert "scheduled_for" in deletion_response.json()
    desativado.refresh_from_db()
    agendado.refresh_from_db()
    assert desativado.is_active is False
    assert agendado.is_active is False
    assert agendado.exclusao_agendada_para is not None


@override_settings(
    ACCOUNT_REACTIVATION_FRONTEND_URL="https://example.com/reativar",
    ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600,
)
def test_pedido_publico_nao_enumera_e_confirmacao_reativa_sem_restaurar_vinculo(django_capture_on_commit_callbacks):
    usuario = criar_usuario(email="reativacao-rota@example.com")
    organizacao = Organizacao.objects.create(nome="Organização", slug="reativacao-rota")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    Contas.desativar(usuario)
    mail.outbox.clear()
    client = APIClient()

    with django_capture_on_commit_callbacks(execute=True):
        existente = client.post(REACTIVATION_URL, {"email": usuario.email}, format="json")
    with django_capture_on_commit_callbacks(execute=True):
        ausente = client.post(REACTIVATION_URL, {"email": "ausente@example.com"}, format="json")

    assert existente.status_code == ausente.status_code == 202
    assert existente.json() == ausente.json()
    assert len(mail.outbox) == 1
    token = _token_from(mail.outbox[0])

    response = client.post(REACTIVATION_CONFIRM_URL, {"token": token}, format="json")

    assert response.status_code == 204
    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    assert usuario.is_active is True
    assert vinculo.is_active is False


@override_settings(
    ACCOUNT_REACTIVATION_FRONTEND_URL="https://example.com/reativar",
    ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600,
    CELERY_BROKER_URL="memory://",
    CELERY_TASK_ALWAYS_EAGER=False,
)
def test_pedido_publica_somente_id_tecnico_e_task_entrega_link_valido(django_capture_on_commit_callbacks, monkeypatch):
    from apps.usuarios import accounts, tasks

    usuario = criar_usuario(email="segredo-no-broker@example.com")
    Contas.desativar(usuario)
    mail.outbox.clear()
    mensagens = []

    def capturar_mensagem(sender=None, body=None, headers=None, **kwargs):
        if sender == tasks.send_account_reactivation.name:
            mensagens.append({"body": body, "headers": headers})

    before_task_publish.connect(capturar_mensagem, weak=False)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(accounts, "emitir_token_reativacao", lambda conta: "token-secreto-no-broker", raising=False)
            with django_capture_on_commit_callbacks(execute=True):
                response = APIClient().post(REACTIVATION_URL, {"email": usuario.email}, format="json")
    finally:
        before_task_publish.disconnect(capturar_mensagem)

    mensagem_publicada = json.dumps(mensagens, default=str)
    assert response.status_code == 202
    assert mensagens[0]["body"][0] == (usuario.pk,)
    assert "token-secreto-no-broker" not in mensagem_publicada
    assert usuario.email not in mensagem_publicada

    tasks.send_account_reactivation.run(usuario.pk)
    token = _token_from(mail.outbox[0])

    confirmacao = APIClient().post(REACTIVATION_CONFIRM_URL, {"token": token}, format="json")
    assert confirmacao.status_code == 204


def test_confirmacao_publica_invalida_usa_erro_generico(api_client):
    response = api_client.post(REACTIVATION_CONFIRM_URL, {"token": "invalido"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.reactivation_invalid"


@override_settings(ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600)
def test_falha_de_entrega_nao_registra_token_ou_email(monkeypatch, caplog):
    from apps.usuarios import tasks

    usuario = criar_usuario(email="nao-logar@example.com")
    Contas.desativar(usuario)
    tokens = []

    def falhar_envio(*args, **kwargs):
        token = _token_from(type("Message", (), {"body": args[1]})())
        tokens.append(token)
        raise RuntimeError(f"backend incluiu {usuario.email} e {token}")

    monkeypatch.setattr(tasks, "send_mail", falhar_envio)
    caplog.set_level("ERROR", logger="apps.usuarios.tasks")

    tasks.send_account_reactivation.run(usuario.pk)

    assert tokens[0] not in caplog.text
    assert usuario.email not in caplog.text


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7)
def test_exclusao_com_mfa_exige_o_segundo_fator_da_reautenticacao(api_client, usuario, django_capture_on_commit_callbacks):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        created_by=usuario,
        expiry=None,
        metadata_input={},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    assert api_client.post("/auth/reauthenticate/", {"password": "Senha123!"}, format="json").status_code == 202
    assert api_client.post(DELETION_URL, format="json").status_code == 401
    assert api_client.post("/auth/reauthenticate/challenge/start/", {"type": "totp"}, format="json").status_code == 201
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            "/auth/reauthenticate/challenge/verify/",
            {"type": "totp", "code": pyotp.TOTP(enrollment.plain_secret).now()},
            format="json",
        )
    assert response.status_code == 204
    assert api_client.post(DELETION_URL, format="json").status_code == 202
