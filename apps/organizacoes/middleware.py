"""Ponto único de resolução do tenant antes da view."""

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When

from django_rls.context import clear_rls_context

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenType
from apps.api.core.errors import APIError, error_response_for_api_error
from apps.api.core.route_markers import (
    MARCADOR_IO_EXTERNO_SEM_TRANSACAO,
    MARCADOR_PUBLICA,
    MARCADOR_REGULARIZACAO_ASSINATURA,
    MARCADOR_SEM_TENANCY,
    rota_tem_marcador,
)
from apps.api.core.routes_registry import routes_registry
from apps.organizacoes.constants import HEADER_ORGANIZACAO, META_HEADER_ORGANIZACAO
from apps.organizacoes.context import (
    CHAVE_TENANT,
    ContextoOrganizacao,
    PoliticaComercialTenant,
    definir_organizacao_atual,
)
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Vinculo
from apps.organizacoes.routes import tenant_free_registry


def _is_api_key(request):
    return getattr(getattr(request, "auth", None), "type", None) == TokenType.API_KEY


def resolve_token_organization(request, token):
    """Resolve o slug de tenant a partir do header e/ou do token.

    Sessões humanas continuam usando só o header. Uma API key sempre deriva o
    tenant da própria credencial; um header divergente é rejeitado — a key
    nunca opera em outra organização.
    """
    header_slug = request.META.get(META_HEADER_ORGANIZACAO) or None

    token_type = getattr(token, "type", None)
    if token_type != TokenType.API_KEY:
        return header_slug

    organizacao = getattr(token, "organization", None)
    if organizacao is None:
        return header_slug

    if header_slug and header_slug != organizacao.slug:
        raise APIError(
            OrganizationErrorCode.TENANT_MISMATCH,
            status_code=409,
            field=HEADER_ORGANIZACAO,
        )

    return organizacao.slug


def resolver_contexto_comercial(
    organizacao,
    vinculo,
    *,
    regularizacao_assinatura: bool,
) -> ContextoOrganizacao:
    """Carrega o contrato e a ocupação uma vez e aplica a política pura."""
    from django.utils import timezone

    from apps.assinaturas.access_policies import PoliticaAcessoAssinatura, StatusAcesso
    from apps.assinaturas.errors import BillingErrorCode
    from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura
    from apps.assinaturas.subscriptions import Assinaturas
    from apps.organizacoes.memberships import Vinculos
    from apps.organizacoes.models import Papel

    assinatura = (
        AssinaturaOrganizacao.objects.select_related("versao_plano", "proposta_comercial")
        .filter(
            organizacao_id=organizacao.pk,
            status__in=(StatusAssinatura.PENDENTE, StatusAssinatura.EM_TRIAL, StatusAssinatura.ATIVA),
        )
        .first()
    )
    if assinatura is None:
        raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)

    ocupacao = Vinculos.calcular_ocupacao(organizacao, Assinaturas.papeis_isentos_seat(assinatura))
    utilizacao = Assinaturas.calcular_utilizacao(assinatura, ocupacao)
    situacao = PoliticaAcessoAssinatura.avaliar(assinatura, utilizacao, timezone.now())
    contexto = ContextoOrganizacao(
        organizacao=organizacao,
        vinculo=vinculo,
        assinatura=assinatura,
        utilizacao_seats=utilizacao,
        situacao_acesso=situacao,
    )

    pode_regularizar = regularizacao_assinatura and vinculo is not None and vinculo.papel >= Papel.ADMINISTRADOR
    if situacao.status == StatusAcesso.RESTRITO and not pode_regularizar:
        raise APIError(
            BillingErrorCode.ORGANIZATION_RESTRICTED,
            status_code=403,
            context={
                "reasons": [motivo.value for motivo in situacao.motivos],
                "regularize_by": situacao.regularizar_ate.isoformat() if situacao.regularizar_ate is not None else None,
            },
        )
    return contexto


