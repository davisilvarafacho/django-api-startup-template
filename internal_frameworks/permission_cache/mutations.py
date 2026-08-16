from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models import QuerySet

from guardian.utils import get_group_obj_perms_model, get_user_obj_perms_model

from apps.organizacoes.models import Vinculo
from apps.usuarios.models import Usuario
from internal_frameworks.permission_cache.invalidation import schedule_epoch_bumps
from internal_frameworks.permission_cache.keys import guardian_object_scope, layer_scope, user_scope
from internal_frameworks.permission_cache.signals.guardian import suppress_guardian_signal_invalidation


def _database_alias(instances: list[models.Model], fallback: str = DEFAULT_DB_ALIAS) -> str:
    if not instances:
        return fallback
    return instances[0]._state.db or fallback


def _schedule_user_scopes(user_ids: Iterable[object], database_alias: str, layers: tuple[str, ...]) -> None:
    ordered_ids = tuple(sorted(set(user_ids), key=str))
    if not ordered_ids:
        return
    for layer in layers:
        schedule_epoch_bumps(
            tuple(user_scope(layer, user_id) for user_id in ordered_ids),
            database_alias=database_alias,
            layer=layer,
        )


def _schedule_guardian_objects(objects: list[models.Model], database_alias: str) -> None:
    scopes = tuple(sorted({guardian_object_scope(_guardian_content_type(obj, database_alias).pk, str(obj.pk)) for obj in objects}))
    if scopes:
        schedule_epoch_bumps(scopes, database_alias=database_alias, layer="guardian")


def _schedule_global_permission_layers(database_alias: str) -> None:
    for layer in ("django", "guardian"):
        schedule_epoch_bumps((layer_scope(layer),), database_alias=database_alias, layer=layer)


def _guardian_model(identity: Usuario | Group):
    if isinstance(identity, Group):
        return get_group_obj_perms_model()
    return get_user_obj_perms_model()


def _guardian_content_type(obj: models.Model, database_alias: str) -> ContentType:
    return ContentType.objects.db_manager(database_alias).get_for_model(obj)


def _guardian_permission(perm: Permission | str, content_type: ContentType, database_alias: str) -> Permission:
    codename = perm if isinstance(perm, str) else perm.codename
    return Permission.objects.using(database_alias).get(content_type=content_type, codename=codename)


def _object_permission_keys(manager, filters: dict[str, object], objects: list[models.Model], content_type: ContentType) -> set[str]:
    object_keys = [str(obj.pk) for obj in objects]
    if manager.is_generic():
        rows = manager.filter(**filters, content_type_id=content_type.pk, object_pk__in=object_keys).values_list("object_pk", flat=True)
    else:
        rows = manager.filter(**filters, content_object_id__in=object_keys).values_list("content_object_id", flat=True)
    return {str(object_pk) for object_pk in rows}


def _effective_object_permission_keys(
    manager,
    permission: Permission,
    identity: Usuario | Group,
    objects: list[models.Model],
    content_type: ContentType,
    database_alias: str,
) -> set[str]:
    if isinstance(identity, Usuario):
        if identity.is_superuser:
            return {str(obj.pk) for obj in objects}
        if not identity.is_active:
            return set()
        effective = _object_permission_keys(manager, {"permission_id": permission.pk, "user_id": identity.pk}, objects, content_type)
        group_ids = identity.groups.using(database_alias).values_list("pk", flat=True)
        group_manager = get_group_obj_perms_model().objects.db_manager(database_alias)
        effective.update(
            _object_permission_keys(
                group_manager,
                {"permission_id": permission.pk, "group_id__in": group_ids},
                objects,
                content_type,
            )
        )
        return effective
    return _object_permission_keys(manager, {"permission_id": permission.pk, "group_id": identity.pk}, objects, content_type)


def _object_permission_row(manager, permission: Permission, identity: Usuario | Group, obj: models.Model, content_type: ContentType):
    identity_field = manager.user_or_group_field
    values = {"permission_id": permission.pk, f"{identity_field}_id": identity.pk}
    if manager.is_generic():
        values.update(content_type_id=content_type.pk, object_pk=obj.pk)
    else:
        values["content_object_id"] = obj.pk
    return manager.model(**values)


