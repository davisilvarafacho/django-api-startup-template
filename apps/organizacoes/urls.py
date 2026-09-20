from django.urls import include, path

from apps.api.core.routers import DefaultRouter
from apps.organizacoes.views import ConviteViewSet, OrganizacaoViewSet, TimeViewSet, VinculoViewSet

router = DefaultRouter()
router.register("organizacoes", OrganizacaoViewSet, basename="organizacao")
router.register("times", TimeViewSet, basename="time")
router.register("vinculos", VinculoViewSet, basename="vinculo")
router.register("convites", ConviteViewSet, basename="convite")

urlpatterns = [
    path("", include(router.urls)),
]
