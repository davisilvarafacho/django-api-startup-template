import atexit

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.api.core'

    def ready(self):
        from django.conf import settings

        import posthog

        posthog.api_key = settings.POSTHOG_PROJECT_TOKEN
        posthog.host = settings.POSTHOG_HOST
        posthog.disabled = settings.POSTHOG_DISABLED

        if settings.DEBUG:
            posthog.debug = True

        atexit.register(posthog.shutdown)
