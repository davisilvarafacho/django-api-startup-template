from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema_view

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.permissions import CustomDjangoModelPermissions, TokenScopePermission, require_token_scopes
from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.core.errors import APIError
from apps.api.core.scope_mixins import ScopeResourceMixin
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from apps.organizacoes.onboarding import OrganizationOnboarding
from apps.organizacoes.organizations import (
    AssinaturasCicloOrganizacao,
    EncerramentoAgendado,
    EncerramentoSemAlteracao,
    Organizacoes,
)
from apps.organizacoes.permissions import CanAcceptConvitePermission, PapelMinimoPermission, TenantPermission
from apps.organizacoes.schema import (
    document_invitation_accept,
    document_membership_delete,
    document_membership_update,
    document_organization_billing_email_update,
    document_organization_closure_delete,
    document_organization_closure_post,
)
from apps.organizacoes.serializers import (
    AceitarConviteResponseSerializer,
    AceitarConviteSerializer,
    ConviteCreateSerializer,
    ConviteSerializer,
    OrganizacaoEmailFaturamentoSerializer,
    OrganizacaoSerializer,
    TimeSerializer,
    VinculoSerializer,
)
from apps.organizacoes.teams import Times
from apps.usuarios.policies import exigir_email_verificado


def _carregar_assinaturas() -> type[AssinaturasCicloOrganizacao]:
    """Importa o ciclo comercial real somente quando a Task 9 estiver instalada."""
    from apps.assinaturas.subscriptions import Assinaturas

    return Assinaturas


@extend_schema_view(
    update=document_organization_billing_email_update,
    partial_update=document_organization_billing_email_update,
)
class OrganizacaoViewSet(
    ScopeResourceMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = OrganizacaoSerializer
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, CustomDjangoModelPermissions, RecentAuthenticationPermission]
    # Sem `queryset` estático (depende do usuário autenticado); a superfície
    # pública corresponde ao model mesmo assim.
    scope_resource = "organizations"
    session_only_actions = {"create", "encerramento", "update", "partial_update"}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organizacao = OrganizationOnboarding.criar(usuario=request.user, **serializer.validated_data)
        output = self.get_serializer(organizacao)
        return Response(output.data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        auth_token = getattr(self.request, "auth", None)
        if getattr(auth_token, "type", None) == TokenType.API_KEY:
            return Organizacao.objects.filter(pk=auth_token.organization_id, is_active=True)

        organizacao_ids = Vinculo.objects.filter(
            usuario=self.request.user,
            is_active=True,
            organizacao__is_active=True,
        ).values_list("organizacao_id", flat=True)
        return Organizacao.objects.filter(id__in=organizacao_ids, is_active=True).order_by("nome")

    def get_serializer_class(self):
        if self.action in {"update", "partial_update"}:
            return OrganizacaoEmailFaturamentoSerializer
        return super().get_serializer_class()

    @require_recent_auth()
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @require_recent_auth()
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        auth_token = getattr(self.request, "auth", None)
        if getattr(auth_token, "type", None) == TokenType.API_KEY:
            context["include_personal_role"] = False
            return context

        vinculos = Vinculo.objects.filter(
            usuario=self.request.user,
            is_active=True,
            organizacao_id__in=self.get_queryset().values_list("id", flat=True),
        )
        context["vinculos_por_organizacao"] = {vinculo.organizacao_id: vinculo for vinculo in vinculos}
        return context

    def _organizacao_do_proprietario(self):
        organizacao = self.get_object()
        vinculo = Vinculo.objects.filter(
            organizacao=organizacao,
            usuario=self.request.user,
            is_active=True,
        ).first()
        if vinculo is None or vinculo.papel != Papel.PROPRIETARIO:
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=403)
        return organizacao

    @document_organization_closure_post
    @document_organization_closure_delete
    @action(detail=True, methods=["post", "delete"], url_path="encerramento")
    @require_recent_auth()
    def encerramento(self, request, *args, **kwargs):
        organizacao = self._organizacao_do_proprietario()
        exigir_email_verificado(request.user)
        assinaturas = _carregar_assinaturas()
        if request.method == "DELETE":
            Organizacoes.cancelar_encerramento(organizacao, assinaturas=assinaturas, ator=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)

        resultado = Organizacoes.solicitar_encerramento(
            organizacao,
            assinaturas=assinaturas,
            ator=request.user,
        )
        if isinstance(resultado, EncerramentoAgendado):
            return Response({"scheduled_for": resultado.agendado_para.isoformat()}, status=status.HTTP_202_ACCEPTED)
        if isinstance(resultado, EncerramentoSemAlteracao) and resultado.agendado_para is not None:
            return Response({"scheduled_for": resultado.agendado_para.isoformat()}, status=status.HTTP_202_ACCEPTED)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantViewSetMixin(ScopeResourceMixin):
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, CustomDjangoModelPermissions, PapelMinimoPermission]
    papel_minimo = Papel.VISUALIZADOR

    def get_organizacao_id(self):
        return self.request.organizacao_id


class TimeViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    serializer_class = TimeSerializer
    queryset = Time.objects.select_related("organizacao")
    papeis_por_action = {
        "list": Papel.VISUALIZADOR,
        "retrieve": Papel.VISUALIZADOR,
        "create": Papel.GESTOR,
        "update": Papel.GESTOR,
        "partial_update": Papel.GESTOR,
        "destroy": Papel.GESTOR,
    }

    def get_queryset(self):
        return super().get_queryset().filter(organizacao_id=self.get_organizacao_id(), is_active=True).order_by("nome")

    def perform_create(self, serializer):
        serializer.save(organizacao_id=self.get_organizacao_id())

    def perform_destroy(self, instance):
        Times.remover(
            instance,
            ator=self.request.user,
            validar_papel_ator=getattr(getattr(self.request, "auth", None), "type", None) != TokenType.API_KEY,
        )


@extend_schema_view(
    update=document_membership_update,
    partial_update=document_membership_update,
    destroy=document_membership_delete,
)
class VinculoViewSet(
    TenantViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = VinculoSerializer
    queryset = Vinculo.objects.select_related("usuario", "organizacao").prefetch_related("times")
    session_only_actions = {"destroy"}
    papeis_por_action = {
        "list": Papel.VISUALIZADOR,
        "retrieve": Papel.VISUALIZADOR,
        "update": Papel.ADMINISTRADOR,
        "partial_update": Papel.ADMINISTRADOR,
        "destroy": Papel.ADMINISTRADOR,
    }

    def get_queryset(self):
        return super().get_queryset().filter(organizacao_id=self.get_organizacao_id(), is_active=True).order_by("usuario__email")

    def perform_destroy(self, instance):
        Vinculos.remover_vinculo(
            instance,
            ator=self.request.user,
            validar_papel_ator=getattr(getattr(self.request, "auth", None), "type", None) != TokenType.API_KEY,
        )


class ConviteViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ConviteSerializer
    queryset = Convite.objects.select_related("convidado_por", "organizacao")
    papeis_por_action = {
        "list": Papel.GESTOR,
        "retrieve": Papel.GESTOR,
        "create": Papel.GESTOR,
        "update": Papel.GESTOR,
        "partial_update": Papel.GESTOR,
        "destroy": Papel.GESTOR,
    }

    def get_permissions(self):
        if self.action == "aceitar":
            return [IsAuthenticated(), TenantPermission(), TokenScopePermission(), CanAcceptConvitePermission()]
        return super().get_permissions()

    def get_queryset(self):
        return super().get_queryset().filter(organizacao_id=self.get_organizacao_id(), is_active=True).order_by("-id")

    def get_serializer_class(self):
        if self.action == "create":
            return ConviteCreateSerializer
        if self.action == "aceitar":
            return AceitarConviteSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(organizacao_id=self.get_organizacao_id(), convidado_por=self.request.user)

    def perform_destroy(self, instance):
        Vinculos.remover_convite(
            instance,
            ator=self.request.user,
            validar_papel_ator=getattr(getattr(self.request, "auth", None), "type", None) != TokenType.API_KEY,
        )

    @document_invitation_accept
    @action(detail=False, methods=["post"], url_path="aceitar")
    @require_token_scopes("invitations:accept")
    def aceitar(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vinculo = serializer.save()
        response_serializer = AceitarConviteResponseSerializer(
            {"organizacao": vinculo.organizacao, "vinculo": vinculo},
            context={"request": request},
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )
