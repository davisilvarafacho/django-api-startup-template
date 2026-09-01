from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.assinaturas.subapps.faturamento.views import AceitarPropostaView
from apps.assinaturas.views import (
    AlteracoesAssinaturaView,
    AssinaturaView,
    CancelamentoAssinaturaView,
    RecursosAssinaturaView,
    UtilizacaoSeatsView,
)

router = DefaultRouter()

urlpatterns = [
    path("assinatura/", AssinaturaView.as_view(), name="assinatura-atual"),
    path("assinatura/recursos/", RecursosAssinaturaView.as_view(), name="recursos-assinatura"),
    path("assinatura/utilizacao-seats/", UtilizacaoSeatsView.as_view(), name="utilizacao-seats-assinatura"),
    path("assinatura/alteracoes/", AlteracoesAssinaturaView.as_view(), name="alteracoes-assinatura"),
    path("assinatura/cancelamento/", CancelamentoAssinaturaView.as_view(), name="cancelamento-assinatura"),
    path("assinatura/propostas/<int:id>/aceitar/", AceitarPropostaView.as_view(), name="aceitar-proposta"),
    path("", include(router.urls)),
]
