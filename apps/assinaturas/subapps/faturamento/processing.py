"""Processamento e reconciliação de eventos financeiros.

O módulo mantém as fases de banco e gateway separadas: nenhuma função que
recebe um client executa I/O dentro de uma transação Django.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from celery import current_app
from django_checkouts.enums import InvoiceStatus, ResourceKind, RetryDisposition, SetupStatus, SubscriptionStatus
from django_checkouts.exceptions import CheckoutError
from prometheus_client import Counter, Histogram

from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.proposals import Propostas
from apps.assinaturas.subapps.faturamento.checkouts import ConflitoCheckout, decodificar_referencia_checkout
from apps.assinaturas.subapps.faturamento.events import EventosCobranca
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    CheckpointReconciliacao,
    ComponentePreco,
    EventoCobranca,
    FaturaAssinatura,
    FinalidadeCheckout,
    ReaberturaEventoCobranca,
    ReferenciaPrecoGateway,
    SolicitacaoReconciliacaoCobranca,
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
LEASE_RECONCILIACAO_OPERACIONAL = timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)
PROCESSAMENTOS = Counter("billing_event_processing_total", "Resultado finito do worker financeiro.", ("variante", "familia", "resultado"))
DURACAO_IO = Histogram("billing_gateway_operation_seconds", "Duração de I/O financeiro.", ("variante", "operacao"))
CLAIMS = Counter("billing_event_claim_total", "Resultado do claim financeiro.", ("variante", "resultado"))
RETRIES = Counter("billing_event_retry_total", "Destino do retry financeiro.", ("variante", "disposicao", "resultado"))
IDADE_FILA = Histogram("billing_event_queue_age_seconds", "Idade do evento ao ser reivindicado.", ("variante",))
RECONCILIACAO = Counter("billing_reconciliation_total", "Resultado finito da reconciliação.", ("variante", "resultado"))


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
    assinatura_pk: int
    assinatura_revisao: int
    checkout_pk: int | None
    mapping_pk: int | None
    gateway: str = ""


def calcular_backoff(tentativa: int, *, jitter: float = 0.0) -> timedelta:
    """Backoff determinístico, limitado a uma hora; jitter é injetável em teste."""
    segundos = min(30 * (2 ** max(tentativa - 1, 0)), 3600)
    segundos = min(int(segundos * (1 + max(-0.25, min(jitter, 0.25)))), 3600)
    return timedelta(seconds=max(segundos, 1))


def claim_evento(evento_id: int, organizacao_id: int, *, variante: str, agora: datetime | None = None) -> ClaimEvento | None:
    agora = agora or timezone.now()
    with organizacao_atual_privilegiada(organizacao_id):
        from apps.organizacoes.models import Organizacao

        Organizacao.all_objects.select_for_update().get(pk=organizacao_id)
        evento_base = EventoCobranca.objects.filter(pk=evento_id).first()
        if (
            evento_base is None
            or evento_base.organizacao_id != organizacao_id
            or evento_base.variante != variante
            or evento_base.status not in {StatusEventoCobranca.ROTEADO, StatusEventoCobranca.PROCESSANDO}
        ):
            return None
        familia = evento_base.tipo.split(".", 1)[0]
        checkout = None
        mapping = None
        if familia in {"checkout", "setup"}:
            checkout_ref = (
                CheckoutCobranca.objects.filter(variante=evento_base.variante, identificador_externo=evento_base.identificador_checkout)
                .only("pk", "assinatura_id")
                .first()
            )
            if checkout_ref is None:
                return None
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(pk=checkout_ref.assinatura_id)
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=checkout_ref.pk, assinatura_id=assinatura.pk)
        else:
            mapping_ref = (
                AssinaturaGateway.objects.filter(
                    variante=evento_base.variante,
                    identificador_externo=evento_base.identificador_assinatura,
                    organizacao_id=organizacao_id,
                )
                .only("pk", "assinatura_id")
                .first()
            )
            if mapping_ref is None:
                return None
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(pk=mapping_ref.assinatura_id)
            mapping = AssinaturaGateway.objects.select_for_update().get(pk=mapping_ref.pk, assinatura_id=assinatura.pk)
        evento = EventoCobranca.objects.select_for_update().get(pk=evento_id)
        if evento is None or evento.status in (StatusEventoCobranca.PROCESSADO, StatusEventoCobranca.IGNORADO):
            return None
        if evento.organizacao_id is None or evento.status == StatusEventoCobranca.RECEBIDO:
            return None
        if evento.status == StatusEventoCobranca.PROCESSANDO and evento.last_modified_at > agora - LEASE:
            return None
        if evento.proxima_tentativa_em and evento.proxima_tentativa_em > agora:
            return None
        if evento.tentativas_automaticas_ciclo >= MAX_TENTATIVAS:
            if evento.status != StatusEventoCobranca.FALHOU:
                evento.status = StatusEventoCobranca.FALHOU
                evento.erro = "retry_exhausted"
                evento.save(update_fields=["status", "erro", "last_modified_at"])
            return None
        evento.status = StatusEventoCobranca.PROCESSANDO
        evento.tentativas_processamento += 1
        evento.tentativas_automaticas_ciclo += 1
        evento.proxima_tentativa_em = agora + LEASE
        evento.recuperacao_arrendada_ate = None
        evento.erro = ""
        evento.save(
            update_fields=[
                "status",
                "tentativas_processamento",
                "tentativas_automaticas_ciclo",
                "proxima_tentativa_em",
                "recuperacao_arrendada_ate",
                "erro",
                "last_modified_at",
            ]
        )
        CLAIMS.labels(evento.variante, "claimed").inc()
        if evento.ocorrido_em is not None:
            IDADE_FILA.labels(evento.variante).observe(max((agora - evento.ocorrido_em).total_seconds(), 0))
        return ClaimEvento(
            evento.pk,
            organizacao_id,
            evento.variante,
            evento.tipo,
            evento.identificador_checkout,
            evento.identificador_assinatura,
            evento.identificador_fatura,
            evento.tentativas_processamento,
            assinatura.pk,
            assinatura.revisao,
            checkout.pk if checkout else None,
            mapping.pk if mapping else None,
            str(import_string(settings.CHECKOUT_VARIANTS[evento.variante][0]).name),
        )


def recuperar_remoto(claim: ClaimEvento, client: CheckoutClient):
    if connection.in_atomic_block:
        raise RuntimeError("I/O de faturamento não pode ocorrer dentro de transação.")
    if client.variant != claim.variante or str(client.gateway) != claim.gateway:
        raise ValueError("remote_client_identity_mismatch")
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
    familia = claim.tipo.split(".", 1)[0]
    esperado_kind = {
        "checkout": ResourceKind.CHECKOUT,
        "setup": ResourceKind.SETUP,
        "subscription": ResourceKind.SUBSCRIPTION,
        "invoice": ResourceKind.INVOICE,
    }.get(familia)
    if kind != esperado_kind:
        raise ValueError("remote_kind_mismatch")
    recurso = remoto[0] if kind == ResourceKind.INVOICE else remoto
    esperado = {
        ResourceKind.CHECKOUT: claim.checkout_id,
        ResourceKind.SETUP: claim.checkout_id,
        ResourceKind.SUBSCRIPTION: claim.assinatura_id,
        ResourceKind.INVOICE: claim.fatura_id,
    }[kind]
    if recurso.variant != claim.variante or recurso.external_id != esperado or (claim.gateway and str(recurso.gateway) != claim.gateway):
        raise ValueError("remote_identity_mismatch")
    if kind == ResourceKind.INVOICE:
        invoice, subscription = remoto
        if invoice.subscription_id != claim.assinatura_id:
            raise ValueError("remote_invoice_subscription_mismatch")
        referencias = tuple(
            referencia
            for referencia in (getattr(invoice, "reference_id", None), getattr(subscription, "reference_id", None) if subscription else None)
            if referencia is not None
        )
        try:
            for referencia in referencias:
                decodificar_referencia_checkout(referencia, organizacao_id=claim.organizacao_id)
        except ConflitoCheckout as exc:
            raise ValueError("remote_invoice_reference_tenant_mismatch") from exc
        if len(referencias) == 2 and referencias[0] != referencias[1]:
            raise ValueError("remote_invoice_subscription_mismatch")
        if subscription is not None:
            if (
                subscription.variant != claim.variante
                or subscription.external_id != claim.assinatura_id
                or (claim.gateway and str(subscription.gateway) != claim.gateway)
            ):
                raise ValueError("remote_invoice_subscription_mismatch")


def finalizar_evento(claim: ClaimEvento, kind, remoto, *, agora: datetime | None = None) -> bool:
    agora = agora or timezone.now()
    _validar_identidade(claim, kind, remoto)
    with organizacao_atual_privilegiada(claim.organizacao_id):
        from apps.organizacoes.models import Organizacao

        Organizacao.all_objects.select_for_update().get(pk=claim.organizacao_id)
        assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(pk=claim.assinatura_pk, organizacao_id=claim.organizacao_id)
        checkout = None
        if claim.checkout_pk is not None:
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=claim.checkout_pk, assinatura_id=assinatura.pk)
        mapping = None
        if claim.mapping_pk is not None:
            mapping = AssinaturaGateway.objects.select_for_update().get(pk=claim.mapping_pk, assinatura_id=assinatura.pk)
            if mapping.variante != claim.variante or mapping.identificador_externo != claim.assinatura_id:
                return False
        evento = EventoCobranca.objects.select_for_update().get(pk=claim.evento_id)
        if evento.status != StatusEventoCobranca.PROCESSANDO or evento.tentativas_processamento != claim.tentativa:
            return False
        if assinatura.revisao != claim.assinatura_revisao and claim.tipo.split(".", 1)[0] in {"checkout", "setup"}:
            return False
        if kind in (ResourceKind.CHECKOUT, ResourceKind.SETUP):
            assert checkout is not None
            assinatura = _aplicar_checkout(evento, assinatura, checkout, remoto, setup=kind == ResourceKind.SETUP, agora=agora)
        elif kind == ResourceKind.SUBSCRIPTION:
            assert mapping is not None
            _aplicar_assinatura(assinatura, mapping, remoto, agora=agora)
        elif kind == ResourceKind.INVOICE:
            invoice, subscription = remoto
            _aplicar_fatura(assinatura, invoice, agora=agora)
            if subscription is not None:
                assert mapping is not None
                _aplicar_assinatura(assinatura, mapping, subscription, agora=agora)
        evento.status = StatusEventoCobranca.PROCESSADO
        evento.processado_em = agora
        evento.proxima_tentativa_em = None
        evento.erro = ""
        evento.save(update_fields=["status", "processado_em", "proxima_tentativa_em", "erro", "last_modified_at"])
    return True


def _aplicar_checkout(evento, assinatura, checkout, remoto, *, setup: bool, agora):
    finalidades_checkout = {FinalidadeCheckout.CONTRATACAO, FinalidadeCheckout.ALTERACAO, FinalidadeCheckout.PROPOSTA}
    if (setup and checkout.finalidade != FinalidadeCheckout.FORMA_PAGAMENTO) or (not setup and checkout.finalidade not in finalidades_checkout):
        raise ValueError("remote_checkout_purpose_mismatch")
    if not remoto.reference_id:
        raise ValueError("remote_reference_missing")
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
            historico = assinatura.status == StatusAssinatura.ENCERRADA
            if historico:
                pass
            elif checkout.alteracao_id:
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
            if remoto.subscription_id and not historico:
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


def _aplicar_assinatura(assinatura, mapping, remoto: Subscription, *, agora):
    if mapping.identificador_externo != remoto.external_id or mapping.variante != remoto.variant:
        raise ValueError("remote_subscription_mapping_mismatch")
    if remoto.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
        mapping.is_active = False
        mapping.save(update_fields=["is_active", "last_modified_at"])
        Assinaturas.encerrar_por_gateway_bloqueado(assinatura, encerrada_em=agora)
        return
    if assinatura.status == StatusAssinatura.ENCERRADA:
        return
    _validar_itens_assinatura(assinatura, remoto)
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


def _validar_itens_assinatura(assinatura: AssinaturaOrganizacao, remoto: Subscription) -> None:
    versao_plano = assinatura.versao_plano
    preco = versao_plano.precos.filter(periodicidade=assinatura.periodicidade, moeda=assinatura.moeda).first() if versao_plano is not None else None
    esperados = {}
    if assinatura.valor_base_centavos > 0:
        esperados[ComponentePreco.BASE] = (1, assinatura.valor_base_centavos)
    seats = max(assinatura.seats_contratados - assinatura.seats_inclusos, 0)
    if assinatura.valor_seat_centavos > 0 and seats > 0:
        esperados[ComponentePreco.SEAT] = (seats, assinatura.valor_seat_centavos)
    observados = {}
    for item in remoto.items:
        componente = None
        if preco is not None and item.price_id:
            referencia = ReferenciaPrecoGateway.objects.filter(
                variante=remoto.variant,
                identificador_externo=item.price_id,
                preco_plano=preco,
                is_active=True,
                is_deleted=False,
            ).first()
            componente = referencia.componente if referencia else None
        elif preco is None:
            candidatos = [c for c, (_, valor) in esperados.items() if valor == item.unit_amount and item.currency == assinatura.moeda]
            componente = candidatos[0] if len(candidatos) == 1 else None
        if componente is None or componente in observados:
            raise ValueError("remote_subscription_price_mismatch")
        observados[componente] = (item.quantity, item.unit_amount)
    if set(observados) != set(esperados):
        raise ValueError("remote_subscription_items_mismatch")
    for componente, (quantidade, valor) in esperados.items():
        item_quantidade, item_valor = observados[componente]
        if item_quantidade != quantidade or (item_valor is not None and item_valor != valor):
            raise ValueError("remote_subscription_quantity_mismatch")


def _aplicar_fatura(assinatura, invoice: Invoice, *, agora):
    if invoice.currency != assinatura.moeda or any(linha.currency != invoice.currency for linha in invoice.lines):
        raise ValueError("remote_invoice_currency_mismatch")
    if min(invoice.amount_due, invoice.amount_paid, invoice.amount_remaining) < 0:
        raise ValueError("remote_invoice_amount_invalid")
    if invoice.amount_paid + invoice.amount_remaining != invoice.amount_due:
        raise ValueError("remote_invoice_amount_inconsistent")
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
    # `None` preserva indisponibilidade; zero continua significando fato explícito.
    fatura.subtotal_centavos = invoice.subtotal
    fatura.desconto_centavos = invoice.discount_total
    fatura.imposto_centavos = invoice.tax_total
    fatura.total_centavos = invoice.total
    fatura.moeda = invoice.currency
    fatura.vencimento_em, fatura.paga_em = invoice.due_at, invoice.paid_at
    inicios = [linha.period_start for linha in invoice.lines if linha.period_start is not None]
    fins = [linha.period_end for linha in invoice.lines if linha.period_end is not None]
    fatura.periodo_iniciado_em = min(inicios) if inicios else None
    fatura.periodo_termina_em = max(fins) if fins else None
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
    advice = exc.retry_advice if isinstance(exc, CheckoutError) else None
    retry = advice is not None and advice.disposition in {RetryDisposition.RETRY, RetryDisposition.RETRY_SAME_KEY}
    with organizacao_atual_privilegiada(claim.organizacao_id):
        evento = EventoCobranca.objects.select_for_update().get(pk=claim.evento_id)
        if evento.status != StatusEventoCobranca.PROCESSANDO or evento.tentativas_processamento != claim.tentativa:
            return
        esgotou = evento.tentativas_automaticas_ciclo >= MAX_TENTATIVAS
        reconciliar = bool(advice is not None and advice.disposition == RetryDisposition.RECONCILE_FIRST and not esgotou)
        evento.status = StatusEventoCobranca.ROTEADO if retry and not esgotou else StatusEventoCobranca.FALHOU
        evento.aguarda_reconciliacao = reconciliar
        espera = (
            advice.retry_after if advice is not None and advice.retry_after is not None else calcular_backoff(evento.tentativas_automaticas_ciclo)
        )
        evento.proxima_tentativa_em = agora + espera if retry and not esgotou else None
        evento.erro = "gateway_temporary" if retry else "processing_permanent"
        evento.save(update_fields=["status", "aguarda_reconciliacao", "proxima_tentativa_em", "erro", "last_modified_at"])
        disposicao = advice.disposition.value if advice is not None else "unknown"
        RETRIES.labels(evento.variante, disposicao, "scheduled" if evento.status == StatusEventoCobranca.ROTEADO else "terminal").inc()
        if reconciliar:
            transaction.on_commit(
                lambda: current_app.send_task(
                    "faturamento.reconciliar_evento_cobranca",
                    args=(evento.pk, evento.variante, claim.organizacao_id),
                )
            )


def reabrir_evento_operacional(*, evento: EventoCobranca, ator, motivo: str, chave_idempotencia: str) -> EventoCobranca:
    """Reabre terminal explicitamente sem apagar o total histórico de tentativas."""
    motivo = motivo.strip()
    organizacao_id = evento.organizacao_id
    if not motivo or not chave_idempotencia.strip() or not getattr(ator, "pk", None) or organizacao_id is None:
        raise ValueError("Retry operacional exige tenant, ator, motivo e chave idempotente.")
    with organizacao_atual_privilegiada(organizacao_id):
        from apps.organizacoes.models import Organizacao

        Organizacao.all_objects.select_for_update().get(pk=organizacao_id)
        bloqueado = EventoCobranca.objects.select_for_update().get(pk=evento.pk)
        existente = ReaberturaEventoCobranca.objects.filter(chave_idempotencia=chave_idempotencia).first()
        if existente is not None:
            if existente.evento_id != bloqueado.pk:
                raise ValueError("Chave operacional já usada em outro evento.")
            return bloqueado
        if bloqueado.status != StatusEventoCobranca.FALHOU:
            raise ValueError("Somente evento terminal falho pode ser reaberto.")
        ReaberturaEventoCobranca.objects.create(
            organizacao_id=organizacao_id,
            evento=bloqueado,
            motivo=motivo,
            ator=ator,
            chave_idempotencia=chave_idempotencia,
            tentativas_anteriores=bloqueado.tentativas_processamento,
        )
        bloqueado.status = StatusEventoCobranca.ROTEADO
        bloqueado.aguarda_reconciliacao = False
        bloqueado.tentativas_automaticas_ciclo = 0
        bloqueado.proxima_tentativa_em = timezone.now()
        bloqueado.erro = ""
        bloqueado.save(
            update_fields=[
                "status",
                "aguarda_reconciliacao",
                "tentativas_automaticas_ciclo",
                "proxima_tentativa_em",
                "erro",
                "last_modified_at",
            ]
        )
        transaction.on_commit(
            lambda: current_app.send_task("faturamento.processar_evento_cobranca", args=(bloqueado.pk, bloqueado.variante, bloqueado.organizacao_id))
        )
        return bloqueado


def reabrir_evento_reconciliado(*, evento_id: int, organizacao_id: int) -> EventoCobranca | None:
    """Conclui RECONCILE_FIRST idempotentemente sob o tenant já descoberto."""
    with organizacao_atual_privilegiada(organizacao_id):
        from apps.organizacoes.models import Organizacao

        Organizacao.all_objects.select_for_update().get(pk=organizacao_id)
        evento = EventoCobranca.objects.select_for_update().filter(pk=evento_id, organizacao_id=organizacao_id).first()
        if evento is None:
            return None
        chave = f"system:reconcile:{evento.pk}:{evento.tentativas_processamento}"
        auditoria = ReaberturaEventoCobranca.objects.filter(chave_idempotencia=chave).first()
        reaberto = evento.aguarda_reconciliacao
        if reaberto:
            if auditoria is None:
                ReaberturaEventoCobranca.objects.create(
                    organizacao_id=organizacao_id,
                    evento=evento,
                    motivo="Reconciliação remota conclusiva.",
                    ator=None,
                    automatica=True,
                    chave_idempotencia=chave,
                    tentativas_anteriores=evento.tentativas_processamento,
                )
            evento.status = StatusEventoCobranca.ROTEADO
            evento.aguarda_reconciliacao = False
            evento.proxima_tentativa_em = timezone.now()
            evento.erro = ""
            evento.save(
                update_fields=[
                    "status",
                    "aguarda_reconciliacao",
                    "proxima_tentativa_em",
                    "erro",
                    "last_modified_at",
                ]
            )
        elif auditoria is None or evento.status != StatusEventoCobranca.ROTEADO:
            return None
        if reaberto:
            transaction.on_commit(
                lambda: current_app.send_task("faturamento.processar_evento_cobranca", args=(evento.pk, evento.variante, organizacao_id))
            )
        return evento


def solicitar_reconciliacao_operacional(
    *, evento: EventoCobranca, ator, motivo: str, chave_idempotencia: str, agora: datetime | None = None, janela: timedelta = timedelta(minutes=20)
) -> SolicitacaoReconciliacaoCobranca:
    """Agenda reconciliação uma vez e persiste todos os parâmetros operacionais."""
    agora = agora or timezone.now()
    if not motivo.strip() or not chave_idempotencia.strip():
        raise ValueError("motivo e chave de idempotência são obrigatórios")
    organizacao_id = evento.organizacao_id
    if organizacao_id is None:
        raise ValueError("evento sem organização não pode ser reconciliado operacionalmente")
    with transaction.atomic(), organizacao_atual_privilegiada(organizacao_id):
        solicitacao, criada = SolicitacaoReconciliacaoCobranca.objects.select_for_update().get_or_create(
            organizacao_id=organizacao_id,
            chave_idempotencia=chave_idempotencia,
            defaults={
                "variante": evento.variante,
                "ator": ator,
                "motivo": motivo.strip(),
                "janela_inicio": agora - janela,
                "janela_fim": agora,
                "parametros": {"evento_id": evento.pk, "janela_segundos": int(janela.total_seconds())},
                "resultado": "agendada",
            },
        )
        recuperavel = not criada and (
            solicitacao.resultado == "falhou"
            or (solicitacao.resultado == "executando" and solicitacao.last_modified_at <= agora - LEASE_RECONCILIACAO_OPERACIONAL)
        )
        if recuperavel:
            solicitacao.resultado = "agendada"
            solicitacao.save(update_fields=["resultado", "last_modified_at"])
        if criada or recuperavel:
            tarefa = {"stripe": "faturamento.reconciliar_eventos_stripe"}.get(evento.variante)
            if tarefa is None:
                solicitacao.resultado = "variante_ignorada"
                solicitacao.save(update_fields=["resultado", "last_modified_at"])
            else:
                transaction.on_commit(
                    lambda: current_app.send_task(
                        "faturamento.executar_reconciliacao_operacional",
                        args=(solicitacao.pk, organizacao_id),
                    )
                )
        return solicitacao


def _destino_reconcile(evento_id: int) -> int | None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
        cursor.execute("SELECT set_config('rls.tenant_id','0',true)")
        cursor.execute("SELECT set_config('rls.billing_ingress','1',true)")
        cursor.execute("SELECT faturamento_destino_reconcile(%s)", [evento_id])
        linha = cursor.fetchone()
    return linha[0] if linha and linha[0] is not None else None


def processar_evento(evento_id: int, organizacao_id: int, variante: str, *, client: CheckoutClient) -> bool:
    claim = claim_evento(evento_id, organizacao_id, variante=variante)
    if claim is None:
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
        PROCESSAMENTOS.labels(variante, claim.tipo.split(".", 1)[0], "falhou").inc()
        raise
    PROCESSAMENTOS.labels(variante, claim.tipo.split(".", 1)[0], "processado" if resultado else "ignorado").inc()
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


def reconciliar_duravel(
    *,
    variante: str,
    client: CheckoutClient,
    agora: datetime | None = None,
    janela: timedelta = timedelta(minutes=20),
    overlap: timedelta = timedelta(minutes=5),
    limite: int = 100,
    receber=None,
) -> int:
    """Processa páginas com checkpoint após a ingestão idempotente de cada página."""
    agora = agora or timezone.now()
    receber = receber or EventosCobranca().receber
    with transaction.atomic():
        checkpoint, _ = CheckpointReconciliacao.objects.select_for_update().get_or_create(
            variante=variante,
            defaults={"janela_inicio": agora - janela, "janela_fim": agora},
        )
        if checkpoint.lease_ate and checkpoint.lease_ate > agora:
            return 0
        checkpoint.lease_ate = agora + LEASE
        checkpoint.revisao += 1
        checkpoint.save(update_fields=["lease_ate", "revisao", "last_modified_at"])
        inicio, fim, cursor = checkpoint.janela_inicio, checkpoint.janela_fim, checkpoint.cursor or None
    if connection.in_atomic_block:
        raise RuntimeError("Reconciliação remota não pode executar dentro de transação.")
    pagina = client.events.list(occurred_since=inicio, occurred_before=fim, cursor=cursor, limit=limite)
    total = 0
    for evento in pagina.items:
        try:
            resultado = receber(variante, evento, client=client)
            evento_id = getattr(resultado, "evento_id", None)
            if evento_id is not None:
                destino = _destino_reconcile(evento_id)
                if destino is not None:
                    reabrir_evento_reconciliado(evento_id=evento_id, organizacao_id=destino)
        except Exception:
            RECONCILIACAO.labels(variante, "falhou").inc()
            raise
        total += 1
    with transaction.atomic():
        checkpoint = CheckpointReconciliacao.objects.select_for_update().get(variante=variante)
        if checkpoint.janela_inicio != inicio or checkpoint.janela_fim != fim or (checkpoint.cursor or None) != cursor:
            return total
        if pagina.next_cursor:
            checkpoint.cursor = pagina.next_cursor
        else:
            checkpoint.ultimo_limite_concluido = fim
            checkpoint.janela_inicio = fim - overlap
            checkpoint.janela_fim = max(agora, fim + timedelta(microseconds=1))
            checkpoint.cursor = ""
        checkpoint.lease_ate = None
        checkpoint.revisao += 1
        checkpoint.save()
    RECONCILIACAO.labels(variante, "pagina" if pagina.next_cursor else "janela_concluida").inc()
    return total
