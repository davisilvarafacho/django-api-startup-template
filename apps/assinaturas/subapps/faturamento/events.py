from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from celery import current_app
from django_checkouts import get_checkout_gateway
from django_checkouts.enums import CheckoutStatus, EventType, InvoiceStatus, ResourceKind, SetupStatus, SubscriptionStatus
from django_checkouts.exceptions import ConfigurationError, GatewayProtocolError, WebhookVerificationError
from prometheus_client import Counter

from apps.assinaturas.subapps.faturamento.checkouts import SALT_REFERENCIA, decodificar_referencia_checkout
from apps.assinaturas.subapps.faturamento.models import AssinaturaGateway, CheckoutCobranca, FinalidadeCheckout, StatusEventoCobranca
from apps.assinaturas.subapps.faturamento.payloads import normalizar_payload_evento, validar_tipo_evento
from apps.organizacoes.context import organizacao_atual_privilegiada

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django_checkouts.client import CheckoutClient
    from django_checkouts.types import WebhookEvent


class ColisaoEventoCobranca(RuntimeError):
    """O gateway reutilizou um ID de evento para conteúdo autenticado distinto."""


class EventoWebhookInvalido(ValueError):
    """Um evento autenticado conhecido não cumpre o protocolo normalizado."""


class AssinaturaWebhookInvalida(ValueError):
    """A origem do webhook não pôde ser autenticada."""


class VarianteWebhookInvalida(ValueError):
    """A variante solicitada não existe ou não está configurada."""


@dataclass(frozen=True, slots=True)
class EventoNormalizado:
    variante: str
    identificador_evento: str
    tipo: str
    identificador_assinatura: str
    identificador_checkout: str
    identificador_fatura: str
    exige_tenant: bool
    payload: dict[str, str | int]
    ocorrido_em: object
    status: StatusEventoCobranca
    hash_payload: str = ""


@dataclass(frozen=True, slots=True)
class DestinoEvento:
    organizacao_id: int
    assinatura_id: int


@dataclass(frozen=True, slots=True)
class EventoPersistido:
    id: int
    organizacao_id: int | None
    status: int
    hash_payload: str


@dataclass(frozen=True, slots=True)
class ResultadoRecebimento:
    evento_id: int
    novo: bool
    status: int


class RepositorioEventos(Protocol):
    def receber(self, dados: EventoNormalizado) -> tuple[EventoPersistido, bool]: ...

    def rotear(self, evento: EventoPersistido, destino: DestinoEvento) -> EventoPersistido: ...


PROTOCOLO_EVENTOS = {
    EventType.CHECKOUT_PENDING: (ResourceKind.CHECKOUT, CheckoutStatus),
    EventType.CHECKOUT_PAID: (ResourceKind.CHECKOUT, CheckoutStatus),
    EventType.CHECKOUT_FAILED: (ResourceKind.CHECKOUT, CheckoutStatus),
    EventType.CHECKOUT_EXPIRED: (ResourceKind.CHECKOUT, CheckoutStatus),
    EventType.CHECKOUT_CANCELED: (ResourceKind.CHECKOUT, CheckoutStatus),
    EventType.SETUP_PENDING: (ResourceKind.SETUP, SetupStatus),
    EventType.SETUP_COMPLETED: (ResourceKind.SETUP, SetupStatus),
    EventType.SETUP_FAILED: (ResourceKind.SETUP, SetupStatus),
    EventType.SETUP_EXPIRED: (ResourceKind.SETUP, SetupStatus),
    EventType.SUBSCRIPTION_CREATED: (ResourceKind.SUBSCRIPTION, SubscriptionStatus),
    EventType.SUBSCRIPTION_UPDATED: (ResourceKind.SUBSCRIPTION, SubscriptionStatus),
    EventType.SUBSCRIPTION_CANCELED: (ResourceKind.SUBSCRIPTION, SubscriptionStatus),
    EventType.INVOICE_OPENED: (ResourceKind.INVOICE, InvoiceStatus),
    EventType.INVOICE_PAID: (ResourceKind.INVOICE, InvoiceStatus),
    EventType.INVOICE_PAYMENT_FAILED: (ResourceKind.INVOICE, InvoiceStatus),
    EventType.INVOICE_VOIDED: (ResourceKind.INVOICE, InvoiceStatus),
    EventType.INVOICE_UNCOLLECTIBLE: (ResourceKind.INVOICE, InvoiceStatus),
}
WEBHOOKS_TOTAL = Counter(
    "billing_webhooks_total",
    "Eventos de webhook por resultado e família normalizados.",
    ("variante", "resultado", "familia"),
)


def _familia_evento(evento: WebhookEvent) -> str:
    if isinstance(evento.type, EventType):
        return str(evento.type).split(".", 1)[0]
    return "unknown"


