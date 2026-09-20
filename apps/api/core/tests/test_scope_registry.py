from dataclasses import replace

from django.core.exceptions import ImproperlyConfigured

import pytest

from apps.api.core.scope_registry import (
    ScopeRegistry,
    discover_scope_resources,
    matches_scope,
    parse_scope,
    required_django_permissions,
    scope_registry,
)
from apps.organizacoes.models import Time
from apps.organizacoes.views import ConviteViewSet, TimeViewSet
from tests.support.usuarios import criar_usuario


def test_parse_scope_global_wildcard():
    assert parse_scope("*") == ("*", "*")


def test_parse_scope_recurso_e_action():
    assert parse_scope("organizations:read") == ("organizations", "read")


def test_parse_scope_wildcard_de_recurso():
    assert parse_scope("organizations:*") == ("organizations", "*")


@pytest.mark.parametrize("valor", ["organizations", "Organizations:read", "org anizations:read", "org:Read", "teams:read\n", "teams\n:read"])
def test_parse_scope_rejeita_formato_invalido(valor):
    with pytest.raises(ValueError, match="[Ss]cope|[Rr]ecurso|[Aa]ction"):
        parse_scope(valor)


@pytest.mark.parametrize(
    ("granted", "required"),
    [
        ("organizations:read", "organizations:read"),
        ("organizations:*", "organizations:delete"),
        ("*", "teams:update"),
    ],
)
def test_scope_concedido_satisfaz_requisito(granted, required):
    assert matches_scope(granted, required)


@pytest.mark.parametrize(
    ("granted", "required"),
    [
        ("organizations:read", "organizations:update"),
        ("organizations:*", "teams:read"),
        ("teams:read", "organizations:read"),
    ],
)
def test_scope_concedido_nao_satisfaz_requisito(granted, required):
    assert not matches_scope(granted, required)


def test_registry_rejeita_recurso_duplicado():
    registry = ScopeRegistry()
    registry.register(TimeViewSet, {"list", "create", "partial_update", "destroy"})

    with pytest.raises(ImproperlyConfigured, match="teams"):
        registry.register(TimeViewSet, {"list", "create", "partial_update", "destroy"})


def test_registry_rejeita_nome_de_recurso_invalido():
    registry = ScopeRegistry()

    class InvalidView(TimeViewSet):
        authorization_policy = replace(TimeViewSet.authorization_policy, resource="Teams")

    with pytest.raises(ImproperlyConfigured):
        registry.register(InvalidView, {"list"})


def test_registry_deriva_permissoes_das_actions_expostas():
    registry = ScopeRegistry()
    definition = registry.register(TimeViewSet, {"list", "create", "partial_update", "destroy"})

    assert definition.action_permissions == {
        "read": "organizacoes.view_time",
        "create": "organizacoes.add_time",
        "update": "organizacoes.change_time",
        "delete": "organizacoes.delete_time",
    }


def test_registry_mapeia_action_customizada_para_permission_django():
    registry = ScopeRegistry()
    definition = registry.register(ConviteViewSet, {"aceitar"})

    assert definition.action_permissions["accept"] == "organizacoes.can_accept_convite"


def test_registry_lookup():
    registry = ScopeRegistry()
    registry.register(TimeViewSet, {"list", "create", "partial_update", "destroy"})

    assert registry.lookup("teams").model is Time
    assert registry.lookup("nao_existe") is None


@pytest.fixture
def registro_isolado(monkeypatch):
    registry = ScopeRegistry()
    registry.register(TimeViewSet, {"list", "create", "partial_update", "destroy"})
    registry.discovered = True
    monkeypatch.setattr("apps.api.core.scope_registry.scope_registry", registry)
    return registry


def test_required_django_permissions_action_concreta(registro_isolado):
    assert required_django_permissions("teams:read") == ["organizacoes.view_time"]


def test_required_django_permissions_wildcard_de_recurso(registro_isolado):
    permissoes = required_django_permissions("teams:*")

    assert set(permissoes) == {
        "organizacoes.view_time",
        "organizacoes.add_time",
        "organizacoes.change_time",
        "organizacoes.delete_time",
    }


