"""Orquestração de checkout recorrente sem locks durante I/O externo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from django.conf import settings
from django.core import signing
from django.db import IntegrityError

from django_checkouts import get_checkout_gateway
from django_checkouts.enums import BillingCycle, CheckoutMode, RetryDisposition
from django_checkouts.exceptions import GatewayPermanentError, GatewayTemporaryError
from django_checkouts.types import CatalogPrice, Checkout, CheckoutCreate, CheckoutItem, InlinePrice, Recurrence, Setup, SetupCreate
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


class CheckoutPendente(ConflitoCheckout):
    """Já há uma chamada ao gateway em andamento para a operação."""


class CheckoutIndisponivel(ConflitoCheckout):
    """A configuração/capability não permite criar a operação."""


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
    request: CheckoutCreate | SetupCreate | None
    chave_gateway: str
    existente: bool
    gateway: str


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
                checkout = CheckoutCobranca.objects.get(pk=preparacao.checkout_id)
                if checkout.status == StatusCheckout.ABERTO and checkout.url:
                    return ResultadoCheckout(checkout, referencia)
                raise CheckoutPendente("O checkout desta operação ainda está sendo criado.")
        assert preparacao.request is not None
        if isinstance(preparacao.request, SetupCreate):
            request = SetupCreate(
                success_url=preparacao.request.success_url,
                cancel_url=preparacao.request.cancel_url,
                payment_methods=preparacao.request.payment_methods,
                customer=preparacao.request.customer,
                reference_id=referencia,
                metadata=preparacao.request.metadata,
            )
            command = client.setups.create
        else:
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
            command = client.checkouts.create
        try:
            remoto = command(request, idempotency_key=preparacao.chave_gateway)
        except GatewayPermanentError as exc:
            incerta = exc.retry_advice.disposition != RetryDisposition.NEVER
            cls.falhar(preparacao, incerta=incerta)
            if incerta:
                raise FalhaCheckoutIncerta("A criação precisa ser conciliada antes de nova tentativa.") from exc
            CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="falha_conhecida").inc()
            raise ConflitoCheckout("O gateway recusou a criação do checkout.") from exc
        except GatewayTemporaryError as exc:
            cls.falhar(preparacao, incerta=True)
            CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="falha_incerta").inc()
            raise FalhaCheckoutIncerta("A criação precisa ser conciliada antes de nova tentativa.") from exc
        except Exception as exc:
            cls.falhar(preparacao, incerta=True)
            CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="falha_incerta").inc()
            raise FalhaCheckoutIncerta("A criação precisa ser conciliada antes de nova tentativa.") from exc
        resultado = ResultadoCheckout(cls.confirmar(preparacao, remoto, referencia=referencia), referencia)
        CHECKOUTS_TOTAL.labels(finalidade=criacao.finalidade.label, resultado="aberto").inc()
        return resultado

    @classmethod
    def preparar(cls, criacao: CriacaoCheckout, *, client: CheckoutClient) -> _Preparacao:
        organizacao_id = criacao.assinatura.organizacao_id
        with organizacao_atual_privilegiada(organizacao_id):
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update(of=("self",)).get(pk=criacao.assinatura.pk)
            alteracao = None
            if criacao.alteracao is not None:
                alteracao = AlteracaoAssinatura.all_objects.select_for_update(of=("self",)).get(pk=criacao.alteracao.pk)
            proposta = None
            if criacao.proposta is not None:
                from apps.assinaturas.models import PropostaComercial

                proposta = PropostaComercial.all_objects.select_for_update(of=("self",)).get(pk=criacao.proposta.pk)
            operacao_chave = cls._operacao_chave(criacao, assinatura, alteracao=alteracao, proposta=proposta)
            tentativa_em_voo = CheckoutCobranca.objects.filter(
                chave_idempotencia=criacao.chave_idempotencia,
                status=StatusCheckout.AGUARDANDO_GATEWAY,
            ).exists()
            outra_ativa = (
                CheckoutCobranca.objects.filter(
                    operacao_chave=operacao_chave,
                    status__in=(StatusCheckout.CRIADO, StatusCheckout.AGUARDANDO_GATEWAY, StatusCheckout.ABERTO),
                )
                .exclude(chave_idempotencia=criacao.chave_idempotencia)
                .exists()
            )
            if tentativa_em_voo or outra_ativa:
                raise CheckoutPendente("Já existe checkout pendente para esta operação.")
            cls._validar_finalidade(criacao, assinatura, alteracao=alteracao, proposta=proposta)
            total, moeda, preco, valores = cls._snapshot(criacao, assinatura, alteracao=alteracao, proposta=proposta)
            snapshot_hash = cls._snapshot_hash(criacao, assinatura, total, moeda, valores, alteracao=alteracao, proposta=proposta)
            request: CheckoutCreate | SetupCreate
            if criacao.finalidade == FinalidadeCheckout.FORMA_PAGAMENTO:
                if not client.capabilities.checkouts.supports_setup:
                    raise CheckoutIndisponivel("A variante não declara capability de setup de forma de pagamento.")
                request = SetupCreate(success_url=settings.BILLING_CHECKOUT_SUCCESS_URL, cancel_url=settings.BILLING_CHECKOUT_CANCEL_URL)
            else:
                items = cls._itens(client, criacao.variante, preco, valores, moeda)
                cycle = BillingCycle.MONTHLY if valores["periodicidade"] == Periodicidade.MENSAL else BillingCycle.YEARLY
                if cycle not in client.capabilities.checkouts.billing_cycles:
                    raise CheckoutIndisponivel("O gateway não suporta a periodicidade solicitada.")
                request = CheckoutCreate(
                    items=items,
                    success_url=settings.BILLING_CHECKOUT_SUCCESS_URL,
                    cancel_url=settings.BILLING_CHECKOUT_CANCEL_URL,
                    mode=CheckoutMode.SUBSCRIPTION,
                    recurrence=Recurrence(cycle=cycle),
                )
            existente = CheckoutCobranca.objects.filter(chave_idempotencia=criacao.chave_idempotencia).first()
            if existente is not None:
                coerente = (
                    existente.assinatura_id == assinatura.pk
                    and existente.finalidade == criacao.finalidade
                    and existente.alteracao_id == getattr(alteracao, "pk", None)
                    and existente.proposta_id == getattr(proposta, "pk", None)
                    and existente.valor_esperado_centavos == total
                    and existente.moeda_esperada == moeda
                    and existente.variante == criacao.variante
                    and existente.snapshot_hash == snapshot_hash
                )
                if not coerente or existente.status in STATUS_TERMINAIS:
                    raise ConflitoCheckout("A chave idempotente já foi usada para outro estado ou conteúdo.")
                return _Preparacao(
                    existente.pk, organizacao_id, None, cls._chave_gateway(organizacao_id, criacao.chave_idempotencia), True, str(client.gateway)
                )
            if CheckoutCobranca.objects.filter(
                operacao_chave=operacao_chave, status__in=(StatusCheckout.CRIADO, StatusCheckout.AGUARDANDO_GATEWAY, StatusCheckout.ABERTO)
            ).exists():
                raise CheckoutPendente("Já existe checkout pendente para esta operação.")
            try:
                checkout = CheckoutCobranca.objects.create(
                    organizacao_id=organizacao_id,
                    assinatura=assinatura,
                    alteracao=alteracao,
                    proposta=proposta,
                    finalidade=criacao.finalidade,
                    status=StatusCheckout.AGUARDANDO_GATEWAY,
                    chave_idempotencia=criacao.chave_idempotencia,
                    operacao_chave=operacao_chave,
                    snapshot_hash=snapshot_hash,
                    variante=criacao.variante,
                    valor_esperado_centavos=total,
                    moeda_esperada=moeda,
                    created_by=criacao.ator,
                )
            except IntegrityError as exc:
                raise CheckoutPendente("Já existe checkout pendente para esta operação.") from exc
            if alteracao is not None:
                Assinaturas.marcar_aguardando_gateway(alteracao)
            return _Preparacao(
                checkout.pk, organizacao_id, request, cls._chave_gateway(organizacao_id, criacao.chave_idempotencia), False, str(client.gateway)
            )

    @classmethod
    def confirmar(cls, preparacao: _Preparacao, remoto: Checkout | Setup, *, referencia: str) -> CheckoutCobranca:
        with organizacao_atual_privilegiada(preparacao.organizacao_id):
            ponte = CheckoutCobranca.objects.only("assinatura_id").get(pk=preparacao.checkout_id)
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update(of=("self",)).get(pk=ponte.assinatura_id)
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=preparacao.checkout_id)
            if checkout.status != StatusCheckout.AGUARDANDO_GATEWAY:
                raise ConflitoCheckout("A tentativa deixou de ser vigente durante a comunicação com o gateway.")
            alteracao = None
            if checkout.alteracao_id:
                alteracao = AlteracaoAssinatura.all_objects.select_for_update(of=("self",)).get(pk=checkout.alteracao_id)
            proposta = None
            if checkout.proposta_id:
                from apps.assinaturas.models import PropostaComercial

                proposta = PropostaComercial.all_objects.select_for_update(of=("self",)).get(pk=checkout.proposta_id)
            intencao = CriacaoCheckout(
                assinatura=assinatura,
                finalidade=FinalidadeCheckout(checkout.finalidade),
                chave_idempotencia=checkout.chave_idempotencia,
                alteracao=alteracao,
                proposta=proposta,
                variante=checkout.variante,
            )
            try:
                cls._validar_finalidade(intencao, assinatura, alteracao=alteracao, proposta=proposta, confirmacao=True)
            except ConflitoCheckout as exc:
                raise FalhaCheckoutIncerta("A operação deixou de ser vigente; exige conciliação.") from exc
            total, moeda, _, valores = cls._snapshot(intencao, assinatura, alteracao=alteracao, proposta=proposta)
            atual = cls._snapshot_hash(intencao, assinatura, total, moeda, valores, alteracao=alteracao, proposta=proposta)
            if atual != checkout.snapshot_hash:
                raise FalhaCheckoutIncerta("A operação mudou durante a chamada; o checkout exige conciliação.")
            cls._validar_resultado(preparacao, checkout, remoto, referencia=referencia)
            if not isinstance(remoto, Setup) and (
                remoto.amount_total != checkout.valor_esperado_centavos or remoto.currency != checkout.moeda_esperada
            ):
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
            ponte = CheckoutCobranca.objects.only("assinatura_id").get(pk=preparacao.checkout_id)
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update(of=("self",)).get(pk=ponte.assinatura_id)
            checkout = CheckoutCobranca.objects.select_for_update().get(pk=preparacao.checkout_id)
            if checkout.status == StatusCheckout.AGUARDANDO_GATEWAY and not incerta:
                alteracao = None
                if checkout.alteracao_id:
                    alteracao = AlteracaoAssinatura.all_objects.select_for_update(of=("self",)).get(pk=checkout.alteracao_id)
                proposta = None
                if checkout.proposta_id:
                    from apps.assinaturas.models import PropostaComercial

                    proposta = PropostaComercial.all_objects.select_for_update(of=("self",)).get(pk=checkout.proposta_id)
                intencao = CriacaoCheckout(
                    assinatura=assinatura,
                    finalidade=FinalidadeCheckout(checkout.finalidade),
                    chave_idempotencia=checkout.chave_idempotencia,
                    alteracao=alteracao,
                    proposta=proposta,
                    variante=checkout.variante,
                )
                try:
                    cls._validar_finalidade(intencao, assinatura, alteracao=alteracao, proposta=proposta, confirmacao=True)
                except ConflitoCheckout:
                    return
                total, moeda, _, valores = cls._snapshot(intencao, assinatura, alteracao=alteracao, proposta=proposta)
                atual = cls._snapshot_hash(intencao, assinatura, total, moeda, valores, alteracao=alteracao, proposta=proposta)
                if atual != checkout.snapshot_hash:
                    return
                checkout.status = StatusCheckout.FALHOU
                checkout.save(update_fields=["status", "last_modified_at"])
                if alteracao is not None:
                    Assinaturas.falhar_alteracao(alteracao, codigo="checkout_failed", mensagem="O gateway recusou o checkout.")

    @staticmethod
    def _chave_gateway(organizacao_id: int, chave: str) -> str:
        return f"org:{organizacao_id}:checkout:{chave}"

    @staticmethod
    def _validar_finalidade(criacao, assinatura, *, alteracao=None, proposta=None, confirmacao=False) -> None:
        if assinatura.status == StatusAssinatura.ENCERRADA:
            raise ConflitoCheckout("Assinatura encerrada não aceita checkout.")
        if criacao.finalidade == FinalidadeCheckout.CONTRATACAO and assinatura.status != StatusAssinatura.PENDENTE:
            raise ConflitoCheckout("Contratação exige assinatura pendente.")
        if criacao.finalidade == FinalidadeCheckout.ALTERACAO:
            status_esperado = StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY if confirmacao else StatusAlteracaoAssinatura.SOLICITADA
            if alteracao is None or alteracao.assinatura_id != assinatura.pk or alteracao.status != status_esperado:
                raise ConflitoCheckout("Alteração não está disponível para checkout.")
        elif criacao.alteracao is not None:
            raise ConflitoCheckout("Esta finalidade não aceita alteração.")
        if criacao.finalidade == FinalidadeCheckout.PROPOSTA:
            if proposta is None or proposta.organizacao_id != assinatura.organizacao_id or proposta.status != StatusPropostaComercial.ACEITA:
                raise ConflitoCheckout("Proposta não está aceita para checkout.")
        elif criacao.proposta is not None:
            raise ConflitoCheckout("Esta finalidade não aceita proposta.")

    @staticmethod
    def _snapshot(criacao: CriacaoCheckout, assinatura: AssinaturaOrganizacao, *, alteracao=None, proposta=None):
        if criacao.finalidade == FinalidadeCheckout.ALTERACAO:
            assert alteracao is not None
            snapshot = alteracao.snapshot_pretendido
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
                assert proposta is not None
                origem = proposta
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
    def _operacao_chave(criacao, assinatura, *, alteracao=None, proposta=None) -> str:
        if alteracao is not None:
            return f"alteracao:{alteracao.pk}"
        if proposta is not None:
            return f"proposta:{proposta.pk}"
        return f"{criacao.finalidade}:{assinatura.pk}"

    @staticmethod
    def _snapshot_hash(criacao, assinatura, total, moeda, valores, *, alteracao=None, proposta=None) -> str:
        payload = {
            "finalidade": int(criacao.finalidade),
            "assinatura": assinatura.pk,
            "revisao_assinatura": assinatura.revisao,
            "status_assinatura": int(assinatura.status),
            "status_financeiro": int(assinatura.status_financeiro),
            "alteracao": getattr(alteracao, "pk", None),
            "revisao_alteracao": getattr(alteracao, "revisao", None),
            "proposta": getattr(proposta, "pk", None),
            "status_proposta": getattr(proposta, "status", None),
            "revisao_proposta": getattr(proposta, "revisao", None),
            "total": total,
            "moeda": moeda,
            "valores": valores,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def _validar_resultado(preparacao, checkout, remoto, *, referencia) -> None:
        esperado_setup = checkout.finalidade == FinalidadeCheckout.FORMA_PAGAMENTO
        if (esperado_setup and type(remoto) is not Setup) or (not esperado_setup and type(remoto) is not Checkout):
            raise FalhaCheckoutIncerta("O gateway devolveu um tipo incompatível; exige conciliação.")
        if str(remoto.gateway) != preparacao.gateway or remoto.variant != checkout.variante:
            raise FalhaCheckoutIncerta("O gateway devolveu identidade incompatível; exige conciliação.")
        if remoto.reference_id != referencia:
            raise FalhaCheckoutIncerta("O gateway devolveu referência incompatível; exige conciliação.")
        if esperado_setup:
            if str(remoto.status) != "open":
                raise FalhaCheckoutIncerta("O gateway devolveu setup em estado inseguro; exige conciliação.")
        elif remoto.mode != CheckoutMode.SUBSCRIPTION or str(remoto.status) != "pending":
            raise FalhaCheckoutIncerta("O gateway devolveu checkout em modo ou estado inseguro; exige conciliação.")

    @staticmethod
    def _itens(client: CheckoutClient, variante: str, preco: PrecoPlano | None, valores: dict, moeda: str):
        caps = client.capabilities.checkouts
        if CheckoutMode.SUBSCRIPTION not in caps.modes:
            raise CheckoutIndisponivel("O gateway não suporta checkout recorrente.")
        if not caps.supports_catalog_prices and not caps.supports_inline_prices:
            raise CheckoutIndisponivel("O gateway não suporta preços de catálogo ou inline.")
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
                raise CheckoutIndisponivel("Referência de preço obrigatória não configurada.")
            items.append(CheckoutItem(price=price, quantity=quantidade, reference_id=str(componente)))
        if not items:
            raise CheckoutIndisponivel("Checkout recorrente sem itens cobrados não é suportado.")
        return tuple(items)


def criar_checkout(criacao: CriacaoCheckout) -> ResultadoCheckout:
    """Caso de uso público para consumidores HTTP fora do subapp."""
    return CheckoutsCobranca.criar(criacao)
