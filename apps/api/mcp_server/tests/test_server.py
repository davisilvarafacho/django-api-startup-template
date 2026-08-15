import pytest
from mcp import Client

from apps.api.mcp_server import tools
from apps.api.mcp_server.server import create_mcp_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_is_discoverable_and_returns_structured_content(monkeypatch):
    expected = {
        "status": "healthy",
        "checks": {
            "databases": {"default": {"status": "healthy"}},
            "caches": {"default": {"status": "healthy"}},
        },
    }
    monkeypatch.setattr(tools, "collect_dependency_health", lambda: expected)

    async with Client(create_mcp_server(), raise_exceptions=True) as client:
        discovered = await client.list_tools()
        result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert discovered.tools[0].output_schema["type"] == "object"
    assert result.is_error is False
    assert result.structured_content == expected


@pytest.mark.anyio
async def test_health_hides_unexpected_adapter_error(monkeypatch):
    def fail():
        raise ValueError("secret database URL")

    monkeypatch.setattr(tools, "collect_dependency_health", fail)

    async with Client(create_mcp_server()) as client:
        result = await client.call_tool("health")

    assert result.is_error is True
    assert "Health check failed" in str(result.content)
    assert "secret database URL" not in str(result.content)
