from rest_framework import status
from rest_framework.response import Response

from apps.api.base.views import BaseModelViewSet

from .serializers import ParametroAlteracaoSerializer, ParametroVisualizacaoSerializer


class ParametroViewSet(BaseModelViewSet):
    serializer_classes = {
        "list": ParametroVisualizacaoSerializer,
        "retrieve": ParametroVisualizacaoSerializer,
        "parital_update": ParametroAlteracaoSerializer,
    }

    def create(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
