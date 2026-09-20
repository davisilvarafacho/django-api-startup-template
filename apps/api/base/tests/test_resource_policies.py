from dataclasses import FrozenInstanceError
from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest

from apps.api.base import views
from apps.organizacoes.models import Papel, Time


def policy_types():
    from apps.api.base.resource_policies import ActionPolicy, ResourcePolicy

    return ActionPolicy, ResourcePolicy


@pytest.mark.parametrize(
    ("action_name", "operation", "permission"),
    [
        ("list", "read", "organizacoes.view_time"),
        ("retrieve", "read", "organizacoes.view_time"),
        ("create", "create", "organizacoes.add_time"),
        ("update", "update", "organizacoes.change_time"),
        ("partial_update", "update", "organizacoes.change_time"),
        ("destroy", "delete", "organizacoes.delete_time"),
    ],
)
def test_policy_resolves_drf_action_without_request_path(action_name, operation, permission):
    _, ResourcePolicy = policy_types()
    policy = ResourcePolicy(resource="teams", minimum_roles={operation: Papel.GESTOR})
    view = SimpleNamespace(queryset=Time.objects.all(), action=action_name)
    resolved = policy.resolve(view, action_name)
    assert resolved.action == operation
    assert resolved.permission == permission
    assert resolved.minimum_role == Papel.GESTOR
    assert resolved.scope == f"teams:{operation}"
    assert policy.get_model(view) is Time


def test_custom_action_requires_explicit_permission_role_and_key_opt_in():
    ActionPolicy, ResourcePolicy = policy_types()
    policy = ResourcePolicy(
        resource="teams",
        minimum_roles={"archive": Papel.GESTOR},
        custom_actions={"arquivar": ActionPolicy(action="archive", permission="change_time")},
    )
    resolved = policy.resolve(SimpleNamespace(queryset=Time.objects.all()), "arquivar")
    assert resolved.permission == "organizacoes.change_time"
    assert resolved.api_key_allowed is False
    with pytest.raises(ImproperlyConfigured):
        policy.resolve(SimpleNamespace(queryset=Time.objects.all()), "missing")
    incomplete = ResourcePolicy(resource="teams", custom_actions={"arquivar": ActionPolicy(action="archive", permission="change_time")})
    with pytest.raises(ImproperlyConfigured, match="papel"):
        incomplete.resolve(SimpleNamespace(queryset=Time.objects.all()), "arquivar")


def test_policy_copies_and_freezes_mutable_input():
    ActionPolicy, ResourcePolicy = policy_types()
    roles = {"read": Papel.GESTOR}
    custom = {"extra": ActionPolicy(action="read", permission="view_time")}
    forbidden = {"delete"}
    policy = ResourcePolicy(resource="teams", minimum_roles=roles, custom_actions=custom, api_key_forbidden_actions=forbidden)
    roles["read"] = None
    custom.clear()
    forbidden.clear()
    assert policy.minimum_roles["read"] == Papel.GESTOR
    assert "extra" in policy.custom_actions
    assert policy.api_key_forbidden_actions == frozenset({"delete"})
    with pytest.raises(TypeError):
        policy.minimum_roles["read"] = None
    with pytest.raises(FrozenInstanceError):
        policy.resource = "other"


def test_missing_policy_is_denied_even_with_authenticated_class_permission():
    class Unconfigured(views.BaseModelViewSet):
        queryset = Time.objects.none()
        permission_classes = [IsAuthenticated]
        throttle_classes = []

        def list(self, request):
            return Response({"unsafe": True})

    request = APIRequestFactory().get("/arbitrary/prefix/")
    request.tenant_required = False
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    response = Unconfigured.as_view({"get": "list"})(request)
    assert response.status_code == 403


