from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fuyao_agent.agent import ask_detailed, list_mcp_tools
from fuyao_agent.config import load_memory_db_path, load_settings
from fuyao_agent.knowledge import quant_knowledge_injection
from fuyao_agent.markdown import decode_markdown_output
from fuyao_agent.memory import MemoryStore, extract_memory_json, format_memory_context
from fuyao_agent.neutrality import neutrality_report
from fuyao_agent.workflows import WORKFLOWS, get_workflow


STATIC_ROOT = Path(__file__).with_name("web_static")


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


class FuyaoWebHandler(BaseHTTPRequestHandler):
    server_version = "FuyaoAgentWeb/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api(lambda: self._get_api(parsed.path, parse_qs(parsed.query)))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_json({"ok": False, "error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
            return
        self._handle_api(lambda: self._post_api(parsed.path, self._read_json()))

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _handle_api(self, action: Any) -> None:
        try:
            payload = action()
            self._send_json({"ok": True, "data": payload})
        except ApiError as exc:
            self._send_json({"ok": False, "error": str(exc)}, exc.status)
        except asyncio.CancelledError as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": str(exc) or "Request cancelled while waiting for upstream service",
                },
                HTTPStatus.GATEWAY_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - API should return concise JSON errors.
            self._send_json(
                {"ok": False, "error": str(exc) or exc.__class__.__name__},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _get_api(self, path: str, query: dict[str, list[str]]) -> Any:
        if path == "/api/status":
            return _status_payload()
        if path == "/api/workflows":
            return _workflows_payload()
        if path == "/api/tools":
            settings = load_settings()
            return asyncio.run(list_mcp_tools(settings))
        if path == "/api/knowledge":
            return {"content": quant_knowledge_injection()}
        if path == "/api/memory/stats":
            store, warning = _optional_memory_store(load_memory_db_path())
            if not store:
                return _empty_memory_stats(load_memory_db_path(), warning)
            payload = store.stats()
            if warning:
                payload["warning"] = warning
            return payload
        if path == "/api/memory/pending":
            limit = _int_query(query, "limit", 20, 1, 200)
            store, _warning = _optional_memory_store(load_memory_db_path())
            if not store:
                return []
            return store.pending_predictions(limit=limit)
        raise ApiError("Unknown endpoint", HTTPStatus.NOT_FOUND)

    def _post_api(self, path: str, payload: dict[str, Any]) -> Any:
        if path == "/api/ask":
            return asyncio.run(_run_agent(payload))
        if path == "/api/neutrality":
            text = str(payload.get("text") or "")
            return neutrality_report(text)
        raise ApiError("Unknown endpoint", HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"Invalid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ApiError("Request body must be a JSON object")
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative.startswith("api/"):
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return

        target = (STATIC_ROOT / relative).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN.value)
            return

        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return

        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def _run_agent(payload: dict[str, Any]) -> dict[str, Any]:
    user_input = str(payload.get("question") or "").strip()
    workflow_name = str(payload.get("workflow") or "").strip()
    no_memory = bool(payload.get("no_memory"))
    max_tool_rounds = _bounded_int(payload.get("max_tool_rounds"), 8, 1, 20)
    pending_limit = _bounded_int(payload.get("pending_limit"), 20, 1, 200)

    if not user_input and not workflow_name:
        raise ApiError("Question is required")

    settings = load_settings()
    store = None
    memory_warning = None
    if not no_memory:
        store, memory_warning = _optional_memory_store(settings.memory_db_path)
    rendered_question = user_input

    if workflow_name:
        workflow = get_workflow(workflow_name)
        memory_context = (
            format_memory_context(store, workflow_name, pending_limit)
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
    if workflow_name and store:
        try:
            memory_payload = extract_memory_json(answer)
        except json.JSONDecodeError as exc:
            memory_warning = f"MEMORY_JSON parse failed: {exc}"

    memory_write = None
    if workflow_name and store:
        write_result = store.add_run(
            workflow=workflow_name,
            user_input=user_input,
            output=answer,
            memory_payload=memory_payload,
            observations=result.observations,
        )
        memory_write = {
            "run_id": write_result.run_id,
            "predictions_added": write_result.predictions_added,
            "invalid_predictions_added": write_result.invalid_predictions_added,
            "reviews_added": write_result.reviews_added,
            "invalid_reviews_added": write_result.invalid_reviews_added,
            "lessons_added": write_result.lessons_added,
        }

    return {
        "answer": answer,
        "observations": result.observations,
        "observation_count": len(result.observations),
        "workflow": workflow_name or None,
        "memory_payload_detected": memory_payload is not None,
        "memory_warning": memory_warning,
        "memory_write": memory_write,
    }


def _status_payload() -> dict[str, Any]:
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - status should report missing env cleanly.
        return {
            "ready": False,
            "error": str(exc),
            "env_file": None,
            "memory_db_path": load_memory_db_path(),
            "workflows": sorted(WORKFLOWS),
        }

    return {
        "ready": True,
        "env_file": settings.env_file,
        "modelscope_base_url": settings.modelscope_base_url,
        "modelscope_model": settings.modelscope_model,
        "fuyao_base_url": settings.fuyao_base_url,
        "enabled_mcp_servers": list(settings.enabled_mcp_servers),
        "memory_db_path": settings.memory_db_path,
        "workflows": sorted(WORKFLOWS),
    }


def _workflows_payload() -> list[dict[str, str]]:
    return [
        {
            "name": workflow.name,
            "title": workflow.title,
            "description": workflow.description,
        }
        for workflow in WORKFLOWS.values()
    ]


def _memory_store() -> MemoryStore:
    try:
        return MemoryStore(load_memory_db_path())
    except sqlite3.Error as exc:
        raise ApiError(f"Memory database unavailable: {exc}", HTTPStatus.SERVICE_UNAVAILABLE) from exc


def _optional_memory_store(path: str) -> tuple[MemoryStore | None, str | None]:
    try:
        return MemoryStore(path), None
    except sqlite3.Error as exc:
        return None, f"Memory disabled: {exc}"


def _empty_memory_stats(db_path: str, warning: str | None) -> dict[str, Any]:
    return {
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
        "warning": warning,
    }


def _int_query(
    query: dict[str, list[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    values = query.get(name)
    raw = values[0] if values else default
    return _bounded_int(raw, default, minimum, maximum)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fuyao-agent-web",
        description="Run the local Fuyao MCP agent web console.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), FuyaoWebHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Fuyao Agent Web is running at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Fuyao Agent Web")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
