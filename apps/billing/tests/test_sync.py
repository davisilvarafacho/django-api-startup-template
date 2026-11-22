"""Testes do serviço de assinatura: espelho local, sincronização com o gateway e limite de membros."""

import logging
from datetime import timedelta

from django.db import connection
from django.utils import timezone

import pytest
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import CancellationTiming
from django_checkouts.enums import SubscriptionStatus as RemoteStatus
from django_checkouts.gateways.commands import CancelRemoteSubscription, RetrieveSubscription
from django_checkouts.types.subscriptions import SubscriptionItem

from apps.billing.context import organization_scope
from apps.billing.members import MemberLimits
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.billing.subscriptions import ReferenceMismatch, Subscriptions
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.billing import fake_gateway, remote_subscription
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _organizacao(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _plano(slug, *, price=None, rank=1, limit=5):
    return Plan.objects.create(name=slug, slug=slug, rank=rank, member_limit=limit, gateway_price_id=price)


def _assinatura(organizacao, plano, **campos):
    with organization_scope(organizacao.pk):
        return Subscription.all_objects.create(organizacao=organizacao, plan=plano, **{"status": SubscriptionStatus.ACTIVE, **campos})


def _vincular(organizacao, plano, **campos):
    """Assinatura local já ligada à assinatura remota `sub_1`."""
    return _assinatura(organizacao, plano, external_id="sub_1", gateway_variant="stripe", **campos)


def _estado(organizacao):
    with organization_scope(organizacao.pk):
        return Subscription.all_objects.get(organizacao_id=organizacao.pk)


def _vinculos(organizacao, quantidade):
    return [Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(), papel=Papel.MEMBRO) for _ in range(quantidade)]


def _gateway(monkeypatch, results):
    gateway = fake_gateway(results)
    monkeypatch.setattr("apps.billing.subscriptions.get_client", lambda: CheckoutClient(gateway))
    return gateway


def _remota_com_preco(organizacao, preco):
    item = SubscriptionItem(external_id="si_2", price_id=preco, quantity=1, unit_amount=None, currency=None, billing_cycle=None, raw={})
    return remote_subscription(reference_id=str(organizacao.pk), items=(item,))


def test_ensure_free_is_idempotent():
    organizacao = _organizacao()

    primeira = Subscriptions.ensure_free(organizacao.pk)
    segunda = Subscriptions.ensure_free(organizacao.pk)

    assert primeira.pk == segunda.pk
    assert primeira.plan == Plan.get_free()
    assert primeira.status == SubscriptionStatus.ACTIVE
    assert primeira.external_id is None
    with organization_scope(organizacao.pk):
        assert Subscription.all_objects.filter(organizacao_id=organizacao.pk).count() == 1


def test_sync_free_without_external_id_only_applies_limit(monkeypatch):
    organizacao = _organizacao()
    Subscriptions.ensure_free(organizacao.pk)
    limite = Plan.get_free().member_limit
    _vinculos(organizacao, limite + 2)
    gateway = _gateway(monkeypatch, {})

    Subscriptions.sync(organizacao.pk)

    assert gateway.commands == []
    assert MemberLimits.active_count(organizacao.pk) == limite


def test_sync_maps_price_to_plan_and_status(monkeypatch):
    organizacao = _organizacao()
    basico = _plano("basico", price="price_basico", rank=1)
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, basico)
    fim = timezone.now() + timedelta(days=30)
    _gateway(
        monkeypatch,
        {
            RetrieveSubscription: remote_subscription(
                reference_id=str(organizacao.pk),
                status=RemoteStatus.PAST_DUE,
                customer_id="cus_1",
                current_period_end=fim,
            ),
        },
    )

    Subscriptions.sync(organizacao.pk)

    estado = _estado(organizacao)
    assert estado.plan_id == pro.pk
    assert estado.status == SubscriptionStatus.PAST_DUE
    assert estado.has_access is True
    assert estado.customer_id == "cus_1"
    assert estado.current_period_end == fim
    assert estado.synced_at is not None


def test_sync_clears_pending_plan_when_applied(monkeypatch):
    organizacao = _organizacao()
    basico = _plano("basico", price="price_basico", rank=1)
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, basico, pending_plan=pro)
    _gateway(monkeypatch, {RetrieveSubscription: remote_subscription(reference_id=str(organizacao.pk))})

    Subscriptions.sync(organizacao.pk)

    estado = _estado(organizacao)
    assert estado.plan_id == pro.pk
    assert estado.pending_plan_id is None


