from django.apps import AppConfig


class BaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api.base"

    def ready(self):
        # O import é o que registra o system check das regras de unicidade
        # multi-tenant (`base.W001` / `base.W002`).
        from . import model_checks  # noqa: F401 -- import por efeito colateral (@register).
