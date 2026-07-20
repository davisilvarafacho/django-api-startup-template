import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from debug_toolbar.toolbar import debug_toolbar_urls
from django_scalar.views import scalar_viewer
from drf_spectacular.views import SpectacularAPIView

from apps.api.core.health_check import health_check
from apps.api.core.status_handlers import (
    custom_400_handler,
    custom_401_handler,
    custom_403_handler,
    custom_404_handler,
    custom_500_handler,
)

apps_urls = [
    path("", include(app + ".urls"))
    for app in settings.BASE_APPS
    if os.path.exists(os.path.join(settings.BASE_DIR, app.replace(".", "/"), "urls.py"))
]

handler400 = custom_400_handler
handler404 = custom_404_handler
handler403 = custom_403_handler
handler401 = custom_401_handler
handler500 = custom_500_handler

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", scalar_viewer, name="scalar-docs"),
    *apps_urls,
    *static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]

if settings.IN_DEVELOPMENT:
    urlpatterns.append(path("health/", health_check))
    urlpatterns.append(path("sentry-error/", lambda request: 1 / 0))

    urlpatterns.append(path("hijack/", include("hijack.urls")))

    urlpatterns += debug_toolbar_urls()
