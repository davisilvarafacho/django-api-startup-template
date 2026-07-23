"""Registries de rotas descobertas por convenção.

O padrão é sempre o mesmo: um comportamento é aplicado globalmente por padrão
(o *fallback* do framework) e cada app declara suas exceções num módulo próprio,
coletado no boot. Assim a sobrescrita é fácil e global, sem tocar em settings.

Hoje existem dois registries:

- `routes_registry` — rotas **públicas** (dispensam token). Cada app declara
  `public_routes.py` com `PUBLIC_ROUTES`.
- `tenant_free_registry` — rotas que exigem token mas **não** exigem uma
  organização. Cada app declara `tenant_free_routes.py` com `TENANT_FREE_ROUTES`
  (ver `apps.organizacoes`).

A comparação é por `startswith`, então declare prefixos granulares: `/auth/`
tornaria pública inclusive `logout/`.
"""
import importlib
import logging

from django.conf import settings

__all__ = ["RouteRegistry", "routes_registry"]

logger = logging.getLogger(__name__)


class RouteRegistry:
    """Coleciona prefixos de rota declarados pelos apps.

    Args:
        file_name: Módulo procurado dentro de cada app (sem o `.py`).
        attr_name: Nome da lista exportada por esse módulo.
        defaults: Prefixos válidos independente de descoberta.
    """

    def __init__(self, file_name: str, attr_name: str, defaults=()):
        self.file_name = file_name
        self.attr_name = attr_name
        self._paths = set(defaults)
        self._discovered = False

    def matches(self, path: str) -> bool:
        """Informa se `path` está coberto por algum prefixo registrado."""
        self._ensure_discovered()
        return any(path.startswith(prefixo) for prefixo in self._paths)

    def discover(self) -> None:
        if self._discovered:
            return
        for app_name in settings.BUSINESS_APPS:
            self._load_app_routes(app_name)
        self._discovered = True

    def _load_app_routes(self, app_name: str) -> None:
        try:
            module = importlib.import_module(f"{app_name}.{self.file_name}")
        except ModuleNotFoundError:
            return

        routes = getattr(module, self.attr_name, None)
        if routes is None:
            logger.debug("%s.%s existe mas não define %s. Ignorando.", app_name, self.file_name, self.attr_name)
            return

        if not isinstance(routes, (list, tuple)):
            raise TypeError(f"{app_name}.{self.file_name}.{self.attr_name} deve ser list ou tuple, recebeu: {type(routes).__name__}")

        for path in routes:
            if not isinstance(path, str):
                raise TypeError(f"{app_name}.{self.file_name}.{self.attr_name} contém entrada inválida: {path!r}. Deve ser uma string.")
            self._paths.add(path)

    def _ensure_discovered(self) -> None:
        if not self._discovered:
            raise RuntimeError(f"RouteRegistry({self.file_name}).discover() não foi chamado. Verifique o ready() da AppConfig.")

    def __repr__(self) -> str:
        return f"RouteRegistry({self.file_name}, paths={len(self._paths)}, discovered={self._discovered})"


routes_registry = RouteRegistry(
    file_name="public_routes",
    attr_name="PUBLIC_ROUTES",
    # Rotas públicas que não dependem de descoberta. Health check e métricas são
    # consumidos por orquestrador e scraper, que não têm token — o controle de
    # acesso do /metrics é feito por rede/token próprio (ver core/metrics.py).
    defaults={"/admin/", "/health/", "/metrics"},
)
