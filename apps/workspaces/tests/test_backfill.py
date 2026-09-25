from django.db import connection
from django.db.migrations.executor import MigrationExecutor

import pytest


@pytest.mark.django_db(transaction=True)
def test_backfill_cria_workspace_e_acessos_sem_apagar_dados_ao_reverter():
    targets_finais = MigrationExecutor(connection).loader.graph.leaf_nodes()
    alvo_anterior = [
        ("organizacoes", "0004_vinculo_current_workspace"),
        ("workspaces", "0001_initial"),
    ]
    alvo_backfill = [("workspaces", "0002_backfill_workspaces_iniciais")]

    try:
        executor = MigrationExecutor(connection)
        executor.migrate(alvo_anterior)
        state = executor.loader.project_state(alvo_anterior)
        Organizacao = state.apps.get_model("organizacoes", "Organizacao")
        Usuario = state.apps.get_model("usuarios", "Usuario")
        Vinculo = state.apps.get_model("organizacoes", "Vinculo")

        organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
        usuario_ativo = Usuario.objects.create(email="ativo@exemplo.com", first_name="Usuário", last_name="Ativo", password="")
        usuario_inativo = Usuario.objects.create(email="inativo@exemplo.com", first_name="Usuário", last_name="Inativo", password="")
        vinculo_ativo = Vinculo.objects.create(usuario_id=usuario_ativo.pk, organizacao_id=organizacao.pk, papel=20)
        vinculo_inativo = Vinculo.objects.create(usuario_id=usuario_inativo.pk, organizacao_id=organizacao.pk, papel=10, is_active=False)

        executor = MigrationExecutor(connection)
        executor.migrate(alvo_backfill)
        state = executor.loader.project_state(alvo_backfill)
        Workspace = state.apps.get_model("workspaces", "Workspace")
        VinculoWorkspace = state.apps.get_model("workspaces", "VinculoWorkspace")
        Vinculo = state.apps.get_model("organizacoes", "Vinculo")

        workspace = Workspace.objects.get(organizacao_id=organizacao.pk, slug="principal")
        assert workspace.nome == "Principal"
        assert VinculoWorkspace.objects.filter(vinculo_id=vinculo_ativo.pk, workspace_id=workspace.pk).exists()
        assert Vinculo.objects.get(pk=vinculo_ativo.pk).current_workspace_id == workspace.pk
        assert not VinculoWorkspace.objects.filter(vinculo_id=vinculo_inativo.pk).exists()
        assert Vinculo.objects.get(pk=vinculo_inativo.pk).current_workspace_id is None

        executor = MigrationExecutor(connection)
        executor.migrate(alvo_anterior)
        state = executor.loader.project_state(alvo_anterior)
        Workspace = state.apps.get_model("workspaces", "Workspace")
        VinculoWorkspace = state.apps.get_model("workspaces", "VinculoWorkspace")
        Vinculo = state.apps.get_model("organizacoes", "Vinculo")

        assert Workspace.objects.filter(pk=workspace.pk).exists()
        assert VinculoWorkspace.objects.filter(vinculo_id=vinculo_ativo.pk, workspace_id=workspace.pk).exists()
        assert Vinculo.objects.get(pk=vinculo_ativo.pk).current_workspace_id == workspace.pk

        # A reversão é deliberadamente não destrutiva. Limpa-se apenas o banco
        # de teste para que a restauração do alvo final possa aplicar o forward
        # novamente sem colidir com os dados que acabamos de preservar.
        Workspace.objects.all().delete()
    finally:
        MigrationExecutor(connection).migrate(targets_finais)
