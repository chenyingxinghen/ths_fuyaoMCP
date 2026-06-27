from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fuyao_agent.config import Settings, resolve_mcp_urls
from fuyao_agent.knowledge import quant_knowledge_injection


SYSTEM_PROMPT = """You are a financial data assistant for A-share market queries.
Use the available Fuyao MCP tools whenever current market data, ticker resolution,
historical prices, financial statements, index constituents, or trading calendars are needed.

Important rules:
- Answer in the same language as the user's question.
- If the user mentions a company or index name but no thscode, first resolve it with get_meta_tickers_search.
- Prefer thscode identifiers such as 600519.SH over plain tickers.
- Summarize numeric results clearly and mention the data timestamp or reporting period when available.
- Do not stop at listing tool outputs. Convert raw data into derived observations, cross-tool synthesis,
  contradictions, changes across time windows, concentration/dispersion patterns, and data-gap impact.
- Lead with analysis that would be hard to assemble manually; keep raw data tables short and use them only as evidence.
- Use neutral factual language for computed data. Report values, changes, ranks, thresholds, and missing data without emotional wording.
- Mark interpretations as interpretations or hypotheses; never blend subjective labels into factual calculation results.
- Do not infer weekdays, trading-day status, or timestamps in prose unless they come from a tool response or deterministic code.
- Do not provide investment advice; describe data and observable facts only.
"""


@dataclass(frozen=True)
class AgentRunResult:
    answer: str
    observations: list[dict[str, Any]]


async def ask(settings: Settings, question: str, max_tool_rounds: int = 8) -> str:
    result = await ask_detailed(settings, question, max_tool_rounds=max_tool_rounds)
    return result.answer


async def ask_detailed(
    settings: Settings,
    question: str,
    max_tool_rounds: int = 8,
) -> AgentRunResult:
    from openai import AsyncOpenAI
    from fuyao_agent.mcp_hub import FuyaoMcpHub

    server_urls = resolve_mcp_urls(settings.fuyao_base_url, settings.enabled_mcp_servers)
    observations: list[dict[str, Any]] = []

    async with FuyaoMcpHub(server_urls, settings.fuyao_api_key) as hub:
        client = AsyncOpenAI(
            api_key=settings.modelscope_api_key,
            base_url=settings.modelscope_base_url,
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": question},
        ]
        tools = hub.as_openai_tools()

        for _ in range(max_tool_rounds):
            response = await client.chat.completions.create(
                model=settings.modelscope_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            message = response.choices[0].message
            messages.append(_assistant_message_to_dict(message))

            tool_calls = message.tool_calls or []
            if not tool_calls:
                return AgentRunResult(answer=message.content or "", observations=observations)

            for tool_call in tool_calls:
                name = tool_call.function.name
                arguments = _parse_tool_arguments(tool_call.function.arguments)
                result = await hub.call_tool(name, arguments)
                observations.append(
                    {
                        "tool_name": name,
                        "arguments": arguments,
                        "result": result,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": result,
                    },
                )

        raise RuntimeError(f"Model did not finish after {max_tool_rounds} tool round(s)")


def _build_system_prompt() -> str:
    knowledge = quant_knowledge_injection()
    if not knowledge:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{knowledge}"


async def list_mcp_tools(settings: Settings) -> list[dict[str, str]]:
    from fuyao_agent.mcp_hub import FuyaoMcpHub

    server_urls = resolve_mcp_urls(settings.fuyao_base_url, settings.enabled_mcp_servers)
    async with FuyaoMcpHub(server_urls, settings.fuyao_api_key) as hub:
        return [
            {
                "server": tool.server_name,
                "name": tool.name,
                "description": tool.description,
            }
            for tool in hub.tools
        ]


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model returned invalid tool arguments JSON: {raw}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Tool arguments must be a JSON object, got: {raw}")
    return parsed


def _assistant_message_to_dict(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": "assistant"}
    if message.content is not None:
        payload["content"] = message.content
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": item.id,
                "type": item.type,
                "function": {
                    "name": item.function.name,
                    "arguments": item.function.arguments,
                },
            }
            for item in message.tool_calls
        ]
    return payload
