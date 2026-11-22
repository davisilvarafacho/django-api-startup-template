"""Construtores exclusivos da infraestrutura de teste da cobrança."""

from uuid import uuid4

from django_checkouts.client import CheckoutClient
from django_checkouts.enums import Gateway, SubscriptionStatus
from django_checkouts.gateways.stripe.capabilities import STRIPE_CAPABILITIES
from django_checkouts.testing.fakes import FakeCheckoutGateway
from django_checkouts.types.subscriptions import Subscription, SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.models import Plan
from apps.billing.models import Subscription as LocalSubscription
from apps.organizacoes.models import Organizacao


class FakeGatewayVariant(FakeCheckoutGateway):
    """Variante fake instanciável por `CHECKOUT_VARIANTS`, sem resultados configurados."""

    def __init__(self, *, variant: str) -> None:
        super().__init__(results={}, capabilities=STRIPE_CAPABILITIES, variant=variant)


def fake_gateway(results: dict) -> FakeCheckoutGateway:
    """Cria um gateway fake que responde `results` por tipo de comando e guarda os comandos recebidos."""
    return FakeCheckoutGateway(results=results, capabilities=STRIPE_CAPABILITIES, variant="stripe")


def fake_client(results: dict) -> CheckoutClient:
    """Cria um cliente sobre um gateway fake que responde `results` por tipo de comando."""
    return CheckoutClient(fake_gateway(results))


def remote_subscription(**overrides) -> Subscription:
    """Cria uma assinatura remota válida, com a sobrescrita de campos pedida."""
    item = SubscriptionItem(
        external_id="si_1",
        price_id="price_pro",
        quantity=1,
        unit_amount=None,
        currency=None,
        billing_cycle=None,
        raw={},
    )
    fields = {
        "external_id": "sub_1",
        "gateway": Gateway.STRIPE,
        "variant": "stripe",
        "status": SubscriptionStatus.ACTIVE,
        "customer_id": None,
        "items": (item,),
        "current_period_start": None,
        "current_period_end": None,
        "trial_end": None,
        "cancel_at": None,
        "canceled_at": None,
        "ended_at": None,
        "latest_invoice_id": None,
        "reference_id": None,
        "metadata": {},
        "raw": {},
    }
    fields.update(overrides)
    return Subscription(**fields)


def subscribe_organization(organization: Organizacao, *, member_limit: int) -> LocalSubscription:
    """Assina a organização, ativa, num plano pago com o limite de membros informado."""
    plan = Plan.objects.create(
        name=f"Plano de teste {member_limit}",
        slug=f"teste-{uuid4().hex[:12]}",
        rank=1,
        member_limit=member_limit,
        is_free=False,
    )
    with organization_scope(organization.pk):
        subscription, _ = LocalSubscription.all_objects.update_or_create(
            organizacao=organization,
            defaults={"plan": plan, "status": LocalSubscription.Status.ACTIVE},
        )
    return subscription
