from django.contrib.auth.backends import ModelBackend

from asgiref.sync import sync_to_async
from guardian.backends import ObjectPermissionBackend, check_support
from guardian.ctypes import get_content_type
from guardian.exceptions import WrongAppError

from internal_frameworks.permission_cache.resolvers.django import DjangoPermissionResolver
from internal_frameworks.permission_cache.resolvers.guardian import GuardianPermissionResolver


def conta_utilizavel(user_obj) -> bool:
    """Conta que ainda pode receber permissão: ativa e não excluída.

    Vale inclusive para superusuário — o atalho `is_superuser` dos backends não
    pode sobreviver à exclusão da conta.
    """
    return bool(getattr(user_obj, "is_active", False)) and not getattr(user_obj, "is_deleted", False)


class CachedModelBackend(ModelBackend):
    def get_user_permissions(self, user_obj, obj=None):
        if obj is not None:
            return set()
        return set(DjangoPermissionResolver().resolve(user_obj).user_permissions)

    async def aget_user_permissions(self, user_obj, obj=None):
        return await sync_to_async(self.get_user_permissions, thread_sensitive=True)(user_obj, obj=obj)

    def get_group_permissions(self, user_obj, obj=None):
        if obj is not None:
            return set()
        return set(DjangoPermissionResolver().resolve(user_obj).group_permissions)

    async def aget_group_permissions(self, user_obj, obj=None):
        return await sync_to_async(self.get_group_permissions, thread_sensitive=True)(user_obj, obj=obj)

    def get_all_permissions(self, user_obj, obj=None):
        if obj is not None:
            return set()
        return set(DjangoPermissionResolver().resolve(user_obj).all_permissions)

    async def aget_all_permissions(self, user_obj, obj=None):
        return await sync_to_async(self.get_all_permissions, thread_sensitive=True)(user_obj, obj=obj)

    def has_perm(self, user_obj, perm, obj=None):
        return conta_utilizavel(user_obj) and perm in self.get_all_permissions(user_obj, obj=obj)

    async def ahas_perm(self, user_obj, perm, obj=None):
        return await sync_to_async(self.has_perm, thread_sensitive=True)(user_obj, perm, obj=obj)

    def has_module_perms(self, user_obj, app_label):
        return conta_utilizavel(user_obj) and any(
            permission[: permission.index(".")] == app_label for permission in self.get_all_permissions(user_obj)
        )

    async def ahas_module_perms(self, user_obj, app_label):
        return await sync_to_async(self.has_module_perms, thread_sensitive=True)(user_obj, app_label)


class CachedObjectPermissionBackend(ObjectPermissionBackend):
    def has_perm(self, user_obj, perm, obj=None):
        support, user_obj = check_support(user_obj, obj)
        if not support:
            return False
        if getattr(user_obj, "is_deleted", False):
            return False

        if not conta_utilizavel(user_obj):
            return False

        codename = perm
        if "." in perm:
            app_label, codename = perm.split(".", 1)
            if app_label != obj._meta.app_label:
                content_type = get_content_type(obj)
                if app_label != content_type.app_label:
                    raise WrongAppError(
                        f"Passed perm has app label of '{app_label}' while given obj has app label "
                        f"'{obj._meta.app_label}' and given obj content_type has app label '{content_type.app_label}'"
                    )

        if user_obj.is_superuser:
            return True
        return codename in GuardianPermissionResolver().resolve(user_obj, obj).all_permissions

    def get_group_permissions(self, user_obj, obj=None):
        support, user_obj = check_support(user_obj, obj)
        if not support:
            return set()
        return set(GuardianPermissionResolver().resolve(user_obj, obj).group_permissions)

    def get_all_permissions(self, user_obj, obj=None):
        support, user_obj = check_support(user_obj, obj)
        if not support:
            return set()
        return set(GuardianPermissionResolver().resolve(user_obj, obj).all_permissions)

    async def ahas_perm(self, user_obj, perm, obj=None):
        bound_method = self.has_perm
        return await sync_to_async(bound_method, thread_sensitive=True)(user_obj, perm, obj=obj)

    async def aget_group_permissions(self, user_obj, obj=None):
        bound_method = self.get_group_permissions
        return await sync_to_async(bound_method, thread_sensitive=True)(user_obj, obj=obj)

    async def aget_all_permissions(self, user_obj, obj=None):
        bound_method = self.get_all_permissions
        return await sync_to_async(bound_method, thread_sensitive=True)(user_obj, obj=obj)
