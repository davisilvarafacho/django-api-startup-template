"""Endpoint `/metrics` para o Prometheus.

O `django-prometheus` já expõe uma view pronta, mas ela é aberta. Métricas
revelam rotas internas, volume de tráfego e nomes de modelos — informação de
reconhecimento para quem estiver sondando a API. Aqui o acesso é restrito à
rede interna (ou a um token compartilhado com o scraper).
"""

import logging

from django.http import HttpResponseForbidden

from django_prometheus.exports import ExportToDjangoView

from apps.api.core.ip_utils import ip_in_networks, peer_ip
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
        logger.warning("Acesso negado a /metrics de %s", peer_ip(request))
        return HttpResponseForbidden("Acesso restrito.")

    return ExportToDjangoView(request)


def _autorizado(request):
    if TOKEN_METRICS and request.META.get(HEADER_TOKEN_METRICS) == TOKEN_METRICS:
        return True

    return ip_in_networks(peer_ip(request), REDES_PERMITIDAS)
