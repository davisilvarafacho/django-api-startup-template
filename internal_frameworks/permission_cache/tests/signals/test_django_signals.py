import contextvars
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import call, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models.signals import post_migrate

import pytest

from internal_frameworks.permission_cache.signals.django import _affected_user_ids, _capture_reverse_clear_user_ids
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db(transaction=True)

Usuario = get_user_model()


@contextmanager
def without_dangling_rls_test_model():
    """Hide a suite-only model whose fixture drops its table but leaves it registered."""
    registered_models = apps.all_models["organizacoes"]
    test_model = registered_models.pop("registrorls", None)
    apps.clear_cache()
    try:
        yield
    finally:
        if test_model is not None:
            registered_models["registrorls"] = test_model
            apps.clear_cache()


def assert_user_layer_bumps(bump, user_ids, layers):
    expected = [
        call(
            tuple(f"{layer}:user:{user_id}" for user_id in user_ids),
            database_alias="default",
            layer=layer,
        )
        for layer in layers
    ]
    assert bump.call_args_list == expected


def assert_global_layer_bumps(bump, layers=("django", "guardian")):
    assert bump.call_args_list == [
        call(
            (f"{layer}:global",),
            database_alias="default",
            layer=layer,
        )
        for layer in layers
    ]


def test_direct_permission_bumps_only_after_commit():
    user = criar_usuario()
    permission = Permission.objects.first()

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            user.user_permissions.add(permission)
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"django:user:{user.pk}",),
            database_alias="default",
            layer="django",
        )


def test_rollback_does_not_bump_user_epoch():
    user = criar_usuario()
    permission = Permission.objects.first()

    def add_permission_then_rollback():
        with transaction.atomic():
            user.user_permissions.add(permission)
            raise RuntimeError("rollback")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with pytest.raises(RuntimeError, match="rollback"):
            add_permission_then_rollback()

        bump.assert_not_called()


def test_user_group_add_remove_and_clear_bump_both_user_layers():
    group = Group.objects.create(name="membership changes")

    for operation in ("add", "remove", "clear"):
        user = criar_usuario()
        if operation in {"remove", "clear"}:
            user.groups.add(group)

        with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
            with transaction.atomic():
                if operation == "add":
                    user.groups.add(group)
                elif operation == "remove":
                    user.groups.remove(group)
                else:
                    user.groups.clear()
                bump.assert_not_called()

            assert_user_layer_bumps(bump, (user.pk,), ("django", "guardian"))


def test_reverse_group_clear_captures_users_in_pre_clear():
    group = Group.objects.create(name="reverse clear")
    users = (criar_usuario(), criar_usuario())
    group.user_set.add(*users)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            group.user_set.clear()
            bump.assert_not_called()

        assert_user_layer_bumps(
            bump,
            tuple(sorted(user.pk for user in users)),
            ("django", "guardian"),
        )


def test_reverse_clear_isolated_between_execution_contexts():
    class FakeUserSet:
        def __init__(self, user_ids):
            self.user_ids = user_ids

        def using(self, alias):
            return self

        def values_list(self, field, flat):
            assert field == "pk"
            assert flat is True
            return self.user_ids

    instance_a = SimpleNamespace(pk=7, user_set=FakeUserSet(("user-a",)))
    instance_b = SimpleNamespace(pk=7, user_set=FakeUserSet(("user-b",)))
    context_a = contextvars.Context()
    context_b = contextvars.Context()

    context_a.run(_capture_reverse_clear_user_ids, Group, instance_a, "pre_clear", True, "default")
    context_b.run(_capture_reverse_clear_user_ids, Group, instance_b, "pre_clear", True, "default")

    affected_a = context_a.run(_affected_user_ids, Group, instance_a, "post_clear", True, None, "default")
    affected_b = context_b.run(_affected_user_ids, Group, instance_b, "post_clear", True, None, "default")

    assert affected_a == ("user-a",)
    assert affected_b == ("user-b",)


def test_group_permission_change_bumps_django_global():
    group = Group.objects.create(name="permission changes")
    permission = Permission.objects.first()

    for operation in ("add", "remove", "clear"):
        if operation in {"remove", "clear"}:
            group.permissions.add(permission)

        with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
            with transaction.atomic():
                if operation == "add":
                    group.permissions.add(permission)
                elif operation == "remove":
                    group.permissions.remove(permission)
                else:
                    group.permissions.clear()
                bump.assert_not_called()

            assert_global_layer_bumps(bump, ("django",))


def test_group_delete_bumps_django_and_guardian_global():
    group = Group.objects.create(name="deleted group")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            group.delete()
            bump.assert_not_called()

        assert_global_layer_bumps(bump)


def test_user_authorization_state_change_bumps_all_user_layers():
    for field in ("is_active", "is_superuser", "is_deleted"):
        user = criar_usuario()
        setattr(user, field, not getattr(user, field))

        with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
            with transaction.atomic():
                user.save(update_fields=[field])
                bump.assert_not_called()

            assert_user_layer_bumps(bump, (user.pk,), ("django", "tenant", "guardian"))


def test_bulk_update_de_flags_bumps_all_user_layers_after_commit():
    user = criar_usuario()

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            Usuario.all_objects.filter(pk=user.pk).update(is_active=False)
            bump.assert_not_called()

        assert_user_layer_bumps(bump, (user.pk,), ("django", "tenant", "guardian"))


def test_user_delete_bumps_all_user_layers():
    user = criar_usuario()
    user_id = user.pk

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with without_dangling_rls_test_model():
            with transaction.atomic():
                user.delete()
                bump.assert_not_called()

        assert_user_layer_bumps(bump, (user_id,), ("django", "tenant", "guardian"))
    user.refresh_from_db(from_queryset=Usuario.all_objects.all())
    assert user.is_deleted is True
    assert user.is_active is False

    # O epoch é defesa secundária: o estado que a invalidação anuncia precisa
    # estar de fato persistido na linha do usuário.
    excluido = Usuario.all_objects.get(pk=user_id)
    assert excluido.is_deleted is True
    assert excluido.is_active is False


def test_permission_and_content_type_changes_bump_global_layers():
    content_type = ContentType.objects.get_for_model(Group)
    permission = Permission.objects.create(
        content_type=content_type,
        codename="permission_cache_signal",
        name="Permission cache signal",
    )

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            permission.name = "Updated permission cache signal"
            permission.save(update_fields=["name"])
            bump.assert_not_called()
        assert_global_layer_bumps(bump)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            permission.delete()
            bump.assert_not_called()
        assert_global_layer_bumps(bump)

    original_model = content_type.model
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            content_type.model = f"{original_model}_renamed"
            content_type.save(update_fields=["model"])
            bump.assert_not_called()
        assert_global_layer_bumps(bump)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            content_type.model = original_model
            content_type.save(update_fields=["model"])
            bump.assert_not_called()
        assert_global_layer_bumps(bump)


def test_post_migrate_bumps_global_layers():
    app_config = apps.get_app_config("autenticacao")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            post_migrate.send(
                sender=app_config,
                app_config=app_config,
                verbosity=0,
                interactive=False,
                using="default",
                plan=(),
                apps=apps,
            )
            bump.assert_not_called()

        assert_global_layer_bumps(bump)
