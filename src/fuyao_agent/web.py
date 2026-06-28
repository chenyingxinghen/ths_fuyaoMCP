from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fuyao_agent.agent import ask_detailed, list_mcp_tools
from fuyao_agent.config import load_memory_db_path, load_settings
from fuyao_agent.knowledge import quant_knowledge_injection
from fuyao_agent.markdown import decode_markdown_output
from fuyao_agent.memory import MemoryStore, extract_memory_json, format_memory_context
from fuyao_agent.neutrality import neutrality_report
from fuyao_agent.workflows import WORKFLOWS, get_workflow


STATIC_ROOT = Path(__file__).with_name("web_static")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _optional_memory_store(path: str) -> tuple[MemoryStore | None, str | None]:
    try:
        return MemoryStore(path), None
    except sqlite3.Error as exc:
        return None, f"Memory disabled: {exc}"


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


# ── API handlers ──────────────────────────────────────────────────────────


async def api_status(request: Request) -> JSONResponse:
    try:
        settings = load_settings()
    except Exception as exc:
        return JSONResponse(
            {
                "ready": False,
                "error": str(exc),
                "env_file": None,
                "memory_db_path": load_memory_db_path(),
                "workflows": sorted(WORKFLOWS),
            }
        )
    return JSONResponse(
        {
            "ready": True,
            "env_file": settings.env_file,
            "modelscope_base_url": settings.modelscope_base_url,
            "modelscope_model": settings.modelscope_model,
            "fuyao_base_url": settings.fuyao_base_url,
            "enabled_mcp_servers": list(settings.enabled_mcp_servers),
            "memory_db_path": settings.memory_db_path,
            "workflows": sorted(WORKFLOWS),
        }
    )


async def api_workflows(request: Request) -> JSONResponse:
    return JSONResponse(
        [
            {"name": w.name, "title": w.title, "description": w.description}
            for w in WORKFLOWS.values()
        ]
    )


async def api_tools(request: Request) -> JSONResponse:
    settings = load_settings()
    tools = await list_mcp_tools(settings)
    return JSONResponse(tools)


async def api_knowledge(request: Request) -> JSONResponse:
    return JSONResponse({"content": quant_knowledge_injection()})


async def api_memory_stats(request: Request) -> JSONResponse:
    db_path = load_memory_db_path()
    store, warning = _optional_memory_store(db_path)
    if not store:
        return JSONResponse(
            {
                "db_path": db_path,
                "prediction_total": 0,
                "valid_prediction_total": 0,
                "invalid_prediction_total": 0,
                "pending_total": 0,
                "reviewed_total": 0,
                "average_score": None,
                "outcomes": {},
                "by_scope": {},
                "by_metric": {},
                "by_confidence": [],
                "evidence_trace_total": 0,
                "predictions_with_evidence_total": 0,
                "validation_error_total": 0,
                "validation_errors_by_type": {},
                "run_audit_total": 0,
                "warning": warning,
            }
        )
    payload = store.stats()
    if warning:
        payload["warning"] = warning
    return JSONResponse(payload)


async def api_memory_pending(request: Request) -> JSONResponse:
    limit = _bounded_int(request.query_params.get("limit", "20"), 20, 1, 200)
    store, _warning = _optional_memory_store(load_memory_db_path())
    if not store:
        return JSONResponse([])
    return JSONResponse(store.pending_predictions(limit=limit))


async def api_memory_errors(request: Request) -> JSONResponse:
    limit = _bounded_int(request.query_params.get("limit", "20"), 20, 1, 200)
    store, _warning = _optional_memory_store(load_memory_db_path())
    if not store:
        return JSONResponse([])
    return JSONResponse(store.recent_validation_errors(limit=limit))


async def api_memory_audits(request: Request) -> JSONResponse:
    store, _warning = _optional_memory_store(load_memory_db_path())
    run_id_raw = request.query_params.get("run_id")
    if not store:
        if run_id_raw is not None:
            return JSONResponse(None)
        return JSONResponse([])

    if run_id_raw is not None:
        try:
            run_id = int(run_id_raw)
        except (TypeError, ValueError):
            raise ApiError("run_id must be an integer")
        if run_id < 1:
            raise ApiError("run_id must be >= 1")
        audit = store.run_audit(run_id)
        if audit is None:
            raise ApiError("Run audit not found", 404)
        return JSONResponse(audit)

    limit = _bounded_int(request.query_params.get("limit", "20"), 20, 1, 200)
    return JSONResponse(store.recent_run_audits(limit=limit))


async def api_memory_performance(request: Request) -> JSONResponse:
    workflow = request.query_params.get("workflow", "market-weather")
    if workflow not in WORKFLOWS:
        raise ApiError("Unknown workflow")
    store, _warning = _optional_memory_store(load_memory_db_path())
    if not store:
        return JSONResponse(
            {
                "reviewed_total": 0,
                "scored_total": 0,
                "hit_count": 0,
                "miss_count": 0,
                "unknown_count": 0,
                "hit_rate": None,
                "average_score": None,
                "by_metric": [],
                "by_confidence": [],
            }
        )
    return JSONResponse(store.workflow_performance_summary(workflow))


