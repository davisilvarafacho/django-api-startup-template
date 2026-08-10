from rest_framework.viewsets import ReadOnlyModelViewSet

from drf_spectacular.utils import extend_schema_view

from .filtersets import LogAlteracaoFilterSet
from .models import LogAlteracao
from .schema import LOG_ALTERACAO_SCHEMA
from .serializers import LogAlteracaoSerpySerializer


@extend_schema_view(**LOG_ALTERACAO_SCHEMA)
class LogAlteracaoViewSet(ReadOnlyModelViewSet):
    queryset = LogAlteracao.objects.select_related("content_type", "actor")
    serializer_class = LogAlteracaoSerpySerializer
    filterset_class = LogAlteracaoFilterSet
    search_fields = ("object_repr", "actor_email")
    ordering_fields = ("timestamp", "action", "object_id")
    ordering = ("-timestamp",)
