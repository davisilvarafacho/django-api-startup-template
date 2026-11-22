"""Rotas da cobrança, explícitas e sob `/cobranca/` (sem router)."""

from django.urls import path

from apps.billing.views import (
    PlanListView,
    SubscriptionCancelView,
    SubscriptionChangeView,
    SubscriptionCheckoutView,
    SubscriptionView,
)

urlpatterns = [
    path("cobranca/planos/", PlanListView.as_view(), name="cobranca-planos"),
    path("cobranca/assinatura/", SubscriptionView.as_view(), name="cobranca-assinatura"),
    path("cobranca/assinatura/checkout/", SubscriptionCheckoutView.as_view(), name="cobranca-assinatura-checkout"),
    path("cobranca/assinatura/alteracao/", SubscriptionChangeView.as_view(), name="cobranca-assinatura-alteracao"),
    path("cobranca/assinatura/cancelamento/", SubscriptionCancelView.as_view(), name="cobranca-assinatura-cancelamento"),
]
