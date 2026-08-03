"""Testes da permission `IsInternalIP` (não tocam o banco)."""
from django.test import RequestFactory

import pytest

from apps.api.core.errors import APIError, CoreErrorCode
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

    with pytest.raises(APIError) as exc_info:
        permission.has_permission(request, view=None)

    error = exc_info.value
    assert error.status_code == 403
    assert error.code == CoreErrorCode.INTERNAL_IP_REQUIRED.value


def test_x_forwarded_for_nao_concede_acesso(factory, permission, settings):
    settings.INTERNAL_IPS = ["127.0.0.1"]
    request = factory.get(
        "/",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_FORWARDED_FOR="127.0.0.1",
    )

    with pytest.raises(APIError) as exc_info:
        permission.has_permission(request, view=None)

    assert exc_info.value.status_code == 403
