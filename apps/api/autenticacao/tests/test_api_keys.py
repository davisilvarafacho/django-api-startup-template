"""Contrato HTTP e de serviço do CRUD/ciclo de vida de API keys."""

from datetime import timedelta

from django.contrib.auth.models import Permission
from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.api.autenticacao.services import resume_api_key, rotate_api_key, suspend_api_key
from apps.api.core.errors import APIError
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()

TODAS_AS_PERMISSIONS = ("view_apikey", "add_apikey", "change_apikey", "delete_apikey", "rotate_apikey")


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Org", slug="org-api-keys")


@pytest.fixture
def ator(organizacao):
    usuario = criar_usuario()
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    for codename in TODAS_AS_PERMISSIONS:
        permission = Permission.objects.get(content_type__app_label="autenticacao", codename=codename)
        usuario.user_permissions.add(permission)
    # Necessário para delegar o scope "teams:read" usado nos testes de criação.
    usuario.user_permissions.add(Permission.objects.get(content_type__app_label="organizacoes", codename="view_time"))
    return usuario


@pytest.fixture
def responsavel(organizacao):
    usuario = criar_usuario()
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    return usuario


def _client_com_sessao(usuario, *, reauthenticated=True):
    instance, token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(
        token=instance,
        reauthenticated_at=timezone.now() if reauthenticated else None,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _com_header(client, organizacao):
    client.credentials(
        HTTP_AUTHORIZATION=client._credentials.get("HTTP_AUTHORIZATION"),
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )
    return client


def _client_api_key(usuario, organizacao, scopes=()):
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Outra key",
        scopes=list(scopes),
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_cria_api_key_e_devolve_o_plain_token_uma_vez(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)

    response = client.post(
        "/auth/api_keys/",
        {"name": "Integração", "responsavel": responsavel.pk, "scopes": ["teams:read"]},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["token"]
    assert response.data["scopes"] == ["teams:read"]
    assert response.data["status"] == "active"

    instance = AuthToken.objects.get(uuid=response.data["uuid"])
    assert instance.organization == organizacao
    assert instance.responsavel == responsavel
    assert instance.created_by == ator


def test_criacao_exige_autenticacao_recente(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator, reauthenticated=False), organizacao)

    response = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.reauthentication_required"


def test_criacao_exige_responsavel_vinculado_a_organizacao(ator, organizacao):
    usuario_sem_vinculo = criar_usuario()
    client = _com_header(_client_com_sessao(ator), organizacao)

    response = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": usuario_sem_vinculo.pk}, format="json")

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.membership_required"


def test_criacao_recusa_scope_que_o_ator_nao_possui(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)

    response = client.post(
        "/auth/api_keys/",
        {"name": "Integração", "responsavel": responsavel.pk, "scopes": ["*"]},
        format="json",
    )

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "auth.scope_not_delegable"


def test_criacao_com_expiracao_opcional(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    expira_em = timezone.now() + timedelta(days=30)

    response = client.post(
        "/auth/api_keys/",
        {"name": "Integração", "responsavel": responsavel.pk, "expiry": expira_em.isoformat()},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["expiry"] is not None


def test_usuario_sem_permission_nao_cria_api_key(organizacao, responsavel):
    usuario_sem_permissao = criar_usuario()
    Vinculo.objects.create(usuario=usuario_sem_permissao, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    client = _com_header(_client_com_sessao(usuario_sem_permissao), organizacao)

    response = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json")

    assert response.status_code == 403


def test_lista_e_detalhe_sao_fixos_na_organizacao_do_header(ator, organizacao, responsavel):
    outra_organizacao = Organizacao.objects.create(nome="Outra", slug="org-api-keys-outra")
    Vinculo.objects.create(usuario=ator, organizacao=outra_organizacao, papel=Papel.ADMINISTRADOR)
    client = _com_header(_client_com_sessao(ator), organizacao)

    criada = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json").data

    resposta_org_certa = client.get(f"/auth/api_keys/{criada['uuid']}/")
    assert resposta_org_certa.status_code == 200

    client_outra_org = _com_header(_client_com_sessao(ator), outra_organizacao)
    resposta_org_errada = client_outra_org.get(f"/auth/api_keys/{criada['uuid']}/")
    assert resposta_org_errada.status_code == 404


def test_patch_altera_nome_sem_expor_segredo(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    criada = client.post("/auth/api_keys/", {"name": "Nome antigo", "responsavel": responsavel.pk}, format="json").data

    response = client.patch(f"/auth/api_keys/{criada['uuid']}/", {"name": "Nome novo"}, format="json")

    assert response.status_code == 200
    assert response.data["name"] == "Nome novo"
    assert "token" not in response.data


def test_delete_revoga_sem_apagar(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    criada = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json").data

    response = client.delete(f"/auth/api_keys/{criada['uuid']}/")

    assert response.status_code == 204
    instance = AuthToken.objects.get(uuid=criada["uuid"])
    assert instance.revoked_at is not None


def test_rotate_invalida_o_segredo_anterior_e_devolve_um_novo(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    criada = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json").data

    response = client.post(f"/auth/api_keys/{criada['uuid']}/rotate/")

    assert response.status_code == 201
    novo_token = response.data["token"]
    assert novo_token

    antiga = AuthToken.objects.get(uuid=criada["uuid"])
    assert antiga.revoked_at is not None
    nova = AuthToken.objects.get(uuid=response.data["uuid"])
    assert antiga.replaced_by_id == nova.pk

    cliente_com_token_antigo = APIClient()
    cliente_com_token_antigo.credentials(HTTP_AUTHORIZATION=f"Bearer {criada['token']}")
    resposta_com_token_antigo = cliente_com_token_antigo.get("/times/")
    assert resposta_com_token_antigo.status_code == 401


def test_suspend_e_resume(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    criada = client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk, "scopes": ["teams:read"]}, format="json").data
    api_key_client = _client_api_key_do_token(criada["token"])

    suspensa = client.post(f"/auth/api_keys/{criada['uuid']}/suspend/", {"reason": "Investigação"}, format="json")
    assert suspensa.status_code == 200
    assert suspensa.data["status"] == "suspended"

    resposta_suspensa = api_key_client.get("/times/")
    assert resposta_suspensa.status_code == 401
    assert resposta_suspensa.json()["errors"][0]["code"] == "auth.api_key_suspended"

    retomada = client.post(f"/auth/api_keys/{criada['uuid']}/resume/")
    assert retomada.status_code == 200
    assert retomada.data["status"] == "active"


def _client_api_key_do_token(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_resume_recusa_quando_responsavel_perdeu_o_vinculo(organizacao):
    responsavel_local = criar_usuario()
    vinculo = Vinculo.objects.create(usuario=responsavel_local, organizacao=organizacao, papel=Papel.MEMBRO)

    instance, _token = AuthToken.objects.create(
        responsavel=responsavel_local,
        type=TokenType.API_KEY,
        created_by=responsavel_local,
        organization=organizacao,
        name="Key",
    )
    TokenMetaData.objects.create(token=instance)
    suspend_api_key(instance, actor=responsavel_local, reason="manual")

    vinculo.is_active = False
    vinculo.save(update_fields=["is_active"])

    with pytest.raises(APIError) as excinfo:
        resume_api_key(instance, actor=responsavel_local)
    assert excinfo.value.code == AuthErrorCode.RESPONSIBLE_INACTIVE.value


def test_api_key_nao_administra_outras_credenciais(ator, organizacao, responsavel):
    client = _com_header(_client_com_sessao(ator), organizacao)
    client.post("/auth/api_keys/", {"name": "Integração", "responsavel": responsavel.pk}, format="json")

    api_key_client = _com_header(_client_api_key(ator, organizacao, scopes=["*"]), organizacao)
    response = api_key_client.get("/auth/api_keys/")

    assert response.status_code == 403


def test_rotacao_atomica_via_service_preserva_organizacao_e_scopes(organizacao, responsavel):
    instance, _token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    TokenMetaData.objects.create(token=instance)

    issued = rotate_api_key(instance, actor=responsavel)

    assert issued.instance.organization == organizacao
    assert issued.instance.scopes == ["teams:read"]
    assert issued.instance.name == "Integração"
    instance.refresh_from_db()
    assert instance.revoked_at is not None
    assert instance.replaced_by_id == issued.instance.pk


@pytest.mark.parametrize(
    ("field", "error_code"),
    [
        ("revoked_at", AuthErrorCode.REVOKED_TOKEN),
        ("suspended_at", AuthErrorCode.API_KEY_SUSPENDED),
    ],
)
def test_rotacao_recusa_api_key_inativa(
    organizacao,
    responsavel,
    field,
    error_code,
):
    instance, _token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
    )
    setattr(instance, field, timezone.now())
    instance.save(update_fields=[field])

    with pytest.raises(APIError) as exc:
        rotate_api_key(instance, actor=responsavel)

    assert exc.value.code == error_code
    assert AuthToken.objects.filter(organization=organizacao).count() == 1


def test_rotacao_recusa_api_key_expirada(organizacao, responsavel):
    instance, _token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
        expiry=timedelta(seconds=-1),
    )

    with pytest.raises(APIError) as exc:
        rotate_api_key(instance, actor=responsavel)

    assert exc.value.code == AuthErrorCode.EXPIRED_TOKEN
    assert AuthToken.objects.filter(organization=organizacao).count() == 1


def test_rotacao_recusa_api_key_cujo_responsavel_perdeu_vinculo(
    organizacao,
    responsavel,
):
    instance, _token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
    )
    Vinculo.objects.filter(
        usuario=responsavel,
        organizacao=organizacao,
    ).update(is_active=False)

    with pytest.raises(APIError) as exc:
        rotate_api_key(instance, actor=responsavel)

    assert exc.value.code == AuthErrorCode.RESPONSIBLE_INACTIVE
    assert AuthToken.objects.filter(organization=organizacao).count() == 1


def test_rotacao_recusa_token_que_nao_e_api_key(responsavel):
    instance, _token = AuthToken.objects.create(user=responsavel)

    with pytest.raises(APIError) as exc:
        rotate_api_key(instance, actor=responsavel)

    assert exc.value.code == AuthErrorCode.INVALID_TOKEN
