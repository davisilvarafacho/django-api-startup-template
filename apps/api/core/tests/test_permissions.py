"""Testes da permission `IsInternalIP` (não tocam o banco)."""

from django.test import RequestFactory

from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

import pytest

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.permissions import IsInternalIP


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture
def permission():
    return IsInternalIP()


def test_liberado_para_ip_interno(factory, permission, settings):
    settings.INTERNAL_IPS = ["127.0.0.1"]
    request = factory.get("/", REMOTE_ADDR="127.0.0.1")

    assert permission.has_permission(request, view=None) is True


def test_liberado_para_rede_cidr(factory, permission, settings):
    settings.INTERNAL_IPS = ["10.0.0.0/8"]
    request = factory.get("/", REMOTE_ADDR="10.1.2.3")

    assert permission.has_permission(request, view=None) is True


def test_bloqueia_ip_externo(factory, permission, settings):
    settings.INTERNAL_IPS = ["127.0.0.1"]
    request = factory.get("/", REMOTE_ADDR="203.0.113.10")

    assert permission.has_permission(request, view=None) is False


def test_x_forwarded_for_nao_concede_acesso(factory, permission, settings):
    settings.INTERNAL_IPS = ["127.0.0.1"]
    request = factory.get(
        "/",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_FORWARDED_FOR="127.0.0.1",
    )

    assert permission.has_permission(request, view=None) is False


class RotaInterna(APIView):
    authentication_classes = []
    permission_classes = [IsInternalIP]

    def get(self, request):
        return Response({"ok": True})


def test_negativa_nao_revela_a_regra_de_rede(settings):
    """O corpo do 403 não pode distinguir `IsInternalIP` das demais permissões.

    Um código ou mensagem próprios entregariam a quem chamou que existe uma rede
    interna com acesso privilegiado a esta rota.
    """
    settings.INTERNAL_IPS = ["127.0.0.1"]
    request = APIRequestFactory().get("/interno/", REMOTE_ADDR="203.0.113.10")

    response = RotaInterna.as_view()(request)
    response.render()

    assert response.status_code == 403
    assert [erro["code"] for erro in response.data["errors"]] == [AuthErrorCode.PERMISSION_DENIED.value]
    corpo = response.content.decode().lower()
    assert "rede interna" not in corpo
    assert "ip" not in corpo
