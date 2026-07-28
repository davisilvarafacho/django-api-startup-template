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
from apps.api.core.scope_registry import ScopeAction

from .handlers import ativar_registro, inativar_registro

# Mapeamento imutável: action padrão do ViewSet -> action CRUD do scope registry.
CRUD_ACTIONS_BY_VIEWSET_ACTION = {
    "list": ScopeAction.READ,
    "retrieve": ScopeAction.READ,
    "create": ScopeAction.CREATE,
    "update": ScopeAction.UPDATE,
    "partial_update": ScopeAction.UPDATE,
    "destroy": ScopeAction.DELETE,
}


class UtilsViewSetMixin:
    """Mixin com métodos utilitários para ViewSets."""

    def get_scope_resource(self):
        """Recurso público (`resource:action`) desta view.

        Usa `scope_resource` quando declarado na própria view (override); caso
        contrário, cai para o default do model consultado pelo `queryset`.
        """
        override = getattr(self, "scope_resource", None)
        if override is not None:
            return override

        queryset = getattr(self, "queryset", None)
        model = queryset.model if queryset is not None else None
        return getattr(model, "api_scope_resource", None) if model is not None else None

    def get_required_token_scopes(self):
        """Scopes exigidos pela action corrente: decorator > CRUD derivado do recurso."""
        action_name = getattr(self, "action", None)
        action_method = getattr(self, action_name, None) if action_name else None
        declared = getattr(action_method, "_required_token_scopes", None)
        if declared is not None:
            return list(declared)

        resource = self.get_scope_resource()
        if resource is None:
            return []

        crud_action = CRUD_ACTIONS_BY_VIEWSET_ACTION.get(action_name)
        if crud_action is None:
            return []

        return [f"{resource}:{crud_action.value}"]

    def check_permissions(self, request):
        base_permissions = {
            "grid": ["%(app_label)s.view_%(model_name)s"],
            "form": ["%(app_label)s.view_%(model_name)s"],
            "ativar": ["%(app_label)s.can_toggle_%(model_name)s"],
            "inativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        }

        for permission in self.get_permissions():
            if issubclass(permission.__class__, DjangoModelPermissions):
                permission.perms_map = {**permission.perms_map, **base_permissions, **self.extra_permissions}

            if not permission.has_permission(request, self):
                self.permission_denied(
                    request, message=getattr(permission, "message", None), code=getattr(permission, "code", None)
                )

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

    def get_queryset(self):
        queryset = self.modify_base_queryset(super().get_queryset())

        modify_queryset_method = getattr(self, f"modify_{self.action}_queryset", None)

        if callable(modify_queryset_method):
            queryset = modify_queryset_method(queryset)

        return queryset

    def modify_base_queryset(self, queryset):
        return queryset

    def get_object(self):
        instance = super().get_object()
        self.instance = instance
        return instance

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
            raise AssertionError(
                f"'{self.__class__.__name__}' não possui o 'serializer_classes' para a ação '{action}'."
            )

        return self.serializer_classes[action]

    def get_aditional_serializer_context(self):
        return {}

    def get_serializer_context(self):
        context = super().get_serializer_context()
        aditional_context = self.get_aditional_serializer_context()
        return {"action": self.action, "token": get_request_variable("token"), **context, **aditional_context}

    # ---- cache ----
    # Namespace versionado por modelo. As views que cacheiam resposta com TTL
    # devem montar a chave com `build_cache_key(...)`; a action `invalidate_cache`
    # vira a versão do namespace e torna todas as chaves antigas inalcançáveis
    # (invalidação em massa O(1), sem depender de `delete_pattern`).

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


class GenericBaseViewSet(UtilsViewSetMixin, GenericViewSet):
    pass


class BaseModelViewSet(UtilsViewSetMixin, ModelViewSet):
    queryset = None
    serializer_class = None
    serializer_classes = {}
    filterset_fields = {}
    search_fields = []
    ordering_fields = []
    extra_permissions = {}
    has_ativo_field = True
    cache_timeout = 60  # TTL padrão (segundos) para caches deste viewset

    def perform_create(self, serializer, **overwrite):
        return serializer.save(**overwrite)

    def perform_update(self, serializer, **overwrite):
        return serializer.save(**overwrite)

    def modify_unique_fields(self, instance):
        pass

    def list(self, request, *args, **kwargs):
        fields = self.queryset.model.get_serializable_column_names()
        queryset = self.filter_queryset(self.get_queryset()).values(*fields)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(page)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        return Response(instance.as_dict())

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

    @action(methods=["post"], detail=False)
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

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

    @action(methods=["get"], detail=True)
    def clonar(self, request, pk):
        instance = self.get_object()
        clone = instance.clonar()
        self.modify_unique_fields(clone)
        clone.save()
        serializer = self.get_serializer(clone)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def values(self, request):
        values = request.query_params.get("values", None)
        if values is None:
            return Response({"values": "Essa query é obrigatória"}, status=status.HTTP_400_BAD_REQUEST)

        values = values.split(",")
        queryset = self.filter_queryset(self.get_queryset()).values(*values)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(page)

    @action(detail=True, methods=["get"])
    def lookup(self, request, pk=None):
        return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)

    @action(methods=["post"], detail=False)
    def invalidate_cache(self, request, *args, **kwargs):
        """Invalida (flush) todo o cache versionado deste recurso.

        O cache com TTL expira sozinho; este endpoint força o frescor imediato
        virando a versão do namespace do viewset.
        """
        self.bump_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if has_ativo_field:
        @action(methods=["get"], detail=True)
        def ativar(self, request, *args, **kwargs):
            instance = self.get_object()
            ativar_registro(instance)
            return Response()

        @action(methods=["get"], detail=True)
        def inativar(self, request, *args, **kwargs):
            instance = self.get_object()
            inativar_registro(instance)
            return Response()

