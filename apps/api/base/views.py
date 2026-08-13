import warnings

from django.core.cache import cache
from django.db import transaction
from django.db.models import ProtectedError

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import DjangoModelPermissions
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from threadlocals.threadlocals import get_request_variable

from apps.api.core.errors import APIError, CoreErrorCode
from apps.api.core.scope_mixins import ScopeResourceMixin
from apps.api.metadata.handlers import aplicar_metadata
from apps.api.metadata.serializers import MetadataAlteracaoSerializer
from apps.logs.models import LogAlteracao
from apps.logs.serializers import LogAlteracaoSerpySerializer

from .handlers import ativar_registro, inativar_registro
from .schema import LOGS_ACTION_SCHEMA


class PermissionsViewSetMixin:
    """Mixin responsável pelas permissões adicionais por action."""

    base_permissions = {
        "grid": ["%(app_label)s.view_%(model_name)s"],
        "form": ["%(app_label)s.view_%(model_name)s"],
        "logs": ["%(app_label)s.view_%(model_name)s"],
        "bulk_create": ["%(app_label)s.add_%(model_name)s"],
        "bulk_update": ["%(app_label)s.change_%(model_name)s"],
        "clonar": ["%(app_label)s.add_%(model_name)s"],
        "ativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "inativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "invalidate_cache": ["%(app_label)s.change_%(model_name)s"],
    }

    def check_permissions(self, request):
        for permission in self.get_permissions():
            if issubclass(permission.__class__, DjangoModelPermissions):
                permission.perms_map = {**permission.perms_map, **self.base_permissions, **self.extra_permissions}

            if not permission.has_permission(request, self):
                self.permission_denied(request, message=getattr(permission, "message", None), code=getattr(permission, "code", None))


class QuerysetViewSetMixin:
    """Mixin responsável pela resolução e modificação do queryset."""

    def get_queryset(self):
        queryset = self.modify_base_queryset(super().get_queryset())

        modify_queryset_method = getattr(self, f"modify_{self.action}_queryset", None)

        if callable(modify_queryset_method):
            queryset = modify_queryset_method(queryset)

        return queryset

    def modify_base_queryset(self, queryset):
        return queryset


class ObjectCacheViewSetMixin:
    """Mixin responsável por armazenar o objeto recuperado em ``self.instance``."""

    def get_object(self):
        instance = super().get_object()
        self.instance = instance
        return instance


class SerializerViewSetMixin:
    """Mixin responsável pela resolução do serializer e de seu contexto."""

    def get_serializer_class(self, overwrite_action=None):
        assert self.serializer_classes != {} or self.serializer_class is not None, (
            f"'{self.__class__.__name__}' deve implementar o 'serializer_class' ou  'serializer_classes'."
        )

        action = overwrite_action or self.action

        if self.serializer_class:
            if self.serializer_class is not None and self.serializer_classes != {}:
                warnings.warn(
                    f"'{self.__class__.__name__}' possui o 'serializer_class' e 'serializer_classes'. O 'serializer_classes' será ignorado.",
                    stacklevel=1,
                )

            return self.serializer_class

        if action not in self.serializer_classes:
            raise AssertionError(f"'{self.__class__.__name__}' não possui o 'serializer_classes' para a ação '{action}'.")

        return self.serializer_classes[action]

    def get_aditional_serializer_context(self):
        return {}

    def get_serializer_context(self):
        context = super().get_serializer_context()
        aditional_context = self.get_aditional_serializer_context()
        return {"action": self.action, "token": get_request_variable("token"), **context, **aditional_context}


class GenericActionViewSetMixin:
    """Mixin responsável pela action genérica baseada em serializer."""

    def generic_action(self, *args, **kwargs):
        instance = None

        if "pk" in kwargs:
            self.get_object()
            instance = self.instance

        many = isinstance(self.request.data, list)
        serializer = self.get_serializer(instance=instance, data=self.request.data, many=many)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        response_status = kwargs.get("status", status.HTTP_200_OK)
        response_data = kwargs.get("data", None)
        return Response(response_data, status=response_status)


