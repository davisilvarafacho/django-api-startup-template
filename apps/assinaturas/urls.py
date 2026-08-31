from django.urls import include, path

from rest_framework.routers import DefaultRouter

from apps.assinaturas.views import AceitarPropostaView

router = DefaultRouter()

urlpatterns = [
    path("assinatura/propostas/<int:id>/aceitar/", AceitarPropostaView.as_view(), name="aceitar-proposta"),
    path("", include(router.urls)),
]
