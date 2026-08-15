import logging

from mcp.server import MCPServer

from apps.api.core.dependency_health import DependencyHealthReport, collect_dependency_health

logger = logging.getLogger(__name__)


def register_tools(server: MCPServer) -> None:
    @server.tool(name="health", structured_output=True)
    def health() -> DependencyHealthReport:
        try:
            return collect_dependency_health()
        except Exception:
            logger.exception("Unexpected MCP health adapter failure")
            raise RuntimeError("Health check failed") from None
