from contextlib import contextmanager
from unittest.mock import Mock

from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.epochs import EpochStore
from common.permission_cache.resolvers.tenant import TenantAccessResolver
from common.permission_cache.store import PermissionCacheStore
from common.permission_cache.types import TenantAccess

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_permission_cache():
    caches["permissions"].clear()


def test_by_slug_returns_primitives_and_second_read_has_no_query(django_assert_num_queries):
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    resolver = TenantAccessResolver()

    first = resolver.by_slug(user.pk, "acme")
    with django_assert_num_queries(0):
        second = resolver.by_slug(user.pk, "acme")

    assert first == second == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)


def test_by_id_returns_the_same_tenant_fact():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    assert TenantAccessResolver().by_organization_id(user.pk, organization.pk) == TenantAccess(organization.pk, "acme", membership.pk, Papel.MEMBRO)


def test_missing_membership_is_a_negative_hit(django_assert_num_queries):
    user = UsuarioFactory()
    resolver = TenantAccessResolver()

    assert resolver.by_slug(user.pk, "missing") is None
    with django_assert_num_queries(0):
        assert resolver.by_slug(user.pk, "missing") is None


def test_loader_disables_cachalot_for_all_queries(monkeypatch):
    calls = []

    @contextmanager
    def recording_context(*, all_queries):
        calls.append(all_queries)
        yield

    monkeypatch.setattr("common.permission_cache.resolvers.tenant.cachalot_disabled", recording_context)
    user = UsuarioFactory()

    assert TenantAccessResolver().by_slug(user.pk, "missing") is None
    assert calls == [True]


@pytest.mark.parametrize(("target", "attribute"), [("membership", "ativo"), ("organization", "ativo")])
def test_inactive_membership_or_organization_is_not_accessible(target, attribute):
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)
    instance = membership if target == "membership" else organization
    setattr(instance, attribute, False)
    instance.save(update_fields=[attribute])

    assert TenantAccessResolver().by_slug(user.pk, "acme") is None


def test_database_aliases_use_distinct_cache_keys(monkeypatch):
    cache = LocMemCache("tenant-resolver-aliases", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))
    resolver = TenantAccessResolver(store=store)
    access = TenantAccess(1, "acme", 2, Papel.MEMBRO)
    loader = Mock(return_value=access)
    monkeypatch.setattr(resolver, "_load_by_slug", loader)

    assert resolver.by_slug(7, "acme", database_alias="default") == access
    assert resolver.by_slug(7, "acme", database_alias="replica") == access
    assert resolver.by_slug(7, "acme", database_alias="default") == access
    assert resolver.by_slug(7, "acme", database_alias="replica") == access

    assert loader.call_count == 2
    assert [call.args[-1] for call in loader.call_args_list] == ["default", "replica"]


def test_disabled_cache_loads_from_database_each_time(settings, django_assert_num_queries):
    settings.AUTHORIZATION_CACHE = {**settings.AUTHORIZATION_CACHE, "ENABLED": False}
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)
    resolver = TenantAccessResolver()

    with django_assert_num_queries(1):
        resolver.by_slug(user.pk, "acme")
    with django_assert_num_queries(1):
        resolver.by_slug(user.pk, "acme")
