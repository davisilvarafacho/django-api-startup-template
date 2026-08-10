"""Decorator + permission de step-up authentication."""

from datetime import timedelta

from django.utils import timezone

from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

import pytest
from knox.models import get_token_model
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.autenticacao.views import ReauthenticateView
from apps.api.core.errors import APIError
from tests.support.usuarios import criar_usuario

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    """Limpa o estado que `ThreadLocalMiddleware` deixa entre requests.

    `ThreadLocalMiddleware` nunca limpa `request` sozinho: sem isso, o
    usuário desta request vazaria como `get_current_user()` para o próximo
    teste que rodar na mesma thread.
    """
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


class MetadataFalsa:
    def __init__(self, reauthenticated_at=None):
        self.reauthenticated_at = reauthenticated_at


class TokenFalso:
    def __init__(self, token_type=TokenType.TOKEN, reauthenticated_at=None):
        self.type = token_type
        self.metadata = MetadataFalsa(reauthenticated_at)


class UsuarioFalso:
    mfa_enabled = False


class RequestFalsa:
    def __init__(self, auth, method="POST"):
        self.auth = auth
        self.method = method
        self.user = UsuarioFalso()


class ViewSemDecorator:
    action = "list"

    def list(self, request):
        raise NotImplementedError


@require_recent_auth()
class ViewComDecoratorNaClasse:
    action = "create"

    def create(self, request):
        raise NotImplementedError


class ViewComDecoratorNaAction:
    action = "create"

    @require_recent_auth(max_age=60)
    def create(self, request):
        raise NotImplementedError

    @require_recent_auth(max_age=3600)
    def update(self, request):
        raise NotImplementedError


def test_sem_decorator_e_no_op():
    permission = RecentAuthenticationPermission()
    request = RequestFalsa(TokenFalso())

    assert permission.has_permission(request, ViewSemDecorator())


def test_decorator_na_classe_aceita_sessao_recente():
    permission = RecentAuthenticationPermission()
    token = TokenFalso(reauthenticated_at=timezone.now())
    request = RequestFalsa(token)

    assert permission.has_permission(request, ViewComDecoratorNaClasse())


def test_decorator_na_classe_recusa_sessao_sem_reautenticacao():
    permission = RecentAuthenticationPermission()
    token = TokenFalso(reauthenticated_at=None)
    request = RequestFalsa(token)

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComDecoratorNaClasse())

    assert exc.value.code == AuthErrorCode.REAUTHENTICATION_REQUIRED.value


def test_decorator_recusa_sessao_reautenticada_ha_muito_tempo():
    permission = RecentAuthenticationPermission()
    token = TokenFalso(reauthenticated_at=timezone.now() - timedelta(seconds=400))
    request = RequestFalsa(token)

    with pytest.raises(APIError):
        permission.has_permission(request, ViewComDecoratorNaClasse())


def test_decorator_na_action_usa_o_max_age_declarado():
    permission = RecentAuthenticationPermission()
    view = ViewComDecoratorNaAction()

    # 90s: satisfaz `update` (max_age=3600) mas não `create` (max_age=60).
    token = TokenFalso(reauthenticated_at=timezone.now() - timedelta(seconds=90))
    request = RequestFalsa(token)

    with pytest.raises(APIError):
        permission.has_permission(request, view)

    view.action = "update"
    assert permission.has_permission(request, view)


def test_api_key_nunca_satisfaz_reautenticacao():
    permission = RecentAuthenticationPermission()
    token = TokenFalso(token_type=TokenType.API_KEY, reauthenticated_at=timezone.now())
    request = RequestFalsa(token)

    with pytest.raises(APIError) as exc:
        permission.has_permission(request, ViewComDecoratorNaClasse())

    assert exc.value.code == AuthErrorCode.REAUTHENTICATION_REQUIRED.value


def test_require_mfa_forcado_sem_mfa_configurado_nunca_e_satisfeito():
    @require_recent_auth(require_mfa=True)
    class ViewComMfaForcado:
        action = "create"

        def create(self, request):
            raise NotImplementedError

    permission = RecentAuthenticationPermission()
    token = TokenFalso(reauthenticated_at=timezone.now())
    request = RequestFalsa(token)

    with pytest.raises(APIError):
        permission.has_permission(request, ViewComMfaForcado())


# ---- HTTP: POST /auth/reauthenticate/ ----


@pytest.fixture
def usuario(db):
    return criar_usuario(email="reauth@example.com", password="senha-forte-123")


def _client_com_sessao(usuario):
    instance, token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _client_com_api_key(usuario, organizacao=None):
    from apps.organizacoes.models import Organizacao, Papel, Vinculo

    organizacao = organizacao or Organizacao.objects.create(nome="Org", slug="org-reauth")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.mark.django_db
def test_reauthenticate_com_senha_correta_atualiza_a_sessao(usuario):
    client = _client_com_sessao(usuario)

    response = client.post("/auth/reauthenticate/", {"password": "senha-forte-123"}, format="json")

    assert response.status_code == 204
    token = AuthToken.objects.get(responsavel=usuario)
    assert token.metadata.reauthenticated_at is not None


def test_reauthenticate_tem_throttle_especifico(settings):
    assert ReauthenticateView.throttle_scope == "auth_reauthenticate"
    assert ReauthenticateView.throttle_classes == [
        UserRateThrottle,
        ScopedRateThrottle,
    ]
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth_reauthenticate"] == "5/min"


@pytest.mark.django_db
def test_reauthenticate_com_senha_errada_e_recusado(usuario):
    client = _client_com_sessao(usuario)

    response = client.post("/auth/reauthenticate/", {"password": "senha-errada"}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.invalid_credentials"


@pytest.mark.django_db
def test_reauthenticate_recusa_api_key(usuario):
    client = _client_com_api_key(usuario)

    response = client.post("/auth/reauthenticate/", {"password": "senha-forte-123"}, format="json")

    assert response.status_code == 403
