"""Tarefas Celery da cobrança: aplicação dos eventos recebidos ao espelho local da assinatura."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import DataError, IntegrityError, models
from django.db.models import F, Q
from django.utils import timezone

from celery import shared_task
from django_checkouts.exceptions import CheckoutError, GatewayTemporaryError

from apps.billing.events import record_event
from apps.billing.gateway import get_client
from apps.billing.models import BillingEvent, Subscription, SubscriptionStatus
from apps.billing.subscriptions import ReferenceMismatch, Subscriptions
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

logger = logging.getLogger("apps.billing")

# Textos gravados em `last_error` quando o evento não pode ser aplicado por causa da organização.
MISSING_REFERENCE_MESSAGE = "assinatura do gateway sem referência à organização"
MISSING_ORGANIZATION_MESSAGE = "organização da assinatura não encontrada"
MAX_RETRY_COUNTDOWN_SECONDS = 900
# Carência antes de reenfileirar um evento pendente, para não disputar com o processamento em curso.
PENDING_GRACE_PERIOD = timedelta(minutes=5)
# Tempo que um evento já aplicado fica na caixa de entrada antes da limpeza.
PROCESSED_RETENTION = timedelta(days=90)


@shared_task(name="billing.process_event", bind=True, ignore_result=True, max_retries=5)
def process_billing_event(self, event_pk: int) -> None:
    """Aplica ao espelho local o `BillingEvent` de id `event_pk`.

    A organização vem do `reference_id` da assinatura remota, e não do evento, para que o
    evento de assinatura funcione mesmo quando chega antes do `checkout.paid`. Evento sem
    assinatura (ex.: checkout expirado) só é marcado como processado.

    Organização encerrada nunca recebe plano pago: a assinatura remota que ainda dá acesso é
    cancelada já, e o espelho não é sincronizado (ver `_close_remote`).

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
        organizacao = Organizacao.all_objects.filter(pk=organization_id).first()
        if organizacao is None:
            _record_failure(event, MISSING_ORGANIZATION_MESSAGE)
            return
        if _is_closed(organizacao):
            _close_remote(organization_id, remote, gateway_variant=event.gateway_variant)
        else:
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
    except Exception as error:
        # Falha não prevista: conta a tentativa para que a reconciliação, após o limite, pare de
        # reenfileirar o evento. O `Retry` do `self.retry` acima não chega aqui, porque é levantado
        # dentro de outro handler do mesmo `try`, e cláusulas irmãs não capturam isso.
        _record_failure(event, f"falha inesperada ({type(error).__name__}): {error}")
        raise

    _mark_processed(event)


def _is_closed(organizacao: Organizacao) -> bool:
    """Organização encerrada: com pedido de encerramento registrado, inativa ou excluída.

    Args:
        organizacao (Organizacao): Organização lida sem filtro de exclusão.

    Returns:
        bool: True se a organização não pode mais receber cobrança.
    """
    return organizacao.encerramento_solicitado_em is not None or not organizacao.is_active or organizacao.is_deleted


def _close_remote(organization_id: int, remote, *, gateway_variant: str) -> None:
    """Trata um evento de assinatura de organização encerrada, sem nunca espelhar plano pago nela.

    Assinatura que ainda dá acesso é vinculada ao espelho e cancelada já; depois, o espelho é
    sincronizado, o que o devolve ao gratuito. Se a organização já tinha outra assinatura
    vinculada, `link_external` cancela esta como duplicada e não há mais nada a fazer aqui.
    Assinatura sem acesso só faz o espelho acompanhar quando é a vinculada e já chegou a um
    estado final (cancelada, expirada ou não paga), para que o espelho não fique com vínculo morto.

    Args:
        organization_id (int): Organização encerrada.
        remote: Assinatura remota, já consultada no gateway.
        gateway_variant (str): Variante do gateway que emitiu o evento.

    Raises:
        CheckoutError: Falha do gateway; a tarefa que chama registra o erro.
    """
    Subscriptions.ensure_free(organization_id)
    status = SubscriptionStatus(remote.status.value)
    has_access = status in Subscription.ACCESS_STATUSES
    if has_access:
        Subscriptions.link_external(organization_id, external_id=remote.external_id, gateway_variant=gateway_variant)
    if Subscriptions.get_current(organization_id).external_id != remote.external_id:
        return
    if has_access:
        Subscriptions.cancel_remote_for_closed(organization_id, external_id=remote.external_id)
        Subscriptions.sync(organization_id)
    elif status in Subscription.TERMINAL_STATUSES | Subscription.ABANDONED_STATUSES:
        Subscriptions.sync(organization_id)


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


