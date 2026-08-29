"""Documentação OpenAPI de autenticação: contrato de erro por endpoint.

CRUD simples de sessões/API keys já é inferido pelo `AutoSchema` a partir do
serializer da view; só os endpoints com efeito colateral ou plain token na
resposta (login, criação/rotação de API key) precisam do detalhe manual
abaixo.
"""

from drf_spectacular.utils import extend_schema

from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode
from apps.usuarios.errors import AccountErrorCode

from .errors import AuthErrorCode
from .serializers import APIKeySerializer, GoogleIdentitySerializer, GoogleLoginSerializer, LoginResponseSerializer

TOKEN_ONCE_DESCRIPTION = (
    "`token` é o segredo em texto plano: só aparece nesta resposta, não é "
    "recuperável depois e nunca aparece em listagem, detalhe ou qualquer "
    "outra resposta."
)

document_login = extend_schema(
    responses={
        200: LoginResponseSerializer,
        401: document_error_codes(AuthErrorCode.INVALID_CREDENTIALS, AuthErrorCode.USER_INACTIVE),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_google_login = extend_schema(
    request=GoogleLoginSerializer,
    responses={
        200: LoginResponseSerializer,
        401: document_error_codes(AuthErrorCode.GOOGLE_TOKEN_INVALID, AuthErrorCode.USER_INACTIVE),
        409: document_error_codes(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_google_connect = extend_schema(
    request=GoogleIdentitySerializer,
    responses={
        204: None,
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.REAUTHENTICATION_REQUIRED,
            AuthErrorCode.GOOGLE_TOKEN_INVALID,
        ),
        403: document_error_codes(AccountErrorCode.EMAIL_NOT_VERIFIED),
        409: document_error_codes(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT),
    },
)

document_google_disconnect = extend_schema(
    request=None,
    responses={
        204: None,
        401: document_error_codes(AuthErrorCode.NOT_AUTHENTICATED, AuthErrorCode.REAUTHENTICATION_REQUIRED),
        409: document_error_codes(AccountErrorCode.EXTERNAL_IDENTITY_LAST_LOGIN),
    },
)

document_reauthenticate = extend_schema(
    responses={
        204: None,
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.INVALID_CREDENTIALS,
        ),
        403: document_error_codes(AuthErrorCode.PERMISSION_DENIED),
    },
)

document_api_key_create = extend_schema(
    responses={
        201: APIKeySerializer,
        401: document_error_codes(AuthErrorCode.REAUTHENTICATION_REQUIRED),
        403: document_error_codes(AuthErrorCode.SCOPE_NOT_DELEGABLE),
        409: document_error_codes(OrganizationErrorCode.CLOSURE_PENDING, OrganizationErrorCode.INACTIVE),
        422: document_error_codes(OrganizationErrorCode.MEMBERSHIP_REQUIRED),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_api_key_rotate = extend_schema(
    responses={
        201: APIKeySerializer,
        401: document_error_codes(AuthErrorCode.REAUTHENTICATION_REQUIRED),
        409: document_error_codes(
            AuthErrorCode.REVOKED_TOKEN,
            OrganizationErrorCode.CLOSURE_PENDING,
            OrganizationErrorCode.INACTIVE,
        ),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_api_key_suspend = extend_schema(responses={200: APIKeySerializer})

document_api_key_resume = extend_schema(
    responses={
        200: APIKeySerializer,
        409: document_error_codes(AuthErrorCode.REVOKED_TOKEN, AuthErrorCode.RESPONSIBLE_INACTIVE),
    },
)


document_password_reset_request = extend_schema(
    responses={202: None},
    description=(
        "Responde 202 com o mesmo corpo exista ou não a conta. Isso é "
        "deliberado: qualquer diferença tornaria o endpoint um verificador de "
        "quem tem cadastro."
    ),
)

document_password_reset_confirm = extend_schema(
    responses={204: None},
    description=(
        "Consome o token do e-mail, grava a nova senha e revoga sessões, "
        "tokens efêmeros e dispositivos confiáveis. Token inválido, expirado, "
        "revogado ou já usado devolvem o mesmo 400 — a distinção seria "
        "informação de graça para quem testa tokens. Não exige MFA: quem perdeu "
        "a senha pode ter perdido o segundo fator junto."
    ),
)

document_password_change = extend_schema(
    responses={
        204: None,
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.REAUTHENTICATION_REQUIRED,
        ),
    },
    description=(
        "Exige reautenticação recente. Encerra **todas** as sessões, inclusive "
        "a que fez a troca; API keys são preservadas, porque pertencem à "
        "integração e não à sessão humana."
    ),
)
