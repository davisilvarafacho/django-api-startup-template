"""Utilitários de endereço IP para checagens de acesso por rede."""

import ipaddress
import logging

from django.http import HttpRequest

logger = logging.getLogger(__name__)


def peer_ip(request: HttpRequest) -> str:
    """Devolve o endereço do peer da conexão.

    Deliberadamente **não** usa `X-Forwarded-For`: o header é escrito pelo
    cliente e qualquer um poderia mandar `X-Forwarded-For: 127.0.0.1` para
    passar por checagens de rede interna. Aqui só vale quem realmente abriu a
    conexão. Quando a API está atrás de um proxy confiável que reescreve esse
    header (ver `docs/how-to/proxy-nginx.md`), `REMOTE_ADDR` já reflete o IP
    real do cliente.

    Diferente de `apps.api.autenticacao.utils.get_client_ip`, que lê
    `X-Forwarded-For` para metadados de sessão — não use aquela função em
    checagens de acesso por rede.
    """
    return request.META.get("REMOTE_ADDR", "")


def ip_in_networks(ip: str, networks: list[str]) -> bool:
    """Informa se `ip` pertence a alguma entrada de `networks`.

    Cada entrada pode ser um endereço exato (`127.0.0.1`, `::1`) ou uma rede
    CIDR (`10.0.0.0/8`, `192.168.0.0/16`).
    """
    if not ip:
        return False

    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for network in networks:
        if "/" in network:
            try:
                if address in ipaddress.ip_network(network):
                    return True
            except ValueError:
                logger.warning("Rede inválida ignorada na checagem de IP: %s", network)
            continue

        if ip == network:
            return True

    return False
