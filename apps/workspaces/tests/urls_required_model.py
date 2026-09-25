from django.db import models
from django.urls import include, path

from rest_framework import viewsets
from rest_framework.routers import DefaultRouter

from apps.api.base.models import Base
from apps.api.base.serializers import BaseModelSerializer


class RegistroWorkspaceRLSRequired(Base):
    api_scope_resource = "required"
    workspace_required = True

    nome = models.CharField(max_length=50)

    class Meta:
        app_label = "workspaces"
        db_table = "registro_workspace_rls_required_teste"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(workspace__isnull=False),
                name="registro_workspace_rls_required_not_null",
            ),
        ]


class RegistroWorkspaceRLSRequiredSerializer(BaseModelSerializer):
    class Meta:
        model = RegistroWorkspaceRLSRequired
        fields = ["id", "nome", "workspace"]


class RegistroWorkspaceRLSRequiredViewSet(viewsets.ModelViewSet):
    permission_classes = []
    serializer_class = RegistroWorkspaceRLSRequiredSerializer
    queryset = RegistroWorkspaceRLSRequired.objects.all()


router = DefaultRouter()
router.register("required", RegistroWorkspaceRLSRequiredViewSet, basename="required")

urlpatterns = [
    path("", include(router.urls)),
]
