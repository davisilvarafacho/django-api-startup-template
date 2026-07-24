from rest_framework.exceptions import AuthenticationFailed

import pytest

from apps.api.autenticacao.authentications import TypedTokenAuthentication
from apps.api.autenticacao.models import TokenMetaData, TokenType


class UsuarioFalso:
    is_active = True


class MetadadosFalsos:
    def __init__(self, token_type):
        self.type = token_type


class TokenFalso:
    def __init__(self, token_type=None):
        self.user = UsuarioFalso()
        if token_type is not None:
            self.metadata = MetadadosFalsos(token_type)


def test_token_metadata_tem_tipo_com_valores_publicos():
    field = TokenMetaData._meta.get_field("type")

    assert field.default == TokenType.TOKEN
    assert TokenType.TOKEN == 1
    assert TokenType.RESET_PASSWORD == 2
    assert TokenType.API_KEY == 999


def test_autenticacao_aceita_token_de_sessao_e_api_key():
    auth = TypedTokenAuthentication()

    for token_type in [TokenType.TOKEN, TokenType.API_KEY]:
        token = TokenFalso(token_type)

        assert auth.validate_user(token) == (token.user, token)


def test_autenticacao_recusa_token_de_reset_de_senha():
    auth = TypedTokenAuthentication()
    token = TokenFalso(TokenType.RESET_PASSWORD)

    with pytest.raises(AuthenticationFailed, match="não permite acesso"):
        auth.validate_user(token)


def test_autenticacao_trata_token_sem_metadata_como_sessao():
    auth = TypedTokenAuthentication()
    token = TokenFalso()

    assert auth.validate_user(token) == (token.user, token)
