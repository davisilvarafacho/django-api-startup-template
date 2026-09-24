"""Contratos da policy RLS de workspace e da herança de ``Base``."""

from django.db import models

from apps.api.base.models import Base
from apps.api.metadata.models import Metadata
from apps.workspaces.policies import WorkspacePolicy


def test_workspace_policy_e_restritiva_e_cobre_os_quatro_modos():
    policy = WorkspacePolicy(name="isolamento_workspace", permissive=False)

    expression = policy.get_sql_expression()

    assert policy.permissive is False
    for mode in ("system", "control", "api_key", "membership"):
        assert f"current_setting('rls.workspace_mode', true), '')) = '{mode}'" in expression
    assert "workspace_vinculo" in expression
    assert "selected_for_view" in expression
    assert "current_setting('rls.membership_id'" in expression


def test_base_combina_tenant_e_workspace_com_policy_restritiva():
    policies = {policy.name: policy for policy in Metadata._rls_policies}

    assert set(policies) == {"isolamento_organizacao", "isolamento_workspace"}
    assert policies["isolamento_workspace"].permissive is False
    assert Metadata._meta.get_field("workspace").null is True


def test_todas_as_subclasses_concretas_de_base_expoem_workspace():
    subclasses = [model for model in Base.__subclasses__() if not model._meta.abstract]

    assert subclasses
    assert all(model._meta.get_field("workspace") for model in subclasses)
    assert all(hasattr(model, "workspace_required") for model in subclasses)


class RegistroWorkspaceObrigatorio(Base):
    workspace_required = True

    codigo = models.CharField(max_length=32)

    class Meta:
        app_label = "workspaces"
        constraints = [
            models.CheckConstraint(condition=models.Q(workspace__isnull=False), name="registro_workspace_obrigatorio"),
        ]
