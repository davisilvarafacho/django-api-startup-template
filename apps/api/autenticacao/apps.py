from django.apps import AppConfig


class AutenticacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api.autenticacao"

    def ready(self):
        from internal_frameworks.permission_cache.signals.django import connect_django_signals
        from internal_frameworks.permission_cache.signals.guardian import connect_guardian_signals

        from . import checks  # noqa: F401

        connect_django_signals()
        connect_guardian_signals()