def _registrar_metrica(variante: str, resultado: str, familia: str) -> None:
    variante_finita = variante if variante in settings.CHECKOUT_VARIANTS else "unknown"
    WEBHOOKS_TOTAL.labels(variante_finita, resultado, familia).inc()


def hash_payload_evento(evento: EventoNormalizado) -> str:
    """Deriva o digest apenas da allowlist normalizada, nunca do corpo recebido."""
    fatos = {
        "event_id": evento.identificador_evento,
        "external_checkout_id": evento.identificador_checkout,
        "external_invoice_id": evento.identificador_fatura,
        "external_subscription_id": evento.identificador_assinatura,
        "occurred_at": evento.ocorrido_em.isoformat(),
        "payload": evento.payload,
        "type": evento.tipo,
        "variant": evento.variante,
    }
    canonico = json.dumps(fatos, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(canonico).hexdigest()


def enfileirar_processamento_evento(evento_id: int, variante: str, organizacao_id: int) -> None:
    """Seam assíncrono consumido pelo worker financeiro da Task 15."""
    current_app.send_task("faturamento.processar_evento_cobranca", args=(evento_id, variante, organizacao_id))


class RepositorioEventosPostgres:
    """Interface mínima que só opera pelas funções SECURITY DEFINER de ingresso."""

    @contextmanager
    def contexto_ingresso(self):
        """Abre a fronteira global antes de qualquer leitura de roteamento."""
        papel = settings.BILLING_INGRESS_DATABASE_ROLE
        if papel != "billing_ingress_runtime":
            raise RuntimeError("O papel operacional de ingresso não corresponde ao contrato instalado.")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            yield

    def receber(self, dados: EventoNormalizado) -> tuple[EventoPersistido, bool]:
        with self.contexto_ingresso(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT evento_id, criado, hash_payload, organizacao_id, status FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    dados.variante,
                    dados.identificador_evento,
                    dados.tipo,
                    dados.identificador_assinatura,
                    dados.identificador_checkout,
                    dados.identificador_fatura,
                    dados.exige_tenant,
                    json.dumps(dados.payload, separators=(",", ":")),
                    dados.hash_payload,
                    dados.ocorrido_em,
                ],
            )
            linha = cursor.fetchone()
        assert linha is not None
        return EventoPersistido(id=linha[0], organizacao_id=linha[3], status=linha[4], hash_payload=linha[2]), linha[1]

    def rotear(self, evento: EventoPersistido, destino: DestinoEvento) -> EventoPersistido:
        with self.contexto_ingresso(), connection.cursor() as cursor:
            cursor.execute("SELECT faturamento_rotear_evento_destino(%s,%s)", [evento.id, destino.organizacao_id])
            roteado = cursor.fetchone()[0]
        if not roteado:
            return evento
        return EventoPersistido(
            id=evento.id,
            organizacao_id=destino.organizacao_id,
            status=StatusEventoCobranca.ROTEADO,
            hash_payload=evento.hash_payload,
        )


