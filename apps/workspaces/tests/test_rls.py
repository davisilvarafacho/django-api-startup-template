from django.db import connection, models
from django.test import override_settings

import psycopg2
import pytest

from apps.api.base.models import Base
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.organizacoes.tests.test_api import client_com_api_key
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

from .urls_required_model import RegistroWorkspaceRLSRequired

PAPEL_TESTE = "rls_workspace_tester"
SENHA_TESTE = "rls_workspace_tester"


class RegistroWorkspaceRLS(Base):
    nome = models.CharField(max_length=50)

    class Meta:
        app_label = "workspaces"
        db_table = "registro_workspace_rls_teste"


TABELAS = (RegistroWorkspaceRLS, RegistroWorkspaceRLSRequired)

pytestmark = pytest.mark.django_db(transaction=True)


def _conectar_como_papel_comum():
    parametros = connection.settings_dict
    return psycopg2.connect(
        dbname=parametros["NAME"],
        user=PAPEL_TESTE,
        password=SENHA_TESTE,
        host=parametros["HOST"] or "127.0.0.1",
        port=parametros["PORT"] or 5432,
    )


def _publicar_contexto(cursor, *, organizacao_id, modo, vinculo_id=None, workspace_id=None):
    valores = {
        "rls.tenant_id": organizacao_id,
        "rls.membership_id": vinculo_id or "",
        "rls.current_workspace_id": workspace_id or "",
        "rls.workspace_mode": modo,
    }
    for chave, valor in valores.items():
        cursor.execute("SELECT set_config(%s, %s, true)", [chave, str(valor)])


def consultar_como_papel_comum(*, organizacao_id, modo, vinculo_id=None, workspace_id=None):
    conexao = _conectar_como_papel_comum()
    try:
        with conexao, conexao.cursor() as cursor:
            _publicar_contexto(
                cursor,
                organizacao_id=organizacao_id,
                modo=modo,
                vinculo_id=vinculo_id,
                workspace_id=workspace_id,
            )
            cursor.execute(f"SELECT nome FROM {RegistroWorkspaceRLS._meta.db_table} ORDER BY nome")
            return [linha[0] for linha in cursor.fetchall()]
    finally:
        conexao.close()


@pytest.fixture
def ambiente_rls(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            for model in TABELAS:
                cursor.execute(f'DROP TABLE IF EXISTS "{model._meta.db_table}" CASCADE')
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [PAPEL_TESTE])
            if cursor.fetchone():
                cursor.execute(f"DROP OWNED BY {PAPEL_TESTE}")
                cursor.execute(f"DROP ROLE {PAPEL_TESTE}")

        with connection.schema_editor() as editor:
            for model in TABELAS:
                editor.create_model(model)
                model.enable_rls()

        with connection.cursor() as cursor:
            cursor.execute(f"CREATE ROLE {PAPEL_TESTE} LOGIN PASSWORD '{SENHA_TESTE}'")
            cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_TESTE}")
            for model in TABELAS:
                tabela = model._meta.db_table
                cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabela} TO {PAPEL_TESTE}")
                cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [tabela])
                sequencia = cursor.fetchone()[0]
                cursor.execute(f"GRANT USAGE, SELECT ON SEQUENCE {sequencia} TO {PAPEL_TESTE}")
            cursor.execute(f"GRANT SELECT, REFERENCES ON workspace, workspace_vinculo, vinculo, organizacao TO {PAPEL_TESTE}")

    yield

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            for model in TABELAS:
                cursor.execute(f"REVOKE ALL ON {model._meta.db_table} FROM {PAPEL_TESTE}")
            cursor.execute(f"REVOKE ALL ON SCHEMA public FROM {PAPEL_TESTE}")
            cursor.execute(f"DROP OWNED BY {PAPEL_TESTE}")
        with connection.schema_editor() as editor:
            for model in reversed(TABELAS):
                editor.delete_model(model)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_TESTE}")


