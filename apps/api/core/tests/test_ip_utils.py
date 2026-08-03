"""Testes dos utilitários de IP (não tocam o banco)."""
import pytest

from apps.api.core.ip_utils import ip_in_networks


@pytest.mark.parametrize("ip", ["10.1.2.3", "172.20.0.5", "192.168.1.7", "::1", "127.0.0.1"])
def test_faixas_privadas_liberadas(ip):
    redes = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

    assert ip_in_networks(ip, redes) is True


@pytest.mark.parametrize("ip", ["", "nao-e-ip", "8.8.8.8"])
def test_valores_invalidos_ou_publicos_bloqueados(ip):
    redes = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

    assert ip_in_networks(ip, redes) is False


def test_endereco_exato():
    assert ip_in_networks("127.0.0.1", ["127.0.0.1"]) is True
    assert ip_in_networks("10.0.0.1", ["127.0.0.1"]) is False
