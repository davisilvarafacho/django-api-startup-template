"""Matriz de autenticação/tenancy de API keys: estado, tipo e organização."""

from datetime import timedelta

from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def _limpar_thread_locals(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def _client_com_api_key(*, responsavel, organizacao, scopes=("teams:read",), **campos):
    instance, token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
        scopes=list(scopes),
        **campos,
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _vincular(usuario, organizacao, papel=Papel.MEMBRO):
    return Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)


def _organizacao(slug="org-tenancy"):
    return Organizacao.objects.create(nome="Org", slug=slug)


def test_api_key_ativa_autentica_sem_precisar_do_header():
    usuario = criar_usuario()
    organizacao = _organizacao()
    _vincular(usuario, organizacao)

    response = _client_com_api_key(responsavel=usuario, organizacao=organizacao).get("/times/")

    assert response.status_code == 200


def test_api_key_com_header_coincidente_autentica():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-coincidente")
    _vincular(usuario, organizacao)

    response = _client_com_api_key(responsavel=usuario, organizacao=organizacao).get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})

    assert response.status_code == 200


def test_api_key_com_header_conflitante_e_recusada():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-a")
    outra_organizacao = _organizacao("org-tenancy-b")
    _vincular(usuario, organizacao)

    response = _client_com_api_key(responsavel=usuario, organizacao=organizacao).get("/times/", **{META_HEADER_ORGANIZACAO: outra_organizacao.slug})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "organizations.tenant_mismatch"


def test_api_key_expirada_e_recusada():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-expirada")
    _vincular(usuario, organizacao)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao, expiry=timedelta(days=-1))

    response = client.get("/times/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.expired_token"


def test_api_key_revogada_e_recusada_mas_permanece_no_banco():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-revogada")
    _vincular(usuario, organizacao)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao, revoked_at=timezone.now())
    digest_antes = AuthToken.objects.filter(responsavel=usuario, organization=organizacao).count()

    response = client.get("/times/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.revoked_token"
    assert AuthToken.objects.filter(responsavel=usuario, organization=organizacao).count() == digest_antes


def test_api_key_suspensa_e_recusada():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-suspensa")
    _vincular(usuario, organizacao)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao, suspended_at=timezone.now())

    response = client.get("/times/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.api_key_suspended"


def test_api_key_com_responsavel_inativo_e_recusada():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-inativo")
    _vincular(usuario, organizacao)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao)
    usuario.is_active = False
    usuario.save(update_fields=["is_active"])

    response = client.get("/times/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.responsible_inactive"


def test_api_key_com_responsavel_sem_vinculo_na_organizacao_e_recusada():
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-sem-vinculo")
    vinculo = _vincular(usuario, organizacao)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao)
    vinculo.is_active = False
    vinculo.save(update_fields=["is_active"])

    response = client.get("/times/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.api_key_suspended"

    instance = AuthToken.objects.get(responsavel=usuario, organization=organizacao)
    assert instance.suspended_at is not None


def test_api_key_ignora_permissions_pessoais_do_responsavel():
    """Sem scope de create, uma API key não cria Time mesmo que o responsável tenha papel de gestor."""
    usuario = criar_usuario()
    organizacao = _organizacao("org-tenancy-scopes")
    _vincular(usuario, organizacao, Papel.GESTOR)

    client = _client_com_api_key(responsavel=usuario, organizacao=organizacao, scopes=["teams:read"])

    response = client.post("/times/", {"nome": "Produto"}, format="json")

    assert response.status_code == 403


def test_api_key_lista_somente_a_organizacao_da_propria_credencial():
    usuario = criar_usuario()
    organizacao = _organizacao("org-da-api-key")
    outra_organizacao = _organizacao("org-pessoal-do-responsavel")
    _vincular(usuario, organizacao)
    _vincular(usuario, outra_organizacao)

    response = _client_com_api_key(
        responsavel=usuario,
        organizacao=organizacao,
        scopes=["organizations:read"],
    ).get("/organizacoes/")

    assert response.status_code == 200
    assert [item["slug"] for item in response.data["resultados"]] == [organizacao.slug]


def test_api_key_nao_cria_outra_organizacao():
    usuario = criar_usuario()
    organizacao = _organizacao("org-da-api-key-criacao")
    _vincular(usuario, organizacao)

    response = _client_com_api_key(
        responsavel=usuario,
        organizacao=organizacao,
        scopes=["organizations:create"],
    ).post(
        "/organizacoes/",
        {"nome": "Outro tenant", "slug": "outro-tenant"},
        format="json",
    )

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "auth.permission_denied"
    assert not Organizacao.objects.filter(slug="outro-tenant").exists()


def test_api_key_nao_aceita_convite_de_outra_organizacao():
    usuario = criar_usuario()
    organizacao = _organizacao("org-da-api-key-convite")
    outra_organizacao = _organizacao("org-do-convite")
    _vincular(usuario, organizacao)
    convite = Convite.objects.create(
        organizacao=outra_organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        convidado_por=criar_usuario(),
    )

    response = _client_com_api_key(
        responsavel=usuario,
        organizacao=organizacao,
        scopes=["invitations:accept"],
    ).post("/convites/aceitar/", {"token": convite.token}, format="json")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "organizations.tenant_mismatch"
