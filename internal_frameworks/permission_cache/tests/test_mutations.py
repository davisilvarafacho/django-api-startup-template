import copy
from unittest.mock import call, patch

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType, ContentTypeManager
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext

import pytest
from guardian.ctypes import get_content_type
from guardian.utils import get_user_obj_perms_model

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory
from apps.usuarios.models import Usuario
from internal_frameworks.permission_cache.mutations import (
    bulk_create_memberships,
    bulk_create_permissions,
    bulk_update_memberships,
    bulk_update_permissions,
    guardian_assign_to_many,
    guardian_bulk_assign,
    guardian_bulk_remove,
    guardian_remove_from_many,
    update_memberships,
    update_user_authorization_state,
)

pytestmark = pytest.mark.django_db(transaction=True)

REPLICA_DATABASE_ALIAS = "permission_cache_replica"
if REPLICA_DATABASE_ALIAS not in connections.databases:
    replica_database = copy.deepcopy(connections.databases["default"])
    replica_database["TEST"] = {**replica_database.get("TEST", {}), "MIRROR": "default"}
    connections.databases[REPLICA_DATABASE_ALIAS] = replica_database


def create_organization(slug: str) -> Organizacao:
    return Organizacao.objects.create(nome=slug.title(), slug=slug)


def assert_object_bump(bump, *organizations: Organizacao) -> None:
    scopes = tuple(sorted(f"guardian:object:{get_content_type(organization).pk}:{organization.pk}" for organization in organizations))
    bump.assert_called_once_with(scopes, database_alias="default", layer="guardian")


@pytest.fixture
def replica_alias():
    try:
        yield REPLICA_DATABASE_ALIAS
    finally:
        connections[REPLICA_DATABASE_ALIAS].close()


def guardian_inserts(queries) -> list[str]:
    return [query["sql"] for query in queries if 'INSERT INTO "guardian_userobjectpermission"' in query["sql"]]


def guardian_deletes(queries) -> list[str]:
    return [query["sql"] for query in queries if 'DELETE FROM "guardian_userobjectpermission"' in query["sql"]]


def create_divergent_guardian_permission(replica_alias, users, organization):
    content_type = ContentType.objects.using(replica_alias).create(app_label="replica_only", model="organizacao")
    permission = Permission.objects.using(replica_alias).create(
        content_type_id=content_type.pk,
        codename="view_organizacao",
        name="Can view replica organization",
    )
    permission_model = get_user_obj_perms_model()
    rows = permission_model.objects.using(replica_alias).bulk_create(
        [
            permission_model(
                user_id=user.pk,
                permission_id=permission.pk,
                content_type_id=content_type.pk,
                object_pk=organization.pk,
            )
            for user in users
        ]
    )
    return content_type, permission_model, rows


def alias_content_type_lookup(replica_alias, divergent_content_type):
    original_get_for_model = ContentTypeManager.get_for_model

    def get_for_model(manager, model, for_concrete_model=True):
        if manager.db == replica_alias:
            return divergent_content_type
        return original_get_for_model(manager, model, for_concrete_model=for_concrete_model)

    return patch.object(ContentTypeManager, "get_for_model", get_for_model)


def test_bulk_create_memberships_invalidates_every_user_after_commit():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("acme")
    memberships = [
        Vinculo(usuario=first, organizacao=organization, papel=Papel.MEMBRO),
        Vinculo(usuario=second, organizacao=organization, papel=Papel.GESTOR),
    ]

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            created = bulk_create_memberships(memberships)
            bump.assert_not_called()

        assert {row.usuario_id for row in created} == {first.pk, second.pk}
        bump.assert_called_once_with(
            (f"tenant:user:{first.pk}", f"tenant:user:{second.pk}"),
            database_alias="default",
            layer="tenant",
        )


def test_bulk_update_memberships_invalidates_old_and_new_users():
    first = UsuarioFactory()
    second = UsuarioFactory()
    replacement = UsuarioFactory()
    first_membership = Vinculo.objects.create(
        usuario=first,
        organizacao=create_organization("bulk-update-first"),
        papel=Papel.MEMBRO,
    )
    second_membership = Vinculo.objects.create(
        usuario=second,
        organizacao=create_organization("bulk-update-second"),
        papel=Papel.MEMBRO,
    )
    rows = [first_membership, second_membership]
    for row in rows:
        row.usuario = replacement

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = bulk_update_memberships(rows, ["usuario"])
            bump.assert_not_called()

        assert updated == 2
        bump.assert_called_once_with(
            tuple(sorted(f"tenant:user:{user.pk}" for user in (first, second, replacement))),
            database_alias="default",
            layer="tenant",
        )


