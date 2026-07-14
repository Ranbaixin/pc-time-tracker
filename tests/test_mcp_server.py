import asyncio
import json

from mcp_server import server


def test_mcp_tools_and_route_dispatch(monkeypatch):
    calls = []

    async def fake_get(path, params=None):
        calls.append((path, params))
        return {"data": {"ok": True}}

    monkeypatch.setattr(server, "_api_get", fake_get)
    tools = asyncio.run(server.list_tools())
    result = asyncio.run(server.call_tool("get_daily_stats", {"days": 7}))

    assert len(tools) == 9
    assert calls == [("/stats/daily", {"days": 7})]
    assert json.loads(result[0].text)["data"]["ok"] is True


def test_mcp_unknown_tool_returns_structured_error():
    result = asyncio.run(server.call_tool("missing", {}))
    assert json.loads(result[0].text)["error"] == "Unknown tool: missing"
