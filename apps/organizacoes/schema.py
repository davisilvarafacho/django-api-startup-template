"""Contrato OpenAPI explícito do ciclo de organizações."""

from drf_spectacular.utils import extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode, ValidationErrorCode
from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.serializers import (
    AceitarConviteResponseSerializer,
    AceitarConviteSerializer,
    EncerramentoAgendadoResponseSerializer,
    OrganizacaoEmailFaturamentoSerializer,
    VinculoSerializer,
)
from apps.usuarios.errors import AccountErrorCode

_AUTH_ERRORS = document_error_codes(
    AuthErrorCode.NOT_AUTHENTICATED,
    AuthErrorCode.REAUTHENTICATION_REQUIRED,
)
_AUTHORIZATION_ERRORS = document_error_codes(
    AuthErrorCode.PERMISSION_DENIED,
    AccountErrorCode.EMAIL_NOT_VERIFIED,
    OrganizationErrorCode.ROLE_INSUFFICIENT,
)
_NOT_FOUND = document_error_codes(CoreErrorCode.NOT_FOUND)

document_organization_closure_post = extend_schema(
    methods=["POST"],
    request=None,
    responses={
        202: EncerramentoAgendadoResponseSerializer,
        204: None,
        401: _AUTH_ERRORS,
        403: _AUTHORIZATION_ERRORS,
        404: _NOT_FOUND,
    },
)

document_organization_closure_delete = extend_schema(
    methods=["DELETE"],
    request=None,
    responses={
        204: None,
        401: _AUTH_ERRORS,
        403: _AUTHORIZATION_ERRORS,
        404: _NOT_FOUND,
    },
)

document_organization_billing_email_update = extend_schema(
    request=OrganizacaoEmailFaturamentoSerializer,
    responses={
        200: OrganizacaoEmailFaturamentoSerializer,
        400: document_error_codes(ValidationErrorCode.MALFORMED),
        401: _AUTH_ERRORS,
        403: _AUTHORIZATION_ERRORS,
        404: _NOT_FOUND,
        422: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.REQUIRED),
    },
)

document_invitation_accept = extend_schema(
    request=AceitarConviteSerializer,
    responses={
        200: AceitarConviteResponseSerializer,
        403: document_error_codes(AccountErrorCode.EMAIL_NOT_VERIFIED),
    },
)

document_membership_update = extend_schema(
    responses={
        200: VinculoSerializer,
        409: document_error_codes(AccountErrorCode.OWNER_TRANSFER_REQUIRED),
    },
)

document_membership_delete = extend_schema(
    responses={
        204: None,
        409: document_error_codes(AccountErrorCode.OWNER_TRANSFER_REQUIRED),
    },
)
