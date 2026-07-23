"""Testes dos health checks (não tocam o banco: as dependências são mockadas)."""
import json

from django.test import RequestFactory

import pytest

from apps.api.core import health_check as modulo


@pytest.fixture
def request_get():
    return RequestFactory().get("/health/ready/")


@pytest.fixture
def dependencias_ok(monkeypatch):
    """Substitui todas as checagens por implementações que passam."""
    for nome in ["_checar_banco", "_checar_cache", "_checar_broker", "_checar_storage"]:
        monkeypatch.setattr(modulo, nome, lambda: None)


def test_liveness_nao_toca_dependencias(monkeypatch):
    # Se o liveness tocasse o banco, uma queda momentânea do Postgres faria o
    # orquestrador matar todos os containers.
    def explodir():
        raise AssertionError("o liveness não pode consultar o banco")

    monkeypatch.setattr(modulo, "_checar_banco", explodir)

    response = modulo.health_check(RequestFactory().get("/health/"))

    assert response.status_code == 200


def test_readiness_responde_200_com_tudo_no_ar(request_get, dependencias_ok):
    response = modulo.readiness_check(request_get)

    assert response.status_code == 200

    corpo = json.loads(response.content)
    assert corpo["ok"] is True
    assert set(corpo["checks"]) == {"banco", "cache", "broker", "storage"}


def test_readiness_responde_503_quando_uma_dependencia_cai(request_get, dependencias_ok, monkeypatch):
    def cache_fora():
        raise ConnectionError("Redis inacessível")

    monkeypatch.setattr(modulo, "_checar_cache", cache_fora)

    response = modulo.readiness_check(request_get)

    assert response.status_code == 503

    corpo = json.loads(response.content)
    assert corpo["ok"] is False
    assert corpo["checks"]["cache"]["ok"] is False
    assert "Redis inacessível" in corpo["checks"]["cache"]["erro"]
    # As demais continuam sendo reportadas: o valor do endpoint é dizer QUAL caiu.
    assert corpo["checks"]["banco"]["ok"] is True


def test_readiness_reporta_duracao_de_cada_checagem(request_get, dependencias_ok):
    response = modulo.readiness_check(request_get)

    corpo = json.loads(response.content)
    for resultado in corpo["checks"].values():
        assert isinstance(resultado["duracao_ms"], (int, float))
