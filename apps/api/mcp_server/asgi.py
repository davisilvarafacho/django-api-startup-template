from __future__ import annotations

from typing import TYPE_CHECKING

from apps.api.mcp_server.bootstrap import setup_django

if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from starlette.types import ASGIApp


def create_asgi_app(
    *,
    token_verifier: TokenVerifier | None = None,
    auth: AuthSettings | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    setup_django()

    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.cors import CORSMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from apps.api.mcp_server.authentications import build_http_auth
    from apps.api.mcp_server.server import create_mcp_server

    if token_verifier is None and auth is None:
        configuration = build_http_auth()
        token_verifier = configuration.verifier
        auth = configuration.auth
        hosts = configuration.allowed_hosts
        origins = configuration.allowed_origins
    elif token_verifier is None or auth is None:
        raise ImproperlyConfigured("token_verifier and auth must be supplied together")
    else:
        hosts = settings.MCP_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
        origins = settings.MCP_ALLOWED_ORIGINS if allowed_origins is None else allowed_origins

    if not hosts:
        raise ImproperlyConfigured("MCP_ALLOWED_HOSTS must not be empty")

    server = create_mcp_server(token_verifier=token_verifier, auth=auth)

    @server.custom_route("/live", methods=["GET"], include_in_schema=False)
    async def live(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
        host="0.0.0.0",
    )

    class PublicRouteBearerFilter:
        def __init__(self, wrapped: ASGIApp) -> None:
            self.wrapped = wrapped

        async def __call__(self, scope, receive, send) -> None:
            path = scope.get("path", "")
            if scope["type"] == "http" and (path == "/live" or path.startswith("/.well-known/oauth-protected-resource")):
                scope = dict(scope)
                scope["headers"] = [(name, value) for name, value in scope.get("headers", ()) if name.lower() != b"authorization"]
            await self.wrapped(scope, receive, send)

    class McpCorsMiddleware:
        def __init__(self, wrapped: ASGIApp) -> None:
            self.wrapped = wrapped
            self.cors = CORSMiddleware(
                wrapped,
                allow_origins=origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=[
                    "Authorization",
                    "Content-Type",
                    "Mcp-Protocol-Version",
                    "Mcp-Method",
                    "Mcp-Name",
                    "Mcp-Session-Id",
                ],
                expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
            )

        async def __call__(self, scope, receive, send) -> None:
            if scope["type"] == "http" and scope.get("path") == "/mcp":
                await self.cors(scope, receive, send)
                return
            await self.wrapped(scope, receive, send)

    if origins:
        app = McpCorsMiddleware(app)
    return PublicRouteBearerFilter(app)
