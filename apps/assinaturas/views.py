from dataclasses import asdict

from django.db.models import Prefetch
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.autenticacao.permissions import TokenScopePermission
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.core.errors import APIError, CoreErrorCode, ValidationErrorCode
from apps.api.core.route_markers import no_tenancy, regularizacao_assinatura
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    Plano,
    PrecoPlano,
    PropostaComercial,
    StatusAssinatura,
    VersaoPlano,
)
from apps.assinaturas.proposals import ConflitoPropostaComercial, Propostas
from apps.assinaturas.schema import (
    document_contractual_proposal_accept,
    document_plan_catalog_get,
    document_subscription_cancel,
    document_subscription_cancel_delete,
    document_subscription_change,
    document_subscription_get,
    document_subscription_resources_get,
    document_subscription_usage_get,
)
from apps.assinaturas.serializers import (
    AceitarPropostaRequestSerializer,
    AceitarPropostaResponseSerializer,
    AlteracaoAssinaturaResponseSerializer,
    AssinaturaResponseSerializer,
    CancelamentoAssinaturaRequestSerializer,
    CancelamentoAssinaturaResponseSerializer,
    PlanoCatalogoSerializer,
    RecursosAssinaturaResponseSerializer,
    SolicitarAlteracaoRequestSerializer,
    UtilizacaoSeatsResponseSerializer,
)
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoIdempotenciaAssinatura,
    ConflitoRevisaoAssinatura,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Papel
from apps.organizacoes.permissions import TenantPermission
from apps.usuarios.policies import exigir_email_verificado


def _exigir_papel(request, papel_minimo):
    vinculo = getattr(request, "vinculo", None)
    if vinculo is None or vinculo.papel < papel_minimo:
        raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=status.HTTP_403_FORBIDDEN)


@no_tenancy
class CatalogoPlanosView(APIView):
    """Expõe somente versões publicadas que podem originar novo contrato."""

    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission]
    session_only = True

    @document_plan_catalog_get
    def get(self, request):
        del request
        precos = PrecoPlano.ativos.order_by("periodicidade", "moeda", "pk")
        versoes = (
            VersaoPlano.ativos.filter(
                atual=True,
                publicada_em__isnull=False,
                precos__is_active=True,
                precos__is_deleted=False,
            )
            .distinct()
            .prefetch_related(Prefetch("precos", queryset=precos, to_attr="precos_contrataveis"))
        )
        planos = (
            Plano.ativos.filter(
                visivel=True,
                versoes__atual=True,
                versoes__publicada_em__isnull=False,
                versoes__is_active=True,
                versoes__is_deleted=False,
                versoes__precos__is_active=True,
                versoes__precos__is_deleted=False,
            )
            .distinct()
            .prefetch_related(Prefetch("versoes", queryset=versoes, to_attr="versoes_contrataveis"))
            .order_by("nome", "pk")
        )
        return Response(PlanoCatalogoSerializer(planos, many=True).data)


class _AssinaturaSessionView(APIView):
    permission_classes = [
        IsAuthenticated,
        TenantPermission,
        TokenScopePermission,
        RecentAuthenticationPermission,
    ]
    session_only = True


