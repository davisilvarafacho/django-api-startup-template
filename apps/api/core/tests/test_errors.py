from rest_framework.exceptions import AuthenticationFailed

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import APIError
from apps.api.core.status_handlers import custom_exception_handler


def test_api_error_expoe_codigo_e_mensagem():
    error = APIError("auth.invalid_token", status_code=401, message="Token inválido.")

    assert error.detail == {
        "code": "auth.invalid_token",
        "message": "Token inválido.",
    }


def test_api_error_nao_expoe_contexto_interno():
    error = APIError("auth.invalid_token", status_code=401, context={"digest": "secret"})

    assert "digest" not in str(error.detail)


def test_custom_exception_handler_preserva_envelope_de_api_error():
    response = custom_exception_handler(
        APIError("auth.invalid_token", status_code=401, message="Token inválido."),
        {},
    )

    assert response.status_code == 401
    assert response.data == {
        "code": "auth.invalid_token",
        "message": "Token inválido.",
    }


def test_custom_exception_handler_normaliza_excecao_drf():
    response = custom_exception_handler(AuthenticationFailed("Credenciais inválidas."), {})

    assert response.status_code == 401
    assert response.data == {
        "code": "authentication_failed",
        "message": "Credenciais inválidas.",
    }


def test_auth_error_codes_usam_valores_literais_estaveis():
    assert {code.name: code.value for code in AuthErrorCode} == {
        "INVALID_CREDENTIALS": "auth.invalid_credentials",
        "INVALID_TOKEN": "auth.invalid_token",
        "EXPIRED_TOKEN": "auth.expired_token",
        "REVOKED_TOKEN": "auth.revoked_token",
        "REAUTHENTICATION_REQUIRED": "auth.reauthentication_required",
        "INVALID_CHALLENGE": "auth.invalid_challenge",
        "INVALID_OTP": "auth.invalid_otp",
        "OTP_COOLDOWN": "auth.otp_cooldown",
        "TOO_MANY_ATTEMPTS": "auth.too_many_attempts",
        "PWNED_PASSWORD": "auth.pwned_password",
        "DELIVERY_UNAVAILABLE": "auth.delivery_unavailable",
    }
