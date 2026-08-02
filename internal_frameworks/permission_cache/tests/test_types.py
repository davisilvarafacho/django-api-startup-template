import pytest

from internal_frameworks.permission_cache.types import (
    DjangoPermissionSnapshot,
    InvalidEnvelope,
    TenantAccess,
    decode_envelope,
    encode_envelope,
)


def test_permission_snapshot_preserves_direct_group_and_union():
    snapshot = DjangoPermissionSnapshot(
        user_permissions=frozenset({"core.add_item"}),
        group_permissions=frozenset({"core.view_item"}),
    )

    assert snapshot.all_permissions == frozenset({"core.add_item", "core.view_item"})


def test_tenant_access_is_immutable_and_compares_role():
    tenant = TenantAccess(organization_id=10, organization_slug="acme", membership_id=27, role=30)

    assert tenant.has_minimum_role(20)
    with pytest.raises(AttributeError):
        tenant.role = 10


def test_envelope_distinguishes_negative_hit_and_invalid_schema():
    assert decode_envelope(encode_envelope(None)) == {"schema": 1, "found": False}
    with pytest.raises(InvalidEnvelope):
        decode_envelope('{"schema": 999, "found": false}')
