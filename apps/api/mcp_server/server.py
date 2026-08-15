from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings

from apps.api.mcp_server.tools import register_tools


def create_mcp_server(
    *,
    token_verifier: TokenVerifier | None = None,
    auth: AuthSettings | None = None,
) -> MCPServer:
    server = MCPServer(
        name="django-api",
        instructions="Operational tools for the Django API.",
        token_verifier=token_verifier,
        auth=auth,
    )
    register_tools(server)
    return server
