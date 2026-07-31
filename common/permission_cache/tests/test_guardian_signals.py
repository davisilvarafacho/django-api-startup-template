from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.db import transaction

import pytest
from guardian.ctypes import get_content_type
from guardian.utils import get_group_obj_perms_model, get_user_obj_perms_model

from apps.organizacoes.models import Organizacao
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.signals.guardian import connect_guardian_signals

pytestmark = pytest.mark.django_db(transaction=True)


def create_organization(slug: str) -> Organizacao:
    return Organizacao.objects.create(nome=slug.title(), slug=slug)


def organization_permission(organization: Organizacao) -> tuple[object, Permission]:
    content_type = get_content_type(organization)
    permission = Permission.objects.get(content_type=content_type, codename="view_organizacao")
    return content_type, permission


def test_user_object_permission_create_bumps_object_after_commit():
    connect_guardian_signals()
    user = UsuarioFactory()
    organization = create_organization("acme")
    content_type, permission = organization_permission(organization)
    model = get_user_obj_perms_model(organization)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            model.objects.create(
                user=user,
                permission=permission,
                content_type=content_type,
                object_pk=str(organization.pk),
            )
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"guardian:object:{content_type.pk}:{organization.pk}",),
            database_alias="default",
            layer="guardian",
        )


def test_user_object_permission_update_bumps_old_and_new_objects():
    connect_guardian_signals()
    user = UsuarioFactory()
    first = create_organization("first")
    second = create_organization("second")
    content_type, permission = organization_permission(first)
    model = get_user_obj_perms_model(first)
    row = model.objects.create(
        user=user,
        permission=permission,
        content_type=content_type,
        object_pk=str(first.pk),
    )

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            row.object_pk = str(second.pk)
            row.save(update_fields=["object_pk"])
            bump.assert_not_called()

        bump.assert_called_once_with(
            tuple(sorted((f"guardian:object:{content_type.pk}:{first.pk}", f"guardian:object:{content_type.pk}:{second.pk}"))),
            database_alias="default",
            layer="guardian",
        )


def test_user_object_permission_delete_bumps_old_object():
    connect_guardian_signals()
    user = UsuarioFactory()
    organization = create_organization("delete-user-permission")
    content_type, permission = organization_permission(organization)
    model = get_user_obj_perms_model(organization)
    row = model.objects.create(
        user=user,
        permission=permission,
        content_type=content_type,
        object_pk=str(organization.pk),
    )

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            row.delete()
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"guardian:object:{content_type.pk}:{organization.pk}",),
            database_alias="default",
            layer="guardian",
        )


def test_group_object_permission_create_update_delete_has_same_matrix():
    connect_guardian_signals()
    group = Group.objects.create(name="guardian signal group")
    first = create_organization("group-first")
    second = create_organization("group-second")
    content_type, permission = organization_permission(first)
    model = get_group_obj_perms_model(first)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            row = model.objects.create(
                group=group,
                permission=permission,
                content_type=content_type,
                object_pk=str(first.pk),
            )
            bump.assert_not_called()
        bump.assert_called_once_with(
            (f"guardian:object:{content_type.pk}:{first.pk}",),
            database_alias="default",
            layer="guardian",
        )

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            row.object_pk = str(second.pk)
            row.save(update_fields=["object_pk"])
            bump.assert_not_called()
        bump.assert_called_once_with(
            tuple(sorted((f"guardian:object:{content_type.pk}:{first.pk}", f"guardian:object:{content_type.pk}:{second.pk}"))),
            database_alias="default",
            layer="guardian",
        )

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            row.delete()
            bump.assert_not_called()
        bump.assert_called_once_with(
            (f"guardian:object:{content_type.pk}:{second.pk}",),
            database_alias="default",
            layer="guardian",
        )


def test_guardian_signal_canonicalizes_string_pk():
    connect_guardian_signals()
    user = UsuarioFactory()
    organization = create_organization("canonical-pk")
    content_type, permission = organization_permission(organization)
    model = get_user_obj_perms_model(organization)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            model.objects.create(
                user=user,
                permission=permission,
                content_type=content_type,
                object_pk=organization.pk,
            )
            bump.assert_not_called()

        scope = bump.call_args.args[0][0]
        assert scope == f"guardian:object:{content_type.pk}:{organization.pk}"
        assert scope.split(":")[-1] == str(organization.pk)


def test_guardian_signal_ignores_rollback():
    connect_guardian_signals()
    user = UsuarioFactory()
    organization = create_organization("rollback")
    content_type, permission = organization_permission(organization)
    model = get_user_obj_perms_model(organization)

    def create_permission_then_rollback():
        with transaction.atomic():
            model.objects.create(
                user=user,
                permission=permission,
                content_type=content_type,
                object_pk=str(organization.pk),
            )
            raise RuntimeError("rollback")

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with pytest.raises(RuntimeError, match="rollback"):
            create_permission_then_rollback()

        bump.assert_not_called()
