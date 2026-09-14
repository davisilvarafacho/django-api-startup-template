from django.apps import AppConfig


class AssinaturasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assinaturas"
    verbose_name = "Assinaturas"

    def ready(self):
        from . import checks  # noqa: F401
