from django.apps import AppConfig


class AutenticacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api.autenticacao"

    def ready(self):
        from . import checks  # noqa: F401