def test_action_and_class_permissions_are_additive_and_deduplicated():
    ActionPolicy, ResourcePolicy = policy_types()
    from apps.api.base.permissions import ModelPermissionMixin, ResourceAccessPermission
    from apps.organizacoes.permissions import TenantPermission

    class ClassExtra(BasePermission):
        pass

    class ActionExtra(BasePermission):
        pass

    class View(ModelPermissionMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
        queryset = Time.objects.all()
        permission_classes = [ClassExtra, IsAuthenticated]
        authorization_policy = ResourcePolicy(
            resource="teams", minimum_roles={"read": None}, custom_actions={"special": ActionPolicy(action="read", permission="view_time")}
        )

        @action(detail=False, permission_classes=[ActionExtra, ClassExtra])
        def special(self, request):
            return Response()

    view = View(permission_classes=[ActionExtra, ClassExtra])
    view.action = "special"
    assert [type(permission) for permission in view.get_permissions()] == [
        IsAuthenticated,
        TenantPermission,
        ResourceAccessPermission,
        ClassExtra,
        ActionExtra,
    ]


@pytest.mark.parametrize(
    ("permission_granted", "role", "expected"),
    [
        (True, Papel.GESTOR, True),
        (False, Papel.GESTOR, False),
        (True, Papel.VISUALIZADOR, False),
    ],
)
def test_session_requires_both_permission_and_role(permission_granted, role, expected):
    from apps.api.base.permissions import ResourceAccessPermission
    from apps.api.core.errors import APIError

    _, ResourcePolicy = policy_types()

    class User:
        is_authenticated = True

        def has_perm(self, permission):
            return permission == "organizacoes.change_time" and permission_granted

    class Tenant:
        def has_minimum_role(self, minimum):
            return role >= minimum

    request = SimpleNamespace(user=User(), auth=None, tenant=Tenant())
    view = SimpleNamespace(
        queryset=Time.objects.all(),
        action="partial_update",
        authorization_policy=ResourcePolicy(resource="teams", minimum_roles={"update": Papel.GESTOR}),
    )
    try:
        allowed = ResourceAccessPermission().has_permission(request, view)
    except APIError:
        allowed = False
    assert allowed is expected


@pytest.mark.parametrize(
    ("scope", "enabled", "forbidden", "expected"),
    [
        ("teams:update", True, set(), True),
        ("teams:read", True, set(), False),
        ("teams:*", True, {"update"}, False),
        ("*", True, {"update"}, False),
        ("*", False, set(), False),
    ],
)
def test_api_key_checks_availability_before_scopes_without_owner_permissions(scope, enabled, forbidden, expected):
    from apps.api.autenticacao.models import TokenType
    from apps.api.base.permissions import ResourceAccessPermission
    from apps.api.core.errors import APIError

    _, ResourcePolicy = policy_types()
    request = SimpleNamespace(
        user=None, auth=SimpleNamespace(type=TokenType.API_KEY, scopes=[scope], organization_id=1), tenant=SimpleNamespace(organization_id=1)
    )
    view = SimpleNamespace(
        queryset=Time.objects.all(),
        action="partial_update",
        authorization_policy=ResourcePolicy(
            resource="teams", minimum_roles={"update": Papel.GESTOR}, api_key_enabled=enabled, api_key_forbidden_actions=forbidden
        ),
    )
    try:
        allowed = ResourceAccessPermission().has_permission(request, view)
    except APIError:
        allowed = False
    assert allowed is expected


def test_permission_constructors_run_once_even_when_repeated():
    from apps.api.base.permissions import ModelPermissionMixin

    instances = []

    class Extra(BasePermission):
        def __init__(self):
            instances.append(self)

    class View(ModelPermissionMixin, viewsets.GenericViewSet):
        permission_classes = [Extra, Extra]

    permissions = View().get_permissions()
    assert len(instances) == 1
    assert sum(isinstance(permission, Extra) for permission in permissions) == 1


def routed_checks(*view_classes):
    from django.test import override_settings
    from django.urls import path

    from apps.api.base.policy_checks import check_resource_policies

    patterns = [path(f"view{index}/", view.as_view({"get": "list"})) for index, view in enumerate(view_classes)]
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": patterns})):
        return check_resource_policies(None)


def test_startup_check_loads_urls_and_rejects_concrete_view_without_policy():
    class Missing(views.BaseModelViewSet):
        queryset = Time.objects.none()

    assert any(error.id == "base.E001" for error in routed_checks(Missing))


@pytest.mark.parametrize("kind", ["role", "permission", "unknown_forbidden", "unknown_action", "invalid_resource", "empty_wildcard"])
def test_startup_check_rejects_incomplete_or_invalid_policies(kind):
    ActionPolicy, ResourcePolicy = policy_types()
    roles = {"read": Papel.GESTOR}
    custom = {}
    forbidden = set()
    resource = "teams"
    if kind == "role":
        roles = {}
    elif kind == "permission":
        custom = {"list": ActionPolicy(action="read", permission="does_not_exist")}
    elif kind == "unknown_forbidden":
        forbidden = {"explode"}
    elif kind == "unknown_action":
        custom = {"explode": ActionPolicy(action="read", permission="view_time")}
    elif kind == "invalid_resource":
        resource = "teams:*"
    elif kind == "empty_wildcard":
        forbidden = {"read"}
    from apps.api.base.permissions import ModelPermissionMixin

    class Invalid(ModelPermissionMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
        queryset = Time.objects.none()
        authorization_policy = ResourcePolicy(resource=resource, minimum_roles=roles, custom_actions=custom, api_key_forbidden_actions=forbidden)

    assert routed_checks(Invalid)


def test_startup_check_rejects_duplicate_resources():
    _, ResourcePolicy = policy_types()
    from apps.api.base.permissions import ModelPermissionMixin

    class One(ModelPermissionMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
        queryset = Time.objects.none()
        authorization_policy = ResourcePolicy(resource="teams", minimum_roles={"read": Papel.GESTOR})

    class Two(One):
        pass

    assert any(error.id == "base.E003" for error in routed_checks(One, Two))


@pytest.mark.parametrize(
    ("name", "action_name", "permission", "role", "available"),
    [
        ("OrganizacaoViewSet", "create", "organizacoes.add_organizacao", None, False),
        ("OrganizacaoViewSet", "encerramento", "organizacoes.change_organizacao", None, False),
        ("OrganizacaoViewSet", "retrieve", "organizacoes.view_organizacao", None, True),
        ("TimeViewSet", "destroy", "organizacoes.delete_time", Papel.GESTOR, True),
        ("VinculoViewSet", "destroy", "organizacoes.delete_vinculo", Papel.ADMINISTRADOR, False),
        ("ConviteViewSet", "aceitar", "organizacoes.can_accept_convite", None, True),
    ],
)
def test_organization_resources_declare_existing_authority(name, action_name, permission, role, available):
    from apps.organizacoes import views as organization_views

    view = getattr(organization_views, name)
    policy = getattr(view, "authorization_policy", None)
    assert policy is not None
    resolved = policy.resolve(view, action_name)
    assert (resolved.permission, resolved.minimum_role, resolved.api_key_allowed) == (permission, role, available)


def test_metadata_read_and_write_have_distinct_drf_actions_on_same_route():
    actions = {item.__name__: item for item in views.BaseModelViewSet.get_extra_actions()}
    assert actions["metadata"].mapping == {"get": "metadata", "patch": "metadata_update"}


def test_options_does_not_borrow_metadata_action_authority():
    from apps.api.base.permissions import ResourceAccessPermission
    from apps.usuarios.models import Usuario

    ActionPolicy, ResourcePolicy = policy_types()
    request = SimpleNamespace(method="OPTIONS", auth=None, user=Usuario(is_active=True, is_superuser=True), tenant_required=False)
    view = SimpleNamespace(
        queryset=Time.objects.none(),
        action="metadata",
        authorization_policy=ResourcePolicy(
            resource="teams",
            minimum_roles={"read": None},
            api_key_enabled=False,
            custom_actions={"metadata": ActionPolicy(action="read", permission="view_time")},
        ),
    )
    assert ResourceAccessPermission().has_permission(request, view) is False


def test_dynamic_queryset_uses_explicit_policy_model_without_calling_get_queryset():
    _, ResourcePolicy = policy_types()

    class Dynamic:
        def get_queryset(self):
            pytest.fail("authorization must not execute a dynamic queryset")

    policy = ResourcePolicy(resource="teams", model=Time, minimum_roles={"read": Papel.GESTOR})
    assert policy.resolve(Dynamic(), "list").permission == "organizacoes.view_time"


def test_startup_check_rejects_same_scope_with_conflicting_permissions():
    from apps.api.base.permissions import ModelPermissionMixin

    ActionPolicy, ResourcePolicy = policy_types()

    class View(ModelPermissionMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
        queryset = Time.objects.none()
        authorization_policy = ResourcePolicy(
            resource="teams",
            minimum_roles={"read": Papel.GESTOR},
            custom_actions={"special": ActionPolicy(action="read", permission="change_time", api_key_allowed=True)},
        )

        @action(detail=False)
        def special(self, request):
            return Response()

    from django.test import override_settings
    from django.urls import path

    from apps.api.base.policy_checks import check_resource_policies

    patterns = [path("list/", View.as_view({"get": "list"})), path("special/", View.as_view({"post": "special"}))]
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": patterns})):
        assert any("duplicado" in error.msg for error in check_resource_policies(None))


@pytest.mark.parametrize("attribute", ["api_key_enabled", "api_key_allowed"])
def test_startup_check_rejects_non_boolean_key_availability(attribute):
    from apps.api.base.permissions import ModelPermissionMixin

    ActionPolicy, ResourcePolicy = policy_types()

    class View(ModelPermissionMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
        queryset = Time.objects.none()
        authorization_policy = ResourcePolicy(
            resource="teams",
            minimum_roles={"read": Papel.GESTOR},
            api_key_enabled="false" if attribute == "api_key_enabled" else True,
            custom_actions={
                "list": ActionPolicy(action="read", permission="view_time", api_key_allowed="false" if attribute == "api_key_allowed" else True)
            },
        )

    assert routed_checks(View)


def test_action_permission_cannot_replace_class_permission_or_policy():
    from apps.api.base.permissions import ModelPermissionMixin
    from apps.api.base.tests.support import TenantFreeAuthenticatedRequestFactory

    ActionPolicy, ResourcePolicy = policy_types()

    class Denied(BasePermission):
        def has_permission(self, request, view):
            return False

    class View(ModelPermissionMixin, viewsets.GenericViewSet):
        queryset = Time.objects.none()
        permission_classes = [Denied]
        authorization_policy = ResourcePolicy(
            resource="teams",
            minimum_roles={"read": None},
            api_key_enabled=False,
            custom_actions={"special": ActionPolicy(action="read", permission="view_time")},
        )

        @action(detail=False, permission_classes=[])
        def special(self, request):
            return Response({"unsafe": True})

    endpoint = View.as_view({"get": "special"}, **View.special.kwargs)
    assert endpoint(TenantFreeAuthenticatedRequestFactory().get("/special/")).status_code == 403


def test_startup_check_does_not_expose_disabled_http_methods():
    from django.test import override_settings
    from django.urls import path

    from apps.api.base.permissions import ModelPermissionMixin
    from apps.api.base.policy_checks import check_resource_policies, routed_model_viewsets

    _, ResourcePolicy = policy_types()

    class View(ModelPermissionMixin, viewsets.ModelViewSet):
        queryset = Time.objects.none()
        http_method_names = ["get", "head"]
        authorization_policy = ResourcePolicy(resource="teams", minimum_roles={"read": Papel.GESTOR})

    patterns = [path("teams/", View.as_view({"get": "list", "post": "create"}))]
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": patterns})):
        assert routed_model_viewsets()[View] == {"list"}
        assert check_resource_policies(None) == []


def test_api_key_malformed_scope_container_does_not_grant_global_authority():
    from apps.api.autenticacao.models import TokenType
    from apps.api.base.permissions import ResourceAccessPermission
    from apps.api.core.errors import APIError

    _, ResourcePolicy = policy_types()
    request = SimpleNamespace(
        auth=SimpleNamespace(type=TokenType.API_KEY, scopes="*", organization_id=1),
        tenant=SimpleNamespace(organization_id=1),
    )
    view = SimpleNamespace(
        queryset=Time.objects.none(), action="list", authorization_policy=ResourcePolicy(resource="teams", minimum_roles={"read": Papel.GESTOR})
    )
    with pytest.raises(APIError) as error:
        ResourceAccessPermission().has_permission(request, view)
    assert error.value.code == "auth.insufficient_scope"
