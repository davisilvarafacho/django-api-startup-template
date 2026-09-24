import threading

from django.db import close_old_connections

import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.models import VinculoWorkspace, Workspace
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


def test_inativar_workspace_limpa_atual_e_desativa_acessos():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-inativar")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(), papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    acesso = AcessosWorkspace.conceder(vinculo=vinculo, workspace=workspace, validar_papel_ator=False)
    AcessosWorkspace.definir_atual(vinculo=vinculo, workspace=workspace)

    Workspaces.inativar(workspace, validar_papel_ator=False)

    workspace.refresh_from_db()
    vinculo.refresh_from_db()
    acesso.refresh_from_db()
    assert workspace.is_active is False
    assert vinculo.current_workspace_id is None
    assert acesso.is_active is False


@pytest.mark.django_db(transaction=True)
def test_criacao_concorrente_com_promocao_garante_acesso_obrigatorio():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-concorrencia-promocao")
    membro = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(email="promovido@example.com"), papel=Papel.MEMBRO)
    barrier = threading.Barrier(2)

    def criar_workspace():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            Workspaces.criar(organizacao=organizacao, nome="Matriz", slug="matriz", validar_papel_ator=False)
        finally:
            close_old_connections()

    def promover_membro():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            Vinculos.atualizar_vinculo(membro, dados={"papel": Papel.ADMINISTRADOR}, validar_papel_ator=False)
        finally:
            close_old_connections()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = [executor.submit(criar_workspace), executor.submit(promover_membro)]
        for resultado in resultados:
            resultado.result(timeout=30)

    membro.refresh_from_db()
    workspaces = Workspace.objects.filter(organizacao=organizacao, is_active=True, is_deleted=False)
    assert membro.papel == Papel.ADMINISTRADOR
    assert all(
        VinculoWorkspace.objects.filter(vinculo=membro, workspace=workspace, is_active=True, is_deleted=False).count() == 1
        for workspace in workspaces
    )


@pytest.mark.django_db(transaction=True)
def test_concessao_com_workspace_inativado_entre_resolucao_e_escrita_nao_cria_acesso():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-concorrencia-inativacao")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(email="concessao@example.com"), papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    resolved = threading.Barrier(2)
    inactivated = threading.Event()

    def conceder_workspace_resolvido():
        close_old_connections()
        try:
            workspace_resolvido = Workspace.objects.get(pk=workspace.pk)
            resolved.wait(timeout=10)
            assert inactivated.wait(timeout=10)
            with pytest.raises(APIError) as excinfo:
                AcessosWorkspace.conceder(vinculo=vinculo, workspace=workspace_resolvido, validar_papel_ator=False)
            return excinfo.value
        finally:
            close_old_connections()

    def inativar_workspace():
        close_old_connections()
        try:
            resolved.wait(timeout=10)
            Workspaces.inativar(workspace, validar_papel_ator=False)
            inactivated.set()
        finally:
            close_old_connections()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as executor:
        concessao = executor.submit(conceder_workspace_resolvido)
        inativacao = executor.submit(inativar_workspace)
        inativacao.result(timeout=30)
        erro = concessao.result(timeout=30)

    assert erro.code == "workspaces.inactive"
    assert not VinculoWorkspace.objects.filter(vinculo=vinculo, workspace=workspace, is_deleted=False).exists()
