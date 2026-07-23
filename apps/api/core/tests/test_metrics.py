"""Testes do controle de acesso ao /metrics (não tocam o banco)."""
from django.test import RequestFactory

import pytest

from apps.api.core import metrics as modulo


@pytest.fixture
def factory():
    return RequestFactory()


def test_libera_ip_da_rede_interna(factory):
    request = factory.get("/metrics", REMOTE_ADDR="127.0.0.1")

    assert modulo._autorizado(request) is True


def test_bloqueia_ip_externo(factory):
    request = factory.get("/metrics", REMOTE_ADDR="203.0.113.10")

    assert modulo._autorizado(request) is False


def test_x_forwarded_for_nao_concede_acesso(factory):
    # O header é escrito pelo cliente: se valesse para a checagem, bastaria
    # mandar `X-Forwarded-For: 127.0.0.1` para expor as métricas na internet.
    request = factory.get(
        "/metrics",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_FORWARDED_FOR="127.0.0.1",
    )

    assert modulo._autorizado(request) is False


def test_token_libera_ip_externo(factory, monkeypatch):
    monkeypatch.setattr(modulo, "TOKEN_METRICS", "segredo")

    request = factory.get(
        "/metrics",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_METRICS_TOKEN="segredo",
    )

    assert modulo._autorizado(request) is True


def test_token_errado_nao_libera(factory, monkeypatch):
    monkeypatch.setattr(modulo, "TOKEN_METRICS", "segredo")

    request = factory.get(
        "/metrics",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_METRICS_TOKEN="chute",
    )

    assert modulo._autorizado(request) is False


def test_view_responde_403_para_externo(factory):
    response = modulo.metrics_view(factory.get("/metrics", REMOTE_ADDR="203.0.113.10"))

    assert response.status_code == 403


@pytest.mark.parametrize("ip", ["10.1.2.3", "172.20.0.5", "192.168.1.7", "::1"])
def test_faixas_privadas_liberadas(factory, ip):
    # Cobre o cenário de container falando com container (redes do Docker/K8s).
    assert modulo._ip_interno(ip) is True


@pytest.mark.parametrize("ip", ["", "nao-e-ip", "8.8.8.8"])
def test_valores_invalidos_ou_publicos_bloqueados(ip):
    assert modulo._ip_interno(ip) is False
