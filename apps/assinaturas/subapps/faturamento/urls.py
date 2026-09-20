from django.urls import include, path

from apps.api.core.routers import DefaultRouter
from apps.assinaturas.subapps.faturamento.views import (
    AceitarPropostaView,
    CriarCheckoutAssinaturaView,
    CriarCheckoutFormaPagamentoView,
    ListarCheckoutsView,
    ListarFaturasView,
    WebhookFaturamentoView,
)

router = DefaultRouter()

urlpatterns = [
    path("assinatura/propostas/<int:id>/aceitar/", AceitarPropostaView.as_view(), name="aceitar-proposta"),
    path("assinatura/checkouts/", CriarCheckoutAssinaturaView.as_view(), name="criar-checkout-assinatura"),
    path("faturamento/checkouts/", ListarCheckoutsView.as_view(), name="listar-checkouts-faturamento"),
    path("faturamento/faturas/", ListarFaturasView.as_view(), name="listar-faturas-faturamento"),
    path(
        "faturamento/forma-pagamento/checkouts/",
        CriarCheckoutFormaPagamentoView.as_view(),
        name="criar-checkout-forma-pagamento",
    ),
    path("faturamento/webhooks/<str:variante>/", WebhookFaturamentoView.as_view(), name="webhook-faturamento"),
    path("", include(router.urls)),
]
