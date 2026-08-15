import sys
import tempfile

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stdio_boots_django_and_calls_health():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apps.api.mcp_server"],
        env={"DJANGO_SETTINGS_MODULE": "apps.api.mcp_server.tests.settings"},
    )

    with tempfile.TemporaryFile(mode="w+") as stderr:
        async with Client(stdio_client(parameters, errlog=stderr), raise_exceptions=True) as client:
            discovered = await client.list_tools()
            result = await client.call_tool("health")

    assert [tool.name for tool in discovered.tools] == ["health"]
    assert result.is_error is False
    assert result.structured_content["status"] == "healthy"
