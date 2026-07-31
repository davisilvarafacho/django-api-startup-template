"""Contratos do step-up de reautenticação recente."""

from datetime import timedelta
from types import SimpleNamespace

from django.test import override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory

import pytest

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.permissions import RecentAuthenticationPermission
from apps.api.autenticacao.recent_auth import require_recent_auth
from apps.api.autenticacao.services import issue_token


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


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="apps.api.autenticacao.tests.recent_auth_urls")
def test_endpoint_marcado_exige_reautenticacao_recente_pela_permissao_global(api_client, usuario):
    """Uma view sem ``permission_classes`` recebe o step-up do padrão global."""
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        expiry=timedelta(hours=1),
        metadata_input={},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    denied = api_client.post("/recent-auth-probe/")

    assert denied.status_code == status.HTTP_403_FORBIDDEN

    issued.instance.metadata.reauthenticated_at = timezone.now()
    issued.instance.metadata.save(update_fields=["reauthenticated_at"])

    accepted = api_client.post("/recent-auth-probe/")

    assert accepted.status_code == status.HTTP_200_OK
    assert accepted.data == {"detail": "step-up aceito"}
