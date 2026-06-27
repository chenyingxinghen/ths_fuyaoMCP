from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass(frozen=True)
class RegisteredTool:
    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]


class FuyaoMcpHub:
    def __init__(self, server_urls: dict[str, str], api_key: str) -> None:
        self._server_urls = server_urls
        self._headers = {"X-api-key": api_key}
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tools: list[RegisteredTool] = []

    async def __aenter__(self) -> "FuyaoMcpHub":
        for server_name, url in self._server_urls.items():
            transport = await self._stack.enter_async_context(
                streamablehttp_client(url, headers=self._headers),
            )
            read_stream, write_stream, _get_session_id = transport
            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream),
            )
            await session.initialize()
            self._sessions[server_name] = session

        await self.refresh_tools()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._stack.aclose()

    @property
    def tools(self) -> list[RegisteredTool]:
        return self._tools

    async def refresh_tools(self) -> list[RegisteredTool]:
        tools: list[RegisteredTool] = []
        tool_to_server: dict[str, str] = {}

        for server_name, session in self._sessions.items():
            response = await session.list_tools()
            for tool in response.tools:
                if tool.name in tool_to_server:
                    raise RuntimeError(f"Duplicate MCP tool name discovered: {tool.name}")

                schema = _jsonable_schema(getattr(tool, "inputSchema", None))
                registered = RegisteredTool(
                    server_name=server_name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=schema,
                )
                tools.append(registered)
                tool_to_server[tool.name] = server_name
                # Fuyao may return JSON null for fields whose output schema says integer.
                # Keep the text result, but skip the MCP client's strict output validation.
                if hasattr(session, "_tool_output_schemas"):
                    session._tool_output_schemas[tool.name] = None

        self._tools = tools
        self._tool_to_server = tool_to_server
        return tools

    def as_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"[{tool.server_name}] {tool.description}".strip(),
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        server_name = self._tool_to_server.get(name)
        if not server_name:
            raise RuntimeError(f"Unknown MCP tool requested by model: {name}")

        if name == "get_a_share_special_data_limit_up_pool":
            await self._fill_latest_trading_day(arguments)

        result = await self._sessions[server_name].call_tool(name, arguments)
        return _tool_result_to_text(result)

    async def _fill_latest_trading_day(self, arguments: dict[str, Any]) -> None:
        if arguments.get("date_ms") is not None:
            return

        calendar_tool = "get_a_share_calendar_trading_days"
        server_name = self._tool_to_server.get(calendar_tool)
        if not server_name:
            return

        result = await self._sessions[server_name].call_tool(calendar_tool, {})
        payload = json.loads(_tool_result_to_text(result))
        items = payload.get("data", {}).get("item", [])
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        candidates = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("date", "")) <= today
            and item.get("date_ms") is not None
        ]
        if not candidates:
            return

        latest = max(candidates, key=lambda item: str(item.get("date", "")))
        arguments["date_ms"] = latest["date_ms"]


def _jsonable_schema(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}}
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        return schema.model_dump(exclude_none=True)
    return json.loads(json.dumps(schema, default=str))


def _tool_result_to_text(result: Any) -> str:
    if getattr(result, "isError", False):
        return json.dumps(
            {
                "is_error": True,
                "content": [_content_to_jsonable(item) for item in result.content],
            },
            ensure_ascii=False,
        )

    parts: list[str] = []
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(json.dumps(_content_to_jsonable(item), ensure_ascii=False))

    return "\n".join(parts) if parts else json.dumps(_content_to_jsonable(result), ensure_ascii=False)


def _content_to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_content_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _content_to_jsonable(item) for key, item in value.items()}
    return str(value)
