from django.conf import settings

from apps.api.autenticacao import permissions
from apps.api.autenticacao.models import TokenMetaData, TokenType


class MetadadosFalsos:
    def __init__(self, token_type, scopes=None):
        self.type = token_type
        self.scopes = scopes or []


class TokenFalso:
    def __init__(self, token_type, scopes=None):
        self.metadata = MetadadosFalsos(token_type, scopes)


class RequestFalsa:
    def __init__(self, method, auth):
        self.method = method
        self.auth = auth


class ViewComScopes:
    required_token_scopes = {
        "GET": ["org:read"],
        "POST": ["org:write"],
    }


def test_token_metadata_tem_lista_de_scopes():
    field = TokenMetaData._meta.get_field("scopes")

    assert field.default is list


def test_api_key_precisa_ter_scope_da_view():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["org:read"]))

    assert permission.has_permission(request, ViewComScopes())


def test_api_key_sem_scope_da_view_e_recusada():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("POST", TokenFalso(TokenType.API_KEY, scopes=["org:read"]))

    assert not permission.has_permission(request, ViewComScopes())


def test_token_de_sessao_nao_e_limitado_por_scopes():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("POST", TokenFalso(TokenType.TOKEN, scopes=[]))

    assert permission.has_permission(request, ViewComScopes())


def test_view_sem_scopes_nao_exige_nada():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=[]))

    assert permission.has_permission(request, object())


def test_token_scope_permission_esta_nas_permissoes_globais():
    permission_classes = settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]

    assert "apps.api.autenticacao.permissions.TokenScopePermission" in permission_classes
