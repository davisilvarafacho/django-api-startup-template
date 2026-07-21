from django.urls import path

from apps.api.core.lookup import LookupView

urlpatterns = [
    path("lookup/<str:key>/", LookupView.as_view(), name="lookup"),
]
