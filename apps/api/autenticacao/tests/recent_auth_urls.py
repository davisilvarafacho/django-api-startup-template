"""URLs isoladas para validar a aplicação runtime do step-up recente."""

from django.urls import path

from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.api.autenticacao.models import AuthToken
from apps.api.autenticacao.recent_auth import require_recent_auth
from apps.api.core.route_markers import no_tenancy


@no_tenancy
class RecentAuthenticationProbeViewSet(viewsets.ViewSet):
    """View de teste sem sobrescrever permissões para exercitar o padrão global."""

    queryset = AuthToken.objects.all()
    _ignore_model_permissions = True

    @require_recent_auth()
    def create(self, request):
        return Response({"detail": "step-up aceito"}, status=status.HTTP_200_OK)


urlpatterns = [
    path("recent-auth-probe/", RecentAuthenticationProbeViewSet.as_view({"post": "create"})),
]
