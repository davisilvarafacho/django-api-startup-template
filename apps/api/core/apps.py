import atexit

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.api.core'

    def ready(self):
        from django.conf import settings

        import posthog

        from .routes_registry import routes_registry

        # Varre os BUSINESS_APPS atrás de `public_routes.PUBLIC_ROUTES`. Sem isso
        # o AuthenticationMiddleware não sabe quais rotas dispensam token.
        routes_registry.discover()

        posthog.api_key = settings.POSTHOG_PROJECT_TOKEN
        posthog.host = settings.POSTHOG_HOST
        posthog.disabled = settings.POSTHOG_DISABLED

        if settings.DEBUG:
            posthog.debug = True

        atexit.register(posthog.shutdown)
