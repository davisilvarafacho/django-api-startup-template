from dataclasses import asdict

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.autenticacao.permissions import TokenScopePermission
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.core.errors import APIError, CoreErrorCode
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.models import PropostaComercial
from apps.assinaturas.proposals import ConflitoPropostaComercial, Propostas
from apps.assinaturas.schema import document_proposal_accept
from apps.assinaturas.serializers import AceitarPropostaRequestSerializer, AceitarPropostaResponseSerializer
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Papel
from apps.organizacoes.permissions import TenantPermission


class AceitarPropostaView(APIView):
    """Traduz o aceite HTTP para o caso de uso nominal, sem CRUD de proposta."""

    permission_classes = [
        IsAuthenticated,
        TenantPermission,
        TokenScopePermission,
        RecentAuthenticationPermission,
    ]
    session_only = True

    @document_proposal_accept
    @require_recent_auth()
    def post(self, request, id: int):
        serializer = AceitarPropostaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vinculo = getattr(request, "vinculo", None)
        if vinculo is None or vinculo.papel != Papel.PROPRIETARIO:
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=status.HTTP_403_FORBIDDEN)

        proposta = PropostaComercial.objects.filter(pk=id, organizacao_id=request.organizacao_id).first()
        if proposta is None:
            raise APIError(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)

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
            "preparacao_checkout": asdict(resultado.preparacao_checkout) if resultado.preparacao_checkout is not None else None,
        }
        return Response(AceitarPropostaResponseSerializer(payload).data)
