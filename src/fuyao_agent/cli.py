from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fuyao_agent.config import load_memory_db_path, load_settings
from fuyao_agent.knowledge import quant_knowledge_injection
from fuyao_agent.markdown import decode_markdown_output
from fuyao_agent.memory import MemoryStore, extract_memory_json, format_memory_context
from fuyao_agent.workflows import WORKFLOWS, get_workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fuyao-agent",
        description="Query Fuyao MCP financial data with a ModelScope OpenAI-compatible LLM.",
    )
    parser.add_argument("question", nargs="*", help="Natural-language financial data question.")
    parser.add_argument("--list-tools", action="store_true", help="List MCP tools and exit.")
    parser.add_argument("--list-workflows", action="store_true", help="List built-in workflows and exit.")
    parser.add_argument("--show-knowledge", action="store_true", help="Show injected quant knowledge base and exit.")
    parser.add_argument("--show-config", action="store_true", help="Show non-secret runtime config and exit.")
    parser.add_argument("--check-neutrality", help="Check text for subjective wording and exit.")
    parser.add_argument("--memory-stats", action="store_true", help="Show local memory statistics and exit.")
    parser.add_argument("--memory-pending", action="store_true", help="Show pending predictions and exit.")
    parser.add_argument("--memory-errors", action="store_true", help="Show recent memory validation errors and exit.")
    parser.add_argument("--memory-audits", action="store_true", help="Show recent workflow run audits and exit.")
    parser.add_argument("--memory-audit-run", type=int, help="Show run audit details for a run_id and exit.")
    parser.add_argument(
        "--memory-performance",
        choices=sorted(WORKFLOWS),
        help="Show scored review performance for a workflow and exit.",
    )
    parser.add_argument(
        "--memory-context",
        choices=sorted(WORKFLOWS),
        help="Show the memory context that would be injected for a workflow and exit.",
    )
    parser.add_argument("--no-memory", action="store_true", help="Do not read or write local memory.")
    parser.add_argument(
        "--pending-limit",
        type=int,
        default=20,
        help="Maximum pending predictions to include in review context.",
    )
    parser.add_argument(
        "--workflow",
        "--skill",
        dest="workflow",
        choices=sorted(WORKFLOWS),
        help="Run a built-in workflow with the given question/input.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=8,
        help="Maximum model/tool-call iterations.",
    )
    args = parser.parse_args()

    try:
        if args.list_workflows:
            _print_workflows()
            return

        if args.show_knowledge:
            print(quant_knowledge_injection())
            return

        if args.check_neutrality is not None:
            from fuyao_agent.neutrality import neutrality_report

            print(json.dumps(neutrality_report(args.check_neutrality), ensure_ascii=False, indent=2))
            return

        if args.memory_stats:
            _print_memory_stats()
            return

        if args.memory_pending:
            _print_pending_predictions(args.pending_limit)
            return

        if args.memory_errors:
            _print_memory_errors(args.pending_limit)
            return

        if args.memory_audits:
            _print_memory_audits(args.pending_limit)
            return

        if args.memory_audit_run is not None:
            _print_memory_audit_run(args.memory_audit_run)
            return

        if args.memory_performance:
            _print_memory_performance(args.memory_performance)
            return

        user_input = " ".join(args.question).strip()

        if args.memory_context:
            _print_memory_context(args.memory_context, args.pending_limit, user_input)
            return

        settings = load_settings()
        if args.show_config:
            _print_config(settings)
            return

        if args.list_tools:
            asyncio.run(_print_tools(settings))
            return

        question = user_input
        store = None if args.no_memory else MemoryStore(settings.memory_db_path)
        if args.workflow:
            memory_context = (
                format_memory_context(
                    store,
                    args.workflow,
                    args.pending_limit,
                    target_hint=user_input,
                )
                if store
                else ""
            )
            question = get_workflow(args.workflow).render(user_input, memory_context)
        elif not question:
            parser.error("question is required unless an exit-only flag is used")

        from fuyao_agent.agent import ask_detailed

        result = asyncio.run(
            ask_detailed(settings, question, max_tool_rounds=args.max_tool_rounds),
        )
        answer = decode_markdown_output(result.answer)
        print(answer)

        if args.workflow and store:
            _save_workflow_memory(
                store,
                args.workflow,
                user_input,
                answer,
                result.observations,
            )
    except Exception as exc:  # noqa: BLE001 - CLI should present a concise error.
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


