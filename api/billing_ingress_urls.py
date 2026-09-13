"""Superfície HTTP mínima do processo dedicado de ingresso financeiro."""

from django.urls import path

from apps.api.core.health_check import health_check, readiness_check
from apps.api.core.status_handlers import (
    custom_400_handler,
    custom_401_handler,
    custom_403_handler,
    custom_404_handler,
    custom_500_handler,
)
from apps.assinaturas.subapps.faturamento.views import WebhookFaturamentoView

handler400 = custom_400_handler
handler401 = custom_401_handler
handler403 = custom_403_handler
handler404 = custom_404_handler
handler500 = custom_500_handler

urlpatterns = [
    path("health/", health_check, name="billing-ingress-health"),
    path("health/ready/", readiness_check, name="billing-ingress-health-ready"),
    path("faturamento/webhooks/<str:variante>/", WebhookFaturamentoView.as_view(), name="webhook-faturamento"),
]
