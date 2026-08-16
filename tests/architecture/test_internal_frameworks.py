from importlib import import_module
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_tenant_access_resolver_belongs_to_organization_domain():
    resolver_module = import_module("apps.organizacoes.access")

    assert resolver_module.TenantAccessResolver.__module__ == "apps.organizacoes.access"
    assert not (REPOSITORY_ROOT / "internal_frameworks/permission_cache/resolvers/tenant.py").exists()
