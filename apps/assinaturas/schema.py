"""Contrato OpenAPI explícito das propostas comerciais."""

from drf_spectacular.utils import extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode, ValidationErrorCode
from apps.api.core.schema import document_error_codes
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.serializers import AceitarPropostaRequestSerializer, AceitarPropostaResponseSerializer
from apps.organizacoes.errors import OrganizationErrorCode

document_proposal_accept = extend_schema(
    request=AceitarPropostaRequestSerializer,
    responses={
        200: AceitarPropostaResponseSerializer,
        400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
        401: document_error_codes(
            AuthErrorCode.TOKEN_NOT_PROVIDED,
            AuthErrorCode.INVALID_TOKEN,
            AuthErrorCode.EXPIRED_TOKEN,
            AuthErrorCode.REVOKED_TOKEN,
            AuthErrorCode.API_KEY_SUSPENDED,
            AuthErrorCode.RESPONSIBLE_INACTIVE,
            AuthErrorCode.REAUTHENTICATION_REQUIRED,
        ),
        403: document_error_codes(
            AuthErrorCode.PERMISSION_DENIED,
            OrganizationErrorCode.MEMBERSHIP_REQUIRED,
            OrganizationErrorCode.MEMBERSHIP_INACTIVE,
            OrganizationErrorCode.ORGANIZATION_INACTIVE,
            OrganizationErrorCode.ROLE_INSUFFICIENT,
        ),
        404: document_error_codes(CoreErrorCode.NOT_FOUND),
        409: document_error_codes(BillingErrorCode.PROPOSAL_INVALID, OrganizationErrorCode.TENANT_MISMATCH),
        422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED),
    },
)
