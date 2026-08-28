"""Vínculo explícito e desvínculo seguro de uma identidade Google."""

from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.models import IdentidadeExterna, ProvedorIdentidade
from tests.support.usuarios import criar_usuario

from .helpers import CLIENT_ID, claims_google, client_com_sessao, substituir_verificador

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _configurar_client_google(settings):
    settings.GOOGLE_OAUTH_CLIENT_IDS = [CLIENT_ID]


def test_conectar_exige_email_local_verificado():
    usuario = criar_usuario(email="pendente@example.com")
    client = client_com_sessao(usuario)

    with substituir_verificador(claims=claims_google(sub="sub-pendente", email="outra@gmail.com")):
        response = client.post("/auth/google/connect/", {"id_token": "token-connect"}, format="json")

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "account.email_not_verified"
    assert IdentidadeExterna.objects.filter(usuario=usuario).exists() is False


def test_conectar_exige_autenticacao_recente():
    usuario = criar_usuario(email="sem-recent@example.com", email_verificado_em=timezone.now())
    client = client_com_sessao(usuario, recente=False)

    with substituir_verificador(claims=claims_google(sub="sub-sem-recent", email="outra@gmail.com")):
        response = client.post("/auth/google/connect/", {"id_token": "token-connect"}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.reauthentication_required"
    assert IdentidadeExterna.objects.filter(usuario=usuario).exists() is False


def test_conectar_vincula_o_sub_ao_usuario_autenticado():
    usuario = criar_usuario(email="verificado@example.com", email_verificado_em=timezone.now())
    client = client_com_sessao(usuario)

    with substituir_verificador(claims=claims_google(sub="sub-conectado", email="outra@gmail.com")):
        response = client.post("/auth/google/connect/", {"id_token": "token-connect"}, format="json")

    assert response.status_code == 204
    identidade = IdentidadeExterna.objects.get(usuario=usuario)
    assert identidade.provedor == ProvedorIdentidade.GOOGLE
    assert identidade.identificador == "sub-conectado"


def test_conectar_recusa_identidade_que_pertence_a_outra_conta():
    dono = criar_usuario(email="dono@example.com")
    IdentidadeExterna.objects.create(usuario=dono, provedor=ProvedorIdentidade.GOOGLE, identificador="sub-ocupado")
    usuario = criar_usuario(email="outra-conta@example.com", email_verificado_em=timezone.now())
    client = client_com_sessao(usuario)

    with substituir_verificador(claims=claims_google(sub="sub-ocupado", email="dono@gmail.com")):
        response = client.post("/auth/google/connect/", {"id_token": "token-ocupado"}, format="json")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "account.external_identity_conflict"
    assert IdentidadeExterna.objects.filter(usuario=usuario).exists() is False


def test_desconectar_remove_vinculo_quando_senha_local_e_utilizavel():
    usuario = criar_usuario(email="com-senha@example.com", password="senha-local-forte")
    identidade = IdentidadeExterna.objects.create(usuario=usuario, provedor=ProvedorIdentidade.GOOGLE, identificador="sub-removivel")
    client = client_com_sessao(usuario)

    response = client.delete("/auth/google/disconnect/")

    assert response.status_code == 204
    assert IdentidadeExterna.objects.filter(pk=identidade.pk).exists() is False
    assert IdentidadeExterna.all_objects.get(pk=identidade.pk).is_deleted is True
    usuario.refresh_from_db()
    assert usuario.check_password("senha-local-forte") is True


def test_desconectar_preserva_o_unico_meio_de_login():
    usuario = criar_usuario(email="somente-google@example.com")
    usuario.set_unusable_password()
    usuario.save(update_fields=["password"])
    identidade = IdentidadeExterna.objects.create(usuario=usuario, provedor=ProvedorIdentidade.GOOGLE, identificador="sub-unico")
    client = client_com_sessao(usuario)

    response = client.delete("/auth/google/disconnect/")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "account.external_identity_last_login"
    assert IdentidadeExterna.objects.filter(pk=identidade.pk).exists() is True


def test_desconectar_exige_autenticacao_recente():
    usuario = criar_usuario(email="disconnect-sem-recent@example.com")
    identidade = IdentidadeExterna.objects.create(usuario=usuario, provedor=ProvedorIdentidade.GOOGLE, identificador="sub-sem-recent")
    client = client_com_sessao(usuario, recente=False)

    response = client.delete("/auth/google/disconnect/")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.reauthentication_required"
    assert IdentidadeExterna.objects.filter(pk=identidade.pk).exists() is True


def test_conectar_sem_sessao_e_recusado_antes_de_validar_google():
    response = APIClient().post("/auth/google/connect/", {"id_token": "token"}, format="json")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.token_not_provided"
