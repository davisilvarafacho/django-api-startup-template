"""Contrato OpenAPI explícito das operações comerciais."""

from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode, ValidationErrorCode
from apps.api.core.schema import document_error_codes
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.serializers import (
    AceitarPropostaRequestSerializer,
    AceitarPropostaResponseSerializer,
    AlteracaoAssinaturaResponseSerializer,
    AssinaturaResponseSerializer,
    CancelamentoAssinaturaRequestSerializer,
    CancelamentoAssinaturaResponseSerializer,
    PlanoCatalogoSerializer,
    RecursosAssinaturaResponseSerializer,
    SolicitarAlteracaoRequestSerializer,
    UtilizacaoSeatsResponseSerializer,
)
from apps.organizacoes.errors import OrganizationErrorCode
from apps.usuarios.errors import AccountErrorCode

AUTH_401 = document_error_codes(
    AuthErrorCode.TOKEN_NOT_PROVIDED,
    AuthErrorCode.INVALID_TOKEN,
    AuthErrorCode.EXPIRED_TOKEN,
    AuthErrorCode.REVOKED_TOKEN,
    AuthErrorCode.API_KEY_SUSPENDED,
    AuthErrorCode.RESPONSIBLE_INACTIVE,
)
AUTH_MUTATION_401 = document_error_codes(
    AuthErrorCode.TOKEN_NOT_PROVIDED,
    AuthErrorCode.INVALID_TOKEN,
    AuthErrorCode.EXPIRED_TOKEN,
    AuthErrorCode.REVOKED_TOKEN,
    AuthErrorCode.API_KEY_SUSPENDED,
    AuthErrorCode.RESPONSIBLE_INACTIVE,
    AuthErrorCode.REAUTHENTICATION_REQUIRED,
)
TENANT_403 = document_error_codes(
    AuthErrorCode.PERMISSION_DENIED,
    OrganizationErrorCode.MEMBERSHIP_REQUIRED,
    OrganizationErrorCode.MEMBERSHIP_INACTIVE,
    OrganizationErrorCode.ORGANIZATION_INACTIVE,
    OrganizationErrorCode.ROLE_INSUFFICIENT,
    BillingErrorCode.ORGANIZATION_RESTRICTED,
)
TENANT_409 = document_error_codes(OrganizationErrorCode.TENANT_MISMATCH)
TENANT_422 = document_error_codes(OrganizationErrorCode.HEADER_REQUIRED)
SUBSCRIPTION_503 = document_error_codes(BillingErrorCode.SUBSCRIPTION_REQUIRED)
TENANT_MUTATION_403 = document_error_codes(
    AuthErrorCode.PERMISSION_DENIED,
    AccountErrorCode.EMAIL_NOT_VERIFIED,
    OrganizationErrorCode.MEMBERSHIP_REQUIRED,
    OrganizationErrorCode.MEMBERSHIP_INACTIVE,
    OrganizationErrorCode.ORGANIZATION_INACTIVE,
    OrganizationErrorCode.ROLE_INSUFFICIENT,
    BillingErrorCode.ORGANIZATION_RESTRICTED,
)


def _consultation_schema(response):
    return extend_schema(
        responses={
            200: response,
            401: AUTH_401,
            403: TENANT_403,
            409: TENANT_409,
            422: TENANT_422,
            503: SUBSCRIPTION_503,
        }
    )


document_subscription_get = _consultation_schema(AssinaturaResponseSerializer)
document_subscription_resources_get = _consultation_schema(RecursosAssinaturaResponseSerializer)
document_subscription_usage_get = _consultation_schema(UtilizacaoSeatsResponseSerializer)
document_plan_catalog_get = extend_schema(
    responses={
        200: PlanoCatalogoSerializer(many=True),
        401: AUTH_401,
        403: document_error_codes(AuthErrorCode.PERMISSION_DENIED),
    }
)

document_contractual_proposal_accept = extend_schema(
    request=AceitarPropostaRequestSerializer,
    responses={
        200: AceitarPropostaResponseSerializer,
        400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
        401: AUTH_MUTATION_401,
        403: TENANT_MUTATION_403,
        404: document_error_codes(CoreErrorCode.NOT_FOUND),
        409: document_error_codes(BillingErrorCode.PROPOSAL_INVALID, OrganizationErrorCode.TENANT_MISMATCH),
        422: TENANT_422,
        503: SUBSCRIPTION_503,
    },
)

MUTATION_ERRORS = {
    400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
    401: AUTH_MUTATION_401,
    403: TENANT_MUTATION_403,
    409: document_error_codes(BillingErrorCode.SUBSCRIPTION_CONFLICT, OrganizationErrorCode.TENANT_MISMATCH),
    422: TENANT_422,
    503: SUBSCRIPTION_503,
}

document_subscription_change = extend_schema(
    request=SolicitarAlteracaoRequestSerializer,
    responses={201: AlteracaoAssinaturaResponseSerializer, **MUTATION_ERRORS},
)
document_subscription_cancel = extend_schema(
    request=CancelamentoAssinaturaRequestSerializer,
    responses={
        200: CancelamentoAssinaturaResponseSerializer,
        202: CancelamentoAssinaturaResponseSerializer,
        **MUTATION_ERRORS,
    },
)
document_subscription_cancel_delete = extend_schema(
    request=CancelamentoAssinaturaRequestSerializer,
    responses={204: OpenApiResponse(description="Cancelamento agendado removido."), **MUTATION_ERRORS},
)
