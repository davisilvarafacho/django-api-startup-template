from django.apps import AppConfig
from django.db.models.signals import post_migrate


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"

    def ready(self):
        from apps.billing.handlers import ensure_free_plan

        post_migrate.connect(ensure_free_plan, dispatch_uid="billing.ensure_free_plan")
