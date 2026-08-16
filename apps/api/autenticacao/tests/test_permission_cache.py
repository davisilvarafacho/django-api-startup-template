import copy
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.management.base import BaseCommand
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest
import rules
from asgiref.sync import async_to_sync
from celery import shared_task
from guardian.shortcuts import assign_perm

from apps.api.autenticacao.permissions import CustomDjangoModelPermissions
from apps.organizacoes.models import Organizacao
from internal_frameworks.permission_cache.backends import CachedModelBackend, CachedObjectPermissionBackend
from internal_frameworks.permission_cache.epochs import EpochStore
from internal_frameworks.permission_cache.keys import global_scope, layer_scope, snapshot_key, user_scope
from internal_frameworks.permission_cache.resolvers.django import DjangoPermissionResolver
from internal_frameworks.permission_cache.store import PermissionCacheStore
from internal_frameworks.permission_cache.types import DjangoPermissionSnapshot, encode_envelope
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

Usuario = get_user_model()


@shared_task(name="tests.permission_cache_reloaded_user_has_permission")
def reloaded_user_has_permission(user_id, permission_name):
    return Usuario.objects.get(pk=user_id).has_perm(permission_name)


@pytest.fixture(autouse=True)
def clear_permission_cache():
    caches["permissions"].clear()


def permission(codename):
    return Permission.objects.get(content_type__app_label="organizacoes", codename=codename)


def test_direct_and_group_permissions_are_separate_and_cached():
    user = criar_usuario()
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
    user = criar_usuario()
    CachedModelBackend().get_all_permissions(user)

    assert not hasattr(user, "_perm_cache")
    assert not hasattr(user, "_user_perm_cache")
    assert not hasattr(user, "_group_perm_cache")


def test_two_user_instances_with_same_pk_share_the_distributed_snapshot():
    original = criar_usuario()
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
    inactive = criar_usuario(is_active=False)
    ordinary = criar_usuario()
    unsaved = Usuario(email="unsaved@example.com", first_name="Un", last_name="Saved")
    superuser = criar_usuario(is_superuser=True, is_staff=True)
    obj = Group.objects.create(name="object")

    assert backend.get_all_permissions(inactive) == set()
    assert backend.get_all_permissions(AnonymousUser()) == set()
    assert backend.get_all_permissions(unsaved) == set()
    assert backend.get_all_permissions(ordinary, obj=obj) == set()
    assert backend.get_all_permissions(superuser, obj=obj) == set()
    assert backend.has_perm(superuser, "organizacoes.view_organizacao") is True
    assert "organizacoes.view_organizacao" in backend.get_user_permissions(superuser)
    assert "organizacoes.view_organizacao" in backend.get_group_permissions(superuser)


def test_usuario_excluido_nao_tem_permissoes_de_modelo_nem_objeto():
    user = criar_usuario(is_superuser=True, is_staff=True)
    organization = Organizacao.objects.create(nome="Org", slug="org-permissoes-conta-excluida")
    user.user_permissions.add(permission("view_organizacao"))
    assign_perm("view_organizacao", user, organization)
    model_backend = CachedModelBackend()
    object_backend = CachedObjectPermissionBackend()

    assert model_backend.get_all_permissions(user)
    assert object_backend.get_all_permissions(user, organization)

    user.is_deleted = True

    assert model_backend.get_all_permissions(user) == set()
    assert model_backend.has_perm(user, "organizacoes.view_organizacao") is False
    assert model_backend.has_module_perms(user, "organizacoes") is False
    assert object_backend.get_all_permissions(user, organization) == set()
    assert object_backend.has_perm(user, "organizacoes.view_organizacao", organization) is False


def test_async_methods_use_same_semantic_snapshot_without_l1_attributes():
    user = criar_usuario()
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


def test_async_permission_checks_delegate_to_sync_overrides():
    class CustomBackend(CachedModelBackend):
        def has_perm(self, user_obj, perm, obj=None):
            return perm == "custom.allowed"

        def has_module_perms(self, user_obj, app_label):
            return app_label == "custom"

    backend = CustomBackend()
    anonymous = AnonymousUser()

    assert async_to_sync(backend.ahas_perm)(anonymous, "custom.allowed") is True
    assert async_to_sync(backend.ahas_module_perms)(anonymous, "custom") is True


def test_has_perms_and_module_permissions_use_cached_backend():
    user = criar_usuario()
    user.user_permissions.add(permission("add_organizacao"), permission("view_organizacao"))
    backend = CachedModelBackend()

    assert user.has_perms(["organizacoes.add_organizacao", "organizacoes.view_organizacao"]) is True
    assert user.has_perms(["organizacoes.add_organizacao", "organizacoes.delete_organizacao"]) is False
    assert backend.has_module_perms(user, "organizacoes") is True
    assert backend.has_module_perms(user, "usuarios") is False


