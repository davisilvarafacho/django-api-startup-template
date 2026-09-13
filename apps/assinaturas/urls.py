from django.conf import settings
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.assinaturas.views import (
    AceitarPropostaContratualView,
    AlteracoesAssinaturaView,
    AssinaturaView,
    CancelamentoAssinaturaView,
    CatalogoPlanosView,
    RecursosAssinaturaView,
    UtilizacaoSeatsView,
)

router = DefaultRouter()

urlpatterns = [
    path("planos/", CatalogoPlanosView.as_view(), name="catalogo-planos"),
    path("assinatura/", AssinaturaView.as_view(), name="assinatura-atual"),
    path("assinatura/recursos/", RecursosAssinaturaView.as_view(), name="recursos-assinatura"),
    path("assinatura/utilizacao-seats/", UtilizacaoSeatsView.as_view(), name="utilizacao-seats-assinatura"),
    path("assinatura/alteracoes/", AlteracoesAssinaturaView.as_view(), name="alteracoes-assinatura"),
    path("assinatura/cancelamento/", CancelamentoAssinaturaView.as_view(), name="cancelamento-assinatura"),
    path("", include(router.urls)),
]

if "apps.assinaturas.subapps.faturamento" not in settings.BUSINESS_APPS:
    urlpatterns.insert(
        0,
        path(
            "assinatura/propostas/<int:id>/aceitar/",
            AceitarPropostaContratualView.as_view(),
            name="aceitar-proposta",
        ),
    )
