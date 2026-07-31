"""URLs isoladas para validar a aplicação runtime do step-up recente."""

from django.urls import path

from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

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


@no_tenancy
class RecentAuthenticationProbeAPIView(APIView):
    """APIView de teste para resolver o handler antes de ``initial()``."""

    _ignore_model_permissions = True

    @require_recent_auth()
    def post(self, request):
        return Response({"detail": "step-up aceito"}, status=status.HTTP_200_OK)


urlpatterns = [
    path("recent-auth-probe/", RecentAuthenticationProbeViewSet.as_view({"post": "create"})),
    path("recent-auth-apiview-probe/", RecentAuthenticationProbeAPIView.as_view()),
]
