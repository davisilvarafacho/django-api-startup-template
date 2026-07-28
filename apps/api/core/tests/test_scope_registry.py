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


def test_parse_scope_global_wildcard():
    assert parse_scope("*") == ("*", "*")


def test_parse_scope_recurso_e_action():
    assert parse_scope("organizations:read") == ("organizations", "read")


def test_parse_scope_wildcard_de_recurso():
    assert parse_scope("organizations:*") == ("organizations", "*")


@pytest.mark.parametrize("valor", ["organizations", "Organizations:read", "org anizations:read", "org:Read"])
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
    registry.register("teams", model=Time)

    with pytest.raises(ImproperlyConfigured, match="teams"):
        registry.register("teams", model=Time)


def test_registry_rejeita_nome_de_recurso_invalido():
    registry = ScopeRegistry()

    with pytest.raises(ImproperlyConfigured):
        registry.register("Teams", model=Time)


def test_registry_deriva_permissoes_crud_do_model():
    registry = ScopeRegistry()
    definition = registry.register("teams", model=Time)

    assert definition.action_permissions == {
        "read": "view_time",
        "create": "add_time",
        "update": "change_time",
        "delete": "delete_time",
    }


def test_registry_lookup():
    registry = ScopeRegistry()
    registry.register("teams", model=Time)

    assert registry.lookup("teams").model is Time
    assert registry.lookup("nao_existe") is None


@pytest.fixture
def registro_isolado(monkeypatch):
    registry = ScopeRegistry()
    registry.register("teams", model=Time)
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

    # Chamar de novo não deve levantar por recurso duplicado.
    discover_scope_resources()

    discover_scope_resources(force=True)