class CacheViewSetMixin:
    """Mixin responsável pelas chaves e versão do cache do recurso."""

    cache_timeout = 60

    def get_cache_namespace(self):
        model = self.queryset.model
        return f"viewcache:{model._meta.app_label}.{model._meta.model_name}"

    def get_cache_version(self):
        return cache.get_or_set(f"{self.get_cache_namespace()}:version", 1, None)

    def build_cache_key(self, *parts):
        suffix = ":".join(str(part) for part in parts)
        return f"{self.get_cache_namespace()}:{self.get_cache_version()}:{suffix}"

    def bump_cache_version(self):
        version_key = f"{self.get_cache_namespace()}:version"
        try:
            cache.incr(version_key)
        except ValueError:
            cache.set(version_key, 2, None)


class UtilsViewSetMixin(
    ScopeResourceMixin,
    PermissionsViewSetMixin,
    QuerysetViewSetMixin,
    ObjectCacheViewSetMixin,
    SerializerViewSetMixin,
    GenericActionViewSetMixin,
):
    """Fachada compatível para os comportamentos compartilhados dos ViewSets."""


class AtivarInativarViewSetMixin:
    """Mixin responsável pelas actions de ativação e inativação."""

    @action(methods=["post"], detail=True)
    def ativar(self, request, *args, **kwargs):
        instance = self.get_object()
        ativar_registro(instance)
        return Response()

    @action(methods=["post"], detail=True)
    def inativar(self, request, *args, **kwargs):
        instance = self.get_object()
        inativar_registro(instance)
        return Response()


