from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from guardian.utils import get_group_obj_perms_model, get_user_obj_perms_model

from internal_frameworks.permission_cache.invalidation import schedule_epoch_bumps
from internal_frameworks.permission_cache.keys import guardian_object_scope

_PREVIOUS_SCOPE_ATTRIBUTE = "_permission_cache_previous_guardian_object_scope"
_DELETED_SCOPE_ATTRIBUTE = "_permission_cache_deleted_guardian_object_scope"
_SIGNAL_INVALIDATION_SUPPRESSED: ContextVar[bool] = ContextVar(
    "permission_cache_guardian_signal_invalidation_suppressed",
    default=False,
)


@contextmanager
def suppress_guardian_signal_invalidation():
    token = _SIGNAL_INVALIDATION_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SIGNAL_INVALIDATION_SUPPRESSED.reset(token)


def _object_scope(instance) -> str | None:
    content_type_id = getattr(instance, "content_type_id", None)
    object_pk = getattr(instance, "object_pk", None)
    if content_type_id is None or object_pk is None:
        return None
    return guardian_object_scope(content_type_id, str(object_pk))


def _remember_previous_object(instance, using, raw, **kwargs) -> None:
    previous_scope = None
    if not raw and not instance._state.adding:
        previous = type(instance).objects.using(using).filter(pk=instance.pk).values("content_type_id", "object_pk").first()
        if previous is not None:
            previous_scope = guardian_object_scope(previous["content_type_id"], str(previous["object_pk"]))
    setattr(instance, _PREVIOUS_SCOPE_ATTRIBUTE, previous_scope)


def _invalidate_saved_object(instance, using, **kwargs) -> None:
    previous_scope = getattr(instance, _PREVIOUS_SCOPE_ATTRIBUTE, None)
    if hasattr(instance, _PREVIOUS_SCOPE_ATTRIBUTE):
        delattr(instance, _PREVIOUS_SCOPE_ATTRIBUTE)
    scopes = tuple(sorted({scope for scope in (previous_scope, _object_scope(instance)) if scope is not None}))
    if scopes:
        schedule_epoch_bumps(scopes, database_alias=using, layer="guardian")


def _remember_deleted_object(instance, **kwargs) -> None:
    setattr(instance, _DELETED_SCOPE_ATTRIBUTE, _object_scope(instance))


def _invalidate_deleted_object(instance, using, **kwargs) -> None:
    scope = getattr(instance, _DELETED_SCOPE_ATTRIBUTE, None)
    if hasattr(instance, _DELETED_SCOPE_ATTRIBUTE):
        delattr(instance, _DELETED_SCOPE_ATTRIBUTE)
    if scope is not None and not _SIGNAL_INVALIDATION_SUPPRESSED.get():
        schedule_epoch_bumps((scope,), database_alias=using, layer="guardian")


def connect_guardian_signals() -> None:
    for model, identity in (
        (get_user_obj_perms_model(), "user"),
        (get_group_obj_perms_model(), "group"),
    ):
        pre_save.connect(
            _remember_previous_object,
            sender=model,
            dispatch_uid=f"permission_cache.guardian.{identity}.pre_save",
        )
        post_save.connect(
            _invalidate_saved_object,
            sender=model,
            dispatch_uid=f"permission_cache.guardian.{identity}.post_save",
        )
        pre_delete.connect(
            _remember_deleted_object,
            sender=model,
            dispatch_uid=f"permission_cache.guardian.{identity}.pre_delete",
        )
        post_delete.connect(
            _invalidate_deleted_object,
            sender=model,
            dispatch_uid=f"permission_cache.guardian.{identity}.post_delete",
        )
