from rest_framework.exceptions import AuthenticationFailed

import pytest

from apps.api.autenticacao.authentications import TypedTokenAuthentication
from apps.api.autenticacao.models import AuthToken, TokenType


class UsuarioFalso:
    is_active = True


class MetadadosFalsos:
    def __init__(self, token_type):
        self.type = token_type


class TokenFalso:
    def __init__(self, token_type=None):
        self.user = UsuarioFalso()
        if token_type is not None:
            self.type = token_type


def test_auth_token_tem_tipo_com_valores_publicos():
    field = AuthToken._meta.get_field("type")

    assert field.default == TokenType.TOKEN
    assert TokenType.TOKEN == 1
    assert TokenType.RESET_PASSWORD == 2
    assert TokenType.PRE_AUTH == 3
    assert TokenType.API_KEY == 999


def test_autenticacao_aceita_token_de_sessao_e_api_key():
    auth = TypedTokenAuthentication()

    for token_type in [TokenType.TOKEN, TokenType.API_KEY]:
        token = TokenFalso(token_type)

        assert auth.validate_user(token) == (token.user, token)


@pytest.mark.parametrize("token_type", [TokenType.RESET_PASSWORD, TokenType.PRE_AUTH])
def test_autenticacao_recusa_tokens_efemeros(token_type):
    auth = TypedTokenAuthentication()
    token = TokenFalso(token_type)

    with pytest.raises(AuthenticationFailed, match="não permite acesso"):
        auth.validate_user(token)


def test_autenticacao_trata_token_sem_tipo_como_sessao():
    auth = TypedTokenAuthentication()
    token = TokenFalso()

    assert auth.validate_user(token) == (token.user, token)