async def api_ask(request: Request) -> JSONResponse:
    try:
        payload: dict[str, Any] = await request.json()
    except json.JSONDecodeError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Invalid JSON: {exc.msg}"},
            status_code=400,
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"ok": False, "error": "Request body must be a JSON object"},
            status_code=400,
        )

    user_input = str(payload.get("question") or "").strip()
    workflow_name = str(payload.get("workflow") or "").strip()
    no_memory = bool(payload.get("no_memory"))
    force_refresh = bool(payload.get("force_refresh"))
    max_tool_rounds = _bounded_int(payload.get("max_tool_rounds"), 8, 1, 20)
    pending_limit = _bounded_int(payload.get("pending_limit"), 20, 1, 200)

    if not user_input and not workflow_name:
        return JSONResponse(
            {"ok": False, "error": "Question is required"},
            status_code=400,
        )

    settings = load_settings()
    store = None
    memory_warning = None
    if not no_memory:
        store, memory_warning = _optional_memory_store(settings.memory_db_path)
    rendered_question = user_input

    if workflow_name:
        workflow = get_workflow(workflow_name)
        if store and not force_refresh:
            cached = store.find_cached_report(
                workflow=workflow_name,
                user_input=user_input,
                ttl_seconds=settings.report_cache_ttl_seconds,
                similarity_threshold=settings.report_cache_similarity_threshold,
            )
            if cached:
                return JSONResponse(
                    {
                        "answer": cached["answer"],
                        "observations": [],
                        "observation_count": 0,
                        "workflow": workflow_name,
                        "memory_payload_detected": cached["memory_payload_detected"],
                        "memory_warning": memory_warning,
                        "memory_write": None,
                        "report_cache": {
                            "hit": True,
                            "matched_run_id": cached["run_id"],
                            "matched_question": cached["user_input"],
                            "created_at": cached["created_at"],
                            "similarity": cached["similarity"],
                            "age_seconds": cached["age_seconds"],
                            "ttl_seconds": cached["ttl_seconds"],
                        },
                    }
                )
        memory_context = (
            format_memory_context(
                store,
                workflow_name,
                pending_limit,
                target_hint=user_input,
            )
            if store
            else ""
        )
        rendered_question = workflow.render(user_input, memory_context)

    result = await ask_detailed(
        settings,
        rendered_question,
        max_tool_rounds=max_tool_rounds,
    )
    answer = decode_markdown_output(result.answer)

    memory_payload = None
    memory_payload_error = None
    if workflow_name and store:
        try:
            memory_payload = extract_memory_json(answer)
        except json.JSONDecodeError as exc:
            memory_payload_error = f"MEMORY_JSON parse failed: {exc}"
            memory_warning = memory_payload_error

    memory_write = None
    if workflow_name and store:
        write_result = store.add_run(
            workflow=workflow_name,
            user_input=user_input,
            output=answer,
            memory_payload=memory_payload,
            memory_payload_error=memory_payload_error,
            observations=result.observations,
        )
        memory_write = {
            "run_id": write_result.run_id,
            "predictions_added": write_result.predictions_added,
            "invalid_predictions_added": write_result.invalid_predictions_added,
            "reviews_added": write_result.reviews_added,
            "invalid_reviews_added": write_result.invalid_reviews_added,
            "lessons_added": write_result.lessons_added,
            "validation_errors": write_result.validation_errors,
            "run_audit": write_result.run_audit,
        }

    return JSONResponse(
        {
            "answer": answer,
            "observations": result.observations,
            "observation_count": len(result.observations),
            "workflow": workflow_name or None,
            "memory_payload_detected": memory_payload is not None,
            "memory_warning": memory_warning,
            "memory_write": memory_write,
            "report_cache": {
                "hit": False,
                "bypassed": force_refresh,
                "ttl_seconds": settings.report_cache_ttl_seconds,
                "similarity_threshold": settings.report_cache_similarity_threshold,
            },
        }
    )


async def api_neutrality(request: Request) -> JSONResponse:
    try:
        payload: dict[str, Any] = await request.json()
    except json.JSONDecodeError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Invalid JSON: {exc.msg}"},
            status_code=400,
        )
    text = str(payload.get("text") or "")
    return JSONResponse(neutrality_report(text))


# ── Error handlers ────────────────────────────────────────────────────────


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)


async def starlette_http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": str(exc) or exc.__class__.__name__},
        status_code=500,
    )


# ── App ───────────────────────────────────────────────────────────────────

app = Starlette(
    routes=[
        Route("/api/status", api_status),
        Route("/api/workflows", api_workflows),
        Route("/api/tools", api_tools),
        Route("/api/knowledge", api_knowledge),
        Route("/api/memory/stats", api_memory_stats),
        Route("/api/memory/pending", api_memory_pending),
        Route("/api/memory/errors", api_memory_errors),
        Route("/api/memory/audits", api_memory_audits),
        Route("/api/memory/performance", api_memory_performance),
        Route("/api/ask", api_ask, methods=["POST"]),
        Route("/api/neutrality", api_neutrality, methods=["POST"]),
        Mount("/", app=StaticFiles(directory=str(STATIC_ROOT), html=True), name="static"),
    ],
    exception_handlers={
        ApiError: api_error_handler,
        StarletteHTTPException: starlette_http_error_handler,
        Exception: generic_error_handler,
    },
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fuyao-agent-web",
        description="Run the local Fuyao MCP agent web console.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8084, help="Port to bind.")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"Fuyao Agent Web is running at http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
