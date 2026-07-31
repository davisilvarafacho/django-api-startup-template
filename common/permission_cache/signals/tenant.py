from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from apps.organizacoes.models import Organizacao, Vinculo
from common.permission_cache.invalidation import schedule_epoch_bumps
from common.permission_cache.keys import layer_scope, user_scope


def _remember_membership_previous(instance, using, raw, update_fields, **kwargs) -> None:
    previous = None
    # `is_deleted` entra aqui porque exclusão é lógica: `Base.delete()` faz um
    # `save(update_fields=["is_deleted"])` e nunca dispara `post_delete`. Sem
    # rastrear o campo, revogar um vínculo não invalidaria o cache e o usuário
    # continuaria autorizado até o TTL expirar.
    tracked_fields = {"usuario", "usuario_id", "organizacao", "organizacao_id", "papel", "is_active", "is_deleted"}
    if not raw and not instance._state.adding and (update_fields is None or tracked_fields.intersection(update_fields)):
        previous = (
            Vinculo.all_objects.using(using)
            .filter(pk=instance.pk)
            .values("usuario_id", "organizacao_id", "papel", "is_active", "is_deleted")
            .first()
        )
    previous = previous or {}
    instance._permission_cache_previous_user_id = previous.get("usuario_id")
    instance._permission_cache_previous_organization_id = previous.get("organizacao_id")
    instance._permission_cache_previous_role = previous.get("papel")
    instance._permission_cache_previous_active = previous.get("is_active")
    instance._permission_cache_previous_deleted = previous.get("is_deleted")


def _invalidate_membership(instance, using, created=False, **kwargs) -> None:
    previous_user_id = getattr(instance, "_permission_cache_previous_user_id", None)
    previous_organization_id = getattr(instance, "_permission_cache_previous_organization_id", None)
    previous_role = getattr(instance, "_permission_cache_previous_role", None)
    previous_active = getattr(instance, "_permission_cache_previous_active", None)
    previous_deleted = getattr(instance, "_permission_cache_previous_deleted", None)
    for attribute in (
        "_permission_cache_previous_user_id",
        "_permission_cache_previous_organization_id",
        "_permission_cache_previous_role",
        "_permission_cache_previous_active",
        "_permission_cache_previous_deleted",
    ):
        if hasattr(instance, attribute):
            delattr(instance, attribute)
    changed = created or (
        previous_user_id is not None
        and (previous_user_id, previous_organization_id, previous_role, previous_active, previous_deleted)
        != (instance.usuario_id, instance.organizacao_id, instance.papel, instance.is_active, instance.is_deleted)
    )
    if not changed:
        return
    user_ids = tuple(user_id for user_id in (previous_user_id, instance.usuario_id) if user_id is not None)
    schedule_epoch_bumps(tuple(sorted({user_scope("tenant", user_id) for user_id in user_ids})), database_alias=using, layer="tenant")


def _remember_deleted_membership(instance, **kwargs) -> None:
    instance._permission_cache_previous_user_id = instance.usuario_id


def _invalidate_deleted_membership(instance, using, **kwargs) -> None:
    user_id = getattr(instance, "_permission_cache_previous_user_id", None)
    if hasattr(instance, "_permission_cache_previous_user_id"):
        delattr(instance, "_permission_cache_previous_user_id")
    if user_id is not None:
        schedule_epoch_bumps((user_scope("tenant", user_id),), database_alias=using, layer="tenant")


def _remember_organization_previous(instance, using, raw, update_fields, **kwargs) -> None:
    previous = None
    if not raw and not instance._state.adding and (update_fields is None or {"slug", "is_active", "is_deleted"}.intersection(update_fields)):
        previous = Organizacao.all_objects.using(using).filter(pk=instance.pk).values("slug", "is_active", "is_deleted").first()
    previous = previous or {}
    instance._permission_cache_previous_slug = previous.get("slug")
    instance._permission_cache_previous_active = previous.get("is_active")
    instance._permission_cache_previous_deleted = previous.get("is_deleted")


def _invalidate_organization(instance, using, created=False, **kwargs) -> None:
    previous_slug = getattr(instance, "_permission_cache_previous_slug", None)
    previous_active = getattr(instance, "_permission_cache_previous_active", None)
    previous_deleted = getattr(instance, "_permission_cache_previous_deleted", None)
    for attribute in ("_permission_cache_previous_slug", "_permission_cache_previous_active", "_permission_cache_previous_deleted"):
        if hasattr(instance, attribute):
            delattr(instance, attribute)
    if created or (
        previous_slug is not None
        and (previous_slug, previous_active, previous_deleted) != (instance.slug, instance.is_active, instance.is_deleted)
    ):
        schedule_epoch_bumps((layer_scope("tenant"),), database_alias=using, layer="tenant")


def _invalidate_deleted_organization(instance, using, **kwargs) -> None:
    schedule_epoch_bumps((layer_scope("tenant"),), database_alias=using, layer="tenant")


def connect_tenant_signals() -> None:
    pre_save.connect(_remember_membership_previous, sender=Vinculo, dispatch_uid="permission_cache.tenant.membership.pre_save")
    post_save.connect(_invalidate_membership, sender=Vinculo, dispatch_uid="permission_cache.tenant.membership.post_save")
    pre_delete.connect(_remember_deleted_membership, sender=Vinculo, dispatch_uid="permission_cache.tenant.membership.pre_delete")
    post_delete.connect(_invalidate_deleted_membership, sender=Vinculo, dispatch_uid="permission_cache.tenant.membership.post_delete")
    pre_save.connect(_remember_organization_previous, sender=Organizacao, dispatch_uid="permission_cache.tenant.organization.pre_save")
    post_save.connect(_invalidate_organization, sender=Organizacao, dispatch_uid="permission_cache.tenant.organization.post_save")
    post_delete.connect(_invalidate_deleted_organization, sender=Organizacao, dispatch_uid="permission_cache.tenant.organization.post_delete")
