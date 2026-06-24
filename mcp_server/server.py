"""MCP Server — wraps PC Time Tracker REST API as MCP tools.

Uses stdio JSON-RPC transport. Each tool calls the local HTTP API.
"""

import json
import os
import asyncio
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# --- Config ---
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080/api/v1")
HTTP_TIMEOUT = 10.0

# --- MCP Server ---
server = Server("pc-time-tracker")


# --- HTTP helper ---
async def _api_get(path: str, params: dict | None = None) -> dict:
    """Call the PC Time Tracker API and return parsed JSON."""
    url = f"{API_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {
            "error": f"无法连接到 PC Time Tracker API ({API_BASE})。请先运行: python -m src.main run"
        }
    except httpx.HTTPStatusError as e:
        return {"error": f"API 返回错误 {e.response.status_code}"}
    except Exception as e:
        return {"error": f"请求失败: {str(e)}"}


# --- Tool definitions ---

TOOLS = [
    Tool(
        name="get_agent_context",
        description="获取 PC Time Tracker 的综合上下文快照，包含当前会话、今日摘要、7天趋势、每小时时间线、自然语言文本摘要。这是 Agent 分析电脑使用数据的首选入口，一次调用即可获取全部关键信息。",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_today_summary",
        description="获取今日电脑使用摘要：活跃时间、空闲时间、Top 10 应用排名及占比。",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_daily_stats",
        description="获取多日使用趋势数据。返回每天的总活跃时间。",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "查询天数，默认 7，最大 90",
                    "default": 7,
                }
            },
            "required": [],
        },
    ),
    Tool(
        name="get_top_processes",
        description="获取指定日期范围内使用时间最长的应用程序排名。",
        inputSchema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD 或 'today'，默认 today",
                    "default": "today",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回前 N 个应用，默认 10，最大 50",
                    "default": 10,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_timeline",
        description="获取某一天每小时的活跃度分布（24 小时数组），用于发现用户的高峰和低谷时段。",
        inputSchema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD 或 'today'，默认 today",
                    "default": "today",
                }
            },
            "required": [],
        },
    ),
    Tool(
        name="get_current_session",
        description="获取当前活跃会话的实时状态：开机时间、已运行时长、当前前台窗口、是否空闲。",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_recent_activity",
        description="获取最近的前台窗口切换记录，包含进程名、窗口标题和持续时长。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 50，最大 500",
                    "default": 50,
                }
            },
            "required": [],
        },
    ),
    Tool(
        name="get_insights",
        description="获取预计算的行为洞察：工作日 vs 周末对比、高峰时段、窗口切换频率、今日与平均水平的偏离程度。",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_status",
        description="获取 PC Time Tracker 追踪器的运行状态：是否在运行、当前追踪的进程、轮询次数、数据库大小。",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

# --- Route mapping ---
ROUTE_MAP = {
    "get_agent_context": ("/agent/context", {}),
    "get_today_summary": ("/stats/summary", {"date": "today"}),
    "get_daily_stats": ("/stats/daily", None),  # params built from args
    "get_top_processes": ("/stats/processes", None),
    "get_timeline": ("/stats/timeline", None),
    "get_current_session": ("/sessions/current", {}),
    "get_recent_activity": ("/activity/recent", None),
    "get_insights": ("/agent/insights", {}),
    "get_status": ("/status", {}),
}


# --- Server lifecycle ---

@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name not in ROUTE_MAP:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False, indent=2))]

    path, default_params = ROUTE_MAP[name]

    # Build params
    if default_params is None:
        # Dynamic params from arguments
        params = {k: v for k, v in arguments.items() if v is not None}
    elif isinstance(default_params, dict) and not default_params:
        # No params (static empty)
        params = {}
    else:
        # Merge arguments over defaults
        params = {**default_params, **{k: v for k, v in arguments.items() if v is not None}}

    result = await _api_get(path, params if params else None)

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


# --- Entry point ---
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run():
    """Run the MCP server (sync wrapper for entry point)."""
    asyncio.run(main())
