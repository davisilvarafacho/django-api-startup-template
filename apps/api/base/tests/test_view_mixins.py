import pytest

from apps.api.autenticacao.permissions import CustomDjangoModelPermissions
from apps.api.base import views


@pytest.mark.parametrize(
    ("mixin_name", "methods"),
    [
        ("PermissionsViewSetMixin", ["check_permissions"]),
        ("QuerysetViewSetMixin", ["get_queryset", "modify_base_queryset"]),
        ("ObjectCacheViewSetMixin", ["get_object"]),
        (
            "SerializerViewSetMixin",
            ["get_serializer_class", "get_aditional_serializer_context", "get_serializer_context"],
        ),
        ("GenericActionViewSetMixin", ["generic_action"]),
        (
            "CacheViewSetMixin",
            ["get_cache_namespace", "get_cache_version", "build_cache_key", "bump_cache_version"],
        ),
        ("AtivarInativarViewSetMixin", ["ativar", "inativar"]),
        ("FormGridViewSetMixin", ["form", "grid"]),
        ("BulkCreateViewSetMixin", ["bulk_create"]),
        ("BulkUpdateViewSetMixin", ["bulk_update"]),
        ("ClonarViewSetMixin", ["clonar", "modify_unique_fields"]),
        ("CacheInvalidationViewSetMixin", ["invalidate_cache"]),
    ],
)
def test_view_mixins_expoem_metodos_de_sua_responsabilidade(mixin_name, methods):
    mixin = getattr(views, mixin_name)

    for method in methods:
        assert method in mixin.__dict__


def test_utils_viewset_mixin_compoe_comportamentos_compartilhados():
    expected_mixins = (
        views.PermissionsViewSetMixin,
        views.QuerysetViewSetMixin,
        views.ObjectCacheViewSetMixin,
        views.SerializerViewSetMixin,
        views.GenericActionViewSetMixin,
    )

    assert all(issubclass(views.UtilsViewSetMixin, mixin) for mixin in expected_mixins)


def test_base_model_viewset_expoe_apenas_crud_e_metadata():
    """Metadata é a única action ligada por padrão; as demais são opt-in.

    A exceção é deliberada: metadata é ponto de extensão do consumidor da API e
    precisa existir em todo recurso sem cerimônia. Quem não deve aceitar
    escrita livre de chaves declara `metadata_habilitado = False`.
    """
    action_names = {action.__name__ for action in views.BaseModelViewSet.get_extra_actions()}

    assert action_names == {"metadata"}
    assert not hasattr(views.BaseModelViewSet, "has_is_active_field")


def test_metadata_pode_ser_desligada_no_viewset():
    class ViewSetSemMetadata(views.BaseModelViewSet):
        metadata_habilitado = False

    assert ViewSetSemMetadata.get_extra_actions() == []


def test_actions_de_modelo_sao_opt_in():
    class ViewSetComActions(
        views.AtivarInativarViewSetMixin,
        views.FormGridViewSetMixin,
        views.BulkCreateViewSetMixin,
        views.BulkUpdateViewSetMixin,
        views.ClonarViewSetMixin,
        views.CacheInvalidationViewSetMixin,
        views.BaseModelViewSet,
    ):
        pass

    action_names = {action.__name__ for action in ViewSetComActions.get_extra_actions()}

    assert action_names == {
        "ativar",
        "bulk_create",
        "bulk_update",
        "clonar",
        "form",
        "grid",
        "inativar",
        "invalidate_cache",
        # Única action não opt-in; ver `test_base_model_viewset_expoe_apenas_crud_e_metadata`.
        "metadata",
    }


def test_values_e_lookup_nao_fazem_parte_das_views_base():
    assert not hasattr(views, "ValuesViewSetMixin")
    assert not hasattr(views, "LookupViewSetMixin")


@pytest.mark.parametrize("action_name", ["ativar", "inativar"])
def test_ativacao_e_inativacao_usam_post(action_name):
    action_method = getattr(views.AtivarInativarViewSetMixin, action_name)

    assert action_method.mapping == {"post": action_name}


def test_clonagem_usa_post():
    assert views.ClonarViewSetMixin.clonar.mapping == {"post": "clonar"}


def test_invalidacao_de_cache_inclui_infraestrutura_de_cache():
    assert issubclass(views.CacheInvalidationViewSetMixin, views.CacheViewSetMixin)
    assert views.CacheViewSetMixin.cache_timeout == 60
    assert not hasattr(views.BaseModelViewSet, "cache_timeout")


def test_permissions_mapeiam_actions_para_operacoes_crud():
    assert views.PermissionsViewSetMixin.base_permissions == {
        "grid": ["%(app_label)s.view_%(model_name)s"],
        "form": ["%(app_label)s.view_%(model_name)s"],
        "bulk_create": ["%(app_label)s.add_%(model_name)s"],
        "bulk_update": ["%(app_label)s.change_%(model_name)s"],
        "clonar": ["%(app_label)s.add_%(model_name)s"],
        "ativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "inativar": ["%(app_label)s.can_toggle_%(model_name)s"],
        "invalidate_cache": ["%(app_label)s.change_%(model_name)s"],
    }


def test_permissions_nao_mantem_action_de_lookup_removida():
    assert "lookup" not in CustomDjangoModelPermissions.perms_map
