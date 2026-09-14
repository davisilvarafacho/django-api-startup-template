"""Contrato OpenAPI literal do encerramento de organizações."""

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.organizacoes import urls as organizacoes_urls
from apps.organizacoes.views import ConviteViewSet, OrganizacaoViewSet, VinculoViewSet


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    for view in (ConviteViewSet, OrganizacaoViewSet, VinculoViewSet):
        monkeypatch.setattr(view, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def _operacoes_encerramento():
    schema = SchemaGenerator(patterns=organizacoes_urls.urlpatterns).get_schema(request=None, public=True)
    path = schema["paths"]["/organizacoes/{id}/encerramento/"]
    return schema, path["post"], path["delete"]


def test_post_encerramento_documenta_sem_body_e_somente_202_204_e_erros_reais():
    schema, post, _delete = _operacoes_encerramento()

    assert "requestBody" not in post
    assert set(post["responses"]) == {"202", "204", "401", "403", "404"}
    assert post["responses"]["202"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/EncerramentoAgendadoResponse"}
    scheduled_for = schema["components"]["schemas"]["EncerramentoAgendadoResponse"]["properties"]["scheduled_for"]
    assert scheduled_for == {"type": "string", "format": "date-time"}
    assert schema["components"]["schemas"]["EncerramentoAgendadoResponse"]["required"] == ["scheduled_for"]
    assert "auth.not_authenticated" in post["responses"]["401"]["description"]
    assert "auth.reauthentication_required" in post["responses"]["401"]["description"]
    assert "organizations.role_insufficient" in post["responses"]["403"]["description"]
    assert "account.email_not_verified" in post["responses"]["403"]["description"]
    assert "core.not_found" in post["responses"]["404"]["description"]


def test_delete_encerramento_documenta_sem_body_204_e_erros_reais():
    _schema, _post, delete = _operacoes_encerramento()

    assert "requestBody" not in delete
    assert set(delete["responses"]) == {"204", "401", "403", "404"}
    assert "auth.not_authenticated" in delete["responses"]["401"]["description"]
    assert "auth.reauthentication_required" in delete["responses"]["401"]["description"]
    assert "organizations.role_insufficient" in delete["responses"]["403"]["description"]
    assert "account.email_not_verified" in delete["responses"]["403"]["description"]
    assert "core.not_found" in delete["responses"]["404"]["description"]


def test_atualizacao_email_faturamento_documenta_body_e_erros_reais():
    schema = SchemaGenerator(patterns=organizacoes_urls.urlpatterns).get_schema(request=None, public=True)
    path = schema["paths"]["/organizacoes/{id}/"]
    componente = schema["components"]["schemas"]["OrganizacaoEmailFaturamento"]

    assert "email_faturamento" in componente["required"]

    for method in ("put", "patch"):
        operation = path[method]
        componente_request = "PatchedOrganizacaoEmailFaturamento" if method == "patch" else "OrganizacaoEmailFaturamento"
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{componente_request}"}
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/OrganizacaoEmailFaturamento"}
        assert set(operation["responses"]) == {"200", "400", "401", "403", "404", "422"}
        assert "auth.reauthentication_required" in operation["responses"]["401"]["description"]
        assert "organizations.role_insufficient" in operation["responses"]["403"]["description"]
        assert "account.email_not_verified" in operation["responses"]["403"]["description"]
        assert "core.not_found" in operation["responses"]["404"]["description"]
        assert "validation.required" in operation["responses"]["422"]["description"]


def test_aceite_de_convite_documenta_payload_de_resposta_e_erros_reais():
    schema = SchemaGenerator(patterns=organizacoes_urls.urlpatterns).get_schema(request=None, public=True)
    post = schema["paths"]["/convites/aceitar/"]["post"]

    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AceitarConvite"}
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AceitarConviteResponse"}
    assert set(post["responses"]) == {"200", "401", "403", "409", "422", "503"}
    componente = schema["components"]["schemas"]["AceitarConviteResponse"]
    assert componente["required"] == ["organizacao", "vinculo"]
    assert componente["properties"] == {
        "organizacao": {"$ref": "#/components/schemas/Organizacao"},
        "vinculo": {"$ref": "#/components/schemas/Vinculo"},
    }
    codigos_por_status = {
        "401": {
            "auth.not_authenticated",
            "auth.token_not_provided",
            "auth.invalid_token",
            "auth.expired_token",
            "auth.revoked_token",
            "auth.api_key_suspended",
            "auth.responsible_inactive",
            "auth.user_inactive",
        },
        "403": {
            "auth.permission_denied",
            "auth.insufficient_scope",
            "account.email_not_verified",
            "organizations.organization_inactive",
            "billing.organization_restricted",
        },
        "409": {
            "organizations.tenant_mismatch",
            "organizations.closure_pending",
            "billing.seat_limit_reached",
        },
        "422": {
            "validation.invalid",
            "validation.required",
            "organizations.invitation_invalid",
            "organizations.invitation_expired",
            "organizations.invitation_email_mismatch",
        },
        "503": {"billing.subscription_required"},
    }
    for status_code, codigos in codigos_por_status.items():
        descricao = post["responses"][status_code]["description"]
        assert all(codigo in descricao for codigo in codigos)


def test_mutacoes_de_vinculo_documentam_protecao_do_ultimo_proprietario():
    schema = SchemaGenerator(patterns=organizacoes_urls.urlpatterns).get_schema(request=None, public=True)
    path = schema["paths"]["/vinculos/{id}/"]

    for method in ("put", "patch", "delete"):
        assert "account.owner_transfer_required" in path[method]["responses"]["409"]["description"]
