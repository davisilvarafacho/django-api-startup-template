import copy

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

import pytest
from asgiref.sync import async_to_sync

from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.backends import CachedModelBackend
from common.permission_cache.epochs import EpochStore
from common.permission_cache.keys import global_scope, layer_scope, snapshot_key, user_scope
from common.permission_cache.resolvers.django import DjangoPermissionResolver
from common.permission_cache.store import PermissionCacheStore
from common.permission_cache.types import DjangoPermissionSnapshot, encode_envelope

pytestmark = pytest.mark.django_db

Usuario = get_user_model()


@pytest.fixture(autouse=True)
def clear_permission_cache():
    caches["permissions"].clear()


def permission(codename):
    return Permission.objects.get(content_type__app_label="organizacoes", codename=codename)


def test_direct_and_group_permissions_are_separate_and_cached():
    user = UsuarioFactory()
    direct = permission("add_organizacao")
    inherited = permission("view_organizacao")
    group = Group.objects.create(name="readers")
    group.permissions.add(inherited)
    user.user_permissions.add(direct)
    user.groups.add(group)
    backend = CachedModelBackend()

    assert backend.get_user_permissions(user) == {"organizacoes.add_organizacao"}
    assert backend.get_group_permissions(user) == {"organizacoes.view_organizacao"}
    with CaptureQueriesContext(connection) as queries:
        assert backend.get_all_permissions(user) == {
            "organizacoes.add_organizacao",
            "organizacoes.view_organizacao",
        }
    assert len(queries) == 0


def test_backend_does_not_create_modelbackend_l1_attributes():
    user = UsuarioFactory()
    CachedModelBackend().get_all_permissions(user)

    assert not hasattr(user, "_perm_cache")
    assert not hasattr(user, "_user_perm_cache")
    assert not hasattr(user, "_group_perm_cache")


def test_two_user_instances_with_same_pk_share_the_distributed_snapshot():
    original = UsuarioFactory()
    original.user_permissions.add(permission("view_organizacao"))
    reloaded = Usuario.objects.get(pk=original.pk)

    assert CachedModelBackend().get_all_permissions(original) == {"organizacoes.view_organizacao"}
    with CaptureQueriesContext(connection) as queries:
        assert CachedModelBackend().get_all_permissions(reloaded) == {"organizacoes.view_organizacao"}
    assert len(queries) == 0
    assert not hasattr(original, "_perm_cache")
    assert not hasattr(reloaded, "_perm_cache")


def test_inactive_anonymous_unsaved_object_and_superuser_match_modelbackend():
    backend = CachedModelBackend()
    inactive = UsuarioFactory(is_active=False)
    ordinary = UsuarioFactory()
    unsaved = Usuario(email="unsaved@example.com", first_name="Un", last_name="Saved")
    superuser = UsuarioFactory(is_superuser=True, is_staff=True)
    obj = Group.objects.create(name="object")

    assert backend.get_all_permissions(inactive) == set()
    assert backend.get_all_permissions(AnonymousUser()) == set()
    assert backend.get_all_permissions(unsaved) == set()
    assert backend.get_all_permissions(ordinary, obj=obj) == set()
    assert backend.get_all_permissions(superuser, obj=obj) == set()
    assert backend.has_perm(superuser, "organizacoes.view_organizacao") is True
    assert "organizacoes.view_organizacao" in backend.get_user_permissions(superuser)
    assert "organizacoes.view_organizacao" in backend.get_group_permissions(superuser)


def test_async_methods_use_same_semantic_snapshot_without_l1_attributes():
    user = UsuarioFactory()
    user.user_permissions.add(permission("view_organizacao"))
    backend = CachedModelBackend()

    assert async_to_sync(backend.aget_user_permissions)(user) == {"organizacoes.view_organizacao"}
    assert async_to_sync(backend.aget_group_permissions)(user) == set()
    assert async_to_sync(backend.aget_all_permissions)(user) == {"organizacoes.view_organizacao"}
    assert async_to_sync(backend.ahas_perm)(user, "organizacoes.view_organizacao") is True
    assert async_to_sync(backend.ahas_module_perms)(user, "organizacoes") is True
    assert not hasattr(user, "_perm_cache")
    assert not hasattr(user, "_user_perm_cache")
    assert not hasattr(user, "_group_perm_cache")


