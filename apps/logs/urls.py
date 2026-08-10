from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.logs.views import LogAlteracaoViewSet

router = DefaultRouter()
router.register("logs-alteracao", LogAlteracaoViewSet, basename="log-alteracao")

urlpatterns = [
    path("", include(router.urls)),
]