def test_sync_terminal_resets_to_free(monkeypatch):
    organizacao = _organizacao()
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, pro, cancel_at=timezone.now() + timedelta(days=5))
    _gateway(
        monkeypatch,
        {RetrieveSubscription: remote_subscription(reference_id=str(organizacao.pk), status=RemoteStatus.CANCELED)},
    )

    Subscriptions.sync(organizacao.pk)

    estado = _estado(organizacao)
    assert estado.plan == Plan.get_free()
    assert estado.status == SubscriptionStatus.ACTIVE
    assert estado.external_id is None
    assert estado.cancel_at is None


def test_sync_unpaid_cancels_remote_and_resets(monkeypatch):
    organizacao = _organizacao()
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, pro)
    gateway = _gateway(
        monkeypatch,
        {
            RetrieveSubscription: remote_subscription(reference_id=str(organizacao.pk), status=RemoteStatus.UNPAID),
            CancelRemoteSubscription: remote_subscription(reference_id=str(organizacao.pk), status=RemoteStatus.CANCELED),
        },
    )

    Subscriptions.sync(organizacao.pk)

    cancelamentos = [comando for comando in gateway.commands if isinstance(comando, CancelRemoteSubscription)]
    assert len(cancelamentos) == 1
    assert cancelamentos[0].external_id == "sub_1"
    assert cancelamentos[0].timing == CancellationTiming.IMMEDIATELY
    assert cancelamentos[0].idempotency_key == "billing:abandon:sub_1"
    estado = _estado(organizacao)
    assert estado.plan == Plan.get_free()
    assert estado.external_id is None


def test_sync_unknown_price_keeps_plan_and_logs(monkeypatch, caplog):
    organizacao = _organizacao()
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, pro)
    _gateway(monkeypatch, {RetrieveSubscription: _remota_com_preco(organizacao, "price_desconhecido")})

    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        Subscriptions.sync(organizacao.pk)

    assert _estado(organizacao).plan_id == pro.pk
    assert any(registro.levelno == logging.ERROR and "price_desconhecido" in registro.getMessage() for registro in caplog.records)


def test_sync_rejects_reference_mismatch(monkeypatch):
    organizacao = _organizacao()
    pro = _plano("pro", price="price_pro", rank=2)
    _vincular(organizacao, pro)
    _gateway(monkeypatch, {RetrieveSubscription: remote_subscription(reference_id="999", status=RemoteStatus.CANCELED)})

    with pytest.raises(ReferenceMismatch):
        Subscriptions.sync(organizacao.pk)

    estado = _estado(organizacao)
    assert estado.plan_id == pro.pk
    assert estado.external_id == "sub_1"
    assert estado.status == SubscriptionStatus.ACTIVE


def test_sync_reads_remote_inside_lock(monkeypatch):
    organizacao = _organizacao()
    _vincular(organizacao, _plano("pro", price="price_pro", rank=2))
    sql_executado = []
    chamadas = []

    def registrar(execute, sql, params, many, context):
        sql_executado.append(sql)
        return execute(sql, params, many, context)

    def recuperar(_comando):
        chamadas.append(_comando)
        assert connection.in_atomic_block
        assert any("FOR UPDATE" in sql and "billing_subscription" in sql for sql in sql_executado)
        return remote_subscription(reference_id=str(organizacao.pk))

    _gateway(monkeypatch, {RetrieveSubscription: recuperar})

    with connection.execute_wrapper(registrar):
        Subscriptions.sync(organizacao.pk)

    assert len(chamadas) == 1


def test_sync_loss_of_access_suspends_members(monkeypatch):
    organizacao = _organizacao()
    pro = _plano("pro", price="price_pro", rank=2, limit=5)
    _vincular(organizacao, pro)
    _vinculos(organizacao, 5)
    _gateway(
        monkeypatch,
        {RetrieveSubscription: remote_subscription(reference_id=str(organizacao.pk), status=RemoteStatus.CANCELED)},
    )

    Subscriptions.sync(organizacao.pk)

    assert MemberLimits.active_count(organizacao.pk) == Plan.get_free().member_limit


def test_link_external_only_fills_empty_external_id(caplog):
    organizacao = _organizacao()
    Subscriptions.ensure_free(organizacao.pk)

    Subscriptions.link_external(organizacao.pk, external_id="sub_1", gateway_variant="stripe")
    with caplog.at_level(logging.ERROR, logger="apps.billing"):
        Subscriptions.link_external(organizacao.pk, external_id="sub_2", gateway_variant="stripe")

    estado = _estado(organizacao)
    assert estado.external_id == "sub_1"
    assert estado.gateway_variant == "stripe"
    assert any(registro.levelno == logging.ERROR for registro in caplog.records)