@pytest.fixture
def cenario_rls(ambiente_rls):
    usuario = criar_usuario(email="rls-workspace@example.com")
    org_a = Organizacao.objects.create(nome="Org A", slug="rls-workspace-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="rls-workspace-b")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=org_a, papel=Papel.MEMBRO)
    workspace_1 = Workspace.objects.create(organizacao=org_a, nome="Workspace 1", slug="workspace-1")
    workspace_2 = Workspace.objects.create(organizacao=org_a, nome="Workspace 2", slug="workspace-2")
    workspace_3 = Workspace.objects.create(organizacao=org_a, nome="Workspace 3", slug="workspace-3")
    workspace_foreign = Workspace.objects.create(organizacao=org_b, nome="Workspace estrangeiro", slug="workspace-foreign")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace_1, selected_for_view=True)
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace_2, selected_for_view=False)
    vinculo.current_workspace = workspace_1
    vinculo.save(update_fields=["current_workspace"])

    for organizacao, prefixo in ((org_a, "a"), (org_b, "b")):
        with organizacao_atual_privilegiada(organizacao.pk):
            RegistroWorkspaceRLS.objects.create(organizacao=organizacao, nome=f"{prefixo}-compartilhado")
            if prefixo == "a":
                RegistroWorkspaceRLS.objects.create(organizacao=organizacao, workspace=workspace_1, nome="a-workspace-1")
                RegistroWorkspaceRLS.objects.create(organizacao=organizacao, workspace=workspace_2, nome="a-workspace-2")
                RegistroWorkspaceRLS.objects.create(organizacao=organizacao, workspace=workspace_3, nome="a-workspace-3")
            else:
                RegistroWorkspaceRLS.objects.create(organizacao=organizacao, workspace=workspace_foreign, nome="b-workspace-foreign")

    return {
        "usuario": usuario,
        "org_a": org_a,
        "org_b": org_b,
        "vinculo": vinculo,
        "workspace_1": workspace_1,
        "workspace_2": workspace_2,
        "workspace_3": workspace_3,
        "workspace_foreign": workspace_foreign,
    }


def test_rls_humano_aplica_selecao_sem_filtro_orm(cenario_rls):
    cenario = cenario_rls
    assert consultar_como_papel_comum(
        organizacao_id=cenario["org_a"].pk,
        modo="membership",
        vinculo_id=cenario["vinculo"].pk,
        workspace_id=cenario["workspace_1"].pk,
    ) == ["a-compartilhado", "a-workspace-1"]

    AcessosWorkspace.selecionar_visualizacao(
        vinculo=cenario["vinculo"],
        workspace_ids={cenario["workspace_1"].pk, cenario["workspace_2"].pk},
    )

    assert consultar_como_papel_comum(
        organizacao_id=cenario["org_a"].pk,
        modo="membership",
        vinculo_id=cenario["vinculo"].pk,
        workspace_id=cenario["workspace_1"].pk,
    ) == ["a-compartilhado", "a-workspace-1", "a-workspace-2"]


def test_rls_isola_api_key_control_system_e_workspace_inativo(cenario_rls):
    cenario = cenario_rls

    def consulta(modo):
        return consultar_como_papel_comum(organizacao_id=cenario["org_a"].pk, modo=modo)

    assert consulta("api_key") == ["a-compartilhado", "a-workspace-1", "a-workspace-2", "a-workspace-3"]
    assert consulta("control") == ["a-compartilhado"]
    assert consulta("system") == ["a-compartilhado", "a-workspace-1", "a-workspace-2", "a-workspace-3"]

    cenario["workspace_3"].is_active = False
    cenario["workspace_3"].save(update_fields=["is_active"])
    assert consulta("api_key") == ["a-compartilhado", "a-workspace-1", "a-workspace-2"]
    assert consulta("system") == ["a-compartilhado", "a-workspace-1", "a-workspace-2", "a-workspace-3"]
    assert consultar_como_papel_comum(organizacao_id=cenario["org_b"].pk, modo="system") == ["b-compartilhado", "b-workspace-foreign"]


