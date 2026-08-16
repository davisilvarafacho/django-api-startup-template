import os
import socket
import subprocess
import sys
import time
from threading import Thread

from django.core.exceptions import ImproperlyConfigured

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from starlette.testclient import TestClient

from apps.api.mcp_server import tools
from apps.api.mcp_server.asgi import create_asgi_app


class StaticTokenVerifier:
    def __init__(self, scopes=None, resource="https://mcp.example.com/mcp"):
        self.scopes = ["health:read"] if scopes is None else scopes
        self.resource = resource
        self.tokens = []

    async def verify_token(self, token):
        self.tokens.append(token)
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=self.scopes,
            resource=self.resource,
        )


def make_auth():
    return AuthSettings.model_validate(
        {
            "issuer_url": "https://auth.example.com",
            "resource_server_url": "https://mcp.example.com/mcp",
            "required_scopes": ["health:read"],
        }
    )


def make_app(*, verifier=None):
    return create_asgi_app(
        token_verifier=verifier or StaticTokenVerifier(),
        auth=make_auth(),
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://client.example.com"],
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def http_server(monkeypatch):
    expected = {
        "status": "healthy",
        "checks": {
            "databases": {"default": {"status": "healthy"}},
            "caches": {"default": {"status": "healthy"}},
        },
    }
    monkeypatch.setattr(tools, "collect_dependency_health", lambda: expected)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    resource = f"{base_url}/mcp"
    auth = AuthSettings.model_validate(
        {
            "issuer_url": "https://auth.example.com",
            "resource_server_url": resource,
            "required_scopes": ["health:read"],
        }
    )
    app = create_asgi_app(
        token_verifier=StaticTokenVerifier(resource=resource),
        auth=auth,
        allowed_hosts=[f"127.0.0.1:{port}"],
        allowed_origins=[],
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    yield base_url, expected

    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    assert not thread.is_alive()


def test_factory_rejects_partial_auth_injection():
    with pytest.raises(ImproperlyConfigured, match="supplied together"):
        create_asgi_app(
            token_verifier=StaticTokenVerifier(),
            allowed_hosts=["mcp.example.com"],
            allowed_origins=[],
        )


def test_uvicorn_factory_bootstraps_django_without_settings_environment():
    with socket.socket() as reserved_socket:
        reserved_socket.bind(("127.0.0.1", 0))
        port = reserved_socket.getsockname()[1]

    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.update(
        {
            "DJANGO_ENVIRONMENT": "development",
            "MCP_SERVER_URL": f"http://127.0.0.1:{port}/mcp",
            "MCP_AUTH_ISSUER_URL": "http://localhost:9000",
            "MCP_AUTH_AUDIENCE": f"http://127.0.0.1:{port}/mcp",
            "MCP_AUTH_JWKS_URL": "http://localhost:9000/jwks",
            "MCP_AUTH_ALGORITHMS": "RS256",
            "MCP_ALLOWED_HOSTS": f"127.0.0.1:{port}",
            "MCP_ALLOWED_ORIGINS": "",
        }
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.api.mcp_server.asgi:create_asgi_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    response = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            try:
                response = httpx2.get(f"http://127.0.0.1:{port}/live", timeout=0.2)
                break
            except httpx2.HTTPError:
                time.sleep(0.05)
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)

    assert response is not None, stderr
    assert response.status_code == 200, stderr
    assert response.json() == {"ok": True}
    assert stdout == ""


def test_liveness_is_public_and_minimal():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.get("/live")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_http_requires_bearer_token_and_exposes_challenge_to_browser():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.post("/mcp", headers={"Origin": "https://client.example.com"}, json={})

    assert response.status_code == 401
    assert "Bearer" in response.headers["www-authenticate"]
    assert "www-authenticate" in response.headers["access-control-expose-headers"].lower()


def test_http_rejects_invalid_bearer_token():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer invalid-token"},
            json={},
        )

    assert response.status_code == 401
    assert "Bearer" in response.headers["www-authenticate"]


def test_http_rejects_missing_required_scope():
    app = make_app(verifier=StaticTokenVerifier(scopes=[]))
    with TestClient(app, base_url="https://mcp.example.com") as client:
        response = client.post("/mcp", headers={"Authorization": "Bearer valid-token"}, json={})

    assert response.status_code == 403
    assert "Bearer" in response.headers["www-authenticate"]


def test_http_rejects_untrusted_host_after_valid_authentication():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer valid-token", "Host": "evil.example.com"},
            json={},
        )

    assert response.status_code == 421


def test_http_rejects_untrusted_origin():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer valid-token", "Origin": "https://evil.example.com"},
            json={},
        )

    assert response.status_code == 403


def test_publishes_exact_protected_resource_metadata():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"Origin": "https://unlisted.example.com"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json()["resource"] == "https://mcp.example.com/mcp"
    assert response.json()["authorization_servers"] == ["https://auth.example.com"]
    assert response.json()["scopes_supported"] == ["health:read"]


def test_public_routes_ignore_bearer_without_reaching_the_verifier():
    verifier = StaticTokenVerifier()
    with TestClient(make_app(verifier=verifier), base_url="https://mcp.example.com") as client:
        metadata = client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"Authorization": "Bearer attacker-controlled-token"},
        )
        liveness = client.get(
            "/live",
            headers={"Authorization": "Bearer attacker-controlled-token"},
        )

    assert metadata.status_code == 200
    assert liveness.status_code == 200
    assert verifier.tokens == []


def test_public_metadata_preflight_keeps_sdk_wildcard_cors():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.options(
            "/.well-known/oauth-protected-resource/mcp",
            headers={
                "Origin": "https://unlisted.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_trusted_browser_preflight_accepts_mcp_v2_headers():
    with TestClient(make_app(), base_url="https://mcp.example.com") as client:
        response = client.options(
            "/mcp",
            headers={
                "Origin": "https://client.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,mcp-protocol-version,mcp-method,mcp-name",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example.com"
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "mcp-method" in allowed_headers
    assert "mcp-name" in allowed_headers


@pytest.mark.anyio
async def test_real_http_transport_discovers_and_calls_health(http_server):
    base_url, expected = http_server
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer valid-token"}) as http_client:
        transport = streamable_http_client(f"{base_url}/mcp", http_client=http_client)
        async with Client(transport, raise_exceptions=True) as client:
            discovered = await client.list_tools()
            result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert discovered.tools[0].output_schema["type"] == "object"
    assert result.is_error is False
    assert result.structured_content == expected