def test_authentication_and_with_perm_remain_modelbackend_compatible():
    direct = criar_usuario()
    inherited = criar_usuario()
    inactive = criar_usuario(is_active=False)
    superuser = criar_usuario(is_superuser=True, is_staff=True)
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
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))
    backend = CachedModelBackend()

    with CaptureQueriesContext(connection) as first_queries:
        assert backend.get_all_permissions(user) == {"organizacoes.view_organizacao"}
    with CaptureQueriesContext(connection) as second_queries:
        assert backend.get_all_permissions(user) == {"organizacoes.view_organizacao"}
    assert len(first_queries) > 0
    assert len(second_queries) > 0


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_direct_user_has_perm_preserves_result_with_cache_enabled_or_disabled(settings, cache_enabled):
    settings.AUTHORIZATION_CACHE = {**settings.AUTHORIZATION_CACHE, "ENABLED": cache_enabled}
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))

    assert user.has_perm("organizacoes.view_organizacao") is True
    assert user.has_perm("organizacoes.delete_organizacao") is False


def test_drf_model_permissions_transparently_use_cached_backend():
    class OrganizationView:
        queryset = Organizacao.objects.all()

    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))
    factory = APIRequestFactory()
    first_raw_request = factory.get("/organizacoes/")
    force_authenticate(first_raw_request, user=user)
    first_request = Request(first_raw_request)
    permission_class = CustomDjangoModelPermissions()

    assert permission_class.has_permission(first_request, OrganizationView()) is True

    reloaded = Usuario.objects.get(pk=user.pk)
    second_raw_request = factory.get("/organizacoes/")
    force_authenticate(second_raw_request, user=reloaded)
    second_request = Request(second_raw_request)
    with CaptureQueriesContext(connection) as queries:
        assert permission_class.has_permission(second_request, OrganizationView()) is True
    assert len(queries) == 0


def test_user_has_module_perms_preserves_django_admin_behavior():
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))

    assert user.has_module_perms("organizacoes") is True
    assert user.has_module_perms("usuarios") is False


def test_celery_eager_task_reloads_user_and_preserves_permission_result():
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))

    assert user.has_perm("organizacoes.view_organizacao") is True
    assert reloaded_user_has_permission.delay(user.pk, "organizacoes.view_organizacao").get() is True


def test_management_command_context_resolves_permissions_without_request():
    class ResolvePermissionCommand(BaseCommand):
        requires_system_checks = []

        def handle(self, *args, **options):
            user = Usuario.objects.get(pk=options["user_id"])
            snapshot = DjangoPermissionResolver().resolve(user)
            return "allowed" if "organizacoes.view_organizacao" in snapshot.all_permissions else "denied"

    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))

    result = ResolvePermissionCommand(stdout=StringIO()).execute(
        force_color=False,
        no_color=False,
        skip_checks=True,
        user_id=user.pk,
    )

    assert result == "allowed"


def test_empty_permission_snapshot_is_cached():
    user = criar_usuario()
    backend = CachedModelBackend()

    with CaptureQueriesContext(connection) as first_queries:
        assert backend.get_all_permissions(user) == set()
    with CaptureQueriesContext(connection) as second_queries:
        assert backend.get_all_permissions(user) == set()
    assert len(first_queries) > 0
    assert len(second_queries) == 0


def test_malformed_permission_payload_is_ignored_and_reloaded():
    user = criar_usuario()
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


def test_malformed_permission_item_is_reloaded_before_module_check():
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))
    cache = caches["permissions"]
    scopes = (global_scope(), layer_scope("django"), user_scope("django", user.pk))
    epochs = EpochStore(cache_backend=cache).read(scopes, "default")
    key = snapshot_key("django", "default", ("user", user.pk), epochs)
    cache.set(
        key,
        encode_envelope(
            {
                "user_permissions": ["corrompido"],
                "group_permissions": [],
            }
        ),
    )

    assert CachedModelBackend().has_module_perms(user, "organizacoes") is True


def test_database_alias_is_part_of_snapshot_identity():
    class AliasResolver(DjangoPermissionResolver):
        def _load(self, user_obj, database_alias):
            permission_name = f"organizacoes.{database_alias}"
            return DjangoPermissionSnapshot(frozenset({permission_name}), frozenset())

    cache = LocMemCache("django-permission-aliases", {})
    store = PermissionCacheStore(cache_backend=cache, epoch_store=EpochStore(cache_backend=cache))
    resolver = AliasResolver(store=store)
    default_user = criar_usuario()
    replica_user = copy.copy(default_user)
    replica_user._state = copy.copy(default_user._state)
    replica_user._state.db = "replica"

    assert resolver.resolve(default_user).all_permissions == {"organizacoes.default"}
    assert resolver.resolve(replica_user).all_permissions == {"organizacoes.replica"}


