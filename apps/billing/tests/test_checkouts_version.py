"""Guarda de versão do django-checkouts: o mapeamento real precisa gravar reference_id na assinatura.

Com django-checkouts 1.0.1, o checkout de assinatura só levava reference_id para
client_reference_id da Session; a assinatura criada ficava com reference_id None, e
_mirror_remote rejeitava o espelho, sem conceder acesso pago. Os testes com fake gateway
não detectam isso, porque as fixtures definem reference_id explicitamente. Este teste usa
o mapeamento da própria biblioteca instalada, sem rede.
"""

from django_checkouts.enums import CheckoutMode
from django_checkouts.gateways.stripe.mapping import build_checkout_params
from django_checkouts.types import CatalogPrice, CheckoutCreate, CheckoutItem


def test_checkout_de_assinatura_grava_reference_id_na_assinatura():
    """Garante que o reference_id do checkout de assinatura vai para subscription_data.metadata."""
    request = CheckoutCreate(
        mode=CheckoutMode.SUBSCRIPTION,
        items=(CheckoutItem(price=CatalogPrice(external_id="price_teste")),),
        success_url="https://app.example.com/sucesso",
        reference_id="42",
    )

    params = build_checkout_params(request)

    assert params["subscription_data"]["metadata"]["reference_id"] == "42"
