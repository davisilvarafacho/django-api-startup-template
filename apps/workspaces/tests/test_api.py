from django.utils import timezone

from rest_framework import status

import pytest

from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.organizacoes.tests.test_api import client_autenticado, client_com_api_key
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _contexto_workspace_api(*, slug="org-workspaces-api"):
    usuario = criar_usuario(email=f"{slug}-usuario@example.com", email_verificado_em=timezone.now())
    organizacao = Organizacao.objects.create(nome="Acme", slug=slug)
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspaces = [
        Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz"),
        Workspace.objects.create(organizacao=organizacao, nome="Filial", slug="filial"),
    ]
    for workspace in workspaces:
        VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace, selected_for_view=True)
    vinculo.current_workspace = workspaces[0]
    vinculo.save(update_fields=["current_workspace"])
    return usuario, organizacao, vinculo, workspaces


def test_lista_de_workspaces_filtra_acesso_humano_e_api_key_ve_todos_ativos():
    usuario, organizacao, vinculo, workspaces = _contexto_workspace_api()
    restrito = criar_usuario(email="restrito-workspaces@example.com")
    vinculo_restrito = Vinculo.objects.create(usuario=restrito, organizacao=organizacao, papel=Papel.MEMBRO)
    VinculoWorkspace.objects.create(vinculo=vinculo_restrito, workspace=workspaces[0])

    resposta_restrito = client_autenticado(restrito).get(
        "/workspaces/",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )
    assert resposta_restrito.status_code == status.HTTP_200_OK
    assert {item["id"] for item in resposta_restrito.data["resultados"]} == {workspaces[0].pk}

    resposta_api_key = client_com_api_key(usuario, ["workspaces:read"], organizacao).get("/workspaces/")
    assert resposta_api_key.status_code == status.HTTP_200_OK
    assert {item["id"] for item in resposta_api_key.data["resultados"]} == {workspace.pk for workspace in workspaces}


def test_troca_atual_e_selecao_nao_permite_remover_o_atual():
    usuario, organizacao, vinculo, workspaces = _contexto_workspace_api(slug="org-workspaces-selecao")
    client = client_autenticado(usuario)
    headers = {META_HEADER_ORGANIZACAO: organizacao.slug}

    resposta_atual = client.post(f"/workspaces/{workspaces[1].pk}/atual/", **headers)
    assert resposta_atual.status_code == status.HTTP_200_OK
    assert resposta_atual.data["current_workspace"] == workspaces[1].pk

    resposta_selecao = client.put(
        "/workspaces/visualizacao/",
        {"workspaces": [workspaces[0].pk, workspaces[1].pk]},
        format="json",
        **headers,
    )
    assert resposta_selecao.status_code == status.HTTP_200_OK
    assert {item["id"] for item in resposta_selecao.data["workspaces"]} == {workspace.pk for workspace in workspaces}

    resposta_invalida = client.put(
        "/workspaces/visualizacao/",
        {"workspaces": [workspaces[0].pk]},
        format="json",
        **headers,
    )
    assert resposta_invalida.status_code == status.HTTP_409_CONFLICT
    assert resposta_invalida.data["errors"][0]["code"] == "workspaces.invalid_selection"
    vinculo.refresh_from_db()
    assert vinculo.current_workspace_id == workspaces[1].pk


def test_acoes_de_sessao_rejeitam_api_key():
    usuario, organizacao, _, workspaces = _contexto_workspace_api(slug="org-workspaces-api-key")
    client = client_com_api_key(usuario, ["workspaces:read"], organizacao)

    resposta_atual = client.post(f"/workspaces/{workspaces[0].pk}/atual/")
    resposta_selecao = client.put("/workspaces/visualizacao/", {"workspaces": [workspaces[0].pk]}, format="json")

    assert resposta_atual.status_code == status.HTTP_403_FORBIDDEN
    assert resposta_selecao.status_code == status.HTTP_403_FORBIDDEN


def test_remocao_de_acesso_obrigatorio_retorna_erro_estavel():
    usuario = criar_usuario(email="owner-workspaces@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="org-workspaces-owner")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    acesso = AcessosWorkspace.conceder(vinculo=vinculo, workspace=workspace, validar_papel_ator=False)

    resposta = client_autenticado(usuario).delete(
        f"/vinculos-workspaces/{acesso.pk}/",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )

    assert resposta.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resposta.data["errors"][0]["code"] == "workspaces.mandatory_access"


def test_organizacoes_expoe_workspace_atual_humano_e_nulo_para_api_key():
    usuario, organizacao, _, workspaces = _contexto_workspace_api(slug="org-workspaces-organizacao")

    resposta_humana = client_autenticado(usuario).get("/organizacoes/")
    item = next(item for item in resposta_humana.data["resultados"] if item["id"] == organizacao.pk)
    assert item["current_workspace"] == {"id": workspaces[0].pk, "nome": workspaces[0].nome, "slug": workspaces[0].slug}

    resposta_api_key = client_com_api_key(usuario, ["organizations:read"], organizacao).get("/organizacoes/")
    item_api_key = next(item for item in resposta_api_key.data["resultados"] if item["id"] == organizacao.pk)
    assert item_api_key["current_workspace"] is None
