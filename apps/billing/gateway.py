"""Cliente do gateway de cobrança e tradução das suas falhas para a API."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings

from django_checkouts.client import CheckoutClient
from django_checkouts.enums import RetryDisposition
from django_checkouts.exceptions import CheckoutError, GatewayTemporaryError
from django_checkouts.registry import get_checkout_gateway

from apps.api.core.errors import APIError
from apps.billing.errors import BillingErrorCode

logger = logging.getLogger("apps.billing")

# Disposições em que o estado no gateway pode ter mudado sem confirmação.
_UNCERTAIN_DISPOSITIONS = {
    RetryDisposition.RETRY,
    RetryDisposition.RETRY_SAME_KEY,
    RetryDisposition.RECONCILE_FIRST,
}


class ReferenceMismatch(Exception):
    """A assinatura remota aponta para outra organização; nada é gravado."""


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

    `ReferenceMismatch` também vira `502 billing.gateway_error`, com log de erro: a operação
    já chegou ao gateway, e a assinatura remota que a fecha não pertence à organização. A
    mutação não pode escapar como exceção não tratada.

    Raises:
        APIError: Sempre que um `CheckoutError` ou `ReferenceMismatch` atravessa o bloco.
    """
    try:
        yield
    except ReferenceMismatch as error:
        logger.error("Resposta do gateway recusada: %s", error)
        raise APIError(BillingErrorCode.GATEWAY_ERROR, status_code=502) from error
    except CheckoutError as error:
        if isinstance(error, GatewayTemporaryError) or error.retry_advice.disposition in _UNCERTAIN_DISPOSITIONS:
            raise APIError(BillingErrorCode.GATEWAY_UNCERTAIN, status_code=503) from error
        raise APIError(BillingErrorCode.GATEWAY_ERROR, status_code=502) from error
