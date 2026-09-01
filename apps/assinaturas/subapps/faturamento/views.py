from dataclasses import asdict

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.autenticacao.permissions import TokenScopePermission
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.core.errors import APIError, CoreErrorCode, ValidationErrorCode
from apps.api.core.route_markers import io_externo_sem_transacao, public, regularizacao_assinatura
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.models import AlteracaoAssinatura, AssinaturaOrganizacao, ModoAtivacaoProposta, PropostaComercial
from apps.assinaturas.proposals import ConflitoPropostaComercial, Propostas
from apps.assinaturas.serializers import AceitarPropostaRequestSerializer, AceitarPropostaResponseSerializer
from apps.assinaturas.subapps.faturamento.checkouts import (
    CheckoutIndisponivel,
    CheckoutPendente,
    ConflitoCheckout,
    CriacaoCheckout,
    FalhaCheckoutIncerta,
    criar_checkout,
)
from apps.assinaturas.subapps.faturamento.errors import ErrosFaturamento
from apps.assinaturas.subapps.faturamento.events import (
    AssinaturaWebhookInvalida,
    ColisaoEventoCobranca,
    EventosCobranca,
    EventoWebhookInvalido,
    VarianteWebhookInvalida,
)
from apps.assinaturas.subapps.faturamento.models import CheckoutCobranca, FinalidadeCheckout
from apps.assinaturas.subapps.faturamento.schema import (
    document_checkout_create,
    document_checkout_list,
    document_proposal_accept,
    document_setup_create,
    document_webhook,
)
from apps.assinaturas.subapps.faturamento.serializers import (
    CheckoutResponseSerializer,
    CriarCheckoutRequestSerializer,
    CriarFormaPagamentoCheckoutRequestSerializer,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Papel
from apps.organizacoes.permissions import TenantPermission


@public
@io_externo_sem_transacao
class WebhookFaturamentoView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @document_webhook
    def post(self, request, variante: str):
        try:
            resultado = EventosCobranca().receber_bytes(variante, request.body, dict(request.headers))
        except VarianteWebhookInvalida as exc:
            raise APIError(ErrosFaturamento.WEBHOOK_VARIANT_INVALID, status_code=status.HTTP_400_BAD_REQUEST) from exc
        except AssinaturaWebhookInvalida as exc:
            raise APIError(ErrosFaturamento.WEBHOOK_SIGNATURE_INVALID, status_code=status.HTTP_400_BAD_REQUEST) from exc
        except EventoWebhookInvalido as exc:
            raise APIError(ErrosFaturamento.WEBHOOK_PROTOCOL_INVALID, status_code=status.HTTP_400_BAD_REQUEST) from exc
        except ColisaoEventoCobranca as exc:
            raise APIError(ErrosFaturamento.WEBHOOK_COLLISION, status_code=status.HTTP_409_CONFLICT) from exc
        return Response({"received": True, "duplicate": not resultado.novo})


class _FaturamentoSessionView(APIView):
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, RecentAuthenticationPermission]
    session_only = True

    @staticmethod
    def exigir_papel(request, minimo):
        if request.vinculo is None or request.vinculo.papel < minimo:
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=status.HTTP_403_FORBIDDEN)