class FormGridViewSetMixin:
    """Mixin responsável pelas actions de formulário e grid."""

    @action(methods=["get"], detail=True)
    def form(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(methods=["get"], detail=False)
    def grid(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class BulkCreateViewSetMixin:
    """Mixin responsável pela criação de registros em lote."""

    @action(methods=["post"], detail=False)
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class BulkUpdateViewSetMixin:
    """Mixin responsável pela atualização parcial e atômica em lote."""

    @action(methods=["patch"], detail=False)
    def bulk_update(self, request):
        """Atualiza vários registros de uma vez (parcial e atômico).

        Espera uma lista de objetos, cada um contendo o campo `id` para identificar
        a instância. Valida todos antes de salvar; se qualquer item falhar, nada é
        persistido.
        """
        if not isinstance(request.data, list):
            raise APIError(
                CoreErrorCode.BAD_REQUEST,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="Envie uma lista de objetos para atualizar.",
            )

        ids = [item.get("id") for item in request.data]
        if not all(ids):
            raise APIError(
                CoreErrorCode.BAD_REQUEST,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="Cada objeto deve conter o campo 'id'.",
                field="id",
            )

        instances = {obj.pk: obj for obj in self.filter_queryset(self.get_queryset()).filter(pk__in=ids)}

        serializers = []
        for item in request.data:
            instance = instances.get(item.get("id"))
            if instance is None:
                raise APIError(
                    CoreErrorCode.NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                    message=f"Registro {item.get('id')} não encontrado.",
                    field="id",
                )
            serializer = self.get_serializer(instance, data=item, partial=True)
            serializer.is_valid(raise_exception=True)
            serializers.append(serializer)

        with transaction.atomic():
            for serializer in serializers:
                self.perform_update(serializer)

        return Response([serializer.data for serializer in serializers], status=status.HTTP_200_OK)


class ClonarViewSetMixin:
    """Mixin responsável pela clonagem de registros."""

    def modify_unique_fields(self, instance):
        pass

    @action(methods=["post"], detail=True)
    def clonar(self, request, pk):
        instance = self.get_object()
        clone = instance.clonar(commit=False)
        serializer = self.get_serializer(clone, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.modify_unique_fields(clone)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MetadataViewSetMixin:
    """Action de metadata genérico do objeto.

    Ligada por padrão em `BaseModelViewSet`. Uma view que não deva aceitar
    escrita livre de chaves declara `metadata_habilitado = False`, e a action
    deixa de ser coletada pelo router — a rota não passa a existir.
    """

    metadata_habilitado = True

    @classmethod
    def get_extra_actions(cls):
        actions = super().get_extra_actions()
        if cls.metadata_habilitado:
            return actions

        return [action for action in actions if action.__name__ != "metadata"]

    @action(methods=["get", "patch"], detail=True)
    def metadata(self, request, *args, **kwargs):
        """Lê ou altera o documento de metadata do objeto.

        `PATCH` funde as chaves enviadas com as existentes; valor `null` remove
        a chave. As permissões vêm do método HTTP: `view_<model>` no `GET` e
        `change_<model>` no `PATCH`.
        """
        instance = self.get_object()

        if request.method == "GET":
            return Response({"dados": instance.raw_metadata})

        serializer = MetadataAlteracaoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registro = aplicar_metadata(instance, serializer.validated_data["dados"])
        return Response({"dados": registro.dados})


class LogsViewSetMixin:
    """Action de histórico de auditoria do objeto.

    Ligada por padrão em `BaseModelViewSet`: todo recurso publica a própria
    trilha, sem endpoint global de logs.
    """

    @LOGS_ACTION_SCHEMA
    @action(methods=["get"], detail=True)
    def logs(self, request, *args, **kwargs):
        """Devolve a trilha de auditoria do registro, do mais recente ao mais antigo.

        Não aceita filtros: o recorte já é o objeto da URL. `self.filter_queryset()`
        e `self.get_serializer()` são deliberadamente ignorados aqui — ambos
        resolvem para o filterset/serializer do recurso, não do log.

        A autorização é dupla: `view_<model>` (declarada em
        `PermissionsViewSetMixin.base_permissions`) e o próprio `get_object()`,
        que passa pelo RLS e responde 404 para objeto de outra organização.

        Args:
            request: Request da action.
            *args: Argumentos posicionais do roteamento.
            **kwargs: Argumentos nomeados do roteamento (inclui `pk`).

        Returns:
            Response paginada com os registros de `LogAlteracao` do objeto.
        """
        instance = self.get_object()
        queryset = LogAlteracao.objects.get_for_object(instance).select_related("content_type", "actor")
        page = self.paginate_queryset(queryset)

        if page is None:
            return Response(LogAlteracaoSerpySerializer(queryset, many=True).data)

        serializer = LogAlteracaoSerpySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class CacheInvalidationViewSetMixin(CacheViewSetMixin):
    """Mixin responsável pela invalidação do cache versionado do recurso."""

    @action(methods=["post"], detail=False)
    def invalidate_cache(self, request, *args, **kwargs):
        """Invalida (flush) todo o cache versionado deste recurso.

        O cache com TTL expira sozinho; este endpoint força o frescor imediato
        virando a versão do namespace do viewset.
        """
        self.bump_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GenericBaseViewSet(UtilsViewSetMixin, GenericViewSet):
    pass


class BaseModelViewSet(UtilsViewSetMixin, MetadataViewSetMixin, LogsViewSetMixin, ModelViewSet):
    queryset = None
    serializer_class = None
    serializer_classes = {}
    filterset_fields = {}
    search_fields = []
    ordering_fields = []
    extra_permissions = {}

    def perform_create(self, serializer, **overwrite):
        return serializer.save(**overwrite)

    def perform_update(self, serializer, **overwrite):
        return serializer.save(**overwrite)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError as exc:
            raise APIError(
                CoreErrorCode.CONFLICT,
                status_code=status.HTTP_409_CONFLICT,
                message="Esse registro já foi utilizado pelo sistema.",
            ) from exc
