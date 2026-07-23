import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from django_scalar.views import scalar_viewer
from drf_spectacular.views import SpectacularAPIView

from apps.api.core.health_check import health_check, readiness_check
from apps.api.core.metrics import metrics_view
from apps.api.core.status_handlers import (
    custom_400_handler,
    custom_401_handler,
    custom_403_handler,
    custom_404_handler,
    custom_500_handler,
)

apps_urls = [
    path("", include(app + ".urls"))
    for app in settings.BUSINESS_APPS
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
    # Health checks e métricas ficam FORA do bloco de desenvolvimento: o
    # `HEALTHCHECK` do Dockerfile aponta para `/health/` e, registrado só em dev,
    # o container era marcado unhealthy em produção.
    path("health/", health_check, name="health"),
    path("health/ready/", readiness_check, name="health-ready"),
    path("metrics", metrics_view, name="metrics"),
    *apps_urls,
    *static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]

# Mesma fonte de verdade que `configure_enviroment` usa para decidir apps e
# middlewares. Com `IN_DEVELOPMENT` (que exige `DJANGO_ENVIRONMENT=development`
# explícito) as duas decisões divergiam quando a variável não estava definida: o
# middleware do debug_toolbar era carregado pelo fallback "development", mas as
# URLs dele não eram registradas — e toda resposta virava 500 com
# `NoReverseMatch: 'djdt'`.
if settings.CONFIG_ENVIRONMENT == "development":
    # Import local: debug_toolbar só está instalado no ambiente de desenvolvimento.
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns.append(path("sentry-error/", lambda request: 1 / 0))

    urlpatterns.append(path("hijack/", include("hijack.urls")))

    urlpatterns += debug_toolbar_urls()
