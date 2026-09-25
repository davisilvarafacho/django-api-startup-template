from rest_framework import serializers

from drf_spectacular.utils import extend_schema

from apps.api.core.schema import document_error_codes
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.serializers import WorkspaceSelectionSerializer, WorkspaceSerializer


class CurrentWorkspaceResponseSerializer(serializers.Serializer):
    current_workspace = serializers.IntegerField()


class WorkspaceSelectionResponseSerializer(serializers.Serializer):
    workspaces = WorkspaceSerializer(many=True)


document_workspace_current = extend_schema(
    request=None,
    responses={
        200: CurrentWorkspaceResponseSerializer,
        409: document_error_codes(WorkspaceErrorCode.ACCESS_REQUIRED, WorkspaceErrorCode.WORKSPACE_INACTIVE),
    },
)


document_workspace_selection = extend_schema(
    request=WorkspaceSelectionSerializer,
    responses={
        200: WorkspaceSelectionResponseSerializer,
        409: document_error_codes(WorkspaceErrorCode.INVALID_SELECTION),
    },
)
