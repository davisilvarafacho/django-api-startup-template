from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.db import close_old_connections, connections
from django.utils import timezone

from rest_framework.test import APIClient

import pyotp
import pytest
from knox.models import get_token_model

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import MFAFactor, MFAFactorType, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

CHANGE_URL = "/account/email/change/"
CONFIRM_URL = "/account/email/change/confirm/"
AuthToken = get_token_model()


def _client_with_recent_session(usuario):
    token, plain_token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now())
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    return client, token


def _token_from(message):
    for word in message.body.split():
        if "token=" in word:
            return parse_qs(urlparse(word.strip()).query)["token"][0]
    raise AssertionError(f"Nenhum token encontrado: {message.body!r}")


def _request_change(client, email, django_capture_on_commit_callbacks):
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(CHANGE_URL, {"email": email}, format="json")
    assert response.status_code == 202
    return _token_from(mail.outbox[-1])


def test_solicitacao_mantem_email_atual_ate_confirmacao(usuario, django_capture_on_commit_callbacks):
    client, _ = _client_with_recent_session(usuario)

    token = _request_change(client, "novo@example.com", django_capture_on_commit_callbacks)

    usuario.refresh_from_db()
    assert usuario.email != "novo@example.com"
    assert token


def test_confirmacao_troca_email_confirma_e_revoga_todas_credenciais(usuario, django_capture_on_commit_callbacks):
    client, current_session = _client_with_recent_session(usuario)
    previous_email = usuario.email
    other_session, _ = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=other_session)
    organization = Organizacao.objects.create(nome="Organização", slug="org-email-change")
    Vinculo.objects.create(usuario=usuario, organizacao=organization, papel=Papel.ADMINISTRADOR)
    api_key, _ = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organization,
        name="Integração",
    )
    TokenMetaData.objects.create(token=api_key)
    token = _request_change(client, "novo@example.com", django_capture_on_commit_callbacks)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(CONFIRM_URL, {"token": token}, format="json")

    assert response.status_code == 204
    usuario.refresh_from_db()
    assert usuario.email == "novo@example.com"
    assert usuario.email_verificado_em is not None
    assert not AuthToken.objects.filter(responsavel=usuario, revoked_at__isnull=True).exists()
    current_session.refresh_from_db()
    assert current_session.revoked_at is not None
    api_key.refresh_from_db()
    assert api_key.revoked_at is not None
    assert {message.to[0] for message in mail.outbox} == {"novo@example.com", previous_email}


@pytest.mark.django_db(transaction=True)
def test_confirmacoes_concorrentes_disputando_o_mesmo_email_tem_um_unico_vencedor(django_capture_on_commit_callbacks):
    first = criar_usuario(email="first@example.com")
    second = criar_usuario(email="second@example.com")
    first_client, _ = _client_with_recent_session(first)
    second_client, _ = _client_with_recent_session(second)
    first_token = _request_change(first_client, "Compartilhado@example.com", django_capture_on_commit_callbacks)
    second_token = _request_change(second_client, "compartilhado@example.com", django_capture_on_commit_callbacks)

    barrier = Barrier(2)

    def confirm(token):
        close_old_connections()
        try:
            barrier.wait()
            response = APIClient().post(CONFIRM_URL, {"token": token}, format="json")
            return response.status_code, response.json() if response.status_code != 204 else {}
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(confirm, (first_token, second_token)))

    assert sorted(status for status, _ in responses) == [204, 409]
    loser_payload = next(payload for status, payload in responses if status == 409)
    assert loser_payload["errors"][0]["code"] == "account.email_already_in_use"


def test_solicitacao_exige_reautenticacao_recente(usuario):
    client, token = _client_with_recent_session(usuario)
    token.metadata.reauthenticated_at = None
    token.metadata.save(update_fields=["reauthenticated_at"])

    response = client.post(CHANGE_URL, {"email": "novo@example.com"}, format="json")

    assert response.status_code == 401


def test_solicitacao_com_mfa_exige_o_segundo_fator_de_reautenticacao(api_client, usuario, django_capture_on_commit_callbacks):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, created_by=usuario, expiry=None, metadata_input={})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    assert api_client.post("/auth/reauthenticate/", {"password": "Senha123!"}, format="json").status_code == 202
    assert api_client.post(CHANGE_URL, {"email": "novo-com-mfa@example.com"}, format="json").status_code == 401
    assert api_client.post("/auth/reauthenticate/challenge/start/", {"type": "totp"}, format="json").status_code == 201
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            "/auth/reauthenticate/challenge/verify/",
            {"type": "totp", "code": pyotp.TOTP(enrollment.plain_secret).now()},
            format="json",
        )
    assert response.status_code == 204
    assert api_client.post(CHANGE_URL, {"email": "novo-com-mfa@example.com"}, format="json").status_code == 202


def test_confirmacao_invalida_nao_revela_estado_da_conta(api_client):
    response = api_client.post(CONFIRM_URL, {"token": "invalido"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.email_verification_invalid"