def test_required_django_permissions_wildcard_global(registro_isolado):
    permissoes = required_django_permissions("*")

    assert set(permissoes) == {
        "organizacoes.view_time",
        "organizacoes.add_time",
        "organizacoes.change_time",
        "organizacoes.delete_time",
    }


def test_required_django_permissions_recurso_desconhecido(registro_isolado):
    with pytest.raises(ImproperlyConfigured):
        required_django_permissions("desconhecido:read")


def test_required_django_permissions_action_desconhecida(registro_isolado):
    with pytest.raises(ImproperlyConfigured):
        required_django_permissions("teams:voar")


def test_discover_scope_resources_e_idempotente_e_sem_inconsistencias():
    discover_scope_resources(force=True)

    assert scope_registry.check() == []
    assert scope_registry.lookup("invitations").action_permissions["accept"] == "organizacoes.can_accept_convite"

    # Chamar de novo não deve levantar por recurso duplicado.
    discover_scope_resources()


@pytest.mark.django_db
def test_display_permissions_for_traduz_permissions_django_para_resource_action(registro_isolado):
    from django.contrib.auth.models import Permission

    usuario = criar_usuario()
    permission = Permission.objects.get(content_type__app_label="organizacoes", codename="view_time")
    usuario.user_permissions.add(permission)
    usuario = type(usuario).objects.get(pk=usuario.pk)

    assert registro_isolado.display_permissions_for(usuario) == ["teams:read"]

    discover_scope_resources(force=True)


def test_discovery_uses_only_exposed_policy_operations():
    registry = discover_scope_resources(ScopeRegistry())
    memberships = registry.lookup("memberships")
    assert set(memberships.action_permissions) == {"read", "update", "delete"}
    assert registry.expand("memberships:*") == ["memberships:read", "memberships:update"]
    assert "memberships:delete" not in registry.expand("*")
    assert registry.lookup("users") is None
    assert "invitations:accept" in registry.expand("invitations:*")


def test_failed_rediscovery_cannot_reuse_previous_scopes(monkeypatch):
    registry = discover_scope_resources(ScopeRegistry())

    class MissingPolicy(TimeViewSet):
        authorization_policy = None

    monkeypatch.setattr("apps.api.base.policy_checks.routed_model_viewsets", lambda: {MissingPolicy: {"list"}})
    with pytest.raises(ImproperlyConfigured):
        discover_scope_resources(registry, force=True)
    assert registry.discovered is False
    assert registry.all_resources() == {}


def test_disabled_resource_has_no_wildcard_expansion():
    from apps.api.core.scope_registry import ScopeNotAvailable

    class DisabledView(TimeViewSet):
        authorization_policy = replace(TimeViewSet.authorization_policy, api_key_enabled=False)

    registry = ScopeRegistry()
    registry.register(DisabledView, {"list"})
    assert registry.lookup("teams").unavailable_actions == {"read"}
    for scope in ("teams:read", "teams:*", "*"):
        with pytest.raises(ScopeNotAvailable):
            registry.expand(scope)


@pytest.mark.parametrize("credential", ["session", "api_key", "anonymous", "missing_tenant"])
def test_routed_api_root_preserves_explicit_session_and_tenant_permissions(credential):
    from types import SimpleNamespace

    from django.urls import resolve

    from rest_framework.test import APIRequestFactory, force_authenticate

    from apps.api.autenticacao.models import TokenType

    match = resolve("/auth/")
    request = APIRequestFactory().get("/auth/")
    request.resolver_match = match
    request.tenant = None if credential == "missing_tenant" else SimpleNamespace(organization_id=1)
    if credential != "anonymous":
        token = SimpleNamespace(type=TokenType.API_KEY, scopes=["*"], organization_id=1) if credential == "api_key" else None
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, pk=1), token=token)
    else:
        from django.contrib.auth.models import AnonymousUser

        force_authenticate(request, user=AnonymousUser())
    response = match.func(request, **match.kwargs)
    assert response.status_code == (200 if credential == "session" else 403)
