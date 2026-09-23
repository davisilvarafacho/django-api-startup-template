import pytest

from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.models import VinculoWorkspace
from apps.workspaces.workspaces import Workspaces
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_criar_concede_workspace_a_administradores_ativos():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-criar")
    administrador = criar_usuario()
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=administrador, papel=Papel.ADMINISTRADOR)

    workspace = Workspaces.criar(organizacao=organizacao, nome="Matriz", slug="matriz", validar_papel_ator=False)

    assert VinculoWorkspace.objects.filter(vinculo=vinculo, workspace=workspace).count() == 1


def test_criar_inicial_define_atual_do_proprietario():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-inicial")
    proprietario = Vinculos.criar_proprietario(organizacao, criar_usuario())

    workspace = Workspaces.criar_inicial(organizacao=organizacao, proprietario=proprietario)

    proprietario.refresh_from_db()
    assert proprietario.current_workspace_id == workspace.pk
