"""Testes da camada HTTP de organizacoes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from internal_frameworks.context import ContextVariable
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def configurar_request_de_teste(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


def client_autenticado(usuario):
    _, token = AuthToken.objects.create(user=usuario)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def client_com_api_key(usuario, scopes, organizacao):
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração de teste",
        scopes=scopes,
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def vincular(usuario, organizacao, papel=Papel.MEMBRO, times=()):
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)
    if times:
        vinculo.times.set(times)
    return vinculo


def test_lista_apenas_organizacoes_do_usuario_sem_exigir_header():
    usuario = criar_usuario()
    outra_pessoa = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    org_de_outra_pessoa = Organizacao.objects.create(nome="Org C", slug="org-c")
    vincular(usuario, org_a, Papel.PROPRIETARIO)
    vincular(usuario, org_b, Papel.MEMBRO)
    vincular(outra_pessoa, org_de_outra_pessoa, Papel.PROPRIETARIO)

    response = client_autenticado(usuario).get("/organizacoes/")

    assert response.status_code == status.HTTP_200_OK, response.content
    resultados = response.data["resultados"]
    assert {item["slug"] for item in resultados} == {"org-a", "org-b"}
    assert {item["slug"]: item["papel"] for item in resultados} == {
        "org-a": Papel.PROPRIETARIO,
        "org-b": Papel.MEMBRO,
    }


def test_cria_organizacao_e_vincula_usuario_como_proprietario():
    usuario = criar_usuario(email_verificado_em=timezone.now())

    response = client_autenticado(usuario).post(
        "/organizacoes/",
        {"nome": "Minha Empresa", "slug": "minha-empresa"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    organizacao = Organizacao.objects.get(slug="minha-empresa")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    assert response.data["slug"] == "minha-empresa"
    assert vinculo.papel == Papel.PROPRIETARIO


def test_times_sao_filtrados_pela_organizacao_do_header():
    usuario = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    vincular(usuario, org_a, Papel.MEMBRO)
    vincular(usuario, org_b, Papel.MEMBRO)
    Time.objects.create(organizacao=org_a, nome="Produto", created_by=usuario)
    Time.objects.create(organizacao=org_b, nome="Financeiro", created_by=usuario)

    response = client_autenticado(usuario).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item["nome"] for item in response.data["resultados"]] == ["Produto"]


def test_vinculos_sao_filtrados_pela_organizacao_do_header():
    usuario = criar_usuario()
    colega = criar_usuario(first_name="Colega")
    pessoa_de_fora = criar_usuario(first_name="Fora")
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    vincular(usuario, org_a, Papel.ADMINISTRADOR)
    vincular(colega, org_a, Papel.MEMBRO)
    vincular(usuario, org_b, Papel.MEMBRO)
    vincular(pessoa_de_fora, org_b, Papel.MEMBRO)

    response = client_autenticado(usuario).get(
        "/vinculos/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert {item["usuario"]["email"] for item in response.data["resultados"]} == {
        usuario.email,
        colega.email,
    }


def test_gestor_cria_convite_na_organizacao_do_header():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)

    response = client_autenticado(usuario).post(
        "/convites/",
        {"email": "nova@example.com", "papel": Papel.MEMBRO},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    convite = Convite.objects.get(email="nova@example.com")
    assert convite.organizacao == organizacao
    assert convite.convidado_por == usuario
    assert response.data["token"] == convite.token


def test_header_de_organizacao_ausente_retorna_422():
    usuario = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, org_a, Papel.MEMBRO)

    response = client_autenticado(usuario).get("/times/")

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "organizations.header_required"
    assert response.json()["errors"][0]["field"] == "X-Organization"


def test_usuario_sem_vinculo_ativo_na_organizacao_retorna_403():
    usuario = criar_usuario()
    Organizacao.objects.create(nome="Org A", slug="org-a")

    response = client_autenticado(usuario).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "organizations.membership_required"


def test_papel_insuficiente_para_criar_time_retorna_403():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_autenticado(usuario).post(
        "/times/",
        {"nome": "Produto"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "organizations.role_insufficient"


def test_convite_com_papel_acima_do_proprio_e_recusado():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)

    response = client_autenticado(usuario).post(
        "/convites/",
        {"email": "nova@example.com", "papel": Papel.PROPRIETARIO},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.role_insufficient"
    assert response.data["errors"][0]["field"] == "papel"


def test_patch_reativa_convite_expirado_sem_limite_de_membros():
    administrador = criar_usuario(email="admin-patch-expiracao@example.com")
    organizacao = Organizacao.objects.create(nome="Patch expiração", slug="patch-expiracao")
    vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="reativar@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() - timedelta(days=1),
    )

    response = client_autenticado(administrador).patch(
        f"/convites/{convite.pk}/",
        {"expira_em": (timezone.now() + timedelta(days=1)).isoformat()},
        format="json",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    convite.refresh_from_db()
    assert convite.expira_em > timezone.now()


@pytest.mark.django_db(transaction=True)
def test_rebaixamentos_concorrentes_preservam_um_proprietario_ativo():
    proprietarios = [
        criar_usuario(email="owner-rebaixamento-1@example.com"),
        criar_usuario(email="owner-rebaixamento-2@example.com"),
    ]
    organizacao = Organizacao.objects.create(nome="Owners concorrentes", slug="owners-rebaixamento-concorrente")
    vinculos = [vincular(usuario, organizacao, Papel.PROPRIETARIO) for usuario in proprietarios]
    clients = [client_autenticado(usuario) for usuario in proprietarios]
    barreira = Barrier(2)

    def rebaixar(indice):
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            response = clients[indice].patch(
                f"/vinculos/{vinculos[indice].pk}/",
                {"papel": Papel.ADMINISTRADOR},
                format="json",
                **{META_HEADER_ORGANIZACAO: organizacao.slug},
            )
            return response.status_code, response.json().get("errors", [{}])[0].get("code")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(rebaixar, (0, 1)))

    assert sorted(status_code for status_code, _ in resultados) == [status.HTTP_200_OK, status.HTTP_409_CONFLICT]
    assert {codigo for _, codigo in resultados if codigo} == {"account.owner_transfer_required"}
    assert (
        Vinculo.objects.filter(
            organizacao=organizacao,
            papel=Papel.PROPRIETARIO,
            is_active=True,
        ).count()
        == 1
    )


def test_aceitar_convite_com_token_invalido_retorna_422():
    usuario = criar_usuario()

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": "token-que-nao-existe"},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_invalid"
    assert response.data["errors"][0]["field"] == "token"


def test_aceitar_convite_ja_utilizado_retorna_422():
    usuario = criar_usuario(email="nova@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
        aceito_em=timezone.now(),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_expired"


def test_aceitar_convite_de_outro_email_retorna_422():
    usuario = criar_usuario(email="usuario@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="outro@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_email_mismatch"


def test_usuario_sem_email_verificado_nao_aceita_convite():
    usuario = criar_usuario(email="nao-verificado@example.com", email_verificado_em=None)
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a-nao-verificado")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["errors"][0]["code"] == "account.email_not_verified"
    assert Vinculo.objects.filter(usuario=usuario, organizacao=organizacao).exists() is False
    convite.refresh_from_db()
    assert convite.aceito_em is None


def test_usuario_convidado_aceita_convite_sem_header_de_organizacao():
    usuario = criar_usuario(email="nova@example.com", email_verificado_em=timezone.now())
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert Vinculo.objects.filter(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO).exists()
    convite.refresh_from_db()
    assert convite.aceito_em is not None


# ---- scopes públicos (resource:action) derivados dos models ----


def test_api_key_com_scope_teams_read_pode_listar_times():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_com_api_key(usuario, ["teams:read"], organizacao).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK


def test_api_key_sem_scope_teams_read_e_recusada():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_com_api_key(usuario, ["organizations:read"], organizacao).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["errors"][0]["code"] == "auth.insufficient_scope"


def test_api_key_com_scopes_crud_ignora_papel_pessoal_em_convites_e_vinculos():
    responsavel = criar_usuario(email="api-key-crud-responsavel@example.com")
    proprietario = criar_usuario(email="api-key-crud-owner@example.com")
    alvo = criar_usuario(email="api-key-crud-alvo@example.com")
    organizacao = Organizacao.objects.create(nome="Org API key CRUD", slug="org-api-key-crud")
    vincular(responsavel, organizacao, Papel.MEMBRO)
    vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    vinculo_alvo = vincular(alvo, organizacao, Papel.MEMBRO)
    client = client_com_api_key(
        responsavel,
        ["invitations:create", "invitations:update", "memberships:update", "memberships:delete"],
        organizacao,
    )

    criacao = client.post(
        "/convites/",
        {"email": "api-key-crud-convite@example.com", "papel": Papel.MEMBRO},
        format="json",
    )
    convite = Convite.objects.get(email="api-key-crud-convite@example.com")
    atualizacao_convite = client.patch(
        f"/convites/{convite.pk}/",
        {"papel": Papel.PROPRIETARIO},
        format="json",
    )
    atualizacao_vinculo = client.patch(
        f"/vinculos/{vinculo_alvo.pk}/",
        {"papel": Papel.GESTOR},
        format="json",
    )
    remocao_vinculo = client.delete(f"/vinculos/{vinculo_alvo.pk}/")

    assert criacao.status_code == status.HTTP_201_CREATED
    assert atualizacao_convite.status_code == status.HTTP_200_OK
    assert atualizacao_vinculo.status_code == status.HTTP_200_OK
    assert remocao_vinculo.status_code == status.HTTP_204_NO_CONTENT
    convite.refresh_from_db()
    assert convite.papel == Papel.PROPRIETARIO
    assert Vinculo.objects.filter(pk=vinculo_alvo.pk).exists() is False


def test_api_key_com_scopes_crud_ignora_papel_pessoal_em_times_e_remocao_de_convite():
    responsavel = criar_usuario(email="api-key-times-responsavel@example.com")
    organizacao = Organizacao.objects.create(nome="Org API key times", slug="org-api-key-times")
    vincular(responsavel, organizacao, Papel.MEMBRO)
    convite = Convite.objects.create(organizacao=organizacao, email="api-key-delete-convite@example.com")
    client = client_com_api_key(
        responsavel,
        ["teams:create", "teams:update", "teams:delete", "invitations:delete"],
        organizacao,
    )

    criacao = client.post("/times/", {"nome": "Antes"}, format="json")
    time = Time.objects.get(organizacao=organizacao, nome="Antes")
    atualizacao = client.patch(f"/times/{time.pk}/", {"nome": "Depois"}, format="json")
    remocao_time = client.delete(f"/times/{time.pk}/")
    remocao_convite = client.delete(f"/convites/{convite.pk}/")

    assert criacao.status_code == status.HTTP_201_CREATED
    assert atualizacao.status_code == status.HTTP_200_OK
    assert remocao_time.status_code == status.HTTP_204_NO_CONTENT
    assert remocao_convite.status_code == status.HTTP_204_NO_CONTENT
    assert Time.objects.filter(pk=time.pk).exists() is False
    assert Convite.objects.filter(pk=convite.pk).exists() is False


def test_api_key_nao_atualiza_email_de_faturamento_mesmo_com_scope():
    responsavel = criar_usuario(email="api-key-email-faturamento@example.com", email_verificado_em=timezone.now())
    organizacao = Organizacao.objects.create(
        nome="Org API key cobrança",
        slug="org-api-key-cobranca",
        email_faturamento="anterior@example.com",
    )
    vincular(responsavel, organizacao, Papel.ADMINISTRADOR)

    response = client_com_api_key(responsavel, ["organizations:update"], organizacao).patch(
        f"/organizacoes/{organizacao.pk}/",
        {"email_faturamento": "indevido@example.com"},
        format="json",
    )

    organizacao.refresh_from_db()
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert organizacao.email_faturamento == "anterior@example.com"


def test_api_key_precisa_do_scope_invitations_accept_para_aceitar_convite():
    usuario = criar_usuario(email="precisa-scope@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_com_api_key(usuario, ["teams:read"], organizacao).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["errors"][0]["code"] == "auth.insufficient_scope"


def test_api_key_com_scope_invitations_accept_aceita_convite():
    usuario = criar_usuario(email="com-scope@example.com", email_verificado_em=timezone.now())
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_com_api_key(usuario, ["invitations:accept"], organizacao).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK


def test_delete_de_time_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    response = client_autenticado(usuario).delete(f"/times/{time.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Time.objects.filter(pk=time.pk).exists()
    assert Time.all_objects.get(pk=time.pk).is_deleted is True


def test_delete_de_vinculo_recusa_remover_unico_proprietario_ativo():
    proprietario = criar_usuario(email="owner-delete-protegido@example.com")
    administrador = criar_usuario(email="admin-delete-protegido@example.com")
    organizacao = Organizacao.objects.create(nome="Owner protegido", slug="owner-delete-protegido")
    vinculo_proprietario = vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    vincular(administrador, organizacao, Papel.ADMINISTRADOR)

    response = client_autenticado(administrador).delete(
        f"/vinculos/{vinculo_proprietario.pk}/",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["errors"][0]["code"] == "account.owner_transfer_required"
    assert Vinculo.objects.filter(pk=vinculo_proprietario.pk).exists() is True


def test_delete_de_vinculo_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.ADMINISTRADOR)
    alvo = vincular(criar_usuario(), organizacao)

    response = client_autenticado(usuario).delete(f"/vinculos/{alvo.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Vinculo.all_objects.get(pk=alvo.pk).is_deleted is True


def test_delete_de_convite_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    convite = Convite.objects.create(organizacao=organizacao, email="nova@example.com", convidado_por=usuario)

    response = client_autenticado(usuario).delete(f"/convites/{convite.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Convite.all_objects.get(pk=convite.pk).is_deleted is True
