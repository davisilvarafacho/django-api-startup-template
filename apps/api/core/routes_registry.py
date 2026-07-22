import importlib
import logging

from django.conf import settings

__all__ = ["routes_registry"]

FILE_NAME = "public_routes"
ATTR_NAME = "PUBLIC_ROUTES"


logger = logging.getLogger(__name__)


class RouteRegistry:
    def __init__(self):
        self._paths = {
            # default public paths that don't require discovery
            "/admin/",
            "/health/",
        }
        self._discovered = False

    def is_public(self, path: str) -> bool:
        self._ensure_discovered()
        return any(path.startswith(p) for p in self._paths)

    def discover(self) -> None:
        if self._discovered:
            return
        for app_name in settings.BUSINESS_APPS:
            self._load_app_routes(app_name)
        self._discovered = True

    def _load_app_routes(self, app_name: str) -> None:
        try:
            module = importlib.import_module(f"{app_name}.{FILE_NAME}")
        except ModuleNotFoundError:
            return

        routes = getattr(module, ATTR_NAME, None)
        if routes is None:
            logger.debug("%s.%s existe mas não define %s. Ignorando.", app_name, FILE_NAME, ATTR_NAME)
            return

        if not isinstance(routes, (list, tuple)):
            raise TypeError(f"{app_name}.{FILE_NAME}.{ATTR_NAME} deve ser list ou tuple, recebeu: {type(routes).__name__}")

        for path in routes:
            if not isinstance(path, str):
                raise TypeError(f"{app_name}.{FILE_NAME}.{ATTR_NAME} contém entrada inválida: {path!r}. Deve ser uma string.")
            self._paths.add(path)

    def _ensure_discovered(self) -> None:
        if not self._discovered:
            raise RuntimeError("RouteRegistry.discover() não foi chamado. Verifique se CoreConfig.ready() está configurado.")

    def __repr__(self) -> str:
        return f"RouteRegistry(paths={len(self._paths)}, discovered={self._discovered})"


routes_registry = RouteRegistry()
