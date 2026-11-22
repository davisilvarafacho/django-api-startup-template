"""Registro dos eventos recebidos do gateway na caixa de entrada `BillingEvent`."""

from django_checkouts.types.checkouts import Checkout
from django_checkouts.types.events import WebhookEvent
from django_checkouts.types.invoices import Invoice
from django_checkouts.types.subscriptions import Subscription as RemoteSubscription

from apps.billing.models import BillingEvent


def _assinatura_do_recurso(recurso) -> str | None:
    """Extrai o id da assinatura no gateway a partir do recurso do evento.

    Args:
        recurso: Recurso normalizado do evento (checkout, assinatura, fatura ou outro).

    Returns:
        str | None: O id da assinatura, ou `None` se o recurso não pertence a uma assinatura.
    """
    if isinstance(recurso, RemoteSubscription):
        return recurso.external_id or None
    if isinstance(recurso, (Checkout, Invoice)):
        return recurso.subscription_id or None
    return None


def record_event(event: WebhookEvent, *, variant: str) -> tuple[BillingEvent, bool]:
    """Grava o evento na caixa de entrada, ignorando-o se a chave única já existir.

    O `bulk_create` com `ignore_conflicts` não devolve o id da linha, então o registro é
    relido pela chave única. Ele foi criado agora se o `created_at` gravado é o do próprio
    insert. Uma colisão de microssegundo com outra inserção concorrente só causaria um
    enfileiramento extra, inócuo porque o processamento é idempotente.

    Args:
        event (WebhookEvent): Evento já verificado pela biblioteca de checkouts.
        variant (str): Variante do gateway que enviou o evento.

    Returns:
        tuple[BillingEvent, bool]: O registro e se ele foi criado nesta chamada.
    """
    novo = BillingEvent(
        gateway_variant=variant,
        event_id=event.event_id,
        event_type=event.event_type,
        resource_kind=event.resource_kind or "",
        resource_id=event.resource_id or "",
        subscription_external_id=_assinatura_do_recurso(event.resource),
        occurred_at=event.occurred_at,
    )
    BillingEvent.all_objects.bulk_create([novo], ignore_conflicts=True)
    registro = BillingEvent.all_objects.get(gateway_variant=variant, event_id=event.event_id)
    return registro, registro.created_at == novo.created_at
