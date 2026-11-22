"""Tarefas Celery da cobrança: aplicação dos eventos recebidos ao espelho local da assinatura."""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import DataError, IntegrityError
from django.db.models import F
from django.utils import timezone

from celery import shared_task
from django_checkouts.exceptions import CheckoutError, GatewayTemporaryError

from apps.billing.gateway import get_client
from apps.billing.models import BillingEvent
from apps.billing.subscriptions import ReferenceMismatch, Subscriptions
from apps.organizacoes.models import Organizacao

logger = logging.getLogger("apps.billing")

# Textos gravados em `last_error` quando o evento não pode ser aplicado por causa da organização.
MISSING_REFERENCE_MESSAGE = "assinatura do gateway sem referência à organização"
MISSING_ORGANIZATION_MESSAGE = "organização da assinatura não encontrada"
MAX_RETRY_COUNTDOWN_SECONDS = 900


@shared_task(name="billing.process_event", bind=True, ignore_result=True, max_retries=5)
def process_billing_event(self, event_pk: int) -> None:
    """Aplica ao espelho local o `BillingEvent` de id `event_pk`.

    A organização vem do `reference_id` da assinatura remota, e não do evento, para que o
    evento de assinatura funcione mesmo quando chega antes do `checkout.paid`. Evento sem
    assinatura (ex.: checkout expirado) só é marcado como processado.

    Erro temporário do gateway registra a falha e agenda nova tentativa com backoff
    exponencial, respeitando `retry_after` quando o gateway o informa. Os demais erros,
    inclusive organização inexistente ou sem assinatura local, registram a falha em
    `last_error` sem nova tentativa, e o evento fica pendente.
    """
    event = BillingEvent.all_objects.filter(pk=event_pk).first()
    if event is None or event.processed_at is not None:
        return

    if not event.subscription_external_id:
        _mark_processed(event)
        return

    try:
        remote = get_client().subscriptions.retrieve(event.subscription_external_id)
        organization_id = _organization_id_from_reference(remote.reference_id)
        if organization_id is None:
            _record_failure(event, MISSING_REFERENCE_MESSAGE)
            return
        # Checagem explícita: a FK da assinatura é deferida ao commit, e `ensure_free` deixaria
        # uma linha órfã dentro da transação até o fim dela.
        if not Organizacao.all_objects.filter(pk=organization_id).exists():
            _record_failure(event, MISSING_ORGANIZATION_MESSAGE)
            return
        Subscriptions.ensure_free(organization_id)
        Subscriptions.link_external(organization_id, external_id=event.subscription_external_id, gateway_variant=event.gateway_variant)
        Subscriptions.sync(organization_id)
    except GatewayTemporaryError as error:
        _record_failure(event, str(error))
        raise self.retry(countdown=_retry_countdown(self.request.retries, error)) from error
    except (CheckoutError, ReferenceMismatch) as error:
        _record_failure(event, str(error))
        return
    except (ObjectDoesNotExist, IntegrityError, DataError):
        # Nova tentativa não cria a organização: o `reference_id` não aponta para uma linha válida.
        _record_failure(event, MISSING_ORGANIZATION_MESSAGE)
        return

    _mark_processed(event)


def _organization_id_from_reference(reference_id: str | None) -> int | None:
    """Converte o `reference_id` remoto no id da organização.

    `isdecimal` (e não `isdigit`) porque só ele garante que `int()` converte o texto: valores
    como '²' passam em `isdigit` e quebram a conversão.

    Args:
        reference_id (str | None): Referência gravada na assinatura remota.

    Returns:
        int | None: O id da organização, ou `None` se a referência é vazia ou não decimal.
    """
    if not reference_id or not reference_id.isdecimal():
        return None
    return int(reference_id)


def _retry_countdown(retries: int, error: GatewayTemporaryError) -> int:
    """Calcula a espera até a próxima tentativa, em segundos.

    Args:
        retries (int): Número de retentativas já feitas.
        error (GatewayTemporaryError): Falha temporária, cuja orientação pode pedir espera maior.

    Returns:
        int: Segundos de espera, com teto de 15 minutos salvo o pedido do gateway.
    """
    countdown = min(2**retries * 30, MAX_RETRY_COUNTDOWN_SECONDS)
    advice = error.retry_advice.retry_after
    if advice is not None:
        countdown = max(countdown, int(advice.total_seconds()))
    return countdown


def _record_failure(event: BillingEvent, message: str) -> None:
    """Soma uma tentativa e grava a descrição da falha, sem fechar o evento."""
    BillingEvent.all_objects.filter(pk=event.pk).update(attempts=F("attempts") + 1, last_error=message)
    logger.warning("Evento de cobrança %s não aplicado: %s", event.pk, message)


def _mark_processed(event: BillingEvent) -> None:
    """Fecha o evento como aplicado ao espelho."""
    BillingEvent.all_objects.filter(pk=event.pk).update(processed_at=timezone.now())
