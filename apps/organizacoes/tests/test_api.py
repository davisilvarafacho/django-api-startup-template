"""Testes da camada HTTP de organizacoes."""

from datetime import timedelta

from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

import pytest
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.models import AuthToken
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configurar_request_de_teste(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def client_autenticado(usuario):
    _, token = AuthToken.objects.create(user=usuario)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def vincular(usuario, organizacao, papel=Papel.MEMBRO, times=()):
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)
    if times:
        vinculo.times.set(times)
    return vinculo


def test_lista_apenas_organizacoes_do_usuario_sem_exigir_header():
    usuario = UsuarioFactory()
    outra_pessoa = UsuarioFactory()
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
    usuario = UsuarioFactory()

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
    usuario = UsuarioFactory()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    vincular(usuario, org_a, Papel.MEMBRO)
    vincular(usuario, org_b, Papel.MEMBRO)
    Time.objects.create(organizacao=org_a, nome="Produto", owner=usuario)
    Time.objects.create(organizacao=org_b, nome="Financeiro", owner=usuario)

    response = client_autenticado(usuario).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item["nome"] for item in response.data["resultados"]] == ["Produto"]


def test_vinculos_sao_filtrados_pela_organizacao_do_header():
    usuario = UsuarioFactory()
    colega = UsuarioFactory(first_name="Colega")
    pessoa_de_fora = UsuarioFactory(first_name="Fora")
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
    usuario = UsuarioFactory()
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


def test_usuario_convidado_aceita_convite_sem_header_de_organizacao():
    usuario = UsuarioFactory(email="nova@example.com")
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
