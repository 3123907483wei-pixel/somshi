"""
MCP server exposing a single tool `web_search`
Uses DuckDuckGo (free, no API key needed)
"""
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore")

import asyncio, json, os, sys
from typing import Optional

from duckduckgo_search import DDGS

from mcp import types as mcp_types
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio


def _log(msg: str):
    print(f"[search] {msg}", file=sys.stderr)


TOOL_NAME = "web_search"
TOOL_DESCRIPTION = "Search the web using DuckDuckGo (free, no API key)"
TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search keywords"},
        "count": {"type": "number", "description": "Number of results (max 10)", "default": 5},
        "region": {"type": "string", "description": "Region e.g. cn-zh, us-en, wt-wt", "default": "cn-zh"},
    },
    "required": ["query"],
}


def web_search(query: str, count: Optional[int] = 5, region: Optional[str] = "cn-zh") -> dict:
    """Search the web using DuckDuckGo."""
    count = max(1, min(count or 5, 10))
    try:
        _log(f"searching: {query[:50]}")
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, region=region or "cn-zh", max_results=count))
        results = [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in raw]
        return {"status": "ok", "query": query, "results": results}
    except Exception as e:
        _log(f"search failed: {e}")
        return {"status": "error", "message": str(e)}


app = Server("search-mcp")

@app.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    return [mcp_types.Tool(name=TOOL_NAME, description=TOOL_DESCRIPTION, inputSchema=TOOL_INPUT_SCHEMA)]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mcp_types.Content]:
    if name != TOOL_NAME:
        return [mcp_types.TextContent(type="text", text=json.dumps({"error": f"unknown tool '{name}'"}))]
    try:
        result = await asyncio.to_thread(web_search, **arguments)
        return [mcp_types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        _log(f"call_tool error: {e}")
        return [mcp_types.TextContent(type="text", text=json.dumps({"error": f"Execution failed: {e}"}))]


async def main():
    async with mcp.server.stdio.stdio_server() as (r, w):
        await app.run(r, w, InitializationOptions(
            server_name=app.name, server_version="0.1.0",
            capabilities=app.get_capabilities(notification_options=NotificationOptions(), experimental_capabilities={}),
        ))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
