from django.contrib.auth.backends import ModelBackend

from asgiref.sync import sync_to_async

from common.permission_cache.resolvers.django import DjangoPermissionResolver


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
        return user_obj.is_active and perm in self.get_all_permissions(user_obj, obj=obj)

    async def ahas_perm(self, user_obj, perm, obj=None):
        return await sync_to_async(self.has_perm, thread_sensitive=True)(user_obj, perm, obj=obj)

    def has_module_perms(self, user_obj, app_label):
        return user_obj.is_active and any(permission[: permission.index(".")] == app_label for permission in self.get_all_permissions(user_obj))

    async def ahas_module_perms(self, user_obj, app_label):
        return await sync_to_async(self.has_module_perms, thread_sensitive=True)(user_obj, app_label)
