"""Cliente do gateway de cobrança e tradução das suas falhas para a API."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings

from django_checkouts.client import CheckoutClient
from django_checkouts.enums import RetryDisposition
from django_checkouts.exceptions import CheckoutError, GatewayTemporaryError
from django_checkouts.registry import get_checkout_gateway

from apps.api.core.errors import APIError
from apps.billing.errors import BillingErrorCode

# Disposições em que o estado no gateway pode ter mudado sem confirmação.
_DISPOSICOES_INCERTAS = {
    RetryDisposition.RETRY,
    RetryDisposition.RETRY_SAME_KEY,
    RetryDisposition.RECONCILE_FIRST,
}


def get_client() -> CheckoutClient:
    """Devolve o cliente da variante de gateway configurada para a cobrança.

    Returns:
        O cliente da variante `settings.BILLING_GATEWAY_VARIANT`.
    """
    return get_checkout_gateway(settings.BILLING_GATEWAY_VARIANT)


@contextmanager
def translate_gateway_errors() -> Iterator[None]:
    """Converte falhas de `django-checkouts` em `APIError` para a resposta HTTP.

    Falhas temporárias, ou cuja orientação de repetição indica que o resultado
    é incerto, viram `503 billing.gateway_uncertain`. As demais viram
    `502 billing.gateway_error`.

    Raises:
        APIError: Sempre que um `CheckoutError` atravessa o bloco.
    """
    try:
        yield
    except CheckoutError as erro:
        if isinstance(erro, GatewayTemporaryError) or erro.retry_advice.disposition in _DISPOSICOES_INCERTAS:
            raise APIError(BillingErrorCode.GATEWAY_UNCERTAIN, status_code=503) from erro
        raise APIError(BillingErrorCode.GATEWAY_ERROR, status_code=502) from erro
