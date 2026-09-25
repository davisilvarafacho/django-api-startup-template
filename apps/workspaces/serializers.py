from rest_framework import serializers

from apps.api.autenticacao.models import TokenType
from apps.organizacoes.models import Vinculo
from apps.workspaces.models import VinculoWorkspace, Workspace


class WorkspaceCompactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ["id", "nome", "slug"]


class WorkspaceSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()
    selected_for_view = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ["id", "nome", "slug", "is_active", "is_current", "selected_for_view"]
        read_only_fields = ["id", "is_current", "selected_for_view"]

    def get_is_current(self, obj):
        request = self.context.get("request")
        return getattr(getattr(request, "vinculo", None), "current_workspace_id", None) == obj.pk

    def get_selected_for_view(self, obj):
        request = self.context.get("request")
        if getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY:
            return obj.is_active and not obj.is_deleted

        vinculo_id = getattr(getattr(request, "vinculo", None), "pk", None)
        if vinculo_id is None:
            return False
        return obj.vinculos.filter(
            vinculo_id=vinculo_id,
            is_active=True,
            is_deleted=False,
            selected_for_view=True,
        ).exists()


class WorkspaceSelectionSerializer(serializers.Serializer):
    workspaces = serializers.PrimaryKeyRelatedField(many=True, queryset=Workspace.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        organizacao_id = getattr(request, "organizacao_id", None)
        if organizacao_id is None:
            return

        queryset = Workspace.objects.filter(organizacao_id=organizacao_id, is_active=True, is_deleted=False)
        if getattr(getattr(request, "auth", None), "type", None) != TokenType.API_KEY:
            vinculo_id = getattr(getattr(request, "vinculo", None), "pk", None)
            queryset = queryset.filter(
                vinculos__vinculo_id=vinculo_id,
                vinculos__is_active=True,
                vinculos__is_deleted=False,
            )
        self.fields["workspaces"].child_relation.queryset = queryset.distinct()


class VinculoWorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = VinculoWorkspace
        fields = ["id", "vinculo", "workspace", "is_active", "selected_for_view"]
        read_only_fields = ["id", "selected_for_view"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        organizacao_id = getattr(request, "organizacao_id", None)
        if organizacao_id is None:
            return
        self.fields["vinculo"].queryset = Vinculo.objects.filter(
            organizacao_id=organizacao_id,
            is_active=True,
            is_deleted=False,
        )
        self.fields["workspace"].queryset = Workspace.objects.filter(
            organizacao_id=organizacao_id,
            is_active=True,
            is_deleted=False,
        )
