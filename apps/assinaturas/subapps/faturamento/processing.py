"""Processamento e reconciliação de eventos financeiros.

O módulo mantém as fases de banco e gateway separadas: nenhuma função que
recebe um client executa I/O dentro de uma transação Django.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import connection
from django.utils import timezone

from django_checkouts.enums import InvoiceStatus, ResourceKind, RetryDisposition, SetupStatus, SubscriptionStatus
from django_checkouts.exceptions import CheckoutError
from prometheus_client import Counter, Histogram

from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.proposals import Propostas
from apps.assinaturas.subapps.faturamento.checkouts import decodificar_referencia_checkout
from apps.assinaturas.subapps.faturamento.events import EventosCobranca
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    EventoCobranca,
    FaturaAssinatura,
    StatusCheckout,
    StatusEventoCobranca,
    StatusFatura,
)
from apps.assinaturas.subscriptions import (
    Assinaturas,
    FallbackTrialGratuito,
    MotivoFallbackTrial,
    PagamentoTrialConfirmado,
)
from apps.organizacoes.context import organizacao_atual_privilegiada

if TYPE_CHECKING:
    from datetime import datetime

    from django_checkouts.client import CheckoutClient
    from django_checkouts.types import Invoice, Subscription


MAX_TENTATIVAS = 8
LEASE = timedelta(minutes=15)
PROCESSAMENTOS = Counter("billing_event_processing_total", "Resultado finito do worker financeiro.", ("variante", "familia", "resultado"))
DURACAO_IO = Histogram("billing_gateway_operation_seconds", "Duração de I/O financeiro.", ("variante", "operacao"))


@dataclass(frozen=True, slots=True)
class ClaimEvento:
    evento_id: int
    organizacao_id: int
    variante: str
    tipo: str
    checkout_id: str
    assinatura_id: str
    fatura_id: str
    tentativa: int


def calcular_backoff(tentativa: int, *, jitter: float = 0.0) -> timedelta:
    """Backoff determinístico, limitado a uma hora; jitter é injetável em teste."""
    segundos = min(30 * (2 ** max(tentativa - 1, 0)), 3600)
    segundos = min(int(segundos * (1 + max(-0.25, min(jitter, 0.25)))), 3600)
    return timedelta(seconds=max(segundos, 1))


def claim_evento(evento_id: int, organizacao_id: int, *, agora: datetime | None = None) -> ClaimEvento | None:
    agora = agora or timezone.now()
    with organizacao_atual_privilegiada(organizacao_id):
        evento = EventoCobranca.objects.select_for_update().filter(pk=evento_id).first()
        if evento is None or evento.status in (StatusEventoCobranca.PROCESSADO, StatusEventoCobranca.IGNORADO):
            return None
        if evento.organizacao_id is None or evento.status == StatusEventoCobranca.RECEBIDO:
            return None
        if evento.status == StatusEventoCobranca.PROCESSANDO and evento.last_modified_at > agora - LEASE:
            return None
        if evento.proxima_tentativa_em and evento.proxima_tentativa_em > agora:
            return None
        if evento.tentativas_processamento >= MAX_TENTATIVAS:
            if evento.status != StatusEventoCobranca.FALHOU:
                evento.status = StatusEventoCobranca.FALHOU
                evento.erro = "retry_exhausted"
                evento.save(update_fields=["status", "erro", "last_modified_at"])
            return None
        evento.status = StatusEventoCobranca.PROCESSANDO
        evento.tentativas_processamento += 1
        evento.proxima_tentativa_em = agora + LEASE
        evento.erro = ""
        evento.save(update_fields=["status", "tentativas_processamento", "proxima_tentativa_em", "erro", "last_modified_at"])
        return ClaimEvento(
            evento.pk,
            organizacao_id,
            evento.variante,
            evento.tipo,
            evento.identificador_checkout,
            evento.identificador_assinatura,
            evento.identificador_fatura,
            evento.tentativas_processamento,
        )


def recuperar_remoto(claim: ClaimEvento, client: CheckoutClient):
    if connection.in_atomic_block:
        raise RuntimeError("I/O de faturamento não pode ocorrer dentro de transação.")
    familia = claim.tipo.split(".", 1)[0]
    with DURACAO_IO.labels(claim.variante, f"{familia}.retrieve").time():
        if familia == "checkout":
            return ResourceKind.CHECKOUT, client.checkouts.retrieve(claim.checkout_id)
        if familia == "setup":
            return ResourceKind.SETUP, client.setups.retrieve(claim.checkout_id)
        if familia == "subscription":
            return ResourceKind.SUBSCRIPTION, client.subscriptions.retrieve(claim.assinatura_id)
        if familia == "invoice":
            invoice = client.invoices.retrieve(claim.fatura_id)
            subscription = client.subscriptions.retrieve(invoice.subscription_id) if invoice.subscription_id else None
            return ResourceKind.INVOICE, (invoice, subscription)
    return None, None


def _validar_identidade(claim: ClaimEvento, kind, remoto) -> None:
    recurso = remoto[0] if kind == ResourceKind.INVOICE else remoto
    esperado = {
        ResourceKind.CHECKOUT: claim.checkout_id,
        ResourceKind.SETUP: claim.checkout_id,
        ResourceKind.SUBSCRIPTION: claim.assinatura_id,
        ResourceKind.INVOICE: claim.fatura_id,
    }[kind]
    if recurso.variant != claim.variante or recurso.external_id != esperado:
        raise ValueError("remote_identity_mismatch")


def finalizar_evento(claim: ClaimEvento, kind, remoto, *, agora: datetime | None = None) -> bool:
    agora = agora or timezone.now()
    _validar_identidade(claim, kind, remoto)
    with organizacao_atual_privilegiada(claim.organizacao_id):
        evento = EventoCobranca.objects.select_for_update().get(pk=claim.evento_id)
        if evento.status != StatusEventoCobranca.PROCESSANDO or evento.tentativas_processamento != claim.tentativa:
            return False
        assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(
            organizacao_id=claim.organizacao_id, status__in=(StatusAssinatura.PENDENTE, StatusAssinatura.EM_TRIAL, StatusAssinatura.ATIVA)
        )
        if kind in (ResourceKind.CHECKOUT, ResourceKind.SETUP):
            assinatura = _aplicar_checkout(evento, assinatura, remoto, setup=kind == ResourceKind.SETUP, agora=agora)
        elif kind == ResourceKind.SUBSCRIPTION:
            _aplicar_assinatura(assinatura, remoto, agora=agora)
        elif kind == ResourceKind.INVOICE:
            invoice, subscription = remoto
            _aplicar_fatura(assinatura, invoice, agora=agora)
            if subscription is not None:
                _aplicar_assinatura(assinatura, subscription, agora=agora)
        evento.status = StatusEventoCobranca.PROCESSADO
        evento.processado_em = agora
        evento.proxima_tentativa_em = None
        evento.erro = ""
        evento.save(update_fields=["status", "processado_em", "proxima_tentativa_em", "erro", "last_modified_at"])
    return True


def _aplicar_checkout(evento, assinatura, remoto, *, setup: bool, agora):
    checkout = CheckoutCobranca.objects.select_for_update().get(variante=evento.variante, identificador_externo=remoto.external_id)
    if remoto.reference_id:
        local_id = decodificar_referencia_checkout(remoto.reference_id, organizacao_id=checkout.organizacao_id)
        if local_id != checkout.pk:
            raise ValueError("remote_reference_mismatch")
    if setup:
        if remoto.status == SetupStatus.COMPLETE:
            checkout.status, checkout.concluido_em = StatusCheckout.CONCLUIDO, agora
        elif remoto.status == SetupStatus.EXPIRED:
            checkout.status = StatusCheckout.EXPIRADO
    else:
        if remoto.amount_total != checkout.valor_esperado_centavos or remoto.currency != checkout.moeda_esperada:
            raise ValueError("remote_amount_mismatch")
        if str(remoto.mode) != "subscription":
            raise ValueError("remote_mode_mismatch")
        if str(remoto.status) == "paid":
            checkout.status, checkout.concluido_em = StatusCheckout.CONCLUIDO, agora
            if checkout.alteracao_id:
                Assinaturas.confirmar_alteracao(
                    checkout.alteracao,
                    evento_gateway=evento.identificador_evento,
                    agora=agora,
                    permitir_evento_atrasado=True,
                )
            elif checkout.proposta_id:
                assinatura = Propostas.ativar_pagamento_confirmado(
                    checkout.proposta,
                    revisao_esperada=checkout.proposta.revisao,
                    evento_gateway=evento.identificador_evento,
                    agora=agora,
                )
            elif assinatura.status == StatusAssinatura.PENDENTE:
                assinatura = Assinaturas.confirmar_contratacao_paga(assinatura, agora=agora)
            if remoto.subscription_id:
                AssinaturaGateway.objects.update_or_create(
                    assinatura=assinatura,
                    defaults={
                        "organizacao_id": assinatura.organizacao_id,
                        "variante": evento.variante,
                        "identificador_externo": remoto.subscription_id,
                        "is_active": True,
                        "is_deleted": False,
                    },
                )
        elif str(remoto.status) == "expired":
            checkout.status = StatusCheckout.EXPIRADO
        elif str(remoto.status) == "canceled":
            checkout.status = StatusCheckout.CANCELADO
        elif str(remoto.status) == "failed":
            checkout.status = StatusCheckout.FALHOU
    checkout.save(update_fields=["status", "concluido_em", "last_modified_at"])
    return assinatura


def _aplicar_assinatura(assinatura, remoto: Subscription, *, agora):
    mapping, _ = AssinaturaGateway.objects.update_or_create(
        assinatura=assinatura,
        defaults={"organizacao_id": assinatura.organizacao_id, "variante": remoto.variant, "identificador_externo": remoto.external_id},
    )
    if remoto.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
        mapping.is_active = False
        mapping.save(update_fields=["is_active", "last_modified_at"])
        return
    if (
        assinatura.status == StatusAssinatura.EM_TRIAL
        and remoto.status == SubscriptionStatus.ACTIVE
        and remoto.current_period_start is not None
        and remoto.current_period_end is not None
    ):
        assinatura = Assinaturas.encerrar_trial(
            assinatura,
            resultado=PagamentoTrialConfirmado(
                seats_contratados=assinatura.seats_contratados,
                periodo_iniciado_em=remoto.current_period_start,
                periodo_termina_em=remoto.current_period_end,
            ),
            agora=agora,
        ).assinatura
    financeiro = None
    if remoto.status == SubscriptionStatus.ACTIVE:
        financeiro = StatusFinanceiro.REGULAR
    elif remoto.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID):
        financeiro = StatusFinanceiro.INADIMPLENTE
    Assinaturas.sincronizar_estado_gateway(
        assinatura,
        status_financeiro=financeiro,
        periodo_iniciado_em=remoto.current_period_start,
        periodo_termina_em=remoto.current_period_end,
        cancelamento_agendado_para=remoto.cancel_at,
        agora=agora,
    )


def _aplicar_fatura(assinatura, invoice: Invoice, *, agora):
    mapa = {
        InvoiceStatus.DRAFT: StatusFatura.ABERTA,
        InvoiceStatus.OPEN: StatusFatura.VENCIDA if invoice.due_at and invoice.due_at < agora else StatusFatura.ABERTA,
        InvoiceStatus.PAID: StatusFatura.PAGA,
        InvoiceStatus.VOID: StatusFatura.ANULADA,
        InvoiceStatus.UNCOLLECTIBLE: StatusFatura.IRRECUPERAVEL,
    }
    fatura, _ = FaturaAssinatura.objects.select_for_update().get_or_create(
        organizacao_id=assinatura.organizacao_id,
        assinatura=assinatura,
        variante=invoice.variant,
        identificador_externo=invoice.external_id,
        defaults={"status": mapa[invoice.status], "moeda": invoice.currency},
    )
    if fatura.status == StatusFatura.PAGA and invoice.status != InvoiceStatus.PAID:
        return
    fatura.status = mapa[invoice.status]
    fatura.motivo = str(invoice.reason)
    fatura.total_centavos = invoice.amount_due
    fatura.subtotal_centavos = invoice.amount_due
    fatura.moeda = invoice.currency
    fatura.vencimento_em, fatura.paga_em = invoice.due_at, invoice.paid_at
    fatura.proxima_tentativa_em, fatura.tentativas = invoice.next_payment_attempt_at, invoice.attempt_count
    fatura.url_hospedada = invoice.hosted_url or ""
    fatura.save()
    if invoice.status == InvoiceStatus.PAID:
        Assinaturas.registrar_pagamento_confirmado(assinatura, agora=agora)
    elif invoice.status == InvoiceStatus.OPEN and invoice.attempt_count > 0:
        if assinatura.status == StatusAssinatura.EM_TRIAL:
            Assinaturas.encerrar_trial(
                assinatura,
                resultado=FallbackTrialGratuito(MotivoFallbackTrial.PRIMEIRA_COBRANCA_FALHOU),
                agora=agora,
            )
        else:
            Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)


def registrar_falha(claim: ClaimEvento, exc: Exception, *, agora: datetime | None = None) -> None:
    agora = agora or timezone.now()
    retry = isinstance(exc, CheckoutError) and exc.retry_advice.disposition != RetryDisposition.NEVER
    with organizacao_atual_privilegiada(claim.organizacao_id):
        evento = EventoCobranca.objects.select_for_update().get(pk=claim.evento_id)
        if evento.status != StatusEventoCobranca.PROCESSANDO or evento.tentativas_processamento != claim.tentativa:
            return
        esgotou = claim.tentativa >= MAX_TENTATIVAS
        evento.status = StatusEventoCobranca.ROTEADO if retry and not esgotou else StatusEventoCobranca.FALHOU
        evento.proxima_tentativa_em = agora + calcular_backoff(claim.tentativa) if retry and not esgotou else None
        evento.erro = "gateway_temporary" if retry else "processing_permanent"
        evento.save(update_fields=["status", "proxima_tentativa_em", "erro", "last_modified_at"])


def processar_evento(evento_id: int, organizacao_id: int, variante: str, *, client: CheckoutClient) -> bool:
    claim = claim_evento(evento_id, organizacao_id)
    if claim is None or claim.variante != variante:
        return False
    try:
        kind, remoto = recuperar_remoto(claim, client)
        if kind is None:
            with organizacao_atual_privilegiada(organizacao_id):
                evento = EventoCobranca.objects.select_for_update().get(pk=evento_id)
                evento.status, evento.processado_em = StatusEventoCobranca.IGNORADO, timezone.now()
                evento.save()
            return True
        resultado = finalizar_evento(claim, kind, remoto)
    except Exception as exc:
        registrar_falha(claim, exc)
        raise
    PROCESSAMENTOS.labels(variante, claim.tipo.split(".", 1)[0], "processado").inc()
    return resultado


def reconciliar_janela(*, variante: str, inicio: datetime, fim: datetime, client: CheckoutClient, receber=None, limite: int = 100) -> int:
    if inicio >= fim:
        raise ValueError("A janela de reconciliação deve ser semiaberta e não vazia.")
    receber = receber or EventosCobranca().receber
    cursor = None
    total = 0
    while True:
        if connection.in_atomic_block:
            raise RuntimeError("Reconciliação remota não pode executar dentro de transação.")
        pagina = client.events.list(occurred_since=inicio, occurred_before=fim, cursor=cursor, limit=limite)
        for evento in pagina.items:
            receber(variante, evento, client=client)
            total += 1
        cursor = pagina.next_cursor
        if cursor is None:
            return total
