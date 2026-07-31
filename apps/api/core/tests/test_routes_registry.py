"""Testes do registry de rotas descobertas por convenção (não tocam o banco)."""

import sys
import types

import pytest

from apps.api.core.routes_registry import RouteRegistry

FILE_NAME = "public_routes"
ATTR_NAME = "PUBLIC_ROUTES"


@pytest.fixture
def registrar_app(monkeypatch):
    """Cria um módulo `<app>.public_routes` importável e devolve o nome do app."""

    def _registrar(app_name, routes):
        modulo = types.ModuleType(f"{app_name}.{FILE_NAME}")
        if routes is not None:
            setattr(modulo, ATTR_NAME, routes)

        pacote = types.ModuleType(app_name)
        pacote.__path__ = []

        monkeypatch.setitem(sys.modules, app_name, pacote)
        monkeypatch.setitem(sys.modules, f"{app_name}.{FILE_NAME}", modulo)
        return app_name

    return _registrar


def build_registry(defaults=()):
    return RouteRegistry(file_name=FILE_NAME, attr_name=ATTR_NAME, defaults=defaults)


def test_matches_antes_do_discover_falha_alto():
    registry = build_registry()

    with pytest.raises(RuntimeError, match="discover"):
        registry.matches("/qualquer/")


def test_defaults_dispensam_descoberta(settings):
    settings.BUSINESS_APPS = []
    registry = build_registry(defaults={"/admin/", "/health/"})
    registry.discover()

    assert registry.matches("/admin/")
    assert registry.matches("/health/")
    assert not registry.matches("/v1/pedidos/")


def test_agrega_rotas_declaradas_pelos_apps(settings, registrar_app):
    settings.BUSINESS_APPS = [registrar_app("app_ficticio", ["/auth/login/"])]

    registry = build_registry()
    registry.discover()

    assert registry.matches("/auth/login/")
    assert not registry.matches("/auth/logout/")


def test_compara_por_prefixo(settings, registrar_app):
    """Documenta o comportamento: a rota declarada é prefixo, não match exato."""
    settings.BUSINESS_APPS = [registrar_app("app_prefixo_amplo", ["/auth/"])]

    registry = build_registry()
    registry.discover()

    assert registry.matches("/auth/logout/")


def test_app_sem_o_modulo_e_ignorado(settings):
    settings.BUSINESS_APPS = ["app_que_nao_existe"]

    registry = build_registry()
    registry.discover()

    assert not registry.matches("/qualquer/")


def test_modulo_sem_a_constante_e_ignorado(settings, registrar_app):
    settings.BUSINESS_APPS = [registrar_app("app_sem_constante", None)]

    registry = build_registry()
    registry.discover()

    assert not registry.matches("/qualquer/")


def test_rejeita_tipo_invalido(settings, registrar_app):
    settings.BUSINESS_APPS = [registrar_app("app_tipo_errado", "/auth/login/")]

    with pytest.raises(TypeError, match="list ou tuple"):
        build_registry().discover()


def test_rejeita_entrada_nao_string(settings, registrar_app):
    settings.BUSINESS_APPS = [registrar_app("app_entrada_errada", ["/auth/login/", 42])]

    with pytest.raises(TypeError, match="entrada inválida"):
        build_registry().discover()


def test_discover_e_idempotente(settings, registrar_app):
    settings.BUSINESS_APPS = [registrar_app("app_idempotente", ["/auth/login/"])]

    registry = build_registry()
    registry.discover()
    quantidade = len(registry._paths)
    registry.discover()

    assert len(registry._paths) == quantidade
