"""Registry das rotas isentas de organização.

Por padrão **toda** rota exige um `X-Organization` válido (a `TenantPermission`
está em `DEFAULT_PERMISSION_CLASSES`). Rotas que são autenticadas mas não
pertencem a nenhuma organização — listar as organizações do usuário, aceitar um
convite, editar o próprio perfil — se declaram aqui.

Cada app expõe um `tenant_free_routes.py`:

    TENANT_FREE_ROUTES = ["/auth/organizacoes/"]

Rotas públicas (sem token) já são isentas por consequência: sem usuário não há
vínculo a validar.
"""
from apps.api.core.routes_registry import RouteRegistry

__all__ = ["tenant_free_registry"]

tenant_free_registry = RouteRegistry(
    file_name="tenant_free_routes",
    attr_name="TENANT_FREE_ROUTES",
    # O admin do Django não passa pelo DRF, mas fica explícito aqui.
    defaults={"/admin/", "/health/"},
)