def test_update_memberships_reads_users_before_queryset_update():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("queryset-update")
    Vinculo.objects.create(usuario=first, organizacao=organization, papel=Papel.MEMBRO)
    Vinculo.objects.create(usuario=second, organizacao=organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = update_memberships(Vinculo.objects.filter(organizacao=organization), papel=Papel.GESTOR)
            bump.assert_not_called()

        assert updated == 2
        assert set(Vinculo.objects.filter(organizacao=organization).values_list("papel", flat=True)) == {Papel.GESTOR}
        bump.assert_called_once_with(
            (f"tenant:user:{first.pk}", f"tenant:user:{second.pk}"),
            database_alias="default",
            layer="tenant",
        )


def test_guardian_bulk_assign_and_remove_invalidate_every_object():
    user = UsuarioFactory()
    first = create_organization("guardian-bulk-first")
    second = create_organization("guardian-bulk-second")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            assigned = guardian_bulk_assign("view_organizacao", user, [first, second])
            bump.assert_not_called()
        assert {str(row.object_pk) for row in assigned} == {str(first.pk), str(second.pk)}
        assert_object_bump(bump, first, second)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            removed = guardian_bulk_remove("view_organizacao", user, [first, second])
            bump.assert_not_called()
        assert removed[0] == 2
        assert sum(removed[1].values()) == 2
        assert_object_bump(bump, first, second)


def test_guardian_assign_to_many_and_remove_from_many_invalidate_once():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-many")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            assigned = guardian_assign_to_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()
        assert len(assigned) == 2
        assert_object_bump(bump, organization)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            removed = guardian_remove_from_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()
        assert removed[0] == 2
        assert sum(removed[1].values()) == 2
        assert_object_bump(bump, organization)


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_bulk_assign_writes_on_object_database_alias(replica_alias):
    user = UsuarioFactory()
    organization = create_organization("guardian-replica-bulk")
    user._state.db = replica_alias
    organization._state.db = replica_alias

    with (
        patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump,
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        with transaction.atomic(using=replica_alias):
            assigned = guardian_bulk_assign("view_organizacao", user, [organization])
            bump.assert_not_called()

        assert len(assigned) == 1
        assert guardian_inserts(default_queries) == []
        assert len(guardian_inserts(replica_queries)) == 1
        bump.assert_called_once_with(
            (f"guardian:object:{get_content_type(organization).pk}:{organization.pk}",),
            database_alias=replica_alias,
            layer="guardian",
        )


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_assign_to_many_writes_on_object_database_alias(replica_alias):
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-replica-many")
    for instance in (first, second, organization):
        instance._state.db = replica_alias

    with (
        patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump,
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        with transaction.atomic(using=replica_alias):
            assigned = guardian_assign_to_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()

        assert len(assigned) == 2
        assert guardian_inserts(default_queries) == []
        assert len(guardian_inserts(replica_queries)) == 1
        bump.assert_called_once_with(
            (f"guardian:object:{get_content_type(organization).pk}:{organization.pk}",),
            database_alias=replica_alias,
            layer="guardian",
        )


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_bulk_remove_uses_alias_content_type_when_ids_diverge(replica_alias):
    user = UsuarioFactory()
    organization = create_organization("guardian-replica-remove-bulk")
    content_type, permission_model, rows = create_divergent_guardian_permission(replica_alias, [user], organization)
    user._state.db = replica_alias
    organization._state.db = replica_alias

    with (
        alias_content_type_lookup(replica_alias, content_type),
        patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes"),
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        removed = guardian_bulk_remove("view_organizacao", user, [organization])

    assert removed[0] == 1
    assert not permission_model.objects.using(replica_alias).filter(pk=rows[0].pk).exists()
    assert guardian_deletes(default_queries) == []
    assert len(guardian_deletes(replica_queries)) == 1


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_remove_from_many_uses_alias_content_type_when_ids_diverge(replica_alias):
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-replica-remove-many")
    users = [first, second]
    content_type, permission_model, rows = create_divergent_guardian_permission(replica_alias, users, organization)
    for instance in (*users, organization):
        instance._state.db = replica_alias

    with (
        alias_content_type_lookup(replica_alias, content_type),
        patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes"),
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        removed = guardian_remove_from_many("view_organizacao", users, organization)

    assert removed[0] == 2
    assert not permission_model.objects.using(replica_alias).filter(pk__in=[row.pk for row in rows]).exists()
    assert guardian_deletes(default_queries) == []
    assert len(guardian_deletes(replica_queries)) == 1


def test_permission_bulk_wrappers_bump_global_layers():
    content_type = get_content_type(create_organization("permission-bulk"))
    permissions = [
        Permission(content_type=content_type, codename="bulk_first", name="Bulk first"),
        Permission(content_type=content_type, codename="bulk_second", name="Bulk second"),
    ]
    expected_bumps = [
        call(("django:global",), database_alias="default", layer="django"),
        call(("guardian:global",), database_alias="default", layer="guardian"),
    ]

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            created = bulk_create_permissions(permissions)
            bump.assert_not_called()
        assert {permission.codename for permission in created} == {"bulk_first", "bulk_second"}
        assert bump.call_args_list == expected_bumps

    for permission in created:
        permission.name = f"Updated {permission.codename}"
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = bulk_update_permissions(created, ["name"])
            bump.assert_not_called()
        assert updated == 2
        assert bump.call_args_list == expected_bumps


def test_bulk_user_state_update_bumps_all_layers():
    first = UsuarioFactory()
    second = UsuarioFactory()
    expected = [
        call(
            tuple(f"{layer}:user:{user.pk}" for user in (first, second)),
            database_alias="default",
            layer=layer,
        )
        for layer in ("django", "tenant", "guardian")
    ]

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = update_user_authorization_state(Usuario.objects.filter(pk__in=[first.pk, second.pk]), is_active=False)
            bump.assert_not_called()

        assert updated == 2
        assert bump.call_args_list == expected
        assert not Usuario.objects.filter(pk__in=[first.pk, second.pk], is_active=True).exists()


def test_bulk_user_state_update_rejects_non_authorization_fields(django_assert_num_queries):
    user = UsuarioFactory()

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with django_assert_num_queries(0):
            with pytest.raises(ValueError, match="first_name"):
                update_user_authorization_state(Usuario.objects.filter(pk=user.pk), first_name="x")
        bump.assert_not_called()
