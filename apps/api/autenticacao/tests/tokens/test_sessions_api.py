"""Contrato HTTP de gerenciamento de sessões (`/auth/sessions/`, logout*)."""

from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from internal_frameworks.context import ContextVariable
from tests.support.assinaturas import garantir_assinatura_corrente
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


@pytest.fixture
def usuario():
    return criar_usuario()


def _sessao(usuario, device_name=""):
    instance, token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=instance, device_name=device_name)
    return instance, token


def _api_key(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-sessions-api")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    garantir_assinatura_corrente(organizacao)
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
    )
    TokenMetaData.objects.create(token=instance)
    return instance, token


def _client_com(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_lista_apenas_sessoes_do_usuario_autenticado(usuario):
    outra_pessoa = criar_usuario()
    _, token = _sessao(usuario, device_name="Notebook do usuário")
    _sessao(outra_pessoa, device_name="Notebook de outra pessoa")

    response = _client_com(token).get("/auth/sessions/")

    assert response.status_code == 200
    nomes = [item["device_name"] for item in response.data["resultados"]]
    assert nomes == ["Notebook do usuário"]


def test_lista_nao_inclui_api_keys_nem_expoe_segredo(usuario):
    _, token = _sessao(usuario)
    _api_key(usuario)

    response = _client_com(token).get("/auth/sessions/")

    assert response.status_code == 200
    assert len(response.data["resultados"]) == 1
    item = response.data["resultados"][0]
    assert "digest" not in item
    assert "token_key" not in item
    assert "token" not in item


def test_current_devolve_a_sessao_da_request(usuario):
    instance, token = _sessao(usuario)

    response = _client_com(token).get("/auth/sessions/current/")

    assert response.status_code == 200
    assert response.data["uuid"] == str(instance.uuid)
    assert response.data["is_current"] is True


def test_patch_renomeia_o_dispositivo(usuario):
    instance, token = _sessao(usuario, device_name="Antigo")

    response = _client_com(token).patch(f"/auth/sessions/{instance.uuid}/", {"device_name": "Novo nome"}, format="json")

    assert response.status_code == 200
    instance.metadata.refresh_from_db()
    assert instance.metadata.device_name == "Novo nome"


def test_patch_ignora_campos_alem_de_device_name(usuario):
    instance, token = _sessao(usuario)

    response = _client_com(token).patch(f"/auth/sessions/{instance.uuid}/", {"risk_score": 99}, format="json")

    assert response.status_code == 200
    instance.metadata.refresh_from_db()
    assert instance.metadata.risk_score == 0


def test_delete_revoga_sem_apagar_o_registro(usuario):
    instance, token = _sessao(usuario)
    outra_instancia, outro_token = _sessao(usuario)
    cliente_outra_sessao = _client_com(outro_token)

    response = cliente_outra_sessao.delete(f"/auth/sessions/{instance.uuid}/")

    assert response.status_code == 204
    instance.refresh_from_db()
    assert instance.revoked_at is not None
    assert AuthToken.objects.filter(digest=instance.digest).exists()


def test_sessao_revogada_nao_autentica_mais(usuario):
    instance, token = _sessao(usuario)
    _, outro_token = _sessao(usuario)

    _client_com(outro_token).delete(f"/auth/sessions/{instance.uuid}/")

    response = _client_com(token).get("/auth/sessions/")
    assert response.status_code == 401


def test_logout_revoga_apenas_a_sessao_atual(usuario):
    instance_a, token_a = _sessao(usuario)
    instance_b, token_b = _sessao(usuario)

    response = _client_com(token_a).post("/auth/logout/")

    assert response.status_code == 204
    instance_a.refresh_from_db()
    instance_b.refresh_from_db()
    assert instance_a.revoked_at is not None
    assert instance_b.revoked_at is None


def test_logout_nunca_revoga_api_key_do_mesmo_responsavel(usuario):
    instance_sessao, token_sessao = _sessao(usuario)
    instance_api_key, _ = _api_key(usuario)

    _client_com(token_sessao).post("/auth/logout/")

    instance_api_key.refresh_from_db()
    assert instance_api_key.revoked_at is None


def test_logout_all_revoga_todas_as_sessoes_incluindo_a_atual(usuario):
    instance_a, token_a = _sessao(usuario)
    instance_b, _ = _sessao(usuario)

    response = _client_com(token_a).post("/auth/logout_all/")

    assert response.status_code == 204
    instance_a.refresh_from_db()
    instance_b.refresh_from_db()
    assert instance_a.revoked_at is not None
    assert instance_b.revoked_at is not None


def test_revoke_all_except_current_preserva_a_sessao_atual(usuario):
    instance_atual, token_atual = _sessao(usuario)
    instance_outra, _ = _sessao(usuario)

    response = _client_com(token_atual).post("/auth/sessions/revoke_all_except_current/")

    assert response.status_code == 200
    assert response.data["revoked_count"] == 1
    instance_atual.refresh_from_db()
    instance_outra.refresh_from_db()
    assert instance_atual.revoked_at is None
    assert instance_outra.revoked_at is not None


def test_api_key_nao_acessa_endpoints_de_sessao(usuario):
    _, token = _api_key(usuario)

    response = _client_com(token).get("/auth/sessions/")

    assert response.status_code == 403