async def _print_tools(settings) -> None:
    from fuyao_agent.agent import list_mcp_tools

    tools = await list_mcp_tools(settings)
    for tool in tools:
        description = tool["description"].replace("\n", " ")
        print(f'{tool["server"]}\t{tool["name"]}\t{description}')


def _print_workflows() -> None:
    for workflow in WORKFLOWS.values():
        print(f"{workflow.name}\t{workflow.title}\t{workflow.description}")


def _print_memory_stats() -> None:
    store = MemoryStore(load_memory_db_path())
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))


def _print_pending_predictions(limit: int) -> None:
    store = MemoryStore(load_memory_db_path())
    print(store.pending_predictions_json(limit=limit))


def _print_memory_errors(limit: int) -> None:
    store = MemoryStore(load_memory_db_path())
    print(json.dumps(store.recent_validation_errors(limit=limit), ensure_ascii=False, indent=2))


def _print_memory_audits(limit: int) -> None:
    store = MemoryStore(load_memory_db_path())
    print(json.dumps(store.recent_run_audits(limit=limit), ensure_ascii=False, indent=2))


def _print_memory_audit_run(run_id: int) -> None:
    store = MemoryStore(load_memory_db_path())
    audit = store.run_audit(run_id)
    if audit is None:
        raise RuntimeError(f"No run audit found for run_id={run_id}")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


def _print_memory_performance(workflow: str) -> None:
    store = MemoryStore(load_memory_db_path())
    print(json.dumps(store.workflow_performance_summary(workflow), ensure_ascii=False, indent=2))


def _print_memory_context(workflow: str, pending_limit: int, target_hint: str) -> None:
    store = MemoryStore(load_memory_db_path())
    print(
        format_memory_context(
            store,
            workflow,
            pending_limit,
            target_hint=target_hint,
        ),
    )


def _print_config(settings) -> None:
    print(
        json.dumps(
            {
                "env_file": settings.env_file,
                "modelscope_base_url": settings.modelscope_base_url,
                "modelscope_model": settings.modelscope_model,
                "fuyao_base_url": settings.fuyao_base_url,
                "enabled_mcp_servers": settings.enabled_mcp_servers,
                "memory_db_path": settings.memory_db_path,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def _save_workflow_memory(
    store: MemoryStore,
    workflow: str,
    user_input: str,
    answer: str,
    observations: list[dict],
) -> None:
    payload = None
    payload_error = None
    try:
        payload = extract_memory_json(answer)
    except json.JSONDecodeError as exc:
        payload_error = f"MEMORY_JSON parse failed: {exc}"
        print(f"Warning: {payload_error}", file=sys.stderr)

    result = store.add_run(
        workflow=workflow,
        user_input=user_input,
        output=answer,
        memory_payload=payload,
        memory_payload_error=payload_error,
        observations=observations,
    )
    print(
        "Memory saved: "
        f"run_id={result.run_id}, "
        f"predictions={result.predictions_added}, "
        f"invalid_predictions={result.invalid_predictions_added}, "
        f"reviews={result.reviews_added}, "
        f"invalid_reviews={result.invalid_reviews_added}, "
        f"lessons={result.lessons_added}",
        file=sys.stderr,
    )
    for error in result.validation_errors:
        print(
            "Memory validation rejected "
            f"{error.get('item_type')}[{error.get('index')}]: "
            f"{'; '.join(error.get('errors') or [])}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