class OrganizacaoMiddleware:
    def __init__(self, get_response, *, politica_comercial: PoliticaComercialTenant | None = None):
        self.get_response = get_response
        self.politica_comercial = politica_comercial if politica_comercial is not None else resolver_contexto_comercial

    def __call__(self, request):
        request.tenant = None
        request.tenant_required = True

        try:
            if rota_tem_marcador(request.path_info, request.method, MARCADOR_IO_EXTERNO_SEM_TRANSACAO):
                with transaction.atomic():
                    try:
                        self._preparar_contexto(request)
                    except APIError as exc:
                        return error_response_for_api_error(exc)
                clear_rls_context({CHAVE_TENANT})
                return self.get_response(request)
            with transaction.atomic():
                try:
                    self._preparar_contexto(request)
                except APIError as exc:
                    return error_response_for_api_error(exc)
                return self.get_response(request)
        finally:
            # A limpeza precisa ocorrer depois que o bloco atômico terminou:
            # dentro de uma transação quebrada, qualquer SQL mascararia a
            # exceção original com TransactionManagementError. O `SET LOCAL`
            # morre no commit/rollback, mas o rastreio em memória da lib não.
            clear_rls_context({CHAVE_TENANT})

    def _preparar_contexto(self, request):
        request.organizacao_slug = resolve_token_organization(request, getattr(request, "auth", None))

        if _is_api_key(request):
            self._resolver_api_key(request)
            return

        if self._is_tenant_free(request):
            request.tenant_required = False
            return

        slug = request.organizacao_slug
        if not slug:
            raise APIError(
                OrganizationErrorCode.HEADER_REQUIRED,
                status_code=422,
                field=HEADER_ORGANIZACAO,
            )

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return

        prioridade_organizacao = Case(
            When(organizacao__is_deleted=False, organizacao__is_active=True, then=Value(0)),
            When(organizacao__is_deleted=False, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        vinculo = (
            Vinculo.objects.select_related("organizacao")
            .filter(usuario_id=user.pk, organizacao__slug=slug)
            .order_by(prioridade_organizacao, "organizacao_id", "pk")
            .first()
        )
        if vinculo is None:
            raise APIError(
                OrganizationErrorCode.MEMBERSHIP_REQUIRED,
                status_code=403,
                message="Usuário sem vínculo nesta organização.",
            )
        if not vinculo.organizacao.is_active or vinculo.organizacao.is_deleted:
            raise APIError(OrganizationErrorCode.ORGANIZATION_INACTIVE, status_code=403)
        if not vinculo.is_active:
            raise APIError(OrganizationErrorCode.MEMBERSHIP_INACTIVE, status_code=403)

        self._aplicar_contexto(request, organizacao=vinculo.organizacao, vinculo=vinculo)

    def _resolver_api_key(self, request):
        from apps.api.autenticacao.services import ensure_api_key_still_valid

        token = request.auth
        ensure_api_key_still_valid(token)
        if token.suspended_at is not None:
            raise APIError(AuthErrorCode.API_KEY_SUSPENDED, status_code=401)

        organizacao = token.organization
        if not organizacao.is_active or organizacao.is_deleted:
            raise APIError(OrganizationErrorCode.ORGANIZATION_INACTIVE, status_code=403)

        self._aplicar_contexto(request, organizacao=organizacao, vinculo=None)

    def _aplicar_contexto(self, request, *, organizacao, vinculo):
        definir_organizacao_atual(organizacao.pk)
        tenant_comercial = self.politica_comercial(
            organizacao,
            vinculo,
            regularizacao_assinatura=rota_tem_marcador(
                request.path_info,
                request.method,
                MARCADOR_REGULARIZACAO_ASSINATURA,
            ),
        )
        if not isinstance(tenant_comercial, ContextoOrganizacao):
            raise TypeError("PoliticaComercialTenant deve devolver ContextoOrganizacao.")
        if tenant_comercial.organization_id != organizacao.pk:
            raise ValueError("PoliticaComercialTenant não pode trocar a organização da request.")

        tenant = tenant_comercial
        request.tenant = tenant
        request.organizacao = tenant.organizacao
        request.vinculo = tenant.vinculo
        request.organizacao_id = tenant.organization_id

    @staticmethod
    def _is_tenant_free(request):
        path = request.path_info
        method = request.method
        return (
            routes_registry.matches(path)
            or tenant_free_registry.matches(path)
            or rota_tem_marcador(path, method, MARCADOR_PUBLICA)
            or rota_tem_marcador(path, method, MARCADOR_SEM_TENANCY)
        )