def test_user_has_perm_keeps_backend_or_semantics():
    rules.add_perm("organizacoes.rule_only", rules.always_allow)
    rules_user = criar_usuario()
    django_user = criar_usuario()
    guardian_user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    django_user.user_permissions.add(permission("view_organizacao"))
    assign_perm("change_organizacao", guardian_user, organization)

    try:
        assert rules_user.has_perm("organizacoes.rule_only") is True
        assert django_user.has_perm("organizacoes.view_organizacao") is True
        assert django_user.has_perm("organizacoes.change_organizacao") is False
        assert guardian_user.has_perm("change_organizacao", organization) is True
        assert guardian_user.has_perm("view_organizacao", organization) is False
    finally:
        rules.remove_perm("organizacoes.rule_only")


def test_usuario_excluido_nao_tem_permissoes_de_modelo_nem_objeto():
    user = criar_usuario()
    superuser = criar_usuario(is_superuser=True, is_staff=True)
    organization = Organizacao.objects.create(nome="Excluídos", slug="excluidos")
    user.user_permissions.add(permission("view_organizacao"))
    assign_perm("change_organizacao", user, organization)
    user.delete()
    superuser.delete()
    user.refresh_from_db(from_queryset=Usuario.all_objects.all())
    superuser.refresh_from_db(from_queryset=Usuario.all_objects.all())
    model_backend = CachedModelBackend()
    object_backend = CachedObjectPermissionBackend()

    assert model_backend.get_all_permissions(user) == set()
    assert model_backend.has_perm(user, "organizacoes.view_organizacao") is False
    assert model_backend.has_module_perms(user, "organizacoes") is False
    assert model_backend.get_all_permissions(superuser) == set()
    assert model_backend.has_perm(superuser, "organizacoes.view_organizacao") is False
    assert object_backend.get_all_permissions(user, organization) == set()
    assert object_backend.has_perm(user, "organizacoes.change_organizacao", organization) is False
    assert object_backend.has_perm(superuser, "organizacoes.change_organizacao", organization) is False


def test_conta_legada_excluida_e_ativa_nao_tem_permissoes():
    """Linha excluída antes da correção: `is_deleted` sem perder `is_active`."""
    user = criar_usuario()
    superuser = criar_usuario(is_superuser=True, is_staff=True)
    organization = Organizacao.objects.create(nome="Legado", slug="legado")
    user.user_permissions.add(permission("view_organizacao"))
    assign_perm("change_organizacao", user, organization)
    models.QuerySet.update(Usuario.all_objects.filter(pk__in=[user.pk, superuser.pk]), is_deleted=True)
    user = Usuario.all_objects.get(pk=user.pk)
    superuser = Usuario.all_objects.get(pk=superuser.pk)
    model_backend = CachedModelBackend()
    object_backend = CachedObjectPermissionBackend()

    assert user.is_active is True
    assert model_backend.get_all_permissions(user) == set()
    assert model_backend.has_perm(user, "organizacoes.view_organizacao") is False
    assert model_backend.has_module_perms(user, "organizacoes") is False
    assert model_backend.get_all_permissions(superuser) == set()
    assert model_backend.has_perm(superuser, "organizacoes.view_organizacao") is False
    assert object_backend.get_all_permissions(user, organization) == set()
    assert object_backend.has_perm(user, "organizacoes.change_organizacao", organization) is False
    assert object_backend.has_perm(superuser, "organizacoes.change_organizacao", organization) is False


def test_barreira_do_modelo_recusa_superuser_e_rules_em_conta_legada_excluida():
    """A barreira precisa vir antes do atalho de superuser e de qualquer backend."""
    rules.add_perm("organizacoes.legacy_rule", rules.always_allow)
    rules_user = criar_usuario()
    superuser = criar_usuario(is_superuser=True, is_staff=True)
    models.QuerySet.update(Usuario.all_objects.filter(pk__in=[rules_user.pk, superuser.pk]), is_deleted=True)
    rules_user = Usuario.all_objects.get(pk=rules_user.pk)
    superuser = Usuario.all_objects.get(pk=superuser.pk)

    try:
        assert rules_user.has_perm("organizacoes.legacy_rule") is False
        assert superuser.has_perm("organizacoes.view_organizacao") is False
        assert superuser.has_module_perms("organizacoes") is False
        assert async_to_sync(superuser.ahas_perm)("organizacoes.view_organizacao") is False
        assert async_to_sync(superuser.ahas_module_perms)("organizacoes") is False
    finally:
        rules.remove_perm("organizacoes.legacy_rule")
