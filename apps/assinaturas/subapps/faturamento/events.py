from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from celery import current_app
from django_checkouts import get_checkout_gateway
from django_checkouts.enums import ResourceKind
from django_checkouts.exceptions import ConfigurationError, GatewayProtocolError, WebhookVerificationError

from apps.assinaturas.subapps.faturamento.checkouts import SALT_REFERENCIA, decodificar_referencia_checkout
from apps.assinaturas.subapps.faturamento.models import AssinaturaGateway, CheckoutCobranca, StatusEventoCobranca
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


def enfileirar_processamento_evento(evento_id: int, variante: str) -> None:
    """Seam assíncrono consumido pelo worker financeiro da Task 15."""
    current_app.send_task("faturamento.processar_evento_cobranca", args=(evento_id, variante))


class RepositorioEventosPostgres:
    """Interface mínima que só opera pelas funções SECURITY DEFINER de ingresso."""

    def receber(self, dados: EventoNormalizado) -> tuple[EventoPersistido, bool]:
        papel = settings.BILLING_INGRESS_DATABASE_ROLE
        if papel != "billing_ingress_runtime":
            raise RuntimeError("O papel operacional de ingresso não corresponde ao contrato instalado.")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
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
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
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
        enqueue: Callable[[int, str], None] = enfileirar_processamento_evento,
    ) -> None:
        self.repositorio = repositorio or RepositorioEventosPostgres()
        self.resolver_destino = resolver_destino or resolver_destino_evento
        self.enqueue = enqueue

    @staticmethod
    def normalizar(evento: WebhookEvent) -> EventoNormalizado:
        if evento.type is None:
            tipo = "unknown"
            status = StatusEventoCobranca.IGNORADO
            exige_tenant = False
            payload = {}
            assinatura = checkout = fatura = ""
        else:
            if evento.resource is None or evento.resource_kind is None or evento.resource_id is None:
                raise EventoWebhookInvalido("Evento conhecido não contém o recurso normalizado obrigatório.")
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
            raise VarianteWebhookInvalida("A variante de webhook não está disponível.") from exc
        try:
            evento = client.webhooks.verify(body, headers)
        except WebhookVerificationError as exc:
            raise AssinaturaWebhookInvalida("A assinatura do webhook não confere.") from exc
        except GatewayProtocolError as exc:
            raise EventoWebhookInvalido("O gateway devolveu um evento conhecido incompatível com o protocolo.") from exc
        return self.receber(variante, evento, client=client)

    def receber(self, variante: str, evento: WebhookEvent, *, client: CheckoutClient) -> ResultadoRecebimento:
        del client
        if evento.variant != variante:
            raise EventoWebhookInvalido("A variante autenticada não coincide com a rota.")
        dados = self.normalizar(evento)
        destino = self.resolver_destino(evento) if dados.exige_tenant else None
        escopo = transaction.atomic() if isinstance(self.repositorio, RepositorioEventosPostgres) else nullcontext()
        with escopo:
            evento_local, novo = self.repositorio.receber(dados)
            if not novo:
                if evento_local.hash_payload != dados.hash_payload:
                    raise ColisaoEventoCobranca("O identificador do evento já existe com conteúdo diferente.")
                return ResultadoRecebimento(evento_local.id, False, evento_local.status)
            if dados.status == StatusEventoCobranca.IGNORADO:
                return ResultadoRecebimento(evento_local.id, True, dados.status)
            if destino is not None:
                evento_local = self.repositorio.rotear(evento_local, destino)
            if evento_local.status == StatusEventoCobranca.ROTEADO:
                transaction.on_commit(lambda: self.enqueue(evento_local.id, variante))
            return ResultadoRecebimento(evento_local.id, True, evento_local.status)


def _iso(valor):
    return valor.isoformat() if valor is not None else None


def resolver_destino_evento(evento: WebhookEvent) -> DestinoEvento | None:
    recurso = evento.resource
    referencia = getattr(recurso, "reference_id", None) if recurso is not None else None
    por_referencia = _resolver_referencia(referencia) if referencia else None
    if referencia and por_referencia is None:
        return None
    identificador = getattr(recurso, "subscription_id", None) if recurso is not None else None
    if evento.resource_kind == ResourceKind.SUBSCRIPTION:
        identificador = evento.resource_id
    por_assinatura = _resolver_assinatura(evento.variant, identificador) if identificador else None
    if por_referencia and por_assinatura and por_referencia != por_assinatura:
        return None
    return por_referencia or por_assinatura


def _resolver_referencia(referencia: str) -> DestinoEvento | None:
    try:
        dados = signing.loads(referencia, salt=SALT_REFERENCIA)
        organizacao_id = dados.get("organizacao") if isinstance(dados, dict) else None
        if type(organizacao_id) is not int or organizacao_id < 1:
            return None
        checkout_id = decodificar_referencia_checkout(referencia, organizacao_id=organizacao_id)
    except (signing.BadSignature, ValueError):
        return None
    with organizacao_atual_privilegiada(organizacao_id):
        checkout = CheckoutCobranca.objects.filter(pk=checkout_id, organizacao_id=organizacao_id).only("assinatura_id", "organizacao_id").first()
    if checkout is None:
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
