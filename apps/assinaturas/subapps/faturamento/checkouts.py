"""Orquestração de checkout recorrente sem locks durante I/O externo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core import signing

from django_checkouts import get_checkout_gateway
from django_checkouts.enums import BillingCycle, CheckoutMode
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.types import CatalogPrice, CheckoutCreate, CheckoutItem, InlinePrice, Recurrence
from prometheus_client import Counter

from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    Periodicidade,
    PrecoPlano,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusPropostaComercial,
)
from apps.assinaturas.subapps.faturamento.models import CheckoutCobranca, ComponentePreco, FinalidadeCheckout, ReferenciaPrecoGateway, StatusCheckout
from apps.assinaturas.subscriptions import Assinaturas
from apps.organizacoes.context import organizacao_atual_privilegiada

if TYPE_CHECKING:
    from django_checkouts.client import CheckoutClient
    from django_checkouts.types import Checkout

    from apps.assinaturas.models import PropostaComercial
    from apps.usuarios.models import Usuario

SALT_REFERENCIA = "faturamento.checkout.referencia.v1"
VERSAO_REFERENCIA = 1
STATUS_TERMINAIS = {StatusCheckout.CONCLUIDO, StatusCheckout.EXPIRADO, StatusCheckout.CANCELADO, StatusCheckout.FALHOU}
CHECKOUTS_TOTAL = Counter(
    "billing_checkouts_total",
    "Tentativas locais de checkout por finalidade e resultado normalizados.",
    ("finalidade", "resultado"),
)


class ConflitoCheckout(ValueError):
    """A intenção não corresponde mais ao snapshot financeiro corrente."""


class FalhaCheckoutIncerta(ConflitoCheckout):
    """O gateway pode ter criado a sessão; é obrigatório conciliar antes de repetir."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CriacaoCheckout:
    assinatura: AssinaturaOrganizacao
    finalidade: FinalidadeCheckout
    chave_idempotencia: str
    alteracao: AlteracaoAssinatura | None = None
    proposta: PropostaComercial | None = None
    ator: Usuario | None = None
    variante: str = "stripe"

    def __post_init__(self):
        if not isinstance(self.assinatura, AssinaturaOrganizacao) or self.assinatura.pk is None:
            raise ValueError("Checkout exige uma assinatura persistida.")
        if not isinstance(self.finalidade, FinalidadeCheckout):
            object.__setattr__(self, "finalidade", FinalidadeCheckout(self.finalidade))
        if not self.chave_idempotencia.strip() or len(self.chave_idempotencia) > 120:
            raise ValueError("Chave idempotente deve ter entre 1 e 120 caracteres.")


@dataclass(frozen=True, slots=True)
class ResultadoCheckout:
    checkout: CheckoutCobranca
    referencia: str


@dataclass(frozen=True, slots=True)
class _Preparacao:
    checkout_id: int
    organizacao_id: int
    request: CheckoutCreate | None
    chave_gateway: str
    existente: bool


def criar_referencia_checkout(checkout_id: int, organizacao_id: int) -> str:
    return signing.dumps({"v": VERSAO_REFERENCIA, "checkout": checkout_id, "organizacao": organizacao_id}, salt=SALT_REFERENCIA, compress=True)


def decodificar_referencia_checkout(referencia: str, *, organizacao_id: int) -> int:
    try:
        dados = signing.loads(referencia, salt=SALT_REFERENCIA)
    except signing.BadSignature as exc:
        raise ConflitoCheckout("A referência do checkout é inválida.") from exc
    if not isinstance(dados, dict) or dados.get("v") != VERSAO_REFERENCIA or dados.get("organizacao") != organizacao_id:
        raise ConflitoCheckout("A referência do checkout não pertence à organização.")
    checkout_id = dados.get("checkout")
    if type(checkout_id) is not int or checkout_id < 1:
        raise ConflitoCheckout("A referência do checkout é inválida.")
    return checkout_id


