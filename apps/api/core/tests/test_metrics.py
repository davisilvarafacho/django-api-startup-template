"""Testes do controle de acesso ao /metrics (não tocam o banco)."""

from django.test import RequestFactory

import pytest

from apps.api.core import metrics as modulo
from common.permission_cache import metrics as permission_cache_metrics


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


def test_exposicao_inclui_metricas_do_cache_de_autorizacao(factory):
    permission_cache_metrics.record_operation("django", "hit")
    permission_cache_metrics.record_invalidation("tenant", "success")
    permission_cache_metrics.record_fallback("guardian", "read_error")
    with permission_cache_metrics.time_resolve("rules", "database"):
        pass

    response = modulo.metrics_view(factory.get("/metrics", REMOTE_ADDR="127.0.0.1"))
    exposition = response.content.decode()

    assert "authorization_cache_operations_total" in exposition
    assert "authorization_cache_invalidations_total" in exposition
    assert "authorization_cache_fallback_total" in exposition
    assert "authorization_cache_resolve_seconds" in exposition
