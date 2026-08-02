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
from internal_frameworks.permission_cache.backends import CachedObjectPermissionBackend
from internal_frameworks.permission_cache.resolvers.guardian import GuardianPermissionResolver

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


def test_guardian_unsupported_inputs_fail_closed_with_upstream_parity():
    active_user = UsuarioFactory()
    inactive_user = UsuarioFactory(is_active=False)
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    upstream = ObjectPermissionBackend()
    cached = CachedObjectPermissionBackend()

    for user, obj in (
        (inactive_user, organization),
        (AnonymousUser(), organization),
        (active_user, None),
        (active_user, Organizacao(nome="Unsaved", slug="unsaved")),
        (active_user, object()),
    ):
        assert cached.has_perm(user, "view_organizacao", obj) is False
        assert cached.has_perm(user, "view_organizacao", obj) == upstream.has_perm(user, "view_organizacao", obj)
        assert cached.get_group_permissions(user, obj) == set(upstream.get_group_permissions(user, obj))
        assert cached.get_all_permissions(user, obj) == set(upstream.get_all_permissions(user, obj))


def test_guardian_backend_accepts_prefixed_and_unprefixed_codename():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    assign_perm("view_organizacao", user, organization)
    upstream = ObjectPermissionBackend()
    cached = CachedObjectPermissionBackend()

    for permission in ("view_organizacao", "organizacoes.view_organizacao"):
        assert cached.has_perm(user, permission, organization) is True
        assert cached.has_perm(user, permission, organization) == upstream.has_perm(user, permission, organization)
    assert cached.get_group_permissions(user, organization) == set(upstream.get_group_permissions(user, organization))
    assert cached.get_all_permissions(user, organization) == set(upstream.get_all_permissions(user, organization))


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
