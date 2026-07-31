import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

import httpx
from user_agents import parse

logger = logging.getLogger(__name__)


def get_geolocation_data(ip_address: str) -> dict:
    """Obtém dados de geolocalização do IP

    Opções de APIs (escolha uma):
    1. ipapi.co (grátis: 1000 req/dia)
    2. ip-api.com (grátis: 45 req/min)
    3. ipgeolocation.io (grátis: 1000 req/dia com API key)
    4. ipinfo.io (grátis: 50.000 req/mês com API key)
    """

    # Verifica cache primeiro
    cache_key = f'geolocation_{ip_address}'
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    # Ignora IPs locais
    if ip_address in ['127.0.0.1', 'localhost'] or ip_address.startswith('192.168.'):
        return {}

    try:
        response = httpx.get(
            f'https://ipapi.co/{ip_address}/json/',
            timeout=3
        )

        if response.status_code == 200:
            data = response.json()

            result = {
                'country': data.get('country_name', ''),
                'country_code': data.get('country_code', ''),
                'region': data.get('region', ''),
                'city': data.get('city', ''),
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'timezone': data.get('timezone', ''),
                'isp': data.get('org', ''),
            }

            # Cache por 24 horas
            cache.set(cache_key, result, 60 * 60 * 24)
            return result

    except Exception:
        logger.warning("Erro ao obter geolocalização", exc_info=True)

    return {}


def get_client_ip(request: HttpRequest) -> str | None:
    """Resolve o IP de origem da request.

    Atrás do proxy de borda o `X-Forwarded-For` é sobrescrito pelo nginx
    (`docker/nginx/snippets/proxy.conf`), então chega com valor único e não
    forjável. Fora do proxy o header é escolhido pelo cliente e precisa ser
    ignorado, sob pena de o bloqueio por IP do django-axes virar contornável.

    Args:
        request: Request HTTP em processamento.

    Returns:
        O IP de origem, ou `None` se não for possível determiná-lo.
    """
    if settings.BEHIND_PROXY:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def parse_user_agent(user_agent_string: str | None) -> dict:
    """Extrai informações do user agent"""

    if not user_agent_string:
        return {}

    ua = parse(user_agent_string)

    return {
        'device_type': get_device_type(ua),
        'device_brand': ua.device.brand or '',
        'device_model': ua.device.model or '',
        'os_name': ua.os.family or '',
        'os_version': ua.os.version_string or '',
        'browser_name': ua.browser.family or '',
        'browser_version': ua.browser.version_string or '',
    }


def get_device_type(ua) -> str:
    """Determina o tipo de dispositivo"""

    if ua.is_mobile:
        return 'mobile'
    if ua.is_tablet:
        return 'tablet'
    if ua.is_pc:
        return 'desktop'
    return 'unknown'
