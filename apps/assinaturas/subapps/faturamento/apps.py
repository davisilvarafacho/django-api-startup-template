from django.apps import AppConfig


class FaturamentoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assinaturas.subapps.faturamento"
    verbose_name = "Faturamento"

    def ready(self):
        from . import (
            checks,  # noqa: F401
            tasks,  # noqa: F401
        )
