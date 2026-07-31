from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import m2m_changed, post_delete, post_migrate, post_save, pre_save

from apps.usuarios.models import Usuario
from common.permission_cache.invalidation import schedule_epoch_bumps
from common.permission_cache.keys import layer_scope, user_scope

_CLEAR_USER_IDS: dict[tuple[type[Any], object, str], tuple[object, ...]] = {}
_PREVIOUS_AUTH_FLAGS_ATTRIBUTE = "_permission_cache_previous_auth_flags"
_POST_ACTIONS = {"post_add", "post_remove", "post_clear"}


def _ordered_ids(user_ids: Iterable[object]) -> tuple[object, ...]:
    return tuple(sorted(set(user_ids), key=str))


def _clear_key(sender: type[Any], instance: object, using: str) -> tuple[type[Any], object, str]:
    return sender, instance.pk, using


def _schedule_user_layers(user_ids: Iterable[object], using: str, layers: tuple[str, ...]) -> None:
    ordered_ids = _ordered_ids(user_ids)
    if not ordered_ids:
        return
    for layer in layers:
        schedule_epoch_bumps(
            tuple(user_scope(layer, user_id) for user_id in ordered_ids),
            database_alias=using,
            layer=layer,
        )


def _schedule_global_layers(using: str, layers: tuple[str, ...] = ("django", "guardian")) -> None:
    for layer in layers:
        schedule_epoch_bumps(
            (layer_scope(layer),),
            database_alias=using,
            layer=layer,
        )


def _capture_reverse_clear_user_ids(sender, instance, action, reverse, using, **kwargs) -> None:
    if action != "pre_clear" or not reverse:
        return
    _CLEAR_USER_IDS[_clear_key(sender, instance, using)] = _ordered_ids(instance.user_set.using(using).values_list("pk", flat=True))


def _affected_user_ids(sender, instance, action, reverse, pk_set, using) -> tuple[object, ...]:
    if not reverse:
        return (instance.pk,)
    if action == "post_clear":
        return _CLEAR_USER_IDS.pop(_clear_key(sender, instance, using), ())
    return _ordered_ids(pk_set or ())


def _user_permission_changed(sender, instance, action, reverse, pk_set, using, **kwargs) -> None:
    _capture_reverse_clear_user_ids(sender, instance, action, reverse, using)
    if action in _POST_ACTIONS:
        _schedule_user_layers(
            _affected_user_ids(sender, instance, action, reverse, pk_set, using),
            using,
            ("django",),
        )


def _user_group_changed(sender, instance, action, reverse, pk_set, using, **kwargs) -> None:
    _capture_reverse_clear_user_ids(sender, instance, action, reverse, using)
    if action in _POST_ACTIONS:
        _schedule_user_layers(
            _affected_user_ids(sender, instance, action, reverse, pk_set, using),
            using,
            ("django", "guardian"),
        )


def _group_permission_changed(action, using, **kwargs) -> None:
    if action in _POST_ACTIONS:
        _schedule_global_layers(using, ("django",))


def _remember_user_auth_flags(instance, using, raw, update_fields, **kwargs) -> None:
    previous_flags = None
    relevant_fields = {"is_active", "is_superuser"}
    if not raw and not instance._state.adding and (update_fields is None or relevant_fields.intersection(update_fields)):
        previous_flags = Usuario.objects.using(using).filter(pk=instance.pk).values_list("is_active", "is_superuser").first()
    setattr(instance, _PREVIOUS_AUTH_FLAGS_ATTRIBUTE, previous_flags)


def _user_saved(instance, using, **kwargs) -> None:
    previous_flags = getattr(instance, _PREVIOUS_AUTH_FLAGS_ATTRIBUTE, None)
    if hasattr(instance, _PREVIOUS_AUTH_FLAGS_ATTRIBUTE):
        delattr(instance, _PREVIOUS_AUTH_FLAGS_ATTRIBUTE)
    if previous_flags is not None and previous_flags != (instance.is_active, instance.is_superuser):
        _schedule_user_layers((instance.pk,), using, ("django", "tenant", "guardian"))


def _user_deleted(instance, using, **kwargs) -> None:
    _schedule_user_layers((instance.pk,), using, ("django", "tenant", "guardian"))


def _global_structure_changed(using, **kwargs) -> None:
    _schedule_global_layers(using)


def _permissions_migrated(using=DEFAULT_DB_ALIAS, **kwargs) -> None:
    _schedule_global_layers(using)


def connect_django_signals() -> None:
    m2m_changed.connect(
        _user_permission_changed,
        sender=Usuario.user_permissions.through,
        dispatch_uid="permission_cache.django.user_permissions",
    )
    m2m_changed.connect(
        _user_group_changed,
        sender=Usuario.groups.through,
        dispatch_uid="permission_cache.django.user_groups",
    )
    m2m_changed.connect(
        _group_permission_changed,
        sender=Group.permissions.through,
        dispatch_uid="permission_cache.django.group_permissions",
    )
    pre_save.connect(
        _remember_user_auth_flags,
        sender=Usuario,
        dispatch_uid="permission_cache.django.user.pre_save",
    )
    post_save.connect(
        _user_saved,
        sender=Usuario,
        dispatch_uid="permission_cache.django.user.post_save",
    )
    post_delete.connect(
        _user_deleted,
        sender=Usuario,
        dispatch_uid="permission_cache.django.user.post_delete",
    )
    for signal, model, event in (
        (post_save, Group, "group.post_save"),
        (post_delete, Group, "group.post_delete"),
        (post_save, Permission, "permission.post_save"),
        (post_delete, Permission, "permission.post_delete"),
        (post_save, ContentType, "content_type.post_save"),
        (post_delete, ContentType, "content_type.post_delete"),
    ):
        signal.connect(
            _global_structure_changed,
            sender=model,
            dispatch_uid=f"permission_cache.django.{event}",
        )
    post_migrate.connect(
        _permissions_migrated,
        sender=apps.get_app_config("autenticacao"),
        dispatch_uid="permission_cache.django.post_migrate",
    )