class CheckoutsCobranca:
    """Prepara, executa e confirma checkouts em três fases explícitas."""

    @classmethod
    def criar(cls, criacao: CriacaoCheckout, *, client: CheckoutClient | None = None) -> ResultadoCheckout:
        client = client or get_checkout_gateway(criacao.variante)
        preparacao = cls.preparar(criacao, client=client)
        referencia = criar_referencia_checkout(preparacao.checkout_id, preparacao.organizacao_id)
        if preparacao.existente:
            with organizacao_atual_privilegiada(preparacao.organizacao_id):
                return ResultadoCheckout(CheckoutCobranca.objects.get(pk=preparacao.checkout_id), referencia)
        assert preparacao.request is not None
        request = CheckoutCreate(
            items=preparacao.request.items,
            success_url=preparacao.request.success_url,
            cancel_url=preparacao.request.cancel_url,
            mode=preparacao.request.mode,
            payment_methods=preparacao.request.payment_methods,
            customer=preparacao.request.customer,
            recurrence=preparacao.request.recurrence,
            reference_id=referencia,
            expires_at=preparacao.request.expires_at,
            metadata=preparacao.request.metadata,
            gateway_options=preparacao.request.gateway_options,
        )
        try:
            remoto = client.checkouts.create(request, idempotency_key=preparacao.chave_gateway)
        except GatewayPermanentError as exc:
            cls.falhar(preparacao, incerta=False)
            CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="falha_conhecida").inc()
            raise ConflitoCheckout("O gateway recusou a criação do checkout.") from exc
        except GatewayTemporaryError as exc:
            cls.falhar(preparacao, incerta=True)
            CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="falha_incerta").inc()
            raise FalhaCheckoutIncerta("A criação precisa ser conciliada antes de nova tentativa.") from exc
        resultado = ResultadoCheckout(cls.confirmar(preparacao, remoto), referencia)
        CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="aberto").inc()
        return resultado

    @classmethod
    def preparar(cls, criacao: CriacaoCheckout, *, client: CheckoutClient) -> _Preparacao:
        organizacao_id = criacao.assinatura.organizacao_id
        with organizacao_atual_privilegiada(organizacao_id):
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update(of=("self",)).get(pk=criacao.assinatura.pk)
            cls._validar_finalidade(criacao, assinatura)
            if criacao.finalidade == FinalidadeCheckout.FORMA_PAGAMENTO:
                raise ConflitoCheckout("A variante não declara capability de setup de forma de pagamento.")
            total, moeda, preco, valores = cls._snapshot(criacao, assinatura)
            existente = CheckoutCobranca.objects.filter(chave_idempotencia=criacao.chave_idempotencia).first()
            if existente is not None:
                coerente = (
                    existente.assinatura_id == assinatura.pk
                    and existente.finalidade == criacao.finalidade
                    and existente.alteracao_id == getattr(criacao.alteracao, "pk", None)
                    and existente.proposta_id == getattr(criacao.proposta, "pk", None)
                    and existente.valor_esperado_centavos == total
                    and existente.moeda_esperada == moeda
                    and existente.variante == criacao.variante
                )
                if not coerente or existente.status in STATUS_TERMINAIS:
                    raise ConflitoCheckout("A chave idempotente já foi usada para outro estado ou conteúdo.")
                return _Preparacao(existente.pk, organizacao_id, None, cls._chave_gateway(organizacao_id, criacao.chave_idempotencia), True)
            items = cls._itens(client, criacao.variante, preco, valores, moeda)
            checkout = CheckoutCobranca.objects.create(
                organizacao_id=organizacao_id,
                assinatura=assinatura,
                alteracao=criacao.alteracao,
                proposta=criacao.proposta,
                finalidade=criacao.finalidade,
                status=StatusCheckout.AGUARDANDO_GATEWAY,
                chave_idempotencia=criacao.chave_idempotencia,
                variante=criacao.variante,
                valor_esperado_centavos=total,
                moeda_esperada=moeda,
                created_by=criacao.ator,
            )
            if criacao.alteracao is not None:
                Assinaturas.marcar_aguardando_gateway(criacao.alteracao)
            request = CheckoutCreate(
                items=items,
                success_url="https://app.example/assinatura/checkout/sucesso",
                cancel_url="https://app.example/assinatura/checkout/cancelado",
                mode=CheckoutMode.SUBSCRIPTION,
                recurrence=Recurrence(cycle=BillingCycle.MONTHLY if valores["periodicidade"] == Periodicidade.MENSAL else BillingCycle.YEARLY),
            )
            return _Preparacao(checkout.pk, organizacao_id, request, cls._chave_gateway(organizacao_id, criacao.chave_idempotencia), False)

    @classmethod
    def confirmar(cls, preparacao: _Preparacao, remoto: Checkout) -> CheckoutCobranca:
        with organizacao_atual_privilegiada(preparacao.organizacao_id):
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=preparacao.checkout_id)
            if checkout.status != StatusCheckout.AGUARDANDO_GATEWAY:
                raise ConflitoCheckout("A tentativa deixou de ser vigente durante a comunicação com o gateway.")
            if remoto.amount_total != checkout.valor_esperado_centavos or remoto.currency != checkout.moeda_esperada:
                checkout.status = StatusCheckout.FALHOU
                checkout.save(update_fields=["status", "last_modified_at"])
                raise ConflitoCheckout("O total normalizado do gateway diverge do snapshot local.")
            checkout.status = StatusCheckout.ABERTO
            checkout.identificador_externo = remoto.external_id
            checkout.url = remoto.url or ""
            checkout.expira_em = remoto.expires_at
            checkout.save(update_fields=["status", "identificador_externo", "url", "expira_em", "last_modified_at"])
            return checkout

    @classmethod
    def falhar(cls, preparacao: _Preparacao, *, incerta: bool) -> None:
        with organizacao_atual_privilegiada(preparacao.organizacao_id):
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=preparacao.checkout_id)
            if checkout.status == StatusCheckout.AGUARDANDO_GATEWAY and not incerta:
                checkout.status = StatusCheckout.FALHOU
                checkout.save(update_fields=["status", "last_modified_at"])
                if checkout.alteracao_id is not None:
                    alteracao = checkout.alteracao
                    assert alteracao is not None
                    Assinaturas.falhar_alteracao(alteracao, codigo="checkout_failed", mensagem="O gateway recusou o checkout.")

    @staticmethod
    def _chave_gateway(organizacao_id: int, chave: str) -> str:
        return f"org:{organizacao_id}:checkout:{chave}"

    @staticmethod
    def _validar_finalidade(criacao: CriacaoCheckout, assinatura: AssinaturaOrganizacao) -> None:
        if assinatura.status == StatusAssinatura.ENCERRADA:
            raise ConflitoCheckout("Assinatura encerrada não aceita checkout.")
        if criacao.finalidade == FinalidadeCheckout.CONTRATACAO and assinatura.status != StatusAssinatura.PENDENTE:
            raise ConflitoCheckout("Contratação exige assinatura pendente.")
        if criacao.finalidade == FinalidadeCheckout.ALTERACAO:
            if (
                criacao.alteracao is None
                or criacao.alteracao.assinatura_id != assinatura.pk
                or criacao.alteracao.status != StatusAlteracaoAssinatura.SOLICITADA
            ):
                raise ConflitoCheckout("Alteração não está disponível para checkout.")
        elif criacao.alteracao is not None:
            raise ConflitoCheckout("Esta finalidade não aceita alteração.")
        if criacao.finalidade == FinalidadeCheckout.PROPOSTA:
            if (
                criacao.proposta is None
                or criacao.proposta.organizacao_id != assinatura.organizacao_id
                or criacao.proposta.status != StatusPropostaComercial.ACEITA
            ):
                raise ConflitoCheckout("Proposta não está aceita para checkout.")
        elif criacao.proposta is not None:
            raise ConflitoCheckout("Esta finalidade não aceita proposta.")

    @staticmethod
    def _snapshot(criacao: CriacaoCheckout, assinatura: AssinaturaOrganizacao):
        if criacao.finalidade == FinalidadeCheckout.ALTERACAO:
            assert criacao.alteracao is not None
            snapshot = criacao.alteracao.snapshot_pretendido
            valores = {
                "valor_base_centavos": snapshot["valor_base_centavos"],
                "valor_seat_centavos": snapshot["valor_seat_centavos"],
                "seats_inclusos": snapshot["seats_inclusos"],
                "seats_contratados": snapshot["seats_contratados"],
                "periodicidade": snapshot["periodicidade"],
            }
            moeda = snapshot["moeda"]
            versao_id = snapshot.get("versao_plano_id")
        else:
            if criacao.finalidade == FinalidadeCheckout.PROPOSTA:
                assert criacao.proposta is not None
                origem = criacao.proposta
                versao_id = origem.versao_plano_referencia_id
            else:
                origem = assinatura
                versao_id = assinatura.versao_plano_id
            valores = {
                campo: getattr(origem, campo)
                for campo in ("valor_base_centavos", "valor_seat_centavos", "seats_inclusos", "seats_contratados", "periodicidade")
            }
            moeda = origem.moeda
        total = (
            0
            if criacao.finalidade == FinalidadeCheckout.FORMA_PAGAMENTO
            else valores["valor_base_centavos"] + max(valores["seats_contratados"] - valores["seats_inclusos"], 0) * valores["valor_seat_centavos"]
        )
        preco = None
        if versao_id is not None:
            preco = PrecoPlano.objects.filter(versao_plano_id=versao_id, periodicidade=valores["periodicidade"], moeda=moeda).first()
        return total, moeda, preco, valores

    @staticmethod
    def _itens(client: CheckoutClient, variante: str, preco: PrecoPlano | None, valores: dict, moeda: str):
        caps = client.capabilities.checkouts
        if CheckoutMode.SUBSCRIPTION not in caps.modes:
            raise ConflitoCheckout("O gateway não suporta checkout recorrente.")
        if not caps.supports_catalog_prices and not caps.supports_inline_prices:
            raise ConflitoCheckout("O gateway não suporta preços de catálogo ou inline.")
        componentes = []
        if valores["valor_base_centavos"] > 0:
            componentes.append((ComponentePreco.BASE, 1, valores["valor_base_centavos"], "Assinatura"))
        seats = max(valores["seats_contratados"] - valores["seats_inclusos"], 0)
        if valores["valor_seat_centavos"] > 0 and seats > 0:
            componentes.append((ComponentePreco.SEAT, seats, valores["valor_seat_centavos"], "Seat adicional"))
        items = []
        referencias = {}
        if caps.supports_catalog_prices:
            if preco is not None:
                referencias = {
                    r.componente: r.identificador_externo
                    for r in ReferenciaPrecoGateway.objects.filter(preco_plano=preco, variante=variante, is_active=True, is_deleted=False)
                }
        for componente, quantidade, valor, nome in componentes:
            external_id = referencias.get(componente)
            if external_id:
                price = CatalogPrice(external_id=external_id)
            elif caps.supports_inline_prices:
                price = InlinePrice(name=nome, unit_amount=valor, currency=moeda)
            else:
                raise ConflitoCheckout("Referência de preço obrigatória não configurada.")
            items.append(CheckoutItem(price=price, quantity=quantidade, reference_id=str(componente)))
        if not items:
            raise ConflitoCheckout("Checkout recorrente sem itens cobrados não é suportado.")
        return tuple(items)
