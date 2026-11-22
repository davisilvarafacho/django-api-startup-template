"""Testes da fronteira com o gateway de cobrança (não tocam o banco)."""

import pytest
from django_checkouts.enums import Gateway, RetryDisposition
from django_checkouts.exceptions import CheckoutError, GatewayPermanentError, GatewayTemporaryError, RetryAdvice
from django_checkouts.registry import GATEWAY_CACHE

from apps.api.core.errors import APIError
from apps.billing.gateway import get_client, translate_gateway_errors


@pytest.fixture(autouse=True)
def limpar_cache_de_gateways():
    GATEWAY_CACHE.clear()
    yield
    GATEWAY_CACHE.clear()


def test_translate_temporary_error_returns_503():
    with pytest.raises(APIError) as excinfo:
        with translate_gateway_errors():
            raise GatewayTemporaryError("falha", gateway=Gateway.STRIPE, variant="stripe")

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "billing.gateway_uncertain"


def test_translate_permanent_error_returns_502():
    with pytest.raises(APIError) as excinfo:
        with translate_gateway_errors():
            raise GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe")

    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "billing.gateway_error"


@pytest.mark.parametrize(
    "disposition",
    [RetryDisposition.RETRY, RetryDisposition.RETRY_SAME_KEY, RetryDisposition.RECONCILE_FIRST],
)
def test_translate_error_with_retryable_advice_returns_503(disposition):
    error = CheckoutError("falha")
    error.retry_advice = RetryAdvice(disposition=disposition)

    with pytest.raises(APIError) as excinfo:
        with translate_gateway_errors():
            raise error

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "billing.gateway_uncertain"


def test_get_client_uses_configured_variant(settings):
    settings.CHECKOUT_VARIANTS = {"fake": ("tests.support.billing.FakeGatewayVariant", {})}
    settings.BILLING_GATEWAY_VARIANT = "fake"

    client = get_client()

    assert client.variant == "fake"
