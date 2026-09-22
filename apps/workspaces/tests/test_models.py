from django.contrib import admin
from django.db import IntegrityError

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_workspace_e_unico_por_slug_ativo_na_organizacao():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")

    with pytest.raises(IntegrityError):
        Workspace.objects.create(organizacao=organizacao, nome="Outra", slug="matriz")


def test_vinculo_workspace_e_unico_enquanto_nao_excluido():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)

    with pytest.raises(IntegrityError):
        VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)


def test_current_workspace_comeca_nulo():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    assert vinculo.current_workspace is None


def test_admin_exibe_organizacao_atividade_e_selecao_sem_mutar_acessos():
    workspace_admin = admin.site._registry[Workspace]
    vinculo_workspace_admin = admin.site._registry[VinculoWorkspace]

    assert {"organizacao", "is_active"}.issubset(workspace_admin.list_display)
    assert {"organizacao", "is_active", "selected_for_view"}.issubset(vinculo_workspace_admin.list_display)
    assert not vinculo_workspace_admin.has_add_permission(request=None)
    assert not vinculo_workspace_admin.has_change_permission(request=None)
    assert not vinculo_workspace_admin.has_delete_permission(request=None)
