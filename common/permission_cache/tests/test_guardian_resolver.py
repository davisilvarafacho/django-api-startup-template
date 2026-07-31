from django.conf import settings
from django.contrib.auth.models import AnonymousUser, Group
from django.core.management import call_command

import pytest
from asgiref.sync import async_to_sync
from guardian.backends import ObjectPermissionBackend
from guardian.exceptions import WrongAppError
from guardian.shortcuts import assign_perm

from apps.organizacoes.models import Organizacao
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.backends import CachedObjectPermissionBackend
from common.permission_cache.resolvers.guardian import GuardianPermissionResolver

pytestmark = pytest.mark.django_db


def test_cached_guardian_backend_replaces_upstream_backend():
    assert settings.AUTHENTICATION_BACKENDS.count("common.permission_cache.backends.CachedObjectPermissionBackend") == 1
    assert "guardian.backends.ObjectPermissionBackend" not in settings.AUTHENTICATION_BACKENDS
    call_command("check")


def test_guardian_snapshot_separates_direct_and_group_codenames():
    user = UsuarioFactory()
    group = Group.objects.create(name="object-readers")
    user.groups.add(group)
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("change_organizacao", user, organization)
    assign_perm("view_organizacao", group, organization)

    snapshot = GuardianPermissionResolver().resolve(user, organization)

    assert snapshot.user_permissions == frozenset({"change_organizacao"})
    assert snapshot.group_permissions == frozenset({"view_organizacao"})
    assert snapshot.all_permissions == frozenset({"change_organizacao", "view_organizacao"})


def test_guardian_empty_snapshot_is_negative_hit_without_second_query(django_assert_num_queries):
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    resolver = GuardianPermissionResolver()

    assert resolver.resolve(user, organization).all_permissions == frozenset()
    with django_assert_num_queries(0):
        snapshot = resolver.resolve(user, organization)
    assert snapshot.user_permissions == frozenset()
    assert snapshot.group_permissions == frozenset()


def test_guardian_superuser_matches_upstream():
    user = UsuarioFactory(is_superuser=True, is_staff=True)
    group = Group.objects.create(name="superuser-object-readers")
    user.groups.add(group)
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("view_organizacao", group, organization)
    upstream = ObjectPermissionBackend()
    cached = CachedObjectPermissionBackend()

    assert cached.has_perm(user, "view_organizacao", organization) == upstream.has_perm(user, "view_organizacao", organization)
    assert cached.get_all_permissions(user, organization) == set(upstream.get_all_permissions(user, organization))
    assert cached.get_group_permissions(user, organization) == {"view_organizacao"}


@pytest.mark.parametrize(("user", "obj"), [(UsuarioFactory.build(is_active=False), None), (AnonymousUser(), None)])
def test_guardian_unsupported_inputs_fail_closed(user, obj):
    backend = CachedObjectPermissionBackend()
    organization = Organizacao(nome="Unsaved", slug="unsaved")

    assert backend.has_perm(user, "view_organizacao", obj) is False
    assert backend.get_group_permissions(user, obj) == set()
    assert backend.get_all_permissions(user, obj) == set()
    assert backend.has_perm(user, "view_organizacao", organization) is False
    assert backend.has_perm(user, "view_organizacao", object()) is False


def test_guardian_backend_accepts_prefixed_and_unprefixed_codename():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("view_organizacao", user, organization)
    backend = CachedObjectPermissionBackend()

    assert backend.has_perm(user, "view_organizacao", organization) is True
    assert backend.has_perm(user, "organizacoes.view_organizacao", organization) is True


def test_guardian_backend_raises_wrong_app_error_like_upstream():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    upstream = ObjectPermissionBackend()
    cached = CachedObjectPermissionBackend()

    with pytest.raises(WrongAppError):
        upstream.has_perm(user, "usuarios.view_organizacao", organization)
    with pytest.raises(WrongAppError):
        cached.has_perm(user, "usuarios.view_organizacao", organization)


def test_guardian_async_methods_use_same_snapshot():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("view_organizacao", user, organization)
    backend = CachedObjectPermissionBackend()

    assert async_to_sync(backend.ahas_perm)(user, "view_organizacao", organization) == backend.has_perm(user, "view_organizacao", organization)
    assert async_to_sync(backend.aget_group_permissions)(user, organization) == backend.get_group_permissions(user, organization)
    assert async_to_sync(backend.aget_all_permissions)(user, organization) == backend.get_all_permissions(user, organization)
