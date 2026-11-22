"""Tarefas Celery da cobrança: aplicação dos eventos recebidos ao espelho local da assinatura."""

import logging

from django.db.models import F
from django.utils import timezone

from celery import shared_task
from django_checkouts.exceptions import CheckoutError, GatewayTemporaryError

from apps.billing.gateway import get_client
from apps.billing.models import BillingEvent
from apps.billing.subscriptions import ReferenceMismatch, Subscriptions

logger = logging.getLogger("apps.billing")

# Texto gravado em `last_error` quando o gateway não informa a organização da assinatura.
MENSAGEM_SEM_REFERENCIA = "missing reference_id"
TETO_DA_ESPERA_SEGUNDOS = 900


@shared_task(name="billing.process_event", bind=True, ignore_result=True, max_retries=5)
def process_billing_event(self, event_pk: int) -> None:
    """Aplica ao espelho local o `BillingEvent` de id `event_pk`.

    A organização vem do `reference_id` da assinatura remota, e não do evento, para que o
    evento de assinatura funcione mesmo quando chega antes do `checkout.paid`. Evento sem
    assinatura (ex.: checkout expirado) só é marcado como processado.

    Erro temporário do gateway registra a falha e agenda nova tentativa com backoff
    exponencial, respeitando `retry_after` quando o gateway o informa. Os demais erros
    registram a falha em `last_error` sem nova tentativa, e o evento fica pendente.
    """
    evento = BillingEvent.all_objects.filter(pk=event_pk).first()
    if evento is None or evento.processed_at is not None:
        return

    if not evento.subscription_external_id:
        _marcar_processado(evento)
        return

    try:
        remota = get_client().subscriptions.retrieve(evento.subscription_external_id)
        organizacao_id = _organizacao_da_referencia(remota.reference_id)
        if organizacao_id is None:
            _registrar_falha(evento, MENSAGEM_SEM_REFERENCIA)
            return
        Subscriptions.link_external(organizacao_id, external_id=evento.subscription_external_id, gateway_variant=evento.gateway_variant)
        Subscriptions.sync(organizacao_id)
    except GatewayTemporaryError as erro:
        _registrar_falha(evento, str(erro))
        raise self.retry(countdown=_espera_da_retentativa(self.request.retries, erro)) from erro
    except (CheckoutError, ReferenceMismatch) as erro:
        _registrar_falha(evento, str(erro))
        return

    _marcar_processado(evento)


def _organizacao_da_referencia(reference_id: str | None) -> int | None:
    """Converte o `reference_id` remoto no id da organização; vazio ou não numérico não serve."""
    if not reference_id or not reference_id.isdigit():
        return None
    return int(reference_id)


def _espera_da_retentativa(tentativas: int, erro: GatewayTemporaryError) -> int:
    """Calcula a espera até a próxima tentativa, em segundos.

    Args:
        tentativas (int): Número de retentativas já feitas.
        erro (GatewayTemporaryError): Falha temporária, cuja orientação pode pedir espera maior.

    Returns:
        int: Segundos de espera, com teto de 15 minutos salvo o pedido do gateway.
    """
    espera = min(2**tentativas * 30, TETO_DA_ESPERA_SEGUNDOS)
    aviso = erro.retry_advice.retry_after
    if aviso is not None:
        espera = max(espera, int(aviso.total_seconds()))
    return espera


def _registrar_falha(evento: BillingEvent, mensagem: str) -> None:
    """Soma uma tentativa e grava a descrição da falha, sem fechar o evento."""
    BillingEvent.all_objects.filter(pk=evento.pk).update(attempts=F("attempts") + 1, last_error=mensagem)
    logger.warning("Evento de cobrança %s não aplicado: %s", evento.pk, mensagem)


def _marcar_processado(evento: BillingEvent) -> None:
    """Fecha o evento como aplicado ao espelho."""
    BillingEvent.all_objects.filter(pk=evento.pk).update(processed_at=timezone.now())