def _bulk_assign_guardian_permissions(
    perm: Permission | str,
    identity: Usuario | Group,
    objects: list[models.Model],
    database_alias: str,
    *,
    ignore_conflicts: bool,
) -> list[models.Model]:
    if not objects:
        return []
    manager = _guardian_model(identity).objects.db_manager(database_alias)
    content_type = _guardian_content_type(objects[0], database_alias)
    permission = _guardian_permission(perm, content_type, database_alias)
    existing_keys = _effective_object_permission_keys(manager, permission, identity, objects, content_type, database_alias)
    assigned = [_object_permission_row(manager, permission, identity, obj, content_type) for obj in objects if str(obj.pk) not in existing_keys]
    manager.bulk_create(assigned, ignore_conflicts=ignore_conflicts)
    return assigned


def _assign_guardian_permission_to_many(
    perm: Permission | str,
    identities: list[Usuario | Group],
    obj: models.Model,
    database_alias: str,
    *,
    ignore_conflicts: bool,
) -> list[models.Model]:
    manager = _guardian_model(identities[0]).objects.db_manager(database_alias)
    content_type = _guardian_content_type(obj, database_alias)
    permission = _guardian_permission(perm, content_type, database_alias)
    assigned = [_object_permission_row(manager, permission, identity, obj, content_type) for identity in identities]
    return manager.bulk_create(assigned, ignore_conflicts=ignore_conflicts)


def _bulk_remove_guardian_permissions(
    perm: Permission | str,
    identity: Usuario | Group,
    objects: list[models.Model],
    database_alias: str,
) -> tuple[int, dict[str, int]]:
    if not objects:
        return 0, {}
    manager = _guardian_model(identity).objects.db_manager(database_alias)
    content_type = _guardian_content_type(objects[0], database_alias)
    permission = _guardian_permission(perm, content_type, database_alias)
    filters = {f"{manager.user_or_group_field}_id": identity.pk, "permission_id": permission.pk}
    if manager.is_generic():
        filters.update(content_type_id=content_type.pk, object_pk__in=[str(obj.pk) for obj in objects])
    else:
        filters["content_object_id__in"] = [obj.pk for obj in objects]
    return manager.filter(**filters).delete()


def _remove_guardian_permission_from_many(
    perm: Permission | str,
    identities: list[Usuario | Group],
    obj: models.Model,
    database_alias: str,
) -> tuple[int, dict[str, int]]:
    manager = _guardian_model(identities[0]).objects.db_manager(database_alias)
    content_type = _guardian_content_type(obj, database_alias)
    permission = _guardian_permission(perm, content_type, database_alias)
    filters = {
        f"{manager.user_or_group_field}_id__in": [identity.pk for identity in identities],
        "permission_id": permission.pk,
    }
    if manager.is_generic():
        filters.update(content_type_id=content_type.pk, object_pk=str(obj.pk))
    else:
        filters["content_object_id"] = obj.pk
    return manager.filter(**filters).delete()


def bulk_create_memberships(
    memberships: Iterable[Vinculo],
    *,
    batch_size: int | None = None,
    ignore_conflicts: bool = False,
) -> list[Vinculo]:
    rows = list(memberships)
    database_alias = _database_alias(rows)
    with transaction.atomic(using=database_alias):
        created = Vinculo.objects.using(database_alias).bulk_create(
            rows,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
        )
        _schedule_user_scopes((row.usuario_id for row in rows), database_alias, ("tenant",))
    return created


def bulk_update_memberships(
    memberships: Iterable[Vinculo],
    fields: Iterable[str],
    *,
    batch_size: int | None = None,
) -> int:
    rows = list(memberships)
    field_names = tuple(fields)
    database_alias = _database_alias(rows)
    with transaction.atomic(using=database_alias):
        old_user_ids = Vinculo.objects.using(database_alias).filter(pk__in=[row.pk for row in rows]).values_list("usuario_id", flat=True)
        affected_user_ids = set(old_user_ids)
        affected_user_ids.update(row.usuario_id for row in rows)
        updated = Vinculo.objects.using(database_alias).bulk_update(rows, field_names, batch_size=batch_size)
        _schedule_user_scopes(affected_user_ids, database_alias, ("tenant",))
    return updated


