from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.permissions import TokenScopePermission, require_token_scopes
from apps.api.core.scope_mixins import ScopeResourceMixin
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.organizacoes.serializers import (
    AceitarConviteSerializer,
    ConviteCreateSerializer,
    ConviteSerializer,
    OrganizacaoSerializer,
    TimeSerializer,
    VinculoSerializer,
)


class OrganizacaoViewSet(
    ScopeResourceMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = OrganizacaoSerializer
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission]
    # Sem `queryset` estático (depende do usuário autenticado); a superfície
    # pública corresponde ao model mesmo assim.
    scope_resource = "organizations"
    session_only_actions = {"create"}

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


class TenantViewSetMixin(ScopeResourceMixin):
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, PapelMinimoPermission]
    papel_minimo = Papel.VISUALIZADOR

    def get_organizacao(self):
        return self.request.organizacao


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
        return super().get_queryset().filter(organizacao=self.get_organizacao(), is_active=True).order_by("nome")

    def perform_create(self, serializer):
        serializer.save(organizacao=self.get_organizacao())


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
    papeis_por_action = {
        "list": Papel.VISUALIZADOR,
        "retrieve": Papel.VISUALIZADOR,
        "update": Papel.ADMINISTRADOR,
        "partial_update": Papel.ADMINISTRADOR,
        "destroy": Papel.ADMINISTRADOR,
    }

    def get_queryset(self):
        return super().get_queryset().filter(organizacao=self.get_organizacao(), is_active=True).order_by("usuario__email")


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
            return [IsAuthenticated(), TenantPermission(), TokenScopePermission()]
        return super().get_permissions()

    def get_queryset(self):
        return super().get_queryset().filter(organizacao=self.get_organizacao(), is_active=True).order_by("-id")

    def get_serializer_class(self):
        if self.action == "create":
            return ConviteCreateSerializer
        if self.action == "aceitar":
            return AceitarConviteSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(organizacao=self.get_organizacao(), convidado_por=self.request.user)

    @action(detail=False, methods=["post"], url_path="aceitar")
    @require_token_scopes("invitations:accept")
    def aceitar(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vinculo = serializer.save()
        return Response(
            {
                "organizacao": OrganizacaoSerializer(vinculo.organizacao, context={"request": request}).data,
                "vinculo": VinculoSerializer(vinculo, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )
