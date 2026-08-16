from contextlib import contextmanager
from unittest.mock import Mock

from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.models import Usuario
from internal_frameworks.permission_cache.epochs import EpochStore
from internal_frameworks.permission_cache.resolvers.tenant import TenantAccessResolver
from internal_frameworks.permission_cache.store import PermissionCacheStore
from internal_frameworks.permission_cache.types import TenantAccess
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_permission_cache():
    caches["permissions"].clear()


def test_by_slug_returns_primitives_and_second_read_checks_user_is_not_deleted(django_assert_num_queries):
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    resolver = TenantAccessResolver()

    first = resolver.by_slug(user.pk, "acme")
    with django_assert_num_queries(1):
        second = resolver.by_slug(user.pk, "acme")

    assert first == second == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)


def test_by_id_returns_the_same_tenant_fact():
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    assert TenantAccessResolver().by_organization_id(user.pk, organization.pk) == TenantAccess(organization.pk, "acme", membership.pk, Papel.MEMBRO)


@pytest.mark.parametrize("by_tenant", ["by_slug", "by_organization_id"])
def test_deleted_user_is_denied_even_when_tenant_access_is_cached(by_tenant):
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)
    resolver = TenantAccessResolver()
    organization_identifier = "acme" if by_tenant == "by_slug" else organization.pk

    assert getattr(resolver, by_tenant)(user.pk, organization_identifier) is not None
    Usuario.all_objects.filter(pk=user.pk).update(is_deleted=True)

    assert getattr(resolver, by_tenant)(user.pk, organization_identifier) is None


def test_missing_membership_is_a_negative_hit(django_assert_num_queries):
    user = criar_usuario()
    resolver = TenantAccessResolver()

    assert resolver.by_slug(user.pk, "missing") is None
    with django_assert_num_queries(1):
        assert resolver.by_slug(user.pk, "missing") is None


def test_loader_disables_cachalot_for_all_queries(monkeypatch):
    calls = []

    @contextmanager
    def recording_context(*, all_queries):
        calls.append(all_queries)
        yield

    monkeypatch.setattr("internal_frameworks.permission_cache.resolvers.tenant.cachalot_disabled", recording_context)
    user = criar_usuario()

    assert TenantAccessResolver().by_slug(user.pk, "missing") is None
    assert calls == [True, True]


@pytest.mark.parametrize(("target", "attribute"), [("membership", "is_active"), ("organization", "is_active")])
def test_inactive_membership_or_organization_is_not_accessible(target, attribute):
    user = criar_usuario()
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
    monkeypatch.setattr(resolver, "_user_is_not_deleted", Mock(return_value=True))

    assert resolver.by_slug(7, "acme", database_alias="default") == access
    assert resolver.by_slug(7, "acme", database_alias="replica") == access
    assert resolver.by_slug(7, "acme", database_alias="default") == access
    assert resolver.by_slug(7, "acme", database_alias="replica") == access

    assert loader.call_count == 2
    assert [call.args[-1] for call in loader.call_args_list] == ["default", "replica"]


def test_disabled_cache_loads_from_database_each_time(settings, django_assert_num_queries):
    settings.AUTHORIZATION_CACHE = {**settings.AUTHORIZATION_CACHE, "ENABLED": False}
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)
    resolver = TenantAccessResolver()

    with django_assert_num_queries(2):
        resolver.by_slug(user.pk, "acme")
    with django_assert_num_queries(2):
        resolver.by_slug(user.pk, "acme")
