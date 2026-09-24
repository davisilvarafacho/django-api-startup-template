from types import SimpleNamespace

import pytest

from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.permissions import WorkspacePermission


def _request(**kwargs):
    defaults = {
        "tenant_required": True,
        "workspace_required": True,
        "auth": SimpleNamespace(type=TokenType.TOKEN),
        "current_workspace": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_workspace_permission_exige_current_em_rota_de_negocio_humana():
    with pytest.raises(APIError) as excinfo:
        WorkspacePermission().has_permission(_request(), object())

    assert excinfo.value.code == WorkspaceErrorCode.CURRENT_REQUIRED.value
    assert excinfo.value.status_code == 409


def test_workspace_permission_aceita_humano_com_current():
    assert WorkspacePermission().has_permission(_request(current_workspace=object()), object()) is True


@pytest.mark.parametrize(
    "request_obj",
    [
        _request(tenant_required=False),
        _request(workspace_required=False),
        _request(auth=SimpleNamespace(type=TokenType.API_KEY)),
    ],
)
def test_workspace_permission_dispensa_current_em_rotas_isentas(request_obj):
    assert WorkspacePermission().has_permission(request_obj, object()) is True
