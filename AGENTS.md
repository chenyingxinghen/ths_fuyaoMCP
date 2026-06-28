# Fuyao ModelScope MCP Agent — AGENTS.md

## Commands

| What | Command |
|---|---|
| Ask a question | `fuyao-agent "query"` or `python -m fuyao_agent "query"` |
| Web UI (port :8765) | `fuyao-agent-web` |
| Run workflow | `fuyao-agent --workflow market-weather "query"` or `--skill stock-analysis` |
| List tools | `fuyao-agent --list-tools` |
| List workflows | `fuyao-agent --list-workflows` |
| Show injected knowledge | `fuyao-agent --show-knowledge` |
| Check subjective wording | `fuyao-agent --check-neutrality "text"` |
| Pending predictions | `fuyao-agent --memory-pending` |
| Memory statistics | `fuyao-agent --memory-stats` |
| Validation errors | `fuyao-agent --memory-errors` |
| Run audits | `fuyao-agent --memory-audits` / `--memory-audit-run <id>` |
| Performance summary | `fuyao-agent --memory-performance <workflow>` |
| Run without memory | `fuyao-agent --no-memory` |

## Test

```powershell
python -m unittest discover -s tests
```

Tests use `unittest.TestCase`. No pytest dependency. All six test files are in `tests/`.

## Architecture

- Single package `fuyao_agent` under `src/`.
- Entrypoints defined in `pyproject.toml`: `fuyao-agent` → `fuyao_agent.cli:main`, `fuyao-agent-web` → `fuyao_agent.web:main`.
- Build: `hatchling`. Install: `pip install -e .`
- MCP client connects to remote HTTP endpoints (`https://fuyao.aicubes.cn/mcp/{meta,a-share,a-share-index}`). Auth via `X-api-key` header (set from `FUYAO_API_KEY`).
- ModelScope LLM via OpenAI-compatible API (`MODELSCOPE_BASE_URL`, defaults to `https://api-inference.modelscope.cn/v1`).
- Quant knowledge base injected into every system prompt from `docs/knowledge/open_source_quant_ai.md`.
- Autofill: `get_a_share_special_data_limit_up_pool` auto-resolves `date_ms` from calendar when omitted (`mcp_hub.py:99-129`).

## Key conventions

- **Config**: `.env` (load with `Copy-Item .env.example .env`). Required vars: `MODELSCOPE_API_KEY`, `FUYAO_API_KEY`. Optional: `MODELSCOPE_BASE_URL`, `MODELSCOPE_MODEL`, `FUYAO_BASE_URL`, `FUYAO_MCP_SERVERS`, `FUYAO_MEMORY_DB`.
- **Memory**: SQLite at `.fuyao-memory/memory.sqlite3`. Stores predictions, reviews, lessons, observations, run audits, validation errors.
- **Prediction confidence** must be in `[0, 0.75]`. System auto-scores hit/miss from `condition`; model must write `outcome: "unknown"` and `score: null`.
- **`analysis_trade_date`** (the date used for analysis) **≠ `prediction_trade_date`** (the next verifiable date). Predictions must use the latter.
- **Bad output triggers `validation_errors`**: missing required output sections, subjective terms, invalid MEMORY_JSON structure, mismatched metric/scope, missing tool evidence, duplicate predictions, generic rationale/lesson/validation_query.
- **Workflow outputs must follow a fixed section order** (see `memory.py:37-59`). `market-weather` requires 3-6 predictions covering ≥2 signal families (index, breadth, liquidity). `stock-analysis` requires ≥2 signal families when generating >1 prediction.
- **Subjective terms** to avoid in factual sections: 火爆, 惨淡, 疯狂, 强势, 弱势, 恐慌, 乐观, 悲观, 活跃, 发酵, 意愿, 尚可, 明显, 中幅, 大幅, 小幅, etc. (full list in `neutrality.py:6-27`).
- **`MEMORY_JSON`** must appear at the end with `reviews`, `predictions`, `lessons` arrays inside a ` ```json` fence.
- **Review fields**: `actual_value` must be finite numeric (count→non-negative int, index_close→positive); `actual_summary` must mention metric, tool, and value; `source_tool` must be from current run's observations; `source_tool` must be compatible with `actual_metric`.
- **`condition.unit`** is required: `pct` for percent metrics, `count` for limit_up/consecutive, `points` for index_close.
- **`target_id`** for stock scope must be `600519.SH`-like; for index scope must be `000001.SH`-like.

## File layout

```
src/fuyao_agent/
  cli.py              # CLI entrypoint (argparse)
  agent.py            # Core agent loop (MCP + OpenAI tool calling)
  mcp_hub.py          # FuyaoMcpHub: connects to MCP HTTP endpoints
  workflows.py        # Workflow dataclass + prompt templates
  memory.py           # MemoryStore (SQLite), MEMORY_JSON parsing
  prediction_schema.py # Validation for predictions/reviews/lessons
  scoring.py          # Auto-score condition evaluation
  neutrality.py       # Subjective term detection
  knowledge.py        # Quant knowledge injection
  markdown.py         # Decode escaped markdown from LLM output
  config.py           # .env loading, settings dataclass
  web.py              # HTTP API + static file server
tests/
  test_config.py
  test_markdown.py
  test_memory.py
  test_prediction_schema.py
  test_scoring.py
  test_workflows.py
docs/knowledge/open_source_quant_ai.md  # Injected system prompt knowledge base
```

## Per-workflow required tools (enforced at runtime)

**market-weather**: `get_a_share_calendar_trading_days`, `get_a_share_index_prices_snapshot`, `get_a_share_special_data_limit_up_pool`, `get_a_share_special_data_limit_up_ladder`.

**stock-analysis**: `get_a_share_calendar_trading_days`, `get_a_share_prices_snapshot`, `get_a_share_prices_historical`.
