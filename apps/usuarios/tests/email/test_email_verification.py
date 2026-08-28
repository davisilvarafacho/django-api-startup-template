from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.cache import cache

import pytest

from apps.usuarios.emails import emitir_token_troca_email, emitir_token_verificacao

pytestmark = pytest.mark.django_db

VERIFY_URL = "/auth/email/verify/"
RESEND_URL = "/auth/email/verification/resend/"


@pytest.fixture(autouse=True)
def _clear_email_verification_cache():
    cache.clear()
    yield
    cache.clear()


def _token_from(message):
    for word in message.body.split():
        if "token=" in word:
            return parse_qs(urlparse(word.strip()).query)["token"][0]
    raise AssertionError(f"Nenhum token encontrado: {message.body!r}")


def _resend_and_get_token(api_client, usuario, django_capture_on_commit_callbacks):
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(RESEND_URL, {"email": usuario.email}, format="json")
    assert response.status_code == 202
    return _token_from(mail.outbox[-1])


def test_verifica_email_com_token_assinado(api_client, usuario, django_capture_on_commit_callbacks):
    token = _resend_and_get_token(api_client, usuario, django_capture_on_commit_callbacks)

    response = api_client.post(VERIFY_URL, {"token": token}, format="json")

    assert response.status_code == 204
    usuario.refresh_from_db()
    assert usuario.email_verificado_em is not None


def test_token_de_troca_nao_serve_para_verificacao(api_client, usuario):
    token = emitir_token_troca_email(usuario, "novo@example.com")

    response = api_client.post(VERIFY_URL, {"token": token}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.email_verification_invalid"


def test_token_de_verificacao_nao_aceita_email_alterado(api_client, usuario):
    token = emitir_token_verificacao(usuario)
    usuario.email = "outro@example.com"
    usuario.save(update_fields=["email"])

    response = api_client.post(VERIFY_URL, {"token": token}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.email_verification_invalid"


def test_token_de_verificacao_expirado_tem_erro_publico_uniforme(api_client, usuario, settings):
    token = emitir_token_verificacao(usuario)
    settings.EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS = -1

    response = api_client.post(VERIFY_URL, {"token": token}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.email_verification_invalid"


def test_replay_de_verificacao_retorna_email_ja_verificado(api_client, usuario):
    token = emitir_token_verificacao(usuario)

    assert api_client.post(VERIFY_URL, {"token": token}, format="json").status_code == 204
    response = api_client.post(VERIFY_URL, {"token": token}, format="json")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "account.email_already_verified"


def test_reenvio_nao_enumera_contas(api_client, usuario):
    existing = api_client.post(RESEND_URL, {"email": usuario.email}, format="json")
    missing = api_client.post(RESEND_URL, {"email": "nao-existe@example.com"}, format="json")

    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json()


def test_reenvio_limita_entregas_no_cache(api_client, usuario, django_capture_on_commit_callbacks):
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        first = api_client.post(RESEND_URL, {"email": usuario.email}, format="json")
    with django_capture_on_commit_callbacks(execute=True):
        second = api_client.post(RESEND_URL, {"email": usuario.email}, format="json")

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(mail.outbox) == 1