def test_has_perms_and_module_permissions_use_cached_backend():
    user = UsuarioFactory()
    user.user_permissions.add(permission("add_organizacao"), permission("view_organizacao"))
    backend = CachedModelBackend()

    assert user.has_perms(["organizacoes.add_organizacao", "organizacoes.view_organizacao"]) is True
    assert user.has_perms(["organizacoes.add_organizacao", "organizacoes.delete_organizacao"]) is False
    assert backend.has_module_perms(user, "organizacoes") is True
    assert backend.has_module_perms(user, "usuarios") is False


def test_authentication_and_with_perm_remain_modelbackend_compatible():
    direct = UsuarioFactory()
    inherited = UsuarioFactory()
    inactive = UsuarioFactory(is_active=False)
    superuser = UsuarioFactory(is_superuser=True, is_staff=True)
    perm = permission("view_organizacao")
    direct.user_permissions.add(perm)
    inactive.user_permissions.add(perm)
    group = Group.objects.create(name="permission-readers")
    group.permissions.add(perm)
    inherited.groups.add(group)
    backend = CachedModelBackend()

    assert backend.authenticate(None, username=direct.email, password="senha-de-teste") == direct
    assert backend.authenticate(None, username=inactive.email, password="senha-de-teste") is None
    assert async_to_sync(backend.aauthenticate)(None, username=direct.email, password="senha-de-teste") == direct
    assert set(backend.with_perm("organizacoes.view_organizacao")) == {direct, inherited, superuser}
    assert set(backend.with_perm(perm, is_active=None, include_superusers=False)) == {direct, inherited, inactive}
    assert not backend.with_perm(perm, obj=group).exists()


@override_settings(
    AUTHORIZATION_CACHE={
        "ENABLED": False,
        "ALIAS": "permissions",
        "TIMEOUT": 1800,
        "KEY_PREFIX": "authz:test",
        "MAX_RETRIES": 2,
    }
)
def test_kill_switch_bypasses_permission_snapshots():
    user = UsuarioFactory()
    user.user_permissions.add(permission("view_organizacao"))
    backend = CachedModelBackend()

    with CaptureQueriesContext(connection) as first_queries:
        assert backend.get_all_permissions(user) == {"organizacoes.view_organizacao"}
    with CaptureQueriesContext(connection) as second_queries:
        assert backend.get_all_permissions(user) == {"organizacoes.view_organizacao"}
    assert len(first_queries) > 0
    assert len(second_queries) > 0


def test_empty_permission_snapshot_is_cached():
    user = UsuarioFactory()
    backend = CachedModelBackend()

    with CaptureQueriesContext(connection) as first_queries:
        assert backend.get_all_permissions(user) == set()
    with CaptureQueriesContext(connection) as second_queries:
        assert backend.get_all_permissions(user) == set()
    assert len(first_queries) > 0
    assert len(second_queries) == 0


def test_malformed_permission_payload_is_ignored_and_reloaded():
    user = UsuarioFactory()
    user.user_permissions.add(permission("view_organizacao"))
    cache = caches["permissions"]
    scopes = (global_scope(), layer_scope("django"), user_scope("django", user.pk))
    epochs = EpochStore(cache_backend=cache).read(scopes, "default")
    key = snapshot_key("django", "default", ("user", user.pk), epochs)
    cache.set(
        key,
        encode_envelope(
            {
                "user_permissions": "organizacoes.view_organizacao",
                "group_permissions": [],
            }
        ),
    )

    assert CachedModelBackend().get_all_permissions(user) == {"organizacoes.view_organizacao"}


def test_database_alias_is_part_of_snapshot_identity():
    class AliasResolver(DjangoPermissionResolver):
        def _load(self, user_obj, database_alias):
            permission_name = f"organizacoes.{database_alias}"
            return DjangoPermissionSnapshot(frozenset({permission_name}), frozenset())

    cache = LocMemCache("django-permission-aliases", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))
    resolver = AliasResolver(store=store)
    default_user = UsuarioFactory()
    replica_user = copy.copy(default_user)
    replica_user._state = copy.copy(default_user._state)
    replica_user._state.db = "replica"

    assert resolver.resolve(default_user).all_permissions == {"organizacoes.default"}
    assert resolver.resolve(replica_user).all_permissions == {"organizacoes.replica"}
