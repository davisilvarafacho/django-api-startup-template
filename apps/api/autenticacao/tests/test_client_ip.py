"""Testes da resolução de IP do cliente."""
from apps.api.autenticacao.utils import get_client_ip


class RequestFalsa:
    def __init__(self, **meta):
        self.META = meta


def test_usa_o_forwarded_for_quando_esta_atras_do_proxy(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="172.18.0.5",
    )

    assert get_client_ip(request) == "203.0.113.10"


def test_ignora_o_forwarded_for_quando_nao_esta_atras_do_proxy(settings):
    settings.BEHIND_PROXY = False
    request = RequestFalsa(
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="172.18.0.5",
    )

    assert get_client_ip(request) == "172.18.0.5"


def test_cai_no_remote_addr_quando_nao_ha_forwarded_for(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(REMOTE_ADDR="172.18.0.5")

    assert get_client_ip(request) == "172.18.0.5"


def test_descarta_espacos_e_usa_o_primeiro_elemento_da_lista(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(HTTP_X_FORWARDED_FOR=" 203.0.113.10 , 172.18.0.5 ")

    assert get_client_ip(request) == "203.0.113.10"


def test_devolve_none_quando_nao_ha_origem_identificavel(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa()

    assert get_client_ip(request) is None
