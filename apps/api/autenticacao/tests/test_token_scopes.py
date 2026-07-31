from django.conf import settings

import pytest

from apps.api.autenticacao import permissions
from apps.api.autenticacao.models import AuthToken, TokenType
from apps.api.core.errors import APIError


class TokenFalso:
    def __init__(self, token_type, scopes=None):
        self.type = token_type
        self.scopes = scopes or []


class RequestFalsa:
    def __init__(self, method, auth):
        self.method = method
        self.auth = auth


class ViewComScopes:
    required_token_scopes = {
        "GET": ["org:read"],
        "POST": ["org:write"],
    }


def test_auth_token_tem_lista_de_scopes():
    field = AuthToken._meta.get_field("scopes")

    assert field.default is list


def test_api_key_precisa_ter_scope_da_view():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["org:read"]))

    assert permission.has_permission(request, ViewComScopes())


def test_api_key_sem_scope_da_view_e_recusada():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("POST", TokenFalso(TokenType.API_KEY, scopes=["org:read"]))

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComScopes())

    assert exc.value.code == "auth.insufficient_scope"


def test_token_de_sessao_nao_e_limitado_por_scopes():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("POST", TokenFalso(TokenType.TOKEN, scopes=[]))

    assert permission.has_permission(request, ViewComScopes())


def test_sessao_em_view_sem_scopes_nao_exige_nada():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.TOKEN, scopes=[]))

    assert permission.has_permission(request, object())


def test_api_key_em_view_sem_scope_declarado_e_recusada():
    """Scope é a única autoridade de uma API key: sem scope exigido, fail-closed."""
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["*"]))

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, object())

    assert exc.value.code == "auth.insufficient_scope"


def test_view_session_only_recusa_api_key_mesmo_com_scope():
    class ViewSessionOnly:
        session_only = True

        def get_required_token_scopes(self):
            return ["organizations:read"]

    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["organizations:read"]))

    assert not permission.has_permission(request, ViewSessionOnly())


def test_action_session_only_recusa_api_key_mesmo_com_scope():
    class ViewComActionSessionOnly:
        action = "create"
        session_only_actions = {"create"}

        def get_required_token_scopes(self):
            return ["organizations:create"]

    permission = permissions.TokenScopePermission()
    request = RequestFalsa(
        "POST",
        TokenFalso(TokenType.API_KEY, scopes=["organizations:create"]),
    )

    assert not permission.has_permission(request, ViewComActionSessionOnly())


def test_token_scope_permission_esta_nas_permissoes_globais():
    permission_classes = settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]

    assert "apps.api.autenticacao.permissions.TokenScopePermission" in permission_classes


class ViewComGetRequiredTokenScopes:
    def get_required_token_scopes(self):
        return ["organizations:read"]


def test_api_key_com_scope_de_recurso_wildcard_satisfaz_requisito():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["organizations:*"]))

    assert permission.has_permission(request, ViewComGetRequiredTokenScopes())


def test_api_key_com_scope_global_wildcard_satisfaz_qualquer_requisito():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["*"]))

    assert permission.has_permission(request, ViewComGetRequiredTokenScopes())


def test_get_required_token_scopes_da_view_tem_prioridade_sobre_o_atributo_legado():
    class ViewComAmbos(ViewComGetRequiredTokenScopes):
        required_token_scopes = {"GET": ["org:read"]}

    permission = permissions.TokenScopePermission()
    request = RequestFalsa("GET", TokenFalso(TokenType.API_KEY, scopes=["org:read"]))

    # `organizations:read` (do método), não `org:read` (do atributo legado).
    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComAmbos())

    assert exc.value.code == "auth.insufficient_scope"


def test_api_key_precisa_satisfazer_todos_os_scopes_exigidos():
    class ViewComDoisScopes:
        def get_required_token_scopes(self):
            return ["organizations:read", "teams:read"]

    permission = permissions.TokenScopePermission()
    request = RequestFalsa(
        "GET",
        TokenFalso(TokenType.API_KEY, scopes=["organizations:read"]),
    )

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComDoisScopes())

    assert exc.value.code == "auth.insufficient_scope"


def test_api_key_com_scope_persistido_invalido_falha_fechado():
    permission = permissions.TokenScopePermission()
    request = RequestFalsa(
        "GET",
        TokenFalso(TokenType.API_KEY, scopes=["scope-invalido"]),
    )

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComGetRequiredTokenScopes())

    assert exc.value.code == "auth.insufficient_scope"
