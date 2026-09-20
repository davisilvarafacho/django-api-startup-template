"""Authorized tenant-free Usuario views used by base behavior tests."""

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.api.base.permissions import ModelPermissionMixin
from apps.api.base.resource_policies import ActionPolicy, ResourcePolicy
from apps.usuarios.models import Usuario


def usuario_policy(*extra_actions, metadata=True):
    """Declare only the actions exposed by each concrete test view."""
    custom = {"logs": ActionPolicy(action="read", permission="view_usuario")}
    if metadata:
        custom.update(
            {
                "metadata": ActionPolicy(action="read", permission="view_usuario"),
                "metadata_update": ActionPolicy(action="update", permission="change_usuario"),
            }
        )
    available = {
        "bulk_update": ActionPolicy(action="update", permission="change_usuario"),
        "clonar": ActionPolicy(action="create", permission="add_usuario"),
    }
    custom.update({name: available[name] for name in extra_actions})
    return ResourcePolicy(
        resource="users",
        minimum_roles={"read": None, "create": None, "update": None, "delete": None},
        custom_actions=custom,
        api_key_enabled=False,
    )


class TenantFreeAuthenticatedRequestFactory(APIRequestFactory):
    """Supply authentication and the tenant-free marker normally set by middleware."""

    def generic(self, *args, **kwargs):
        request = super().generic(*args, **kwargs)
        request.tenant_required = False
        force_authenticate(request, user=Usuario(pk=999999, is_active=True, is_superuser=True))
        return request


def policy_view(**attributes):
    """Build a policy-bound view with class declarations, like a routed ViewSet."""
    return type("PolicyView", (ModelPermissionMixin,), attributes)()
