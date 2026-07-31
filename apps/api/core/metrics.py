"""Endpoint `/metrics` para o Prometheus.

O `django-prometheus` já expõe uma view pronta, mas ela é aberta. Métricas
revelam rotas internas, volume de tráfego e nomes de modelos — informação de
reconhecimento para quem estiver sondando a API. Aqui o acesso é restrito à
rede interna (ou a um token compartilhado com o scraper).
"""

import ipaddress
import logging

from django.http import HttpResponseForbidden

from django_prometheus.exports import ExportToDjangoView

from utils.env import get_env_var, get_list_from_env

logger = logging.getLogger(__name__)

HEADER_TOKEN_METRICS = "HTTP_X_METRICS_TOKEN"

# Redes que podem raspar as métricas. O padrão cobre o cenário local e o de
# container falando com container (redes privadas do Docker/Kubernetes).
REDES_PERMITIDAS = get_list_from_env(
    "PROMETHEUS_ALLOWED_NETWORKS",
    ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
)

TOKEN_METRICS = get_env_var("PROMETHEUS_METRICS_TOKEN")


def metrics_view(request):
    """Devolve as métricas no formato de exposição do Prometheus."""
    if not _autorizado(request):
        logger.warning("Acesso negado a /metrics de %s", _ip_do_cliente(request))
        return HttpResponseForbidden("Acesso restrito.")

    return ExportToDjangoView(request)


def _autorizado(request):
    if TOKEN_METRICS and request.META.get(HEADER_TOKEN_METRICS) == TOKEN_METRICS:
        return True

    return _ip_interno(_ip_do_cliente(request))


def _ip_do_cliente(request):
    """Devolve o endereço do peer da conexão.

    Deliberadamente **não** usa `X-Forwarded-For`: o header é escrito pelo
    cliente e qualquer um poderia mandar `X-Forwarded-For: 127.0.0.1` para
    passar pela checagem de rede interna. Aqui só vale quem realmente abriu a
    conexão. Para raspar de fora das faixas internas (através de um proxy, por
    exemplo), use `PROMETHEUS_METRICS_TOKEN`.
    """
    return request.META.get("REMOTE_ADDR", "")


def _ip_interno(ip):
    if not ip:
        return False

    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for rede in REDES_PERMITIDAS:
        try:
            if endereco in ipaddress.ip_network(rede):
                return True
        except ValueError:
            logger.warning("Rede inválida em PROMETHEUS_ALLOWED_NETWORKS: %s", rede)

    return False
