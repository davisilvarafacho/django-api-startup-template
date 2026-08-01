import atexit

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api.core"

    def ready(self):
        from .errors import discover_error_codes
        from .routes_registry import routes_registry
        from .scope_registry import discover_scope_resources

        # Precisa vir antes de qualquer outra coisa: o exception handler e o
        # system check de códigos de erro dependem do registry já populado.
        discover_error_codes()

        # Idem para o registry `resource:action`: TokenScopePermission e a
        # tradução para codenames Django dependem dele já povoado.
        discover_scope_resources()

        # Varre os BUSINESS_APPS atrás de `public_routes.PUBLIC_ROUTES`. Sem isso
        # o AuthenticationMiddleware não sabe quais rotas dispensam token.
        routes_registry.discover()

        self.configurar_posthog()
        self.configurar_telemetria()

    def configurar_posthog(self):
        """Inicializa o SDK do PostHog.

        Sem token (ou em teste) o SDK fica desligado explicitamente: caso
        contrário ele tenta enfileirar e enviar eventos para a API do PostHog a
        cada request, poluindo a saída de teste e o log de desenvolvimento.
        """
        from django.conf import settings

        import posthog

        posthog.api_key = settings.POSTHOG_PROJECT_TOKEN
        posthog.host = settings.POSTHOG_HOST
        posthog.disabled = settings.POSTHOG_DISABLED or settings.TESTING or not settings.POSTHOG_PROJECT_TOKEN

        if posthog.disabled:
            return

        # O modo debug do SDK sobe o próprio logger para DEBUG; ligar isso com o
        # PostHog desligado só enche o console de "consumer is running".
        if settings.DEBUG:
            posthog.debug = True

        # Só registra o flush final quando há de fato o que enviar: sem api_key,
        # `shutdown()` instancia um client e loga erro já com o stdout fechado.
        atexit.register(posthog.shutdown)

    def configurar_telemetria(self):
        """Liga o OpenTelemetry quando `OTEL_ENABLED` estiver ativo."""
        from api.telemetry import setup_telemetry

        setup_telemetry()
