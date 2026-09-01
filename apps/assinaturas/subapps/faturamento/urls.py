from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.assinaturas.subapps.faturamento.views import (
    CriarCheckoutAssinaturaView,
    CriarCheckoutFormaPagamentoView,
    ListarCheckoutsView,
    WebhookFaturamentoView,
)

router = DefaultRouter()

urlpatterns = [
    path("assinatura/checkouts/", CriarCheckoutAssinaturaView.as_view(), name="criar-checkout-assinatura"),
    path("faturamento/checkouts/", ListarCheckoutsView.as_view(), name="listar-checkouts-faturamento"),
    path(
        "faturamento/forma-pagamento/checkouts/",
        CriarCheckoutFormaPagamentoView.as_view(),
        name="criar-checkout-forma-pagamento",
    ),
    path("faturamento/webhooks/<str:variante>/", WebhookFaturamentoView.as_view(), name="webhook-faturamento"),
    path("", include(router.urls)),
]