def _inserir_como_papel_comum(cenario, *, workspace_id, organizacao_id=None):
    conexao = _conectar_como_papel_comum()
    try:
        with conexao, conexao.cursor() as cursor:
            _publicar_contexto(
                cursor,
                organizacao_id=organizacao_id or cenario["org_a"].pk,
                modo="membership",
                vinculo_id=cenario["vinculo"].pk,
                workspace_id=cenario["workspace_1"].pk,
            )
            cursor.execute(
                f"INSERT INTO {RegistroWorkspaceRLS._meta.db_table} (nome, organizacao_id, workspace_id) VALUES (%s, %s, %s)",
                ["nao-permitido", organizacao_id or cenario["org_a"].pk, workspace_id],
            )
    finally:
        conexao.close()


def test_rls_with_check_rejeita_workspace_nao_selecionado_e_outra_organizacao(cenario_rls):
    cenario = cenario_rls
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _inserir_como_papel_comum(cenario, workspace_id=cenario["workspace_2"].pk)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _inserir_como_papel_comum(
            cenario,
            workspace_id=cenario["workspace_foreign"].pk,
            organizacao_id=cenario["org_b"].pk,
        )


def test_policy_usa_indice_de_vinculo_workspace(ambiente_rls):
    organizacao = Organizacao.objects.create(nome="Org índice", slug="rls-workspace-index")
    usuario = criar_usuario(email="rls-workspace-index@example.com")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspaces = [Workspace(organizacao=organizacao, nome=f"Workspace {numero}", slug=f"workspace-{numero}") for numero in range(5000)]
    Workspace.objects.bulk_create(workspaces, batch_size=500)
    VinculoWorkspace.objects.bulk_create(
        [VinculoWorkspace(vinculo=vinculo, workspace=workspace, selected_for_view=True) for workspace in workspaces],
        batch_size=500,
    )

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE workspace_vinculo")

    conexao = _conectar_como_papel_comum()
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('enable_seqscan', 'off', true)")
            cursor.execute(
                "EXPLAIN (COSTS OFF) SELECT workspace_id FROM workspace_vinculo "
                "WHERE vinculo_id = %s AND is_active AND NOT is_deleted AND selected_for_view",
                [vinculo.pk],
            )
            plano = "\n".join(linha[0] for linha in cursor.fetchall())
    finally:
        conexao.close()

    assert "wv_vinculo_ativo_sel_idx" in plano or "Index" in plano


@override_settings(ROOT_URLCONF="workspaces.tests.urls_required_model")
def test_api_key_model_required_exige_workspace_e_aceita_workspace_ativo(ambiente_rls):
    usuario = criar_usuario(email="rls-workspace-api-key@example.com")
    organizacao = Organizacao.objects.create(nome="Org API key", slug="rls-workspace-api-key")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    client = client_com_api_key(usuario, ["workspaces:create"], organizacao)

    omitido = client.post("/required/", {"nome": "Omitido"}, format="json")
    assert omitido.status_code == 409
    assert omitido.data["errors"][0]["code"] == "workspaces.current_required"

    criado = client.post("/required/", {"nome": "Criado", "workspace": workspace.pk}, format="json")
    assert criado.status_code == 201, criado.content

    workspace.is_active = False
    workspace.save(update_fields=["is_active"])
    inativo = client.post("/required/", {"nome": "Inativo", "workspace": workspace.pk}, format="json")
    assert inativo.status_code == 422
    assert any(error["field"] == "workspace" for error in inativo.data["errors"])

    outra = Organizacao.objects.create(nome="Outra API key", slug="rls-workspace-api-key-outra")
    workspace_estrangeiro = Workspace.objects.create(organizacao=outra, nome="Fora", slug="fora")
    estrangeiro = client.post("/required/", {"nome": "Estrangeiro", "workspace": workspace_estrangeiro.pk}, format="json")
    assert estrangeiro.status_code == 422
    assert any(error["field"] == "workspace" for error in estrangeiro.data["errors"])
