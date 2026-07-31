"""Contratos do step-up de reautenticação recente."""

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from rest_framework.test import APIRequestFactory

import pytest

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.permissions import RecentAuthenticationPermission
from apps.api.autenticacao.recent_auth import require_recent_auth


def test_decorator_marca_idade_maxima():
    @require_recent_auth(max_age=120)
    def action():
        return None

    assert action._recent_auth_required == {"max_age": 120, "require_mfa": None}


def test_permissao_aceita_sessao_reautenticada_recente():
    request = APIRequestFactory().post("/qualquer/")
    request.auth = SimpleNamespace(
        type=TokenType.TOKEN,
        metadata=SimpleNamespace(reauthenticated_at=timezone.now() - timedelta(seconds=30)),
    )

    @require_recent_auth(max_age=60)
    def post():
        return None

    view = SimpleNamespace(action="post", post=post)

    assert RecentAuthenticationPermission().has_permission(request, view) is True


def test_permissao_recusa_sessao_com_reautenticacao_expirada():
    request = APIRequestFactory().post("/qualquer/")
    request.auth = SimpleNamespace(
        type=TokenType.TOKEN,
        metadata=SimpleNamespace(reauthenticated_at=timezone.now() - timedelta(seconds=61)),
    )

    @require_recent_auth(max_age=60)
    def post():
        return None

    view = SimpleNamespace(action="post", post=post)

    assert RecentAuthenticationPermission().has_permission(request, view) is False


def test_permissao_recusa_api_key_mesmo_com_marcador_recente():
    request = APIRequestFactory().post("/qualquer/")
    request.auth = SimpleNamespace(
        type=TokenType.API_KEY,
        metadata=SimpleNamespace(reauthenticated_at=timezone.now()),
    )

    @require_recent_auth()
    def post():
        return None

    view = SimpleNamespace(action="post", post=post)

    assert RecentAuthenticationPermission().has_permission(request, view) is False


@pytest.mark.parametrize("target", ["handler", "class"])
def test_permissao_resolve_marcador_do_handler_ou_da_classe(target):
    request = APIRequestFactory().post("/qualquer/")
    request.auth = SimpleNamespace(
        type=TokenType.TOKEN,
        metadata=SimpleNamespace(reauthenticated_at=timezone.now()),
    )

    @require_recent_auth()
    def handler():
        return None

    if target == "handler":
        view = SimpleNamespace(handler=handler)
    else:
        view = SimpleNamespace()
        view._recent_auth_required = handler._recent_auth_required

    assert RecentAuthenticationPermission().has_permission(request, view) is True


@pytest.mark.parametrize("reauthenticated_at", [None, timezone.now() - timedelta(minutes=6)])
def test_permissao_recusa_sessao_sem_marcador_valido(reauthenticated_at):
    request = APIRequestFactory().post("/qualquer/")
    request.auth = SimpleNamespace(
        type=TokenType.TOKEN,
        metadata=SimpleNamespace(reauthenticated_at=reauthenticated_at),
    )

    @require_recent_auth()
    def post():
        return None

    view = SimpleNamespace(action="post", post=post)

    assert RecentAuthenticationPermission().has_permission(request, view) is False