class EventosCobranca:
    def __init__(
        self,
        *,
        repositorio: RepositorioEventos | None = None,
        resolver_destino: Callable[[WebhookEvent], DestinoEvento | None] | None = None,
        enqueue: Callable[[int, str, int], None] = enfileirar_processamento_evento,
    ) -> None:
        self.repositorio = repositorio or RepositorioEventosPostgres()
        self.resolver_destino = resolver_destino or resolver_destino_evento
        self.enqueue = enqueue

    @staticmethod
    def normalizar(evento: WebhookEvent) -> EventoNormalizado:
        if evento.type is None:
            tipo = evento.event_type
            try:
                validar_tipo_evento(tipo)
            except ValidationError as exc:
                raise EventoWebhookInvalido("O tipo remoto desconhecido não possui formato seguro.") from exc
            status = StatusEventoCobranca.IGNORADO
            exige_tenant = False
            payload = {}
            assinatura = checkout = fatura = ""
        else:
            if evento.resource is None or evento.resource_kind is None or evento.resource_id is None:
                raise EventoWebhookInvalido("Evento conhecido não contém o recurso normalizado obrigatório.")
            _validar_protocolo_evento(evento)
            tipo = str(evento.type)
            status = StatusEventoCobranca.RECEBIDO
            exige_tenant = True
            recurso = evento.resource
            checkout = evento.resource_id if evento.resource_kind in {ResourceKind.CHECKOUT, ResourceKind.SETUP} else ""
            fatura = evento.resource_id if evento.resource_kind == ResourceKind.INVOICE else ""
            assinatura = getattr(recurso, "subscription_id", None) or (
                evento.resource_id if evento.resource_kind == ResourceKind.SUBSCRIPTION else ""
            )
            payload_bruto = {
                "amount": getattr(recurso, "amount_total", None),
                "currency": getattr(recurso, "currency", None),
                "customer_reference": getattr(recurso, "reference_id", None),
                "invoice_status": str(getattr(recurso, "status", "")) if evento.resource_kind == ResourceKind.INVOICE else None,
                "payment_status": (
                    str(getattr(recurso, "status", "")) if evento.resource_kind in {ResourceKind.CHECKOUT, ResourceKind.SETUP} else None
                ),
                "subscription_status": str(getattr(recurso, "status", "")) if evento.resource_kind == ResourceKind.SUBSCRIPTION else None,
                "period_start": _iso(getattr(recurso, "current_period_start", None)),
                "period_end": _iso(getattr(recurso, "current_period_end", None)),
            }
            try:
                payload = normalizar_payload_evento({chave: valor for chave, valor in payload_bruto.items() if valor is not None and valor != ""})
                validar_tipo_evento(tipo)
            except ValidationError as exc:
                raise EventoWebhookInvalido("Evento conhecido contém fatos normalizados inválidos.") from exc
        base = EventoNormalizado(
            variante=evento.variant,
            identificador_evento=evento.event_id,
            tipo=tipo,
            identificador_assinatura=str(assinatura or ""),
            identificador_checkout=str(checkout or ""),
            identificador_fatura=str(fatura or ""),
            exige_tenant=exige_tenant,
            payload=payload,
            ocorrido_em=evento.occurred_at,
            status=status,
        )
        return EventoNormalizado(
            variante=base.variante,
            identificador_evento=base.identificador_evento,
            tipo=base.tipo,
            identificador_assinatura=base.identificador_assinatura,
            identificador_checkout=base.identificador_checkout,
            identificador_fatura=base.identificador_fatura,
            exige_tenant=base.exige_tenant,
            payload=base.payload,
            ocorrido_em=base.ocorrido_em,
            status=base.status,
            hash_payload=hash_payload_evento(base),
        )

    def receber_bytes(self, variante: str, body: bytes, headers: Mapping[str, str], *, client: CheckoutClient | None = None) -> ResultadoRecebimento:
        try:
            client = client or get_checkout_gateway(variante)
        except ConfigurationError as exc:
            _registrar_metrica(variante, "variant_invalid", "unknown")
            raise VarianteWebhookInvalida("A variante de webhook não está disponível.") from exc
        try:
            evento = client.webhooks.verify(body, headers)
        except WebhookVerificationError as exc:
            _registrar_metrica(variante, "signature_invalid", "unknown")
            raise AssinaturaWebhookInvalida("A assinatura do webhook não confere.") from exc
        except GatewayProtocolError as exc:
            _registrar_metrica(variante, "protocol", "unknown")
            raise EventoWebhookInvalido("O gateway devolveu um evento conhecido incompatível com o protocolo.") from exc
        return self.receber(variante, evento, client=client)

    def receber(self, variante: str, evento: WebhookEvent, *, client: CheckoutClient) -> ResultadoRecebimento:
        del client
        if evento.variant != variante:
            _registrar_metrica(variante, "protocol", _familia_evento(evento))
            raise EventoWebhookInvalido("A variante autenticada não coincide com a rota.")
        familia = _familia_evento(evento)
        _registrar_metrica(variante, "received", familia)
        try:
            dados = self.normalizar(evento)
        except EventoWebhookInvalido:
            _registrar_metrica(variante, "protocol", familia)
            raise
        escopo = self.repositorio.contexto_ingresso() if isinstance(self.repositorio, RepositorioEventosPostgres) else nullcontext()
        with escopo:
            # AssinaturaGateway e CheckoutCobranca também são protegidos: o
            # login NOINHERIT só pode resolver o destino depois do SET ROLE.
            destino = self.resolver_destino(evento) if dados.exige_tenant else None
            evento_local, novo = self.repositorio.receber(dados)
            if not novo:
                if evento_local.hash_payload != dados.hash_payload:
                    _registrar_metrica(variante, "collision", familia)
                    raise ColisaoEventoCobranca("O identificador do evento já existe com conteúdo diferente.")
                _registrar_metrica(variante, "duplicate", familia)
                return ResultadoRecebimento(evento_local.id, False, evento_local.status)
            if dados.status == StatusEventoCobranca.IGNORADO:
                _registrar_metrica(variante, "ignored", familia)
                return ResultadoRecebimento(evento_local.id, True, dados.status)
            if destino is not None:
                evento_local = self.repositorio.rotear(evento_local, destino)
            if evento_local.status == StatusEventoCobranca.ROTEADO:
                _registrar_metrica(variante, "routed", familia)
                transaction.on_commit(lambda: self._enfileirar(evento_local.id, variante, evento_local.organizacao_id, familia))
            else:
                _registrar_metrica(variante, "unrouted", familia)
            return ResultadoRecebimento(evento_local.id, True, evento_local.status)

    def _enfileirar(self, evento_id: int, variante: str, organizacao_id: int | None, familia: str) -> None:
        if organizacao_id is None:
            raise RuntimeError("Evento roteado exige organização explícita no enqueue.")
        self.enqueue(evento_id, variante, organizacao_id)
        _registrar_metrica(variante, "enqueue", familia)


