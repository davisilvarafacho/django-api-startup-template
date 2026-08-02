from django.apps import AppConfig


class OrganizacoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizacoes"
    verbose_name = "Organizações"

    def ready(self):
        from apps.organizacoes.routes import tenant_free_registry

        # Varre os BUSINESS_APPS atrás de `tenant_free_routes.TENANT_FREE_ROUTES`.
        # Sem isso a TenantPermission falha alto ao ser consultada.
        tenant_free_registry.discover()

        from internal_frameworks.permission_cache.signals.tenant import connect_tenant_signals

        connect_tenant_signals()
