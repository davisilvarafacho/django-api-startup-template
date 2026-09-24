import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_rebaixamento_preserva_acesso_e_revogacao_obrigatoria_falha():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-acesso")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(), papel=Papel.ADMINISTRADOR)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    acesso = AcessosWorkspace.conceder(vinculo=vinculo, workspace=workspace, validar_papel_ator=False)

    with pytest.raises(APIError) as excinfo:
        AcessosWorkspace.revogar(acesso, validar_papel_ator=False)

    assert excinfo.value.code == WorkspaceErrorCode.MANDATORY_ACCESS


def test_selecao_nao_remove_workspace_atual_nem_atualiza_parcialmente():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-selecao")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(), papel=Papel.MEMBRO)
    primeiro = Workspace.objects.create(organizacao=organizacao, nome="Um", slug="um")
    segundo = Workspace.objects.create(organizacao=organizacao, nome="Dois", slug="dois")
    AcessosWorkspace.conceder(vinculo=vinculo, workspace=primeiro, validar_papel_ator=False)
    AcessosWorkspace.conceder(vinculo=vinculo, workspace=segundo, validar_papel_ator=False)
    AcessosWorkspace.definir_atual(vinculo=vinculo, workspace=primeiro)

    with pytest.raises(APIError) as excinfo:
        AcessosWorkspace.selecionar_visualizacao(vinculo=vinculo, workspace_ids={segundo.pk})

    assert excinfo.value.code == WorkspaceErrorCode.INVALID_SELECTION
    assert {a.workspace_id for a in vinculo.workspaces.filter(selected_for_view=True)} == {primeiro.pk, segundo.pk}


def test_selecao_rejeita_relacao_de_workspace_de_outra_organizacao():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-selecao-cross")
    outra_organizacao = Organizacao.objects.create(nome="Outra", slug="ws-selecao-cross-outra")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(), papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=outra_organizacao, nome="Fora", slug="fora")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)

    with pytest.raises(APIError) as excinfo:
        AcessosWorkspace.selecionar_visualizacao(vinculo=vinculo, workspace_ids={workspace.pk})

    assert excinfo.value.code == WorkspaceErrorCode.ORGANIZATION_MISMATCH
