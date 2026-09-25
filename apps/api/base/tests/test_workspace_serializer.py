from types import SimpleNamespace

from django.db import IntegrityError, connection, models, transaction

import pytest

from apps.api.autenticacao.models import TokenType
from apps.api.base.models import Base
from apps.api.base.serializers import BaseModelSerializer
from apps.api.core.errors import APIError
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario


class RegistroWorkspaceOpcional(Base):
    nome = models.CharField(max_length=50)

    class Meta:
        app_label = "base"
        db_table = "registro_workspace_opcional_serializer"


class RegistroWorkspaceObrigatorioSerializer(Base):
    workspace_required = True

    nome = models.CharField(max_length=50)

    class Meta:
        app_label = "base"
        db_table = "registro_workspace_obrigatorio_serializer"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(workspace__isnull=False),
                name="registro_workspace_serializer_not_null",
            ),
        ]


class RegistroWorkspaceOpcionalSerializer(BaseModelSerializer):
    class Meta:
        model = RegistroWorkspaceOpcional
        fields = ["id", "nome", "workspace"]


class RegistroWorkspaceObrigatorioSerializerSerializer(BaseModelSerializer):
    class Meta:
        model = RegistroWorkspaceObrigatorioSerializer
        fields = ["id", "nome", "workspace"]


def _request(*, organizacao_id, current_workspace=None, api_key=False):
    return SimpleNamespace(
        organizacao_id=organizacao_id,
        current_workspace=current_workspace,
        vinculo=None,
        auth=SimpleNamespace(type=TokenType.API_KEY if api_key else TokenType.TOKEN),
    )


@pytest.fixture
def workspace_context(db):
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace, selected_for_view=True)
    request = _request(organizacao_id=organizacao.pk, current_workspace=workspace)
    request.vinculo = vinculo
    return organizacao, workspace, request


@pytest.mark.django_db
def test_serializer_opcional_omitido_grava_workspace_nulo(workspace_context):
    organizacao, workspace, request = workspace_context
    serializer = RegistroWorkspaceOpcionalSerializer(data={"nome": "Compartilhado"}, context={"request": request})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["workspace"] is None


@pytest.mark.django_db
def test_serializer_opcional_aceita_nulo_explicito_e_workspace_autorizado(workspace_context):
    organizacao, workspace, request = workspace_context
    serializer_nulo = RegistroWorkspaceOpcionalSerializer(
        data={"nome": "Compartilhado", "workspace": None},
        context={"request": request},
    )
    serializer_workspace = RegistroWorkspaceOpcionalSerializer(
        data={"nome": "Matriz", "workspace": workspace.pk},
        context={"request": request},
    )

    assert serializer_nulo.is_valid(), serializer_nulo.errors
    assert serializer_nulo.validated_data["workspace"] is None
    assert serializer_workspace.is_valid(), serializer_workspace.errors
    assert serializer_workspace.validated_data["workspace"].pk == workspace.pk


@pytest.mark.django_db
def test_serializer_obrigatorio_omitido_resolve_workspace_atual(workspace_context):
    organizacao, workspace, request = workspace_context
    serializer = RegistroWorkspaceObrigatorioSerializerSerializer(data={"nome": "Matriz"}, context={"request": request})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["workspace"].pk == workspace.pk


@pytest.mark.django_db
def test_serializer_obrigatorio_rejeita_nulo_explicito(workspace_context):
    organizacao, workspace, request = workspace_context
    serializer = RegistroWorkspaceObrigatorioSerializerSerializer(
        data={"nome": "Inválido", "workspace": None},
        context={"request": request},
    )

    assert serializer.is_valid() is False
    assert "workspace" in serializer.errors


@pytest.mark.django_db
def test_serializer_obrigatorio_api_key_sem_workspace_atual_levanta_erro(workspace_context):
    organizacao, workspace, request = workspace_context
    request = _request(organizacao_id=organizacao.pk, api_key=True)
    serializer = RegistroWorkspaceObrigatorioSerializerSerializer(data={"nome": "API"}, context={"request": request})

    with pytest.raises(APIError) as excinfo:
        serializer.is_valid(raise_exception=True)
    assert excinfo.value.code == WorkspaceErrorCode.CURRENT_REQUIRED.value


@pytest.mark.django_db
def test_serializer_nega_workspace_de_outra_organizacao_e_inativo(workspace_context):
    organizacao, workspace, request = workspace_context
    outra = Organizacao.objects.create(nome="Outra", slug="outra")
    workspace_alheio = Workspace.objects.create(organizacao=outra, nome="Alheio", slug="alheio")
    workspace.is_active = False
    workspace.save(update_fields=["is_active"])

    for workspace_id in (workspace_alheio.pk, workspace.pk, 999999999):
        serializer = RegistroWorkspaceOpcionalSerializer(
            data={"nome": "Negado", "workspace": workspace_id},
            context={"request": request},
        )
        assert serializer.is_valid() is False
        assert "workspace" in serializer.errors


@pytest.mark.django_db(transaction=True)
def test_constraint_de_workspace_obrigatorio_rejeita_create_e_bulk_create_sem_serializer():
    organizacao = Organizacao.objects.create(nome="Acme", slug="ws-serializer-constraint")
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(RegistroWorkspaceObrigatorioSerializer)

    try:
        with organizacao_atual_privilegiada(organizacao.pk):
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    RegistroWorkspaceObrigatorioSerializer.objects.create(nome="Direto")

            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    RegistroWorkspaceObrigatorioSerializer.objects.bulk_create([RegistroWorkspaceObrigatorioSerializer(nome="Lote")])
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(RegistroWorkspaceObrigatorioSerializer)
