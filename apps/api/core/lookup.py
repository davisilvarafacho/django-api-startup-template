"""Registry central e leve para lookups (autocomplete de FKs).

Resolve o caso em que um usuário tem acesso a um modelo que referencia outro por
FK, mas **não** tem permissão de ver/editar o modelo referenciado. Um modelo
opta por ser consultável com o decorator `@lookup` e passa a responder no
endpoint global `GET /lookup/<chave>/`, exigindo apenas autenticação (não a
permissão do modelo-alvo). A chave padrão é o nome do modelo.

Uso simples (saída `{id, label}`):

    from apps.api.core.lookup import lookup

    @lookup(search=["razao_social", "cnpj"])
    class Fornecedor(Base):
        def __str__(self):
            return self.razao_social

Uso com serializer próprio (ex.: dados mínimos do pai em cabeçalho/item). Trate o
N+1 de uma das duas formas:

    # 1) no registro (relação fixa e simples):
    @lookup(search=["descricao"], serializer=ItemLookupSerializer, select_related=["pedido"])
    class ItemPedido(Base):
        ...

    # 2) co-localizado no serializer (recomendado quando a otimização depende dos
    #    campos do próprio serializer); a view aplica automaticamente:
    class ItemLookupSerializer(LookupSerializer):
        pedido_numero = serializers.CharField(source="pedido.numero")

        @staticmethod
        def setup_eager_loading(queryset):
            return queryset.select_related("pedido")

O registry é a fronteira de segurança: modelo não registrado responde 404.
"""

from django.core.exceptions import ImproperlyConfigured

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.filters import SearchFilter
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated


class LookupSerializer(serializers.Serializer):
    """Serializer padrão do lookup: `{id, label}` com o label vindo de `__str__`.

    Para outra saída (label de um campo específico, dados do pai etc.), passe um
    serializer próprio em `@lookup(serializer=...)`.
    """

    id = serializers.IntegerField(read_only=True)
    label = serializers.SerializerMethodField()

    def get_label(self, obj):
        return str(obj)


class LookupConfig:
    """Configuração de um modelo registrado no lookup."""

    def __init__(self, model, *, search, serializer, select_related, prefetch_related, ordering):
        self.model = model
        self.search_fields = list(search or [])
        self.serializer_class = serializer or LookupSerializer
        self.select_related = list(select_related or [])
        self.prefetch_related = list(prefetch_related or [])
        self.ordering = list(ordering or [])

    def get_queryset(self):
        queryset = self.model._default_manager.all()

        if self.select_related:
            queryset = queryset.select_related(*self.select_related)

        if self.prefetch_related:
            queryset = queryset.prefetch_related(*self.prefetch_related)

        if self.ordering:
            queryset = queryset.order_by(*self.ordering)

        return queryset


class LookupRegistry:
    """Registry central. Alimentado pelo decorator; consumido pela `LookupView`."""

    def __init__(self):
        self._registry = {}

    def register(self, model, *, key=None, search=None, serializer=None, select_related=None, prefetch_related=None, ordering=None):
        resolved_key = key or model._meta.model_name

        existing = self._registry.get(resolved_key)
        if existing is not None and existing.model is not model:
            raise ImproperlyConfigured(
                f"Chave de lookup '{resolved_key}' já registrada por "
                f"{existing.model.__name__}. Defina uma chave explícita: "
                f"@lookup('{resolved_key}_<contexto>', ...)."
            )

        self._registry[resolved_key] = LookupConfig(
            model,
            search=search,
            serializer=serializer,
            select_related=select_related,
            prefetch_related=prefetch_related,
            ordering=ordering,
        )
        return resolved_key

    def get(self, key):
        return self._registry.get(key)


registry = LookupRegistry()


def lookup(key=None, *, search=None, serializer=None, select_related=None, prefetch_related=None, ordering=None):
    """Registra o modelo decorado no lookup central.

    Args:
        key: Identificador na URL. Padrão: nome do modelo (`model_name`).
        search: Campos usados na busca por `?search=`.
        serializer: Serializer DRF de saída. Padrão: `{id, label}` via `__str__`.
        select_related: FKs/O2O a pré-carregar (evita N+1 em serializers com pai).
        prefetch_related: M2O/M2M a pré-carregar (evita N+1 em coleções).
        ordering: Ordenação padrão do queryset.
    """

    def decorator(model):
        registry.register(
            model,
            key=key,
            search=search,
            serializer=serializer,
            select_related=select_related,
            prefetch_related=prefetch_related,
            ordering=ordering,
        )
        return model

    return decorator


class LookupView(ListAPIView):
    """Endpoint global e dinâmico de lookup: `GET /lookup/<chave>/?search=...`.

    Sobrescreve as permissões padrão para exigir apenas autenticação — o acesso
    é liberado pelo registro (opt-in), não pela permissão do modelo-alvo.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [SearchFilter]
    serializer_class = LookupSerializer  # default p/ o schema; sobrescrito em runtime

    def get_config(self):
        config = registry.get(self.kwargs["key"])
        if config is None:
            raise NotFound(f"Lookup '{self.kwargs['key']}' não encontrado.")
        return config

    @property
    def search_fields(self):
        return self.get_config().search_fields

    def get_queryset(self):
        config = self.get_config()
        queryset = config.get_queryset()

        # Otimização co-localizada no serializer (ideal p/ cabeçalho/item que lê
        # o pai): trata o N+1 que surge ao percorrer relações na serialização.
        setup_eager_loading = getattr(config.serializer_class, "setup_eager_loading", None)
        if callable(setup_eager_loading):
            queryset = setup_eager_loading(queryset)

        return queryset

    def get_serializer_class(self):
        return self.get_config().serializer_class