def update_memberships(queryset: QuerySet[Vinculo], **changes: object) -> int:
    database_alias = queryset.db
    with transaction.atomic(using=database_alias):
        affected_user_ids = set(queryset.values_list("usuario_id", flat=True))
        new_user = changes.get("usuario", changes.get("usuario_id"))
        if new_user is not None:
            affected_user_ids.add(getattr(new_user, "pk", new_user))
        updated = queryset.update(**changes)
        _schedule_user_scopes(affected_user_ids, database_alias, ("tenant",))
    return updated


def guardian_bulk_assign(
    perm: Permission | str,
    user_or_group: Usuario | Group,
    objects: Iterable[models.Model],
    *,
    ignore_conflicts: bool = False,
) -> list[models.Model]:
    object_list = list(objects)
    database_alias = _database_alias(object_list, user_or_group._state.db or DEFAULT_DB_ALIAS)
    with transaction.atomic(using=database_alias):
        assigned = _bulk_assign_guardian_permissions(
            perm,
            user_or_group,
            object_list,
            database_alias,
            ignore_conflicts=ignore_conflicts,
        )
        _schedule_guardian_objects(object_list, database_alias)
    return assigned


def guardian_assign_to_many(
    perm: Permission | str,
    users_or_groups: Iterable[Usuario | Group],
    obj: models.Model,
    *,
    ignore_conflicts: bool = False,
) -> list[models.Model]:
    identities = list(users_or_groups)
    if not identities:
        return []
    database_alias = obj._state.db or DEFAULT_DB_ALIAS
    with transaction.atomic(using=database_alias):
        assigned = _assign_guardian_permission_to_many(
            perm,
            identities,
            obj,
            database_alias,
            ignore_conflicts=ignore_conflicts,
        )
        _schedule_guardian_objects([obj], database_alias)
    return assigned


def guardian_bulk_remove(
    perm: Permission | str,
    user_or_group: Usuario | Group,
    objects: Iterable[models.Model],
) -> tuple[int, dict[str, int]]:
    object_list = list(objects)
    database_alias = _database_alias(object_list, user_or_group._state.db or DEFAULT_DB_ALIAS)
    with transaction.atomic(using=database_alias):
        with suppress_guardian_signal_invalidation():
            removed = _bulk_remove_guardian_permissions(perm, user_or_group, object_list, database_alias)
        _schedule_guardian_objects(object_list, database_alias)
    return removed


def guardian_remove_from_many(
    perm: Permission | str,
    users_or_groups: Iterable[Usuario | Group],
    obj: models.Model,
) -> tuple[int, dict[str, int]]:
    identities = list(users_or_groups)
    if not identities:
        return 0, {}
    database_alias = obj._state.db or DEFAULT_DB_ALIAS
    with transaction.atomic(using=database_alias):
        with suppress_guardian_signal_invalidation():
            removed = _remove_guardian_permission_from_many(perm, identities, obj, database_alias)
        _schedule_guardian_objects([obj], database_alias)
    return removed


def bulk_create_permissions(
    permissions: Iterable[Permission],
    *,
    batch_size: int | None = None,
    ignore_conflicts: bool = False,
) -> list[Permission]:
    rows = list(permissions)
    database_alias = _database_alias(rows)
    with transaction.atomic(using=database_alias):
        created = Permission.objects.using(database_alias).bulk_create(
            rows,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
        )
        if rows:
            _schedule_global_permission_layers(database_alias)
    return created


def bulk_update_permissions(
    permissions: Iterable[Permission],
    fields: Iterable[str],
    *,
    batch_size: int | None = None,
) -> int:
    rows = list(permissions)
    field_names = tuple(fields)
    database_alias = _database_alias(rows)
    with transaction.atomic(using=database_alias):
        updated = Permission.objects.using(database_alias).bulk_update(rows, field_names, batch_size=batch_size)
        if rows:
            _schedule_global_permission_layers(database_alias)
    return updated


def update_user_authorization_state(queryset: QuerySet[Usuario], **changes: bool) -> int:
    unsupported_fields = set(changes).difference({"is_active", "is_superuser"})
    if unsupported_fields:
        fields = ", ".join(sorted(unsupported_fields))
        raise ValueError(f"Unsupported user authorization fields: {fields}")

    database_alias = queryset.db
    with transaction.atomic(using=database_alias):
        # `UsuarioQuerySet.update()` é a barreira comum: também cobre quem
        # usa o ORM diretamente e agenda um único bump por camada no commit.
        return queryset.update(**changes)
