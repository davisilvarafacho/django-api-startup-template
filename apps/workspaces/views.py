from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.permissions import TokenScopePermission
from apps.api.core.route_markers import no_workspace
from apps.api.core.scope_mixins import ScopeResourceMixin
from apps.organizacoes.models import Papel
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.models import VinculoWorkspace, Workspace
from apps.workspaces.schema import document_workspace_current, document_workspace_selection
from apps.workspaces.serializers import (
    VinculoWorkspaceSerializer,
    WorkspaceSelectionSerializer,
    WorkspaceSerializer,
)
from apps.workspaces.workspaces import Workspaces


def _validar_papel_ator(request):
    return getattr(getattr(request, "auth", None), "type", None) != TokenType.API_KEY


class WorkspaceViewSetMixin(ScopeResourceMixin):
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, PapelMinimoPermission]
    papel_minimo = Papel.VISUALIZADOR

    def get_organizacao_id(self):
        return self.request.organizacao_id


@no_workspace
class WorkspaceViewSet(WorkspaceViewSetMixin, viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    queryset = Workspace.objects.select_related("organizacao")
    scope_resource = "workspaces"
    session_only_actions = {"atual", "visualizacao"}
    papeis_por_action = {
        "list": Papel.VISUALIZADOR,
        "retrieve": Papel.VISUALIZADOR,
        "atual": Papel.VISUALIZADOR,
        "visualizacao": Papel.VISUALIZADOR,
        "create": Papel.ADMINISTRADOR,
        "update": Papel.ADMINISTRADOR,
        "partial_update": Papel.ADMINISTRADOR,
        "destroy": Papel.ADMINISTRADOR,
    }

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(
                organizacao_id=self.get_organizacao_id(),
                is_active=True,
                is_deleted=False,
            )
        )
        if getattr(getattr(self.request, "auth", None), "type", None) == TokenType.API_KEY:
            return queryset.order_by("nome")

        return (
            queryset.filter(
                vinculos__vinculo_id=self.request.vinculo.pk,
                vinculos__is_active=True,
                vinculos__is_deleted=False,
            )
            .distinct()
            .order_by("nome")
        )

    def get_serializer_class(self):
        if self.action == "visualizacao":
            return WorkspaceSelectionSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        workspace = Workspaces.criar(
            organizacao=self.request.tenant.organizacao,
            nome=serializer.validated_data["nome"],
            slug=serializer.validated_data["slug"],
            ator=self.request.user,
            validar_papel_ator=_validar_papel_ator(self.request),
        )
        serializer.instance = workspace

    def perform_update(self, serializer):
        if "is_active" in serializer.validated_data and serializer.validated_data["is_active"] is False:
            Workspaces.inativar(
                serializer.instance,
                ator=self.request.user,
                validar_papel_ator=_validar_papel_ator(self.request),
            )
            serializer.instance.refresh_from_db()
            return

        if not serializer.instance.is_active:
            serializer.validated_data.pop("is_active", None)
        serializer.save()

    def perform_destroy(self, instance):
        Workspaces.inativar(
            instance,
            ator=self.request.user,
            validar_papel_ator=_validar_papel_ator(self.request),
        )

    @action(detail=True, methods=["post"], url_path="atual")
    @document_workspace_current
    def atual(self, request, pk=None):
        workspace = self.get_object()
        vinculo = AcessosWorkspace.definir_atual(vinculo=request.vinculo, workspace=workspace)
        return Response({"current_workspace": vinculo.current_workspace_id}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["put"], url_path="visualizacao")
    @document_workspace_selection
    def visualizacao(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        acessos = AcessosWorkspace.selecionar_visualizacao(
            vinculo=request.vinculo,
            workspace_ids={workspace.pk for workspace in serializer.validated_data["workspaces"]},
        )
        return Response(
            {
                "workspaces": WorkspaceSerializer(
                    [acesso.workspace for acesso in acessos],
                    many=True,
                    context={"request": request},
                ).data
            },
            status=status.HTTP_200_OK,
        )


@no_workspace
class VinculoWorkspaceViewSet(WorkspaceViewSetMixin, viewsets.ModelViewSet):
    serializer_class = VinculoWorkspaceSerializer
    queryset = VinculoWorkspace.objects.select_related("vinculo", "workspace")
    papeis_por_action = {
        "list": Papel.VISUALIZADOR,
        "retrieve": Papel.VISUALIZADOR,
        "create": Papel.ADMINISTRADOR,
        "update": Papel.ADMINISTRADOR,
        "partial_update": Papel.ADMINISTRADOR,
        "destroy": Papel.ADMINISTRADOR,
    }

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                vinculo__organizacao_id=self.get_organizacao_id(),
                vinculo__is_active=True,
                vinculo__is_deleted=False,
                workspace__is_active=True,
                workspace__is_deleted=False,
            )
            .order_by("vinculo_id", "workspace_id")
        )

    def perform_create(self, serializer):
        serializer.instance = AcessosWorkspace.conceder(
            vinculo=serializer.validated_data["vinculo"],
            workspace=serializer.validated_data["workspace"],
            ator=self.request.user,
            validar_papel_ator=_validar_papel_ator(self.request),
        )

    def perform_update(self, serializer):
        acesso = serializer.instance
        is_active = serializer.validated_data.get("is_active")
        if is_active is False:
            AcessosWorkspace.revogar(
                acesso,
                ator=self.request.user,
                validar_papel_ator=_validar_papel_ator(self.request),
            )
            acesso.refresh_from_db()
            serializer.instance = acesso
            return
        if is_active is True:
            serializer.instance = AcessosWorkspace.conceder(
                vinculo=acesso.vinculo,
                workspace=acesso.workspace,
                ator=self.request.user,
                validar_papel_ator=_validar_papel_ator(self.request),
            )
            return
        serializer.save()

    def perform_destroy(self, instance):
        AcessosWorkspace.revogar(
            instance,
            ator=self.request.user,
            validar_papel_ator=_validar_papel_ator(self.request),
        )