def _iso(valor):
    return valor.isoformat() if valor is not None else None


def _validar_protocolo_evento(evento: WebhookEvent) -> None:
    if not isinstance(evento.type, EventType) or evento.type not in PROTOCOLO_EVENTOS:
        raise EventoWebhookInvalido("O tipo normalizado conhecido não pertence ao protocolo suportado.")
    kind, enum_status = PROTOCOLO_EVENTOS[evento.type]
    if evento.resource_kind != kind or evento.resource is None:
        raise EventoWebhookInvalido("O recurso não corresponde à família do evento.")
    if getattr(evento.resource, "external_id", None) != evento.resource_id:
        raise EventoWebhookInvalido("O identificador do recurso normalizado diverge do evento.")
    # O evento é apenas um trigger histórico; o recurso pode ser um snapshot
    # posterior. A Task15 recupera o estado atual antes de qualquer decisão
    # financeira, portanto aqui validamos somente família e enum fechado.
    if not isinstance(getattr(evento.resource, "status", None), enum_status):
        raise EventoWebhookInvalido("O recurso contém status fora do vocabulário fechado da família.")


def resolver_destino_evento(evento: WebhookEvent) -> DestinoEvento | None:
    recurso = evento.resource
    referencia = getattr(recurso, "reference_id", None) if recurso is not None else None
    por_referencia = (
        _resolver_referencia(
            referencia,
            variante=evento.variant,
            resource_kind=evento.resource_kind,
            resource_id=evento.resource_id,
        )
        if referencia
        else None
    )
    if referencia and por_referencia is None:
        return None
    identificador = getattr(recurso, "subscription_id", None) if recurso is not None else None
    if evento.resource_kind == ResourceKind.SUBSCRIPTION:
        identificador = evento.resource_id
    por_assinatura = _resolver_assinatura(evento.variant, identificador) if identificador else None
    if por_referencia and por_assinatura and por_referencia != por_assinatura:
        return None
    return por_referencia or por_assinatura


def _resolver_referencia(
    referencia: str,
    *,
    variante: str,
    resource_kind: ResourceKind | None,
    resource_id: str | None,
) -> DestinoEvento | None:
    try:
        dados = signing.loads(referencia, salt=SALT_REFERENCIA)
        organizacao_id = dados.get("organizacao") if isinstance(dados, dict) else None
        if type(organizacao_id) is not int or organizacao_id < 1:
            return None
        checkout_id = decodificar_referencia_checkout(referencia, organizacao_id=organizacao_id)
    except (signing.BadSignature, ValueError):
        return None
    with organizacao_atual_privilegiada(organizacao_id):
        checkout = (
            CheckoutCobranca.objects.filter(pk=checkout_id, organizacao_id=organizacao_id)
            .only("assinatura_id", "organizacao_id", "variante", "finalidade", "identificador_externo")
            .first()
        )
    if checkout is None:
        return None
    if checkout.variante != variante:
        return None
    finalidade_compativel = (resource_kind == ResourceKind.SETUP) == (checkout.finalidade == FinalidadeCheckout.FORMA_PAGAMENTO)
    if resource_kind not in {ResourceKind.CHECKOUT, ResourceKind.SETUP} or not finalidade_compativel:
        return None
    # Antes da confirmação do checkout, a referência assinada prova apenas o ID
    # local e a organização — não prova qual recurso remoto a reutilizou. O
    # evento fica pendente para recuperação até o ID externo ser conciliado.
    if checkout.identificador_externo is None or checkout.identificador_externo != resource_id:
        return None
    return DestinoEvento(organizacao_id=checkout.organizacao_id, assinatura_id=checkout.assinatura_id)


def _resolver_assinatura(variante: str, identificador: str) -> DestinoEvento | None:
    mappings = list(
        AssinaturaGateway.objects.filter(variante=variante, identificador_externo=identificador, is_active=True, is_deleted=False).values_list(
            "organizacao_id", "assinatura_id"
        )[:2]
    )
    if len(mappings) != 1:
        return None
    return DestinoEvento(organizacao_id=mappings[0][0], assinatura_id=mappings[0][1])
