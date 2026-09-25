from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.workspaces.views import VinculoWorkspaceViewSet, WorkspaceViewSet

router = DefaultRouter()
router.register("workspaces", WorkspaceViewSet, basename="workspace")
router.register("vinculos-workspaces", VinculoWorkspaceViewSet, basename="vinculo-workspace")

urlpatterns = [
    path("", include(router.urls)),
]
