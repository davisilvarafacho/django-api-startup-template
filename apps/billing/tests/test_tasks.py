"""Testes da tarefa `process_billing_event`: aplicação do evento ao espelho, erros e retentativas."""

from django.utils import timezone

import pytest
from celery.exceptions import Retry
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import Gateway
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.gateways.commands import RetrieveSubscription
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.models import BillingEvent, Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import Subscriptions
from apps.billing.tasks import process_billing_event
from apps.organizacoes.models import Organizacao
from tests.support.billing import fake_gateway, remote_subscription

pytestmark = pytest.mark.django_db


def _organizacao(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _plano_pro():
    return Plan.objects.create(name="Pro", slug="pro", rank=2, member_limit=10, gateway_price_id="price_pro")


def _evento_registrado(*, event_id="evt_1", subscription_external_id="sub_1", resource_kind="subscription"):
    return BillingEvent.all_objects.create(
        gateway_variant="stripe",
        event_id=event_id,
        event_type="customer.subscription.updated",
        resource_kind=resource_kind,
        resource_id=subscription_external_id or "",
        subscription_external_id=subscription_external_id,
        occurred_at=timezone.now(),
    )


def _remota(organizacao, external_id="sub_1", price_id="price_pro"):
    """Assinatura remota da organização, no preço informado."""
    item = SubscriptionItem(external_id="si_1", price_id=price_id, quantity=1, unit_amount=None, currency=None, billing_cycle=None, raw={})
    return remote_subscription(external_id=external_id, reference_id=str(organizacao.pk), items=(item,))


def _gateway(monkeypatch, results):
    """Gateway fake, com as respostas por comando, usado tanto pela tarefa quanto pelo serviço de assinatura."""
    gateway = fake_gateway(results)
    cliente = CheckoutClient(gateway)
    monkeypatch.setattr("apps.billing.tasks.get_client", lambda: cliente)
    monkeypatch.setattr("apps.billing.subscriptions.get_client", lambda: cliente)
    return gateway


def _falha(erro):
    """Resposta do gateway fake que levanta `erro` no lugar de devolver um resultado."""

    def responder(comando):
        raise erro

    return responder


def _espelho(organizacao):
    with organization_scope(organizacao.pk):
        return Subscription.all_objects.get(organizacao_id=organizacao.pk)


def _registro(evento):
    return BillingEvent.all_objects.get(pk=evento.pk)


def test_process_event_without_subscription_marks_processed(monkeypatch):
    evento = _evento_registrado(subscription_external_id=None, resource_kind="checkout")
    gateway = _gateway(monkeypatch, {})

    process_billing_event(evento.pk)

    assert _registro(evento).processed_at is not None
    assert gateway.commands == []


def test_process_already_processed_event_does_nothing(monkeypatch):
    evento = _evento_registrado()
    BillingEvent.all_objects.filter(pk=evento.pk).update(processed_at=timezone.now())
    gateway = _gateway(monkeypatch, {})

    process_billing_event(evento.pk)

    assert gateway.commands == []


def test_process_checkout_paid_links_and_syncs(monkeypatch):
    organizacao = _organizacao()
    Subscriptions.ensure_free(organizacao.pk)
    plano = _plano_pro()
    _gateway(monkeypatch, {RetrieveSubscription: lambda comando: _remota(organizacao)})
    evento = _evento_registrado(resource_kind="checkout")

    process_billing_event(evento.pk)

    espelho = _espelho(organizacao)
    assert espelho.external_id == "sub_1"
    assert espelho.gateway_variant == "stripe"
    assert espelho.plan == plano
    assert espelho.status == SubscriptionStatus.ACTIVE
    assert _registro(evento).processed_at is not None
    assert _registro(evento).attempts == 0


def test_process_subscription_event_before_checkout_links_and_syncs(monkeypatch):
    # O evento de assinatura chega antes do checkout.paid: a organização vem do `reference_id`.
    organizacao = _organizacao()
    Subscriptions.ensure_free(organizacao.pk)
    plano = _plano_pro()
    gateway = _gateway(monkeypatch, {RetrieveSubscription: lambda comando: _remota(organizacao)})
    evento = _evento_registrado(resource_kind="subscription")

    process_billing_event(evento.pk)

    espelho = _espelho(organizacao)
    assert espelho.external_id == "sub_1"
    assert espelho.plan == plano
    assert _registro(evento).processed_at is not None
    assert all(comando.external_id == "sub_1" for comando in gateway.commands)


def test_process_missing_reference_records_error(monkeypatch):
    organizacao = _organizacao()
    Subscriptions.ensure_free(organizacao.pk)
    remota = remote_subscription(external_id="sub_1", reference_id=None)
    _gateway(monkeypatch, {RetrieveSubscription: remota})
    evento = _evento_registrado()

    process_billing_event(evento.pk)

    registro = _registro(evento)
    assert registro.last_error == "missing reference_id"
    assert registro.attempts == 1
    assert registro.processed_at is None
    assert _espelho(organizacao).external_id is None


def test_process_temporary_error_retries(monkeypatch):
    _gateway(monkeypatch, {RetrieveSubscription: _falha(GatewayTemporaryError("indisponível", gateway=Gateway.STRIPE, variant="stripe"))})
    evento = _evento_registrado()

    # Chamada direta da tarefa: a retentativa levanta `Retry` em vez de reenfileirar.
    with pytest.raises(Retry):
        process_billing_event(evento.pk)

    registro = _registro(evento)
    assert registro.attempts == 1
    assert "indisponível" in registro.last_error
    assert registro.processed_at is None


def test_process_permanent_gateway_error_records_without_retry(monkeypatch):
    _gateway(monkeypatch, {RetrieveSubscription: _falha(GatewayPermanentError("recusado", gateway=Gateway.STRIPE, variant="stripe"))})
    evento = _evento_registrado()

    process_billing_event(evento.pk)

    registro = _registro(evento)
    assert registro.attempts == 1
    assert "recusado" in registro.last_error
    assert registro.processed_at is None


def test_process_reference_mismatch_records_error(monkeypatch):
    # O espelho local já aponta para `sub_antiga`, que pertence a outra organização.
    organizacao = _organizacao()
    outra = _organizacao("outra")
    Subscriptions.ensure_free(organizacao.pk)
    with organization_scope(organizacao.pk):
        Subscription.all_objects.filter(organizacao_id=organizacao.pk).update(external_id="sub_antiga", gateway_variant="stripe")
    antes = _espelho(organizacao)

    def retrieve(comando):
        if comando.external_id == "sub_nova":
            return _remota(organizacao, external_id="sub_nova")
        return _remota(outra, external_id="sub_antiga")

    _gateway(monkeypatch, {RetrieveSubscription: retrieve})
    evento = _evento_registrado(subscription_external_id="sub_nova")

    process_billing_event(evento.pk)

    registro = _registro(evento)
    assert registro.attempts == 1
    assert "não pertence" in registro.last_error
    assert registro.processed_at is None
    depois = _espelho(organizacao)
    assert depois.external_id == "sub_antiga"
    assert depois.status == antes.status
    assert depois.plan_id == antes.plan_id