@regularizacao_assinatura
class AceitarPropostaContratualView(_AssinaturaSessionView):
    """Mantém o aceite contratual disponível sem instalar faturamento."""

    @document_contractual_proposal_accept
    @require_recent_auth()
    def post(self, request, id: int):
        serializer = AceitarPropostaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _exigir_papel(request, Papel.PROPRIETARIO)
        exigir_email_verificado(request.user)
        with organizacao_atual_privilegiada(request.organizacao_id):
            proposta = PropostaComercial.objects.filter(pk=id, organizacao_id=request.organizacao_id).first()
        if proposta is None:
            raise APIError(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
        if proposta.modo_ativacao != ModoAtivacaoProposta.CONTRATUAL:
            raise APIError(BillingErrorCode.PROPOSAL_INVALID, status_code=status.HTTP_409_CONFLICT)
        try:
            resultado = Propostas.aceitar(
                proposta,
                ator=request.user,
                revisao_esperada=serializer.validated_data["revisao_esperada"],
            )
        except ConflitoPropostaComercial as exc:
            raise APIError(BillingErrorCode.PROPOSAL_INVALID, status_code=status.HTTP_409_CONFLICT) from exc
        proposta = resultado.proposta
        payload = {
            "id": proposta.pk,
            "status": proposta.status,
            "revisao": proposta.revisao,
            "modo_ativacao": proposta.modo_ativacao,
            "preparacao_checkout": None,
        }
        return Response(AceitarPropostaResponseSerializer(payload).data)


@regularizacao_assinatura
class AssinaturaView(_AssinaturaSessionView):
    @document_subscription_get
    def get(self, request):
        _exigir_papel(request, Papel.ADMINISTRADOR)
        assinatura = request.tenant.assinatura
        return Response(
            AssinaturaResponseSerializer(
                assinatura,
                context={"situacao_acesso": request.tenant.situacao_acesso},
            ).data
        )


@regularizacao_assinatura
class RecursosAssinaturaView(_AssinaturaSessionView):
    @document_subscription_resources_get
    def get(self, request):
        _exigir_papel(request, Papel.VISUALIZADOR)
        recursos = ValoresRecursos(CATALOGO_RECURSOS, request.tenant.assinatura.recursos).materializar()
        return Response(RecursosAssinaturaResponseSerializer(recursos).data)


@regularizacao_assinatura
class UtilizacaoSeatsView(_AssinaturaSessionView):
    @document_subscription_usage_get
    def get(self, request):
        _exigir_papel(request, Papel.ADMINISTRADOR)
        return Response(UtilizacaoSeatsResponseSerializer(asdict(request.tenant.utilizacao_seats)).data)


@regularizacao_assinatura
class AlteracoesAssinaturaView(_AssinaturaSessionView):
    @document_subscription_change
    @require_recent_auth()
    def post(self, request):
        serializer = SolicitarAlteracaoRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _exigir_papel(request, Papel.PROPRIETARIO)
        exigir_email_verificado(request.user)
        dados = serializer.validated_data
        try:
            alteracao = Assinaturas.solicitar_alteracao_catalogo(
                request.tenant.assinatura,
                tipo=dados["tipo"],
                revisao_esperada=dados["revisao_esperada"],
                chave_idempotencia=dados["chave_idempotencia"],
                solicitada_por=request.user,
                versao_plano_id=dados.get("versao_plano_id"),
                periodicidade=dados.get("periodicidade"),
                seats_contratados=dados.get("seats_contratados"),
                seats_consumidos=request.tenant.utilizacao_seats.consumidos,
            )
        except (ConflitoRevisaoAssinatura, ConflitoIdempotenciaAssinatura) as exc:
            raise APIError(BillingErrorCode.SUBSCRIPTION_CONFLICT, status_code=status.HTTP_409_CONFLICT) from exc
        except ValueError as exc:
            raise APIError(ValidationErrorCode.INVALID, status_code=status.HTTP_400_BAD_REQUEST) from exc
        return Response(AlteracaoAssinaturaResponseSerializer(alteracao).data, status=status.HTTP_201_CREATED)


@regularizacao_assinatura
class CancelamentoAssinaturaView(_AssinaturaSessionView):
    @document_subscription_cancel
    @require_recent_auth()
    def post(self, request):
        serializer = CancelamentoAssinaturaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _exigir_papel(request, Papel.PROPRIETARIO)
        exigir_email_verificado(request.user)
        try:
            Assinaturas.solicitar_encerramento(
                request.organizacao,
                agora=timezone.now(),
                revisao_esperada=serializer.validated_data["revisao_esperada"],
                ator=request.user,
            )
        except ConflitoRevisaoAssinatura as exc:
            raise APIError(BillingErrorCode.SUBSCRIPTION_CONFLICT, status_code=status.HTTP_409_CONFLICT) from exc
        assinatura = AssinaturaOrganizacao.all_objects.get(pk=request.tenant.assinatura.pk)
        resposta_status = status.HTTP_200_OK if assinatura.status == StatusAssinatura.ENCERRADA else status.HTTP_202_ACCEPTED
        return Response(CancelamentoAssinaturaResponseSerializer(assinatura).data, status=resposta_status)

    @document_subscription_cancel_delete
    @require_recent_auth()
    def delete(self, request):
        serializer = CancelamentoAssinaturaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _exigir_papel(request, Papel.PROPRIETARIO)
        exigir_email_verificado(request.user)
        try:
            Assinaturas.cancelar_encerramento(
                request.organizacao,
                revisao_esperada=serializer.validated_data["revisao_esperada"],
                ator=request.user,
            )
        except ConflitoRevisaoAssinatura as exc:
            raise APIError(BillingErrorCode.SUBSCRIPTION_CONFLICT, status_code=status.HTTP_409_CONFLICT) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)
