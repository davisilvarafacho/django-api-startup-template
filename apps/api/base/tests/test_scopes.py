"""The resource policy is the only authority for model scopes."""

from types import SimpleNamespace

import pytest

from apps.api.autenticacao.models import TokenType
from apps.api.base.permissions import ResourceAccessPermission
from apps.api.base.resource_policies import ResourcePolicy
from apps.api.base.tests.support import policy_view
from apps.api.core.errors import APIError
from apps.organizacoes.models import Papel, Time


@pytest.mark.parametrize(("scope", "expected"), [("teams:read", True), ("users:read", False)])
def test_policy_resource_cannot_be_replaced_by_legacy_view_metadata(scope, expected):
    view = policy_view(
        queryset=Time.objects.none(),
        action="list",
        scope_resource="users",
        required_token_scopes=["users:read"],
        authorization_policy=ResourcePolicy(resource="teams", minimum_roles={"read": Papel.GESTOR}),
    )
    request = SimpleNamespace(
        auth=SimpleNamespace(type=TokenType.API_KEY, scopes=[scope], organization_id=1),
        tenant=SimpleNamespace(organization_id=1),
    )
    if expected:
        assert ResourceAccessPermission().has_permission(request, view)
    else:
        with pytest.raises(APIError):
            ResourceAccessPermission().has_permission(request, view)
