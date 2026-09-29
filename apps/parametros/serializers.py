import serpy

from apps.api.base.serializers import BaseModelSerializer, BaseModelSerpySerializer

from .models import Parametro


class ParametroVisualizacaoSerializer(BaseModelSerpySerializer):
    code = serpy.StrField()
    description = serpy.StrField()
    value = serpy.StrField()


class ParametroAlteracaoSerializer(BaseModelSerializer):
    class Meta:
        model = Parametro
        fields = ["value"]