@shared_task(name="billing.reconcile", ignore_result=True)
def reconcile_billing() -> None:
    """Corrige o espelho quando um webhook se perdeu ou ficou pendente.

    Em três passos: registra e enfileira os eventos da janela `BILLING_RECONCILE_WINDOW_HOURS`
    que ainda não chegaram à caixa de entrada; reenfileira os eventos pendentes há mais que a
    carência e abaixo de `BILLING_EVENT_MAX_ATTEMPTS`; e apaga os já aplicados há mais de 90 dias.
    Os que atingiram o máximo não são reenfileirados e geram um log de erro por execução.
    Também reenfileira o cancelamento das organizações encerradas que ainda têm assinatura
    vinculada, o que cobre um `.delay` do encerramento que nunca chegou ao broker.
    """
    now = timezone.now()
    _record_missing_events(now)
    _requeue_pending_events(now)
    try:
        _requeue_closed_organizations()
    except Exception:
        # Passo isolado: uma falha na varredura de encerradas não pode impedir a limpeza.
        logger.exception("Varredura de organizações encerradas com assinatura vinculada falhou; os demais passos seguiram.")
    _purge_processed_events(now)


@shared_task(
    name="billing.cancel_closed_organization",
    ignore_result=True,
    autoretry_for=(GatewayTemporaryError,),
    retry_backoff=30,
    retry_backoff_max=900,
    retry_jitter=False,
    max_retries=5,
)
def cancel_for_closed_organization(organization_id: int) -> None:
    """Cancela já a assinatura paga de uma organização encerrada.

    Roda depois do commit do encerramento, e nunca dentro da transação dele: o `sync` trava a
    assinatura antes da organização, e a ordem inversa poderia travar. A organização gratuita
    não tem nada a cancelar.

    Falha temporária do gateway é repetida com backoff exponencial, até 5 vezes. A chave de
    idempotência é fixa por organização, então repetir não cancela duas vezes; sem a repetição,
    a assinatura paga de uma organização encerrada continuaria cobrando.

    Args:
        organization_id (int): Organização que acabou de ser encerrada.
    """
    Subscriptions.cancel_immediately(organization_id)


def _record_missing_events(now) -> None:
    """Percorre a janela remota, página a página, e enfileira só os eventos recém-registrados.

    Args:
        now: Momento de referência; a janela termina nele.
    """
    client = get_client()
    occurred_since = now - timedelta(hours=settings.BILLING_RECONCILE_WINDOW_HOURS)
    cursor = None
    while True:
        page = client.events.list(occurred_since=occurred_since, occurred_before=now, cursor=cursor)
        for event in page.items:
            record, created = record_event(event, variant=event.variant)
            if created:
                process_billing_event.delay(record.pk)
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


def _requeue_pending_events(now) -> None:
    """Reenfileira os pendentes parados e registra, uma vez, quantos atingiram o máximo de tentativas.

    Args:
        now: Momento de referência para a carência dos eventos recentes.
    """
    limit = settings.BILLING_EVENT_MAX_ATTEMPTS
    pending = BillingEvent.all_objects.filter(processed_at__isnull=True)
    stale_pks = list(
        pending.filter(attempts__lt=limit, created_at__lt=now - PENDING_GRACE_PERIOD).values_list("pk", flat=True),
    )
    for event_pk in stale_pks:
        process_billing_event.delay(event_pk)

    exhausted = pending.filter(attempts__gte=limit).count()
    if exhausted:
        logger.error("%s eventos de cobrança atingiram o máximo de %s tentativas e não serão reprocessados.", exhausted, limit)


def _requeue_closed_organizations() -> None:
    """Reenfileira o cancelamento das organizações encerradas que ainda têm assinatura vinculada ao gateway.

    A leitura de `Subscription` é filtrada pelo RLS e não atravessa organizações, nem sob o
    contexto privilegiado (que só troca de tenant). Por isso a lista de encerradas vem de uma
    consulta em `Organizacao`, sem RLS, e cada assinatura é consultada sob o contexto da sua
    própria organização.
    """
    closed_ids = Organizacao.all_objects.filter(
        Q(is_active=False) | Q(is_deleted=True) | Q(encerramento_solicitado_em__isnull=False),
    ).values_list("pk", flat=True)
    pending_ids = []
    for organization_id in list(closed_ids):
        with organizacao_atual_privilegiada(organization_id):
            linked = Subscription.all_objects.filter(organizacao_id=organization_id).exclude(external_id__isnull=True).exclude(external_id="")
            if linked.exists():
                pending_ids.append(organization_id)

    for organization_id in pending_ids:
        cancel_for_closed_organization.delay(organization_id)
    if pending_ids:
        logger.warning(
            "%s organizações encerradas ainda têm assinatura vinculada ao gateway; o cancelamento foi reenfileirado.",
            len(pending_ids),
        )


def _purge_processed_events(now) -> None:
    """Apaga de vez os eventos aplicados há mais que a retenção.

    Args:
        now: Momento de referência para a idade dos eventos.
    """
    processed_before = now - PROCESSED_RETENTION
    # O `delete()` do queryset base faz soft delete; a limpeza precisa do DELETE real do Django.
    models.QuerySet.delete(BillingEvent.all_objects.filter(processed_at__lt=processed_before))
