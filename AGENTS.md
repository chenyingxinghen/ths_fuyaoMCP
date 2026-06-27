# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11 `src`-layout package. Core code lives in `src/fuyao_agent/`: `cli.py` defines the `fuyao-agent` command, `agent.py` handles the chat/tool loop, `mcp_hub.py` manages Fuyao MCP connections, `workflows.py` stores built-in workflow prompts, and `memory.py`, `prediction_schema.py`, and `scoring.py` handle prediction persistence and review logic. Knowledge assets live in `docs/knowledge/`. Runtime data belongs in `.fuyao-memory/`, and local secrets belong in `.env`. No tests are currently committed; add them under `tests/` mirroring package modules.

## Build, Test, and Development Commands

- `python -m venv .venv`: create a local virtual environment.
- `.\.venv\Scripts\Activate.ps1`: activate it in PowerShell.
- `pip install -e .`: install the package in editable mode and expose `fuyao-agent`.
- `fuyao-agent --list-tools`: validate environment variables and MCP connectivity.
- `fuyao-agent --list-workflows`: list available workflow names.
- `fuyao-agent --show-knowledge`: print the injected quant knowledge base.
- `python -m fuyao_agent "上证综指今天表现如何？"`: run via the module entry point.

If tests are added, use `python -m pytest`; `pytest` is not currently declared as a project dependency.

## Coding Style & Naming Conventions

Use four-space indentation, PEP 8 layout, `from __future__ import annotations`, and type hints for public functions and data structures. Prefer `snake_case` for modules, functions, and variables; `PascalCase` for classes and dataclasses; and `UPPER_SNAKE_CASE` for constants. Keep CLI error messages short and actionable. Put configuration changes in `config.py`, workflow prompt changes in `workflows.py`, and SQLite persistence changes in `memory.py`.

## Testing Guidelines

Name tests `tests/test_<module>.py`. Mock ModelScope and Fuyao MCP calls; tests should not require real API keys or network access. Prioritize coverage for CLI argument handling, environment parsing, workflow rendering, prediction schema validation, scoring outcomes, and memory database reads/writes. Use temporary SQLite paths instead of `.fuyao-memory/`.

## Commit & Pull Request Guidelines

No local Git history is available in this workspace, so no repository-specific commit convention can be inferred. Use concise imperative subjects such as `Add daily forecast scoring tests`, and keep related changes in one commit. Pull requests should include purpose, behavior changes, commands run, configuration or migration notes, and terminal output or screenshots for user-facing CLI changes. Link issues when relevant.

## Security & Configuration Tips

Copy `.env.example` to `.env` and never commit secrets. Required keys are `MODELSCOPE_API_KEY` and `FUYAO_API_KEY`. Do not commit generated files from `.venv/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `build/`, or `.fuyao-memory/`. Preserve neutral, data-first language when modifying financial analysis workflows.