def _executar(criacao):
    try:
        return criar_checkout(criacao)
    except CheckoutPendente as exc:
        raise APIError(ErrosFaturamento.CHECKOUT_PENDENTE, status_code=status.HTTP_409_CONFLICT) from exc
    except CheckoutIndisponivel as exc:
        raise APIError(ErrosFaturamento.CHECKOUT_INDISPONIVEL, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    except FalhaCheckoutIncerta as exc:
        raise APIError(ErrosFaturamento.CHECKOUT_INCERTO, status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    except ConflitoCheckout as exc:
        raise APIError(ErrosFaturamento.CHECKOUT_CONFLITO, status_code=status.HTTP_409_CONFLICT) from exc


@regularizacao_assinatura
@io_externo_sem_transacao
class AceitarPropostaView(_FaturamentoSessionView):
    """Aceita proposta e cria checkout pago sem vazar finanças no módulo principal."""

    @document_proposal_accept
    @require_recent_auth()
    def post(self, request, id: int):
        serializer = AceitarPropostaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.exigir_papel(request, Papel.PROPRIETARIO)
        with organizacao_atual_privilegiada(request.organizacao_id):
            proposta = PropostaComercial.objects.filter(pk=id, organizacao_id=request.organizacao_id).first()
        if proposta is None:
            raise APIError(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
        chave = serializer.validated_data.get("chave_idempotencia")
        if proposta.modo_ativacao == ModoAtivacaoProposta.PAGAMENTO and not chave:
            raise APIError(ValidationErrorCode.INVALID, status_code=status.HTTP_400_BAD_REQUEST)
        try:
            resultado = Propostas.aceitar(proposta, ator=request.user, revisao_esperada=serializer.validated_data["revisao_esperada"])
        except ConflitoPropostaComercial as exc:
            raise APIError(BillingErrorCode.PROPOSAL_INVALID, status_code=status.HTTP_409_CONFLICT) from exc
        proposta = resultado.proposta
        preparacao = asdict(resultado.preparacao_checkout) if resultado.preparacao_checkout is not None else None
        if preparacao is not None:
            assert chave is not None
            with organizacao_atual_privilegiada(request.organizacao_id):
                assinatura = AssinaturaOrganizacao.objects.get(organizacao_id=request.organizacao_id)
            checkout = _executar(
                CriacaoCheckout(
                    assinatura=assinatura,
                    finalidade=FinalidadeCheckout.PROPOSTA,
                    chave_idempotencia=chave,
                    proposta=proposta,
                    ator=request.user,
                )
            ).checkout
            preparacao.update(checkout_id=checkout.pk, checkout_url=checkout.url)
        payload = {
            "id": proposta.pk,
            "status": proposta.status,
            "revisao": proposta.revisao,
            "modo_ativacao": proposta.modo_ativacao,
            "preparacao_checkout": preparacao,
        }
        return Response(AceitarPropostaResponseSerializer(payload).data)


@regularizacao_assinatura
@io_externo_sem_transacao
class CriarCheckoutAssinaturaView(_FaturamentoSessionView):
    @document_checkout_create
    @require_recent_auth()
    def post(self, request):
        self.exigir_papel(request, Papel.PROPRIETARIO)
        serializer = CriarCheckoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dados = serializer.validated_data
        alteracao = None
        proposta = None
        with organizacao_atual_privilegiada(request.organizacao_id):
            if "alteracao_id" in dados:
                alteracao = AlteracaoAssinatura.objects.filter(pk=dados["alteracao_id"]).first()
                if alteracao is None:
                    raise APIError(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
            if "proposta_id" in dados:
                proposta = PropostaComercial.objects.filter(pk=dados["proposta_id"]).first()
                if proposta is None:
                    raise APIError(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
        resultado = _executar(
            CriacaoCheckout(
                assinatura=request.tenant.assinatura,
                finalidade=dados["finalidade"],
                chave_idempotencia=dados["chave_idempotencia"],
                alteracao=alteracao,
                proposta=proposta,
                ator=request.user,
            )
        )
        return Response(CheckoutResponseSerializer(resultado.checkout).data, status=status.HTTP_201_CREATED)


@regularizacao_assinatura
class ListarCheckoutsView(_FaturamentoSessionView):
    @document_checkout_list
    def get(self, request):
        self.exigir_papel(request, Papel.ADMINISTRADOR)
        checkouts = CheckoutCobranca.objects.order_by("-created_at", "-pk")[:100]
        return Response(CheckoutResponseSerializer(checkouts, many=True).data)


@regularizacao_assinatura
@io_externo_sem_transacao
class CriarCheckoutFormaPagamentoView(_FaturamentoSessionView):
    @document_setup_create
    @require_recent_auth()
    def post(self, request):
        self.exigir_papel(request, Papel.ADMINISTRADOR)
        serializer = CriarFormaPagamentoCheckoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resultado = _executar(
            CriacaoCheckout(
                assinatura=request.tenant.assinatura,
                finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
                chave_idempotencia=serializer.validated_data["chave_idempotencia"],
                ator=request.user,
            )
        )
        return Response(CheckoutResponseSerializer(resultado.checkout).data, status=status.HTTP_201_CREATED)
