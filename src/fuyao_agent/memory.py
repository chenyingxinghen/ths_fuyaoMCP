from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fuyao_agent.neutrality import find_subjective_terms
from fuyao_agent.prediction_schema import (
    validate_lesson_text,
    validate_prediction_item,
    validate_review_item,
)
from fuyao_agent.scoring import ScoreResult, score_condition


MEMORY_BLOCK_RE = re.compile(
    r"MEMORY_JSON\s*:\s*```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

MARKET_WORKFLOW_NAMES = {
    "daily-forecast",
    "daily-review",
    "market-movers",
    "market-weather",
}
WORKFLOW_SCOPE_FILTERS = {
    "market-weather": {"market", "index", "theme"},
    "stock-analysis": {"stock"},
}
WORKFLOW_OUTPUT_REQUIRED_SECTIONS = {
    "stock-analysis": (
        "分析结论摘要",
        "复盘验证",
        "标的确认",
        "关键证据链",
        "走势与财务的交叉验证",
        "解释/假设",
        "预测清单",
        "方法修正",
        "需要继续核验的数据",
    ),
    "market-weather": (
        "交易日",
        "复盘验证",
        "赚钱效应合成摘要",
        "支持信号",
        "矛盾/背离信号",
        "未来观察假设",
        "预测清单",
        "方法修正",
        "风险与数据缺口",
    ),
}
MARKET_PREDICTION_REQUIRED_TOOLS = {
    "get_a_share_calendar_trading_days",
    "get_a_share_index_prices_snapshot",
    "get_a_share_special_data_limit_up_pool",
    "get_a_share_special_data_limit_up_ladder",
}
STOCK_PREDICTION_REQUIRED_TOOLS = {
    "get_a_share_calendar_trading_days",
    "get_a_share_prices_snapshot",
    "get_a_share_prices_historical",
}
MARKET_METRIC_SIGNAL_FAMILIES = {
    "index_return_pct": "index",
    "index_close": "index",
    "limit_up_count": "breadth",
    "limit_up_count_change_pct": "breadth",
    "consecutive_limit_up_max": "breadth",
    "turnover_amount_change_pct": "liquidity",
}
STOCK_METRIC_SIGNAL_FAMILIES = {
    "stock_return_pct": "return",
    "turnover_amount_change_pct": "liquidity",
}
PREDICTION_METRIC_TOOL_REQUIREMENTS = {
    "index_return_pct": {"get_a_share_index_prices_snapshot"},
    "index_close": {"get_a_share_index_prices_snapshot"},
    "stock_return_pct": {
        "get_a_share_prices_snapshot",
        "get_a_share_prices_historical",
    },
    "limit_up_count": {"get_a_share_special_data_limit_up_pool"},
    "limit_up_count_change_pct": {"get_a_share_special_data_limit_up_pool"},
    "consecutive_limit_up_max": {"get_a_share_special_data_limit_up_ladder"},
    "turnover_amount_change_pct": {
        "get_a_share_prices_snapshot",
        "get_a_share_prices_historical",
    },
}


@dataclass(frozen=True)
class MemoryWriteResult:
    run_id: int
    predictions_added: int
    reviews_added: int
    lessons_added: int
    invalid_predictions_added: int = 0
    invalid_reviews_added: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    run_audit: dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._memory_conn: sqlite3.Connection | None = None
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def add_run(
        self,
        *,
        workflow: str | None,
        user_input: str,
        output: str,
        memory_payload: dict[str, Any] | None = None,
        memory_payload_error: str | None = None,
        observations: list[dict[str, Any]] | None = None,
    ) -> MemoryWriteResult:
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs (created_at, workflow, user_input, output, memory_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    workflow,
                    user_input,
                    output,
                    (
                        json.dumps(memory_payload, ensure_ascii=False)
                        if memory_payload is not None
                        else None
                    ),
                ),
            )
            run_id = int(cursor.lastrowid)

            predictions_added = 0
            invalid_predictions_added = 0
            reviews_added = 0
            invalid_reviews_added = 0
            lessons_added = 0
            validation_errors: list[dict[str, Any]] = []
            observation_records = (
                self._insert_observations(conn, run_id, observations)
                if observations
                else []
            )
            used_tool_names = (
                {
                    str(observation["tool_name"])
                    for observation in observation_records
                    if observation.get("tool_name")
                }
                if observations is not None
                else None
            )

            validation_errors.extend(
                _validate_workflow_output(
                    workflow=workflow,
                    output=output,
                    observations=observations,
                ),
            )

            if workflow and memory_payload is None:
                validation_errors.append(
                    _validation_error(
                        item_type="memory_json",
                        index=None,
                        errors=[
                            memory_payload_error
                            or "MEMORY_JSON block is missing for workflow run",
                        ],
                    ),
                )

            if memory_payload is not None:
                validation_errors.extend(_validate_memory_payload_shape(memory_payload))
                prediction_result = self._insert_predictions(
                    conn,
                    run_id,
                    memory_payload,
                    used_tool_names,
                    observation_records,
                )
                (
                    predictions_added,
                    invalid_predictions_added,
                    prediction_errors,
                ) = prediction_result
                validation_errors.extend(prediction_errors)
                review_result = self._insert_reviews(
                    conn,
                    run_id,
                    memory_payload,
                    used_tool_names,
                )
                (
                    reviews_added,
                    review_lessons_added,
                    invalid_reviews_added,
                    review_errors,
                ) = review_result
                validation_errors.extend(review_errors)
                lesson_added, lesson_errors = self._insert_lessons(
                    conn,
                    run_id,
                    memory_payload,
                )
                validation_errors.extend(lesson_errors)
                lessons_added = review_lessons_added + lesson_added

            if validation_errors:
                self._insert_validation_errors(conn, run_id, validation_errors)

            run_audit = _build_run_audit(
                workflow=workflow,
                output=output,
                memory_payload=memory_payload,
                observations=observations,
                used_tool_names=used_tool_names,
                predictions_added=predictions_added,
                invalid_predictions_added=invalid_predictions_added,
                reviews_added=reviews_added,
                invalid_reviews_added=invalid_reviews_added,
                lessons_added=lessons_added,
                validation_errors=validation_errors,
            )
            self._insert_run_audit(conn, run_id, run_audit)

            return MemoryWriteResult(
                run_id=run_id,
                predictions_added=predictions_added,
                reviews_added=reviews_added,
                lessons_added=lessons_added,
                invalid_predictions_added=invalid_predictions_added,
                invalid_reviews_added=invalid_reviews_added,
                validation_errors=validation_errors,
                run_audit=run_audit,
            )

    def pending_predictions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._pending_predictions(limit=limit, workflow=None)

    def pending_predictions_for_workflow(
        self,
        workflow: str,
        limit: int = 20,
        target_hint: str | None = None,
        due_on_or_before: str | None = None,
        due_after: str | None = None,
        skip_unknown_reviewed_on_or_after: str | None = None,
        require_unknown_reviewed_on_or_after: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._pending_predictions(
            limit=limit,
            workflow=workflow,
            target_hint=target_hint,
            due_on_or_before=due_on_or_before,
            due_after=due_after,
            skip_unknown_reviewed_on_or_after=skip_unknown_reviewed_on_or_after,
            require_unknown_reviewed_on_or_after=require_unknown_reviewed_on_or_after,
        )

    def _pending_predictions(
        self,
        *,
        limit: int,
        workflow: str | None,
        target_hint: str | None = None,
        due_on_or_before: str | None = None,
        due_after: str | None = None,
        skip_unknown_reviewed_on_or_after: str | None = None,
        require_unknown_reviewed_on_or_after: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = ["p.status = 'pending'"]
        params: list[Any] = []

        if workflow:
            workflow_clauses: list[str] = []
            workflow_names = sorted(_workflow_names_for_memory(workflow))
            if workflow_names:
                workflow_clauses.append(f"r.workflow IN ({_placeholders(workflow_names)})")
                params.extend(workflow_names)
            scopes = sorted(WORKFLOW_SCOPE_FILTERS.get(workflow, set()))
            if scopes:
                workflow_clauses.append(f"p.scope IN ({_placeholders(scopes)})")
                params.extend(scopes)
            filters.append(f"({' OR '.join(workflow_clauses)})")

        normalized_hint = _normalize_target_hint(target_hint)
        if normalized_hint:
            filters.append(
                "("
                "UPPER(COALESCE(p.target_id, '')) LIKE ? "
                "OR UPPER(COALESCE(p.target, '')) LIKE ?"
                ")",
            )
            pattern = f"%{normalized_hint}%"
            params.extend([pattern, pattern])

        if due_on_or_before:
            filters.append("p.trade_date <= ?")
            params.append(due_on_or_before)

        if due_after:
            filters.append("(p.trade_date IS NULL OR p.trade_date > ?)")
            params.append(due_after)

        if skip_unknown_reviewed_on_or_after:
            filters.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM reviews rv
                    WHERE rv.prediction_id = p.id
                      AND rv.outcome = 'unknown'
                      AND COALESCE(rv.actual_trade_date, '') >= ?
                )
                """,
            )
            params.append(skip_unknown_reviewed_on_or_after)

        if require_unknown_reviewed_on_or_after:
            filters.append(
                """
                EXISTS (
                    SELECT 1
                    FROM reviews rv
                    WHERE rv.prediction_id = p.id
                      AND rv.outcome = 'unknown'
                      AND COALESCE(rv.actual_trade_date, '') >= ?
                )
                """,
            )
            params.append(require_unknown_reviewed_on_or_after)

        params.append(limit)
        where_sql = " AND ".join(filters)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.created_at, r.workflow, p.as_of_date, p.scope, p.target,
                       p.target_id, p.horizon_days, p.trade_date, p.metric,
                       p.expected_direction, p.expected_range, p.confidence,
                       p.rationale, p.validation_query, p.condition_json,
                       p.validation_status, p.validation_errors, p.raw_json
                FROM predictions p
                JOIN runs r ON r.id = p.run_id
                WHERE {where_sql}
                ORDER BY p.id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            items = [_row_to_dict(row) for row in rows]
            evidence_by_prediction = _evidence_for_predictions(
                conn,
                [_as_int(item.get("id")) for item in items],
            )
            for item in items:
                prediction_id = _as_int(item.get("id"))
                item["evidence"] = evidence_by_prediction.get(prediction_id, [])

        return items

    def pending_predictions_json(self, limit: int = 20) -> str:
        return json.dumps(self.pending_predictions(limit), ensure_ascii=False, indent=2)

    def pending_predictions_json_for_workflow(
        self,
        workflow: str,
        limit: int = 20,
        target_hint: str | None = None,
        due_on_or_before: str | None = None,
        due_after: str | None = None,
    ) -> str:
        return json.dumps(
            self.pending_predictions_for_workflow(
                workflow,
                limit,
                target_hint=target_hint,
                due_on_or_before=due_on_or_before,
                due_after=due_after,
            ),
            ensure_ascii=False,
            indent=2,
        )

    def deferred_predictions_json_for_workflow(
        self,
        workflow: str,
        limit: int = 20,
        target_hint: str | None = None,
        review_cutoff_date: str | None = None,
    ) -> str:
        if not review_cutoff_date:
            return "[]"
        return json.dumps(
            self.pending_predictions_for_workflow(
                workflow,
                limit,
                target_hint=target_hint,
                due_on_or_before=review_cutoff_date,
                require_unknown_reviewed_on_or_after=review_cutoff_date,
            ),
            ensure_ascii=False,
            indent=2,
        )

    def latest_observed_trading_date(self, on_or_before: str | None = None) -> str | None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT result_text
                FROM observations
                WHERE tool_name = 'get_a_share_calendar_trading_days'
                ORDER BY id DESC
                LIMIT 20
                """,
            ).fetchall()
        for row in rows:
            latest = _latest_calendar_date_from_result(
                row["result_text"],
                on_or_before=on_or_before,
            )
            if latest:
                return latest
        return None

    def find_cached_report(
        self,
        *,
        workflow: str,
        user_input: str,
        ttl_seconds: int,
        similarity_threshold: float,
    ) -> dict[str, Any] | None:
        if ttl_seconds <= 0:
            return None

        cutoff = (
            datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(seconds=ttl_seconds)
        ).isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, workflow, user_input, output, memory_json
                FROM runs
                WHERE COALESCE(workflow, '') = ?
                  AND created_at >= ?
                  AND COALESCE(output, '') != ''
                ORDER BY id ASC
                LIMIT 200
                """,
                (workflow, cutoff),
            ).fetchall()

        for row in rows:
            similarity = _question_similarity(user_input, row["user_input"])
            if similarity >= similarity_threshold:
                age_seconds = _run_age_seconds(row["created_at"])
                return {
                    "run_id": int(row["id"]),
                    "created_at": row["created_at"],
                    "workflow": row["workflow"],
                    "user_input": row["user_input"],
                    "answer": row["output"],
                    "memory_payload_detected": row["memory_json"] is not None,
                    "similarity": similarity,
                    "age_seconds": age_seconds,
                    "ttl_seconds": ttl_seconds,
                }
        return None

    def recent_lessons(self, limit: int = 10) -> list[str]:
        return self._recent_lessons(limit=limit, workflow=None)

    def recent_lessons_for_workflow(self, workflow: str, limit: int = 10) -> list[str]:
        return self._recent_lessons(limit=limit, workflow=workflow)

    def _recent_lessons(self, *, limit: int, workflow: str | None) -> list[str]:
        params: list[Any] = []
        workflow_filter = ""
        if workflow:
            workflow_names = sorted(_workflow_names_for_memory(workflow))
            workflow_filter = f"WHERE r.workflow IN ({_placeholders(workflow_names)})"
            params.extend(workflow_names)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.lesson
                FROM lessons l
                JOIN runs r ON r.id = l.run_id
                {workflow_filter}
                ORDER BY l.id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [str(row["lesson"]) for row in rows if row["lesson"]]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            prediction_total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            valid_prediction_total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validation_status = 'valid'",
            ).fetchone()[0]
            invalid_prediction_total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE validation_status = 'invalid'",
            ).fetchone()[0]
            pending_total = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE status = 'pending'",
            ).fetchone()[0]
            reviewed_total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            avg_score = conn.execute("SELECT AVG(score) FROM reviews").fetchone()[0]
            outcomes = conn.execute(
                """
                SELECT outcome, COUNT(*) AS count
                FROM reviews
                GROUP BY outcome
                ORDER BY count DESC
                """,
            ).fetchall()
            by_scope = conn.execute(
                """
                SELECT p.scope AS scope, r.outcome AS outcome, COUNT(*) AS count
                FROM reviews r
                JOIN predictions p ON p.id = r.prediction_id
                GROUP BY p.scope, r.outcome
                ORDER BY p.scope, r.outcome
                """,
            ).fetchall()
            by_metric = conn.execute(
                """
                SELECT p.metric AS metric, r.outcome AS outcome, COUNT(*) AS count
                FROM reviews r
                JOIN predictions p ON p.id = r.prediction_id
                GROUP BY p.metric, r.outcome
                ORDER BY p.metric, r.outcome
                """,
            ).fetchall()
            by_confidence = conn.execute(
                """
                SELECT
                    CASE
                        WHEN p.confidence < 0.4 THEN '<0.4'
                        WHEN p.confidence < 0.6 THEN '0.4-0.6'
                        WHEN p.confidence < 0.8 THEN '0.6-0.8'
                        ELSE '>=0.8'
                    END AS bucket,
                    r.outcome AS outcome,
                    COUNT(*) AS count,
                    AVG(r.score) AS average_score
                FROM reviews r
                JOIN predictions p ON p.id = r.prediction_id
                GROUP BY bucket, r.outcome
                ORDER BY bucket, r.outcome
                """,
            ).fetchall()
            validation_error_total = conn.execute(
                "SELECT COUNT(*) FROM validation_errors",
            ).fetchone()[0]
            evidence_trace_total = conn.execute(
                "SELECT COUNT(*) FROM prediction_evidence",
            ).fetchone()[0]
            predictions_with_evidence_total = conn.execute(
                "SELECT COUNT(DISTINCT prediction_id) FROM prediction_evidence",
            ).fetchone()[0]
            validation_errors_by_type = conn.execute(
                """
                SELECT item_type, COUNT(*) AS count
                FROM validation_errors
                GROUP BY item_type
                ORDER BY count DESC, item_type
                """,
            ).fetchall()
            run_audit_total = conn.execute("SELECT COUNT(*) FROM run_audits").fetchone()[0]

        return {
            "db_path": str(self.db_path),
            "prediction_total": prediction_total,
            "valid_prediction_total": valid_prediction_total,
            "invalid_prediction_total": invalid_prediction_total,
            "pending_total": pending_total,
            "reviewed_total": reviewed_total,
            "average_score": avg_score,
            "outcomes": {row["outcome"]: row["count"] for row in outcomes},
            "by_scope": _grouped_counts(by_scope, "scope"),
            "by_metric": _grouped_counts(by_metric, "metric"),
            "by_confidence": [
                {
                    "bucket": row["bucket"],
                    "outcome": row["outcome"],
                    "count": row["count"],
                    "average_score": row["average_score"],
                }
                for row in by_confidence
            ],
            "evidence_trace_total": evidence_trace_total,
            "predictions_with_evidence_total": predictions_with_evidence_total,
            "validation_error_total": validation_error_total,
            "validation_errors_by_type": {
                row["item_type"]: row["count"] for row in validation_errors_by_type
            },
            "run_audit_total": run_audit_total,
        }

    def recent_validation_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ve.id, ve.created_at, ve.run_id, r.workflow, ve.item_type,
                       ve.item_index, ve.errors_json, ve.item_summary_json
                FROM validation_errors ve
                JOIN runs r ON r.id = ve.run_id
                ORDER BY ve.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_validation_error_row_to_dict(row) for row in rows]

    def recent_validation_errors_for_workflow(
        self,
        workflow: str,
        limit: int = 5,
        target_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        workflow_names = sorted(_workflow_names_for_memory(workflow))
        query_limit = limit * 5 if target_hint else limit
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ve.id, ve.created_at, ve.run_id, r.workflow, ve.item_type,
                       ve.item_index, ve.errors_json, ve.item_summary_json
                FROM validation_errors ve
                JOIN runs r ON r.id = ve.run_id
                WHERE r.workflow IN ({_placeholders(workflow_names)})
                ORDER BY ve.id DESC
                LIMIT ?
                """,
                (*workflow_names, query_limit),
            ).fetchall()
        errors = [_validation_error_row_to_dict(row) for row in rows]
        normalized_hint = _normalize_target_hint(target_hint)
        if normalized_hint:
            errors = [
                error
                for error in errors
                if _validation_error_matches_target_hint(error, normalized_hint)
            ]
        return errors[:limit]

    def recent_run_audits(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, created_at, workflow, tool_names_json,
                       required_tools_json, missing_tools_json, signal_families_json,
                       prediction_count, accepted_prediction_count,
                       invalid_prediction_count, review_count, invalid_review_count,
                       lesson_count, validation_error_count, output_audit_status,
                       memory_json_status, audit_json
                FROM run_audits
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_run_audit_row_to_dict(row) for row in rows]

    def run_audit(self, run_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, run_id, created_at, workflow, tool_names_json,
                       required_tools_json, missing_tools_json, signal_families_json,
                       prediction_count, accepted_prediction_count,
                       invalid_prediction_count, review_count, invalid_review_count,
                       lesson_count, validation_error_count, output_audit_status,
                       memory_json_status, audit_json
                FROM run_audits
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if not row:
                return None
            audit = _run_audit_row_to_dict(row)
            validation_rows = conn.execute(
                """
                SELECT ve.id, ve.created_at, ve.run_id, r.workflow, ve.item_type,
                       ve.item_index, ve.errors_json, ve.item_summary_json
                FROM validation_errors ve
                JOIN runs r ON r.id = ve.run_id
                WHERE ve.run_id = ?
                ORDER BY ve.id
                """,
                (run_id,),
            ).fetchall()
            audit["validation_errors"] = [
                _validation_error_row_to_dict(validation_row)
                for validation_row in validation_rows
            ]
        return audit

    def workflow_performance_summary(self, workflow: str) -> dict[str, Any]:
        workflow_filter, workflow_params = _workflow_filter_sql(
            workflow,
            run_alias="pr",
            prediction_alias="p",
        )
        with self._connect() as conn:
            totals = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS reviewed_total,
                    SUM(CASE WHEN rv.outcome = 'hit' THEN 1 ELSE 0 END) AS hit_count,
                    SUM(CASE WHEN rv.outcome = 'miss' THEN 1 ELSE 0 END) AS miss_count,
                    SUM(CASE WHEN rv.outcome = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
                    AVG(rv.score) AS average_score
                FROM reviews rv
                JOIN predictions p ON p.id = rv.prediction_id
                JOIN runs pr ON pr.id = p.run_id
                WHERE {workflow_filter}
                """,
                tuple(workflow_params),
            ).fetchone()
            by_metric = conn.execute(
                f"""
                SELECT
                    COALESCE(p.metric, 'unknown') AS key,
                    COUNT(*) AS reviewed_total,
                    SUM(CASE WHEN rv.outcome = 'hit' THEN 1 ELSE 0 END) AS hit_count,
                    SUM(CASE WHEN rv.outcome = 'miss' THEN 1 ELSE 0 END) AS miss_count,
                    SUM(CASE WHEN rv.outcome = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
                    AVG(rv.score) AS average_score
                FROM reviews rv
                JOIN predictions p ON p.id = rv.prediction_id
                JOIN runs pr ON pr.id = p.run_id
                WHERE {workflow_filter}
                GROUP BY COALESCE(p.metric, 'unknown')
                ORDER BY reviewed_total DESC, key
                LIMIT 8
                """,
                tuple(workflow_params),
            ).fetchall()
            by_confidence = conn.execute(
                f"""
                SELECT
                    CASE
                        WHEN p.confidence < 0.4 THEN '<0.4'
                        WHEN p.confidence < 0.6 THEN '0.4-0.6'
                        WHEN p.confidence < 0.8 THEN '0.6-0.8'
                        ELSE '>=0.8'
                    END AS key,
                    COUNT(*) AS reviewed_total,
                    SUM(CASE WHEN rv.outcome = 'hit' THEN 1 ELSE 0 END) AS hit_count,
                    SUM(CASE WHEN rv.outcome = 'miss' THEN 1 ELSE 0 END) AS miss_count,
                    SUM(CASE WHEN rv.outcome = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
                    AVG(rv.score) AS average_score
                FROM reviews rv
                JOIN predictions p ON p.id = rv.prediction_id
                JOIN runs pr ON pr.id = p.run_id
                WHERE {workflow_filter}
                GROUP BY key
                ORDER BY key
                """,
                tuple(workflow_params),
            ).fetchall()

        return {
            **_performance_summary_from_row(totals),
            "by_metric": [_performance_summary_from_row(row) for row in by_metric],
            "by_confidence": [_performance_summary_from_row(row) for row in by_confidence],
        }

    def _insert_predictions(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        payload: dict[str, Any],
        used_tool_names: set[str] | None,
        observation_records: list[dict[str, Any]],
    ) -> tuple[int, int, list[dict[str, Any]]]:
        predictions = payload.get("predictions", [])
        if not isinstance(predictions, list):
            return 0, 0, [
                _validation_error(
                    item_type="predictions",
                    index=None,
                    errors=["predictions must be an array"],
                ),
            ]

        workflow = _run_workflow(conn, run_id)
        batch_error = _prediction_batch_error(workflow, predictions, used_tool_names)
        if batch_error:
            return 0, len(predictions), [
                _validation_error(
                    item_type="predictions",
                    index=None,
                    errors=[batch_error],
                ),
            ]
        workflow_tools_error = _workflow_prediction_tools_error(
            workflow,
            predictions,
            used_tool_names,
        )
        if workflow_tools_error:
            return 0, len(predictions), [
                _validation_error(
                    item_type="predictions",
                    index=None,
                    errors=[workflow_tools_error],
                ),
            ]
        diversity_error = _market_prediction_diversity_error(
            workflow,
            predictions,
            used_tool_names,
        )
        if diversity_error:
            return 0, len(predictions), [
                _validation_error(
                    item_type="predictions",
                    index=None,
                    errors=[diversity_error],
                ),
            ]
        stock_diversity_error = _stock_prediction_diversity_error(
            workflow,
            predictions,
            used_tool_names,
        )
        if stock_diversity_error:
            return 0, len(predictions), [
                _validation_error(
                    item_type="predictions",
                    index=None,
                    errors=[stock_diversity_error],
                ),
            ]

        count = 0
        invalid_count = 0
        collected_errors: list[dict[str, Any]] = []
        for index, item in enumerate(predictions):
            if not isinstance(item, dict):
                invalid_count += 1
                collected_errors.append(
                    _validation_error(
                        item_type="prediction",
                        index=index,
                        errors=["prediction item must be an object"],
                    ),
                )
                continue
            condition_json, item_errors = validate_prediction_item(item)
            validation_status = "valid" if not item_errors else "invalid"
            status = "pending" if validation_status == "valid" else "invalid"
            condition_json_text = _condition_json_text(condition_json)
            if status == "pending":
                evidence_error = _prediction_evidence_error(item, used_tool_names)
                if evidence_error:
                    invalid_count += 1
                    collected_errors.append(
                        _validation_error(
                            item_type="prediction",
                            index=index,
                            errors=[evidence_error],
                            item=item,
                        ),
                    )
                    continue
                duplicate_id = _duplicate_pending_prediction_id(
                    conn,
                    workflow=workflow,
                    item=item,
                    condition_json_text=condition_json_text,
                )
                if duplicate_id is not None:
                    invalid_count += 1
                    collected_errors.append(
                        _validation_error(
                            item_type="prediction",
                            index=index,
                            errors=[
                                (
                                    "duplicate pending prediction: "
                                    f"matches prediction_id {duplicate_id}"
                                ),
                            ],
                            item=item,
                        ),
                    )
                    continue
                similar_id = _similar_pending_prediction_id(
                    conn,
                    workflow=workflow,
                    item=item,
                    condition_json=condition_json,
                )
                if similar_id is not None:
                    invalid_count += 1
                    collected_errors.append(
                        _validation_error(
                            item_type="prediction",
                            index=index,
                            errors=[
                                (
                                    "similar pending prediction: "
                                    f"matches prediction_id {similar_id}"
                                ),
                            ],
                            item=item,
                        ),
                    )
                    continue
            cursor = conn.execute(
                """
                INSERT INTO predictions (
                    run_id, created_at, as_of_date, scope, target, target_id,
                    horizon_days, trade_date, metric, expected_direction, expected_range,
                    confidence, rationale, validation_query, condition_json,
                    validation_status, validation_errors, raw_json, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _now(),
                    _as_text(item.get("as_of_date")),
                    _as_text(item.get("scope")),
                    _as_text(item.get("target")),
                    _as_text(item.get("target_id")),
                    _as_int(item.get("horizon_days")),
                    _as_text(item.get("trade_date")),
                    _as_text(item.get("metric")),
                    _as_text(item.get("expected_direction")),
                    _as_text(item.get("expected_range")),
                    _as_float(item.get("confidence")),
                    _as_text(item.get("rationale")),
                    _as_text(item.get("validation_query")),
                    condition_json_text,
                    validation_status,
                    "; ".join(item_errors),
                    json.dumps(item, ensure_ascii=False),
                    status,
                ),
            )
            if status == "pending":
                count += 1
                _insert_prediction_evidence(
                    conn,
                    int(cursor.lastrowid),
                    item,
                    observation_records,
                )
            else:
                invalid_count += 1
                collected_errors.append(
                    _validation_error(
                        item_type="prediction",
                        index=index,
                        errors=item_errors,
                        item=item,
                    ),
                )
        return count, invalid_count, collected_errors

    def _insert_reviews(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        payload: dict[str, Any],
        used_tool_names: set[str] | None,
    ) -> tuple[int, int, int, list[dict[str, Any]]]:
        reviews = payload.get("reviews", [])
        if not isinstance(reviews, list):
            return 0, 0, 0, [
                _validation_error(
                    item_type="reviews",
                    index=None,
                    errors=["reviews must be an array"],
                ),
            ]

        count = 0
        lessons_count = 0
        invalid_count = 0
        validation_errors: list[dict[str, Any]] = []
        for index, item in enumerate(reviews):
            if not isinstance(item, dict):
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=["review item must be an object"],
                    ),
                )
                continue
            item_errors = validate_review_item(item)
            if item_errors:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=item_errors,
                        item=item,
                    ),
                )
                continue
            prediction_id = _as_int(item.get("prediction_id"))
            if prediction_id is None:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=["prediction_id must be a positive integer"],
                        item=item,
                    ),
                )
                continue
            stored_prediction = self._reviewable_prediction(conn, prediction_id)
            if not stored_prediction:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=[
                            (
                                f"prediction_id {prediction_id} is not pending, "
                                "valid, or reviewable"
                            ),
                        ],
                        item=item,
                    ),
                )
                continue
            mismatch_error = _review_mismatch_error(item, stored_prediction)
            if mismatch_error:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=[mismatch_error],
                        item=item,
                    ),
                )
                continue
            source_tool = _as_text(item.get("source_tool"))
            if used_tool_names is not None and source_tool not in used_tool_names:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=[
                            (
                                f"source_tool {source_tool} was not observed in "
                                "the current tool calls"
                            ),
                        ],
                        item=item,
                    ),
                )
                continue
            source_tool_error = _review_source_tool_error(item)
            if source_tool_error:
                invalid_count += 1
                validation_errors.append(
                    _validation_error(
                        item_type="review",
                        index=index,
                        errors=[source_tool_error],
                        item=item,
                    ),
                )
                continue
            stored_condition = stored_prediction["condition"]
            actual_value = item.get("actual_value")
            deterministic = None
            if stored_condition:
                try:
                    deterministic = score_condition(stored_condition, actual_value)
                except (TypeError, ValueError) as exc:
                    deterministic = ScoreResult(
                        outcome="unknown",
                        score=None,
                        actual_value=None,
                        reason=str(exc),
                    )
                    item = {
                        **item,
                        "deterministic_score_error": str(exc),
                    }

            outcome = (
                deterministic.outcome
                if deterministic
                else (_as_text(item.get("outcome")) or "unknown")
            )
            score = deterministic.score if deterministic else _as_float(item.get("score"))
            actual_summary = _as_text(item.get("actual_summary"))
            if deterministic:
                actual_summary = (
                    f"{actual_summary or ''} actual_value={deterministic.actual_value}; "
                    f"{deterministic.reason}"
                ).strip()
            if outcome == "unknown":
                duplicate_unknown_id = _duplicate_unknown_review_id(conn, item)
                if duplicate_unknown_id is not None:
                    invalid_count += 1
                    validation_errors.append(
                        _validation_error(
                            item_type="review",
                            index=index,
                            errors=[
                                (
                                    "duplicate unknown review: "
                                    f"matches review_id {duplicate_unknown_id}"
                                ),
                            ],
                            item=item,
                        ),
                    )
                    continue
            cursor = conn.execute(
                """
                INSERT INTO reviews (
                    run_id, prediction_id, reviewed_at, actual_summary, outcome,
                    score, actual_trade_date, actual_metric, source_tool,
                    error_reason, lesson, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    prediction_id,
                    _now(),
                    actual_summary,
                    outcome,
                    score,
                    _as_text(item.get("actual_trade_date")),
                    _as_text(item.get("actual_metric")),
                    _as_text(item.get("source_tool")),
                    _as_text(item.get("error_reason")),
                    _as_text(item.get("lesson")),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
            if outcome != "unknown":
                conn.execute(
                    "UPDATE predictions SET status = 'reviewed' WHERE id = ? AND status = 'pending'",
                    (prediction_id,),
                )
            if item.get("lesson"):
                self._insert_single_lesson(conn, run_id, int(cursor.lastrowid), str(item["lesson"]))
                lessons_count += 1
            count += 1
        return count, lessons_count, invalid_count, validation_errors

    def _insert_observations(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, observation in enumerate(observations, start=1):
            tool_name = _as_text(observation.get("tool_name"))
            cursor = conn.execute(
                """
                INSERT INTO observations (
                    run_id, created_at, sequence, tool_name, arguments_json, result_text
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _now(),
                    index,
                    tool_name,
                    json.dumps(observation.get("arguments") or {}, ensure_ascii=False),
                    _as_text(observation.get("result")),
                ),
            )
            records.append(
                {
                    "id": int(cursor.lastrowid),
                    "sequence": index,
                    "tool_name": tool_name,
                },
            )
        return records

    def _insert_validation_errors(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        validation_errors: list[dict[str, Any]],
    ) -> None:
        for error in validation_errors:
            conn.execute(
                """
                INSERT INTO validation_errors (
                    run_id, created_at, item_type, item_index,
                    errors_json, item_summary_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _now(),
                    _as_text(error.get("item_type")) or "unknown",
                    _as_int(error.get("index")),
                    json.dumps(error.get("errors") or [], ensure_ascii=False),
                    (
                        json.dumps(error.get("item_summary"), ensure_ascii=False)
                        if error.get("item_summary")
                        else None
                    ),
                ),
            )

    def _insert_run_audit(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        run_audit: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_audits (
                run_id, created_at, workflow, tool_names_json, required_tools_json,
                missing_tools_json, signal_families_json, prediction_count,
                accepted_prediction_count, invalid_prediction_count, review_count,
                invalid_review_count, lesson_count, validation_error_count,
                output_audit_status, memory_json_status, audit_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _now(),
                _as_text(run_audit.get("workflow")),
                json.dumps(run_audit.get("tool_names") or [], ensure_ascii=False),
                json.dumps(run_audit.get("required_tools") or [], ensure_ascii=False),
                json.dumps(run_audit.get("missing_tools") or [], ensure_ascii=False),
                json.dumps(run_audit.get("signal_families") or [], ensure_ascii=False),
                int(run_audit.get("prediction_count") or 0),
                int(run_audit.get("accepted_prediction_count") or 0),
                int(run_audit.get("invalid_prediction_count") or 0),
                int(run_audit.get("review_count") or 0),
                int(run_audit.get("invalid_review_count") or 0),
                int(run_audit.get("lesson_count") or 0),
                int(run_audit.get("validation_error_count") or 0),
                _as_text(run_audit.get("output_audit_status")) or "skipped",
                _as_text(run_audit.get("memory_json_status")) or "skipped",
                json.dumps(run_audit, ensure_ascii=False),
            ),
        )

    def _reviewable_prediction(
        self,
        conn: sqlite3.Connection,
        prediction_id: int,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT condition_json, status, validation_status, trade_date, metric
            FROM predictions
            WHERE id = ?
            """,
            (prediction_id,),
        ).fetchone()
        if (
            not row
            or row["status"] != "pending"
            or row["validation_status"] != "valid"
            or not row["condition_json"]
        ):
            return None
        return {
            "condition": json.loads(row["condition_json"]),
            "trade_date": row["trade_date"],
            "metric": row["metric"],
        }

    def _insert_lessons(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        payload: dict[str, Any],
    ) -> tuple[int, list[dict[str, Any]]]:
        lessons = payload.get("lessons", [])
        if not isinstance(lessons, list):
            return 0, [
                _validation_error(
                    item_type="lessons",
                    index=None,
                    errors=["lessons must be an array"],
                ),
            ]

        count = 0
        validation_errors: list[dict[str, Any]] = []
        for index, item in enumerate(lessons):
            if not isinstance(item, dict):
                validation_errors.append(
                    _validation_error(
                        item_type="lesson",
                        index=index,
                        errors=["lesson item must be an object with a lesson field"],
                    ),
                )
                continue
            lesson = item.get("lesson")
            item_errors = validate_lesson_text(lesson)
            if item_errors:
                validation_errors.append(
                    _validation_error(
                        item_type="lesson",
                        index=index,
                        errors=item_errors,
                        item=item,
                    ),
                )
                continue
            self._insert_single_lesson(conn, run_id, None, str(lesson))
            count += 1
        return count, validation_errors

    def _insert_single_lesson(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        review_id: int | None,
        lesson: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO lessons (created_at, run_id, review_id, lesson)
            VALUES (?, ?, ?, ?)
            """,
            (_now(), run_id, review_id, lesson),
        )

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
            conn = self._memory_conn
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    workflow TEXT,
                    user_input TEXT NOT NULL,
                    output TEXT NOT NULL,
                    memory_json TEXT
                );

                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    as_of_date TEXT,
                    scope TEXT,
                    target TEXT,
                    target_id TEXT,
                    horizon_days INTEGER,
                    trade_date TEXT,
                    metric TEXT,
                    expected_direction TEXT,
                    expected_range TEXT,
                    confidence REAL,
                    rationale TEXT,
                    validation_query TEXT,
                    condition_json TEXT,
                    validation_status TEXT NOT NULL DEFAULT 'legacy',
                    validation_errors TEXT,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    prediction_id INTEGER NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    actual_summary TEXT,
                    outcome TEXT NOT NULL,
                    score REAL,
                    actual_trade_date TEXT,
                    actual_metric TEXT,
                    source_tool TEXT,
                    error_reason TEXT,
                    lesson TEXT,
                    raw_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id),
                    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    run_id INTEGER NOT NULL,
                    review_id INTEGER,
                    lesson TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id),
                    FOREIGN KEY (review_id) REFERENCES reviews(id)
                );

                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    tool_name TEXT,
                    arguments_json TEXT,
                    result_text TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS validation_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    item_index INTEGER,
                    errors_json TEXT NOT NULL,
                    item_summary_json TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS prediction_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_id INTEGER NOT NULL,
                    observation_id INTEGER NOT NULL,
                    tool_name TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (prediction_id) REFERENCES predictions(id),
                    FOREIGN KEY (observation_id) REFERENCES observations(id)
                );

                CREATE TABLE IF NOT EXISTS run_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    workflow TEXT,
                    tool_names_json TEXT NOT NULL,
                    required_tools_json TEXT NOT NULL,
                    missing_tools_json TEXT NOT NULL,
                    signal_families_json TEXT NOT NULL,
                    prediction_count INTEGER NOT NULL,
                    accepted_prediction_count INTEGER NOT NULL,
                    invalid_prediction_count INTEGER NOT NULL,
                    review_count INTEGER NOT NULL,
                    invalid_review_count INTEGER NOT NULL,
                    lesson_count INTEGER NOT NULL,
                    validation_error_count INTEGER NOT NULL,
                    output_audit_status TEXT NOT NULL,
                    memory_json_status TEXT NOT NULL,
                    audit_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_predictions_status
                ON predictions(status, validation_status, id);

                CREATE INDEX IF NOT EXISTS idx_reviews_prediction_id
                ON reviews(prediction_id);

                CREATE INDEX IF NOT EXISTS idx_validation_errors_run_id
                ON validation_errors(run_id, id);

                CREATE INDEX IF NOT EXISTS idx_validation_errors_item_type
                ON validation_errors(item_type, id);

                CREATE INDEX IF NOT EXISTS idx_prediction_evidence_prediction_id
                ON prediction_evidence(prediction_id, id);

                CREATE INDEX IF NOT EXISTS idx_run_audits_created_at
                ON run_audits(created_at, id);
                """
            )
            self._ensure_column(conn, "predictions", "trade_date", "TEXT")
            self._ensure_column(conn, "predictions", "condition_json", "TEXT")
            self._ensure_column(
                conn,
                "predictions",
                "validation_status",
                "TEXT NOT NULL DEFAULT 'legacy'",
            )
            self._ensure_column(conn, "predictions", "validation_errors", "TEXT")
            self._ensure_column(conn, "reviews", "actual_trade_date", "TEXT")
            self._ensure_column(conn, "reviews", "actual_metric", "TEXT")
            self._ensure_column(conn, "reviews", "source_tool", "TEXT")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def extract_memory_json(text: str) -> dict[str, Any] | None:
    match = MEMORY_BLOCK_RE.search(text)
    if not match:
        return None
    return json.loads(match.group(1))


def format_memory_context(
    store: MemoryStore,
    workflow_name: str,
    pending_limit: int,
    target_hint: str | None = None,
    current_date: str | None = None,
    latest_available_trade_date: str | None = None,
) -> str:
    current_date = current_date or _today()
    review_cutoff_date = (
        _normalize_calendar_date(latest_available_trade_date)
        or store.latest_observed_trading_date(on_or_before=current_date)
        or _latest_weekday_on_or_before(current_date)
    )
    filtered_target_hint = target_hint if workflow_name == "stock-analysis" else None
    due_pending = json.dumps(
        store.pending_predictions_for_workflow(
            workflow=workflow_name,
            limit=pending_limit,
            target_hint=filtered_target_hint,
            due_on_or_before=review_cutoff_date,
            skip_unknown_reviewed_on_or_after=review_cutoff_date,
        ),
        ensure_ascii=False,
        indent=2,
    )
    deferred_pending = store.deferred_predictions_json_for_workflow(
        workflow=workflow_name,
        limit=pending_limit,
        target_hint=filtered_target_hint,
        review_cutoff_date=review_cutoff_date,
    )
    future_pending = store.pending_predictions_json_for_workflow(
        workflow=workflow_name,
        limit=pending_limit,
        target_hint=filtered_target_hint,
        due_after=review_cutoff_date,
    )
    lessons = store.recent_lessons_for_workflow(workflow=workflow_name, limit=10)
    lesson_text = "\n".join(f"- {lesson}" for lesson in lessons) if lessons else "暂无历史复盘经验。"
    performance_text = _format_performance_feedback(
        store.workflow_performance_summary(workflow_name),
    )
    validation_feedback = _format_validation_feedback(
        store.recent_validation_errors_for_workflow(
            workflow_name,
            limit=5,
            target_hint=filtered_target_hint,
        ),
    )
    target_line = (
        f"当前输入/标的筛选：{target_hint.strip()}\n"
        if workflow_name == "stock-analysis" and target_hint and target_hint.strip()
        else ""
    )
    return (
        target_line +
        f"当前自然日期：{current_date}\n"
        f"最新可验证交易日：{review_cutoff_date}\n"
        "注意：待复盘划分以最新可验证交易日为准，不以周末/假日的自然日期为准；"
        "日历工具若只返回到最新交易日，不得把更晚自然日期当作已可验证。\n"
        "已到最新可验证交易日、且当前数据日尚未尝试 unknown 的待复盘预测如下，"
        "必须按 prediction_id 回填可验证复盘；若记录为空，说明没有可执行复盘：\n"
        f"{due_pending}\n\n"
        "当前数据日已尝试但仍 unknown 的暂缓复盘预测如下，不得重复写 reviews；"
        "只有最新可验证交易日推进后才重新复盘：\n"
        f"{deferred_pending}\n\n"
        "尚未到最新可验证交易日的待跟踪预测如下，不得写入 reviews；"
        "生成新预测时仅用于避免重复假设和管理观察队列：\n"
        f"{future_pending}\n\n"
        "最近复盘经验，生成新预测时必须参考：\n"
        f"{lesson_text}\n\n"
        "历史系统评分反馈，生成新预测时必须用于校准阈值、置信度和信号权重：\n"
        f"{performance_text}\n\n"
        "最近结构化记忆拒绝，生成 MEMORY_JSON 时必须逐项规避：\n"
        f"{validation_feedback}"
    )


def _latest_calendar_date_from_result(
    result_text: Any,
    *,
    on_or_before: str | None = None,
) -> str | None:
    if not isinstance(result_text, str) or not result_text.strip():
        return None
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return None

    cutoff = _normalize_calendar_date(on_or_before)
    dates = [
        date_text
        for date_text in _calendar_dates_from_payload(payload)
        if cutoff is None or date_text <= cutoff
    ]
    return max(dates) if dates else None


def _calendar_dates_from_payload(payload: Any) -> list[str]:
    items: Any = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("item"), list):
            items = data["item"]
        elif isinstance(payload.get("item"), list):
            items = payload["item"]
    elif isinstance(payload, list):
        items = payload

    dates: list[str] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            date_text = _normalize_calendar_date(item.get("date"))
            if date_text:
                dates.append(date_text)
    return dates


def _normalize_calendar_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    if re.match(r"^\d{8}$", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def _latest_weekday_on_or_before(date_text: str) -> str:
    normalized = _normalize_calendar_date(date_text)
    if not normalized:
        return date_text
    current = datetime.strptime(normalized, "%Y-%m-%d")
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    condition_json = data.pop("condition_json", None)
    if condition_json:
        try:
            data["condition"] = json.loads(condition_json)
        except json.JSONDecodeError:
            data["condition"] = condition_json
    raw_json = data.pop("raw_json", None)
    if raw_json:
        try:
            data["raw"] = json.loads(raw_json)
        except json.JSONDecodeError:
            data["raw"] = raw_json
    return data


def _validate_workflow_output(
    *,
    workflow: str | None,
    output: str,
    observations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if observations is None or not workflow or not output.strip():
        return []

    errors: list[str] = []
    required_sections = _workflow_output_required_sections(workflow)
    missing_sections = [
        section
        for section in required_sections
        if section not in output
    ]
    if missing_sections:
        errors.append(
            "workflow output missing required synthesis section(s): "
            + ", ".join(missing_sections),
        )

    subjective_findings = find_subjective_terms(output)
    if subjective_findings:
        summary = ", ".join(
            f"{finding.term}({finding.count})"
            for finding in subjective_findings
        )
        errors.append(f"workflow output contains subjective wording: {summary}")

    if not errors:
        return []
    return [
        _validation_error(
            item_type="output",
            index=None,
            errors=errors,
        ),
    ]


def _workflow_output_required_sections(workflow: str) -> tuple[str, ...]:
    if workflow in MARKET_WORKFLOW_NAMES:
        return WORKFLOW_OUTPUT_REQUIRED_SECTIONS["market-weather"]
    return WORKFLOW_OUTPUT_REQUIRED_SECTIONS.get(workflow, ())


def _validate_memory_payload_shape(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors = [
        f"missing MEMORY_JSON field: {field}"
        for field in ("reviews", "predictions", "lessons")
        if field not in payload
    ]
    if not errors:
        return []
    return [
        _validation_error(
            item_type="memory_json",
            index=None,
            errors=errors,
        ),
    ]


def _validation_error_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    errors_json = data.pop("errors_json", None)
    if errors_json:
        try:
            data["errors"] = json.loads(errors_json)
        except json.JSONDecodeError:
            data["errors"] = [errors_json]
    else:
        data["errors"] = []

    summary_json = data.pop("item_summary_json", None)
    if summary_json:
        try:
            data["item_summary"] = json.loads(summary_json)
        except json.JSONDecodeError:
            data["item_summary"] = summary_json
    return data


def _run_audit_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "tool_names_json",
        "required_tools_json",
        "missing_tools_json",
        "signal_families_json",
    ):
        output_key = key.removesuffix("_json")
        data[output_key] = _json_list(data.pop(key, None))

    audit_json = data.pop("audit_json", None)
    if audit_json:
        try:
            data["audit"] = json.loads(audit_json)
        except json.JSONDecodeError:
            data["audit"] = {"raw": audit_json}
    else:
        data["audit"] = {}
    return data


def _build_run_audit(
    *,
    workflow: str | None,
    output: str,
    memory_payload: dict[str, Any] | None,
    observations: list[dict[str, Any]] | None,
    used_tool_names: set[str] | None,
    predictions_added: int,
    invalid_predictions_added: int,
    reviews_added: int,
    invalid_reviews_added: int,
    lessons_added: int,
    validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    tool_names = sorted(used_tool_names or [])
    required_tools = sorted(_workflow_prediction_required_tools(workflow))
    missing_tools = (
        sorted(set(required_tools) - set(tool_names))
        if observations is not None and required_tools
        else []
    )
    predictions = _payload_array(memory_payload, "predictions")
    reviews = _payload_array(memory_payload, "reviews")
    lessons = _payload_array(memory_payload, "lessons")
    signal_families = sorted(_prediction_signal_families(workflow, predictions))
    validation_by_type = _validation_error_counts(validation_errors)
    output_missing_sections = (
        _workflow_output_missing_sections(workflow, output)
        if _workflow_output_audit_applies(workflow, output, observations)
        else []
    )
    output_errors = _validation_errors_for_type(validation_errors, "output")
    memory_json_errors = _validation_errors_for_type(validation_errors, "memory_json")

    return {
        "workflow": workflow,
        "tool_names": tool_names,
        "required_tools": required_tools,
        "missing_tools": missing_tools,
        "signal_families": signal_families,
        "prediction_count": len(predictions),
        "accepted_prediction_count": predictions_added,
        "invalid_prediction_count": invalid_predictions_added,
        "review_count": reviews_added,
        "invalid_review_count": invalid_reviews_added,
        "lesson_count": lessons_added,
        "declared_review_count": len(reviews),
        "declared_lesson_count": len(lessons),
        "validation_error_count": len(validation_errors),
        "validation_errors_by_type": validation_by_type,
        "output_audit_status": _workflow_output_audit_status(
            workflow,
            output,
            observations,
            output_errors,
        ),
        "output_missing_sections": output_missing_sections,
        "output_errors": output_errors,
        "memory_json_status": _memory_json_audit_status(
            workflow,
            memory_payload,
            memory_json_errors,
        ),
        "memory_json_errors": memory_json_errors,
    }


def _payload_array(payload: dict[str, Any] | None, key: str) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _prediction_signal_families(
    workflow: str | None,
    predictions: list[Any],
) -> set[str]:
    families: set[str] = set()
    if workflow == "stock-analysis":
        family_map = STOCK_METRIC_SIGNAL_FAMILIES
    else:
        family_map = MARKET_METRIC_SIGNAL_FAMILIES
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        metric = _as_text(prediction.get("metric")) or ""
        family = family_map.get(metric)
        if family:
            families.add(family)
    return families


def _validation_error_counts(validation_errors: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for error in validation_errors:
        item_type = str(error.get("item_type") or "unknown")
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _validation_errors_for_type(
    validation_errors: list[dict[str, Any]],
    item_type: str,
) -> list[str]:
    errors: list[str] = []
    for error in validation_errors:
        if error.get("item_type") != item_type:
            continue
        errors.extend(str(item) for item in (error.get("errors") or []))
    return errors


def _workflow_output_audit_status(
    workflow: str | None,
    output: str,
    observations: list[dict[str, Any]] | None,
    output_errors: list[str],
) -> str:
    if not _workflow_output_audit_applies(workflow, output, observations):
        return "skipped"
    return "failed" if output_errors else "passed"


def _workflow_output_audit_applies(
    workflow: str | None,
    output: str,
    observations: list[dict[str, Any]] | None,
) -> bool:
    return observations is not None and bool(workflow) and bool(output.strip())


def _workflow_output_missing_sections(workflow: str | None, output: str) -> list[str]:
    if not workflow:
        return []
    return [
        section
        for section in _workflow_output_required_sections(workflow)
        if section not in output
    ]


def _memory_json_audit_status(
    workflow: str | None,
    memory_payload: dict[str, Any] | None,
    memory_json_errors: list[str],
) -> str:
    if not workflow:
        return "skipped"
    if memory_payload is None:
        return "failed"
    return "failed" if memory_json_errors else "passed"


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [value]
    return parsed if isinstance(parsed, list) else [parsed]


def _validation_error_matches_target_hint(
    error: dict[str, Any],
    normalized_hint: str,
) -> bool:
    summary = error.get("item_summary")
    if not isinstance(summary, dict):
        return True
    values = [
        summary.get("target_id"),
        summary.get("target"),
        summary.get("prediction_id"),
    ]
    return any(normalized_hint in str(value or "").upper() for value in values)


def _format_validation_feedback(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return "暂无近期结构化记忆拒绝。"
    lines: list[str] = []
    for error in errors:
        item_type = error.get("item_type") or "unknown"
        item_index = error.get("item_index")
        location = f"{item_type}[{item_index}]" if item_index is not None else str(item_type)
        reason = "；".join(str(item) for item in (error.get("errors") or []))
        if not reason:
            reason = "unknown validation error"
        summary = _format_validation_summary(error.get("item_summary"))
        lines.append(f"- {location}: {_short_prompt_text(reason)}{summary}")
    return "\n".join(lines)


def _format_validation_summary(summary: Any) -> str:
    if not isinstance(summary, dict) or not summary:
        return ""
    parts = [
        f"{key}={summary[key]}"
        for key in (
            "target",
            "target_id",
            "scope",
            "metric",
            "actual_metric",
            "source_tool",
        )
        if key in summary and summary[key] not in (None, "")
    ]
    if not parts:
        return ""
    return f" ({_short_prompt_text(', '.join(parts), limit=120)})"


def _short_prompt_text(value: Any, limit: int = 220) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _grouped_counts(rows: list[sqlite3.Row], key: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        group_key = str(row[key] or "unknown")
        grouped.setdefault(group_key, {})[str(row["outcome"])] = int(row["count"])
    return grouped


def _validation_error(
    *,
    item_type: str,
    index: int | None,
    errors: list[str],
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "item_type": item_type,
        "index": index,
        "errors": [str(error) for error in errors],
    }
    if item:
        summary = {
            key: item[key]
            for key in (
                "prediction_id",
                "target",
                "target_id",
                "scope",
                "metric",
                "actual_metric",
                "trade_date",
                "actual_trade_date",
                "source_tool",
                "lesson",
            )
            if key in item
        }
        if summary:
            entry["item_summary"] = summary
    return entry


def _performance_summary_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
    data = dict(row) if row else {}
    hit_count = int(data.get("hit_count") or 0)
    miss_count = int(data.get("miss_count") or 0)
    unknown_count = int(data.get("unknown_count") or 0)
    scored_total = hit_count + miss_count
    summary = {
        "reviewed_total": int(data.get("reviewed_total") or 0),
        "scored_total": scored_total,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "unknown_count": unknown_count,
        "hit_rate": hit_count / scored_total if scored_total else None,
        "average_score": data.get("average_score"),
    }
    if "key" in data:
        summary["key"] = str(data.get("key") or "unknown")
    return summary


def _format_performance_feedback(summary: dict[str, Any]) -> str:
    if int(summary.get("reviewed_total") or 0) == 0:
        return "暂无历史系统评分反馈。"

    lines = [
        (
            "总体："
            f"复盘={summary['reviewed_total']}，"
            f"已评分={summary['scored_total']}，"
            f"hit={summary['hit_count']}，"
            f"miss={summary['miss_count']}，"
            f"unknown={summary['unknown_count']}，"
            f"命中率={_format_rate(summary['hit_rate'])}，"
            f"平均分={_format_score(summary['average_score'])}"
        ),
    ]
    metric_items = summary.get("by_metric") or []
    if metric_items:
        lines.append(
            "按指标："
            + "；".join(_format_performance_item(item) for item in metric_items),
        )
    confidence_items = summary.get("by_confidence") or []
    if confidence_items:
        lines.append(
            "按置信度："
            + "；".join(_format_performance_item(item) for item in confidence_items),
        )
    recommendations = _performance_recommendations(summary)
    if recommendations:
        lines.append("校准建议：" + "；".join(recommendations))
    return "\n".join(lines)


def _performance_recommendations(summary: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    for item in summary.get("by_metric") or []:
        if _is_weak_performance_item(item):
            recommendations.append(
                f"指标 {item.get('key', 'unknown')} 命中率低于 50%，"
                "下一轮提高 condition threshold、降低 confidence 或补充 tool/window 交叉验证",
            )
    for item in summary.get("by_confidence") or []:
        if _is_weak_performance_item(item):
            recommendations.append(
                f"置信度桶 {item.get('key', 'unknown')} 命中率低于 50%，"
                "下调该桶 confidence 权重，除非新增独立工具信号",
            )
    reviewed_total = int(summary.get("reviewed_total") or 0)
    unknown_count = int(summary.get("unknown_count") or 0)
    if reviewed_total >= 3 and unknown_count / reviewed_total >= 0.3:
        recommendations.append(
            "unknown 占比偏高，预测前补充 source_tool 和 actual_value 提取路径检查",
        )
    return recommendations[:5]


def _is_weak_performance_item(item: dict[str, Any]) -> bool:
    scored_total = int(item.get("scored_total") or 0)
    hit_rate = item.get("hit_rate")
    return scored_total >= 2 and hit_rate is not None and float(hit_rate) < 0.5


def _format_performance_item(item: dict[str, Any]) -> str:
    return (
        f"{item.get('key', 'unknown')}: "
        f"复盘={item['reviewed_total']}, "
        f"已评分={item['scored_total']}, "
        f"hit={item['hit_count']}, "
        f"miss={item['miss_count']}, "
        f"unknown={item['unknown_count']}, "
        f"命中率={_format_rate(item['hit_rate'])}, "
        f"平均分={_format_score(item['average_score'])}"
    )


def _format_rate(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _format_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def _run_workflow(conn: sqlite3.Connection, run_id: int) -> str | None:
    row = conn.execute("SELECT workflow FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return _as_text(row["workflow"])


def _condition_json_text(condition_json: dict[str, Any] | None) -> str | None:
    if not condition_json:
        return None
    return json.dumps(condition_json, ensure_ascii=False)


def _prediction_batch_error(
    workflow: str | None,
    predictions: list[Any],
    used_tool_names: set[str] | None,
) -> str | None:
    if used_tool_names is None or workflow not in MARKET_WORKFLOW_NAMES:
        return None
    count = len(predictions)
    if count == 0 or 3 <= count <= 6:
        return None
    return (
        "market-weather predictions must contain 0 records when "
        "prediction_trade_date is unavailable, otherwise 3-6 records"
    )


def _workflow_prediction_tools_error(
    workflow: str | None,
    predictions: list[Any],
    used_tool_names: set[str] | None,
) -> str | None:
    if used_tool_names is None or not predictions:
        return None
    required_tools = _workflow_prediction_required_tools(workflow)
    if not required_tools:
        return None
    missing_tools = sorted(required_tools - used_tool_names)
    if not missing_tools:
        return None
    observed = ", ".join(sorted(used_tool_names)) or "none"
    required = ", ".join(sorted(required_tools))
    missing = ", ".join(missing_tools)
    return (
        "workflow predictions require multi-tool synthesis evidence; "
        f"required tools: {required}; missing tools: {missing}; "
        f"observed tools: {observed}"
    )


def _workflow_prediction_required_tools(workflow: str | None) -> set[str]:
    if workflow in MARKET_WORKFLOW_NAMES:
        return set(MARKET_PREDICTION_REQUIRED_TOOLS)
    if workflow == "stock-analysis":
        return set(STOCK_PREDICTION_REQUIRED_TOOLS)
    return set()


def _market_prediction_diversity_error(
    workflow: str | None,
    predictions: list[Any],
    used_tool_names: set[str] | None,
) -> str | None:
    if used_tool_names is None or workflow not in MARKET_WORKFLOW_NAMES or not predictions:
        return None
    families: set[str] = set()
    metrics: set[str] = set()
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        metric = _as_text(prediction.get("metric")) or ""
        if metric:
            metrics.add(metric)
        family = MARKET_METRIC_SIGNAL_FAMILIES.get(metric)
        if family:
            families.add(family)
    if len(families) >= 2:
        return None
    observed_families = ", ".join(sorted(families)) or "none"
    observed_metrics = ", ".join(sorted(metrics)) or "none"
    return (
        "market-weather predictions require at least two signal families "
        "(index, breadth, liquidity) to avoid single-metric restatement; "
        f"observed families: {observed_families}; observed metrics: {observed_metrics}"
    )


def _stock_prediction_diversity_error(
    workflow: str | None,
    predictions: list[Any],
    used_tool_names: set[str] | None,
) -> str | None:
    if used_tool_names is None or workflow != "stock-analysis" or len(predictions) < 2:
        return None
    metrics = {
        metric
        for prediction in predictions
        if isinstance(prediction, dict)
        for metric in [_as_text(prediction.get("metric")) or ""]
        if metric
    }
    required_metrics = {"stock_return_pct", "turnover_amount_change_pct"}
    if required_metrics.issubset(metrics):
        return None
    observed = ", ".join(sorted(metrics)) or "none"
    required = ", ".join(sorted(required_metrics))
    return (
        "stock-analysis multiple predictions require both return and turnover "
        "signal families to avoid single-metric restatement; "
        f"required metrics: {required}; observed metrics: {observed}"
    )


def _evidence_for_predictions(
    conn: sqlite3.Connection,
    prediction_ids: list[int | None],
) -> dict[int, list[dict[str, Any]]]:
    valid_ids = [prediction_id for prediction_id in prediction_ids if prediction_id is not None]
    if not valid_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT prediction_id, observation_id, tool_name
        FROM prediction_evidence
        WHERE prediction_id IN ({_placeholders(valid_ids)})
        ORDER BY prediction_id, id
        """,
        tuple(valid_ids),
    ).fetchall()
    evidence: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        prediction_id = int(row["prediction_id"])
        evidence.setdefault(prediction_id, []).append(
            {
                "observation_id": int(row["observation_id"]),
                "tool_name": row["tool_name"],
            },
        )
    return evidence


def _insert_prediction_evidence(
    conn: sqlite3.Connection,
    prediction_id: int,
    prediction: dict[str, Any],
    observation_records: list[dict[str, Any]],
) -> None:
    evidence_tools = set(
        _prediction_evidence_tools(
            prediction,
            {
                str(observation["tool_name"])
                for observation in observation_records
                if observation.get("tool_name")
            },
        ),
    )
    if not evidence_tools:
        return
    now = _now()
    for observation in observation_records:
        tool_name = _as_text(observation.get("tool_name"))
        observation_id = _as_int(observation.get("id"))
        if not tool_name or observation_id is None or tool_name not in evidence_tools:
            continue
        conn.execute(
            """
            INSERT INTO prediction_evidence (
                prediction_id, observation_id, tool_name, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (prediction_id, observation_id, tool_name, now),
        )


def _prediction_evidence_tools(
    prediction: dict[str, Any],
    used_tool_names: set[str] | None,
) -> list[str]:
    if used_tool_names is None:
        return []
    metric = _as_text(prediction.get("metric")) or ""
    required_tools = PREDICTION_METRIC_TOOL_REQUIREMENTS.get(metric)
    if not required_tools:
        return []
    return sorted(required_tools.intersection(used_tool_names))


def _prediction_evidence_error(
    prediction: dict[str, Any],
    used_tool_names: set[str] | None,
) -> str | None:
    if used_tool_names is None:
        return None
    metric = _as_text(prediction.get("metric")) or ""
    required_tools = PREDICTION_METRIC_TOOL_REQUIREMENTS.get(metric)
    if not required_tools:
        return None
    if _prediction_evidence_tools(prediction, used_tool_names):
        return None
    required = ", ".join(sorted(required_tools))
    observed = ", ".join(sorted(used_tool_names)) or "none"
    return (
        f"prediction metric {metric} requires evidence from one of: {required}; "
        f"observed tools: {observed}"
    )


def _review_source_tool_error(review: dict[str, Any]) -> str | None:
    actual_metric = _as_text(review.get("actual_metric")) or ""
    source_tool = _as_text(review.get("source_tool")) or ""
    required_tools = PREDICTION_METRIC_TOOL_REQUIREMENTS.get(actual_metric)
    if not required_tools or not source_tool or source_tool in required_tools:
        return None
    required = ", ".join(sorted(required_tools))
    return (
        f"source_tool {source_tool} cannot verify actual_metric {actual_metric}; "
        f"expected one of: {required}"
    )


def _duplicate_pending_prediction_id(
    conn: sqlite3.Connection,
    *,
    workflow: str | None,
    item: dict[str, Any],
    condition_json_text: str | None,
) -> int | None:
    filters = [
        "p.status = 'pending'",
        "p.validation_status = 'valid'",
        "COALESCE(p.scope, '') = ?",
        "COALESCE(p.target_id, '') = ?",
        "COALESCE(p.target, '') = ?",
        "COALESCE(p.trade_date, '') = ?",
        "COALESCE(p.metric, '') = ?",
        "COALESCE(p.condition_json, '') = ?",
    ]
    params: list[Any] = [
        _as_text(item.get("scope")) or "",
        _as_text(item.get("target_id")) or "",
        _as_text(item.get("target")) or "",
        _as_text(item.get("trade_date")) or "",
        _as_text(item.get("metric")) or "",
        condition_json_text or "",
    ]
    if workflow:
        workflow_filter, workflow_params = _workflow_filter_sql(
            workflow,
            run_alias="r",
            prediction_alias="p",
        )
        filters.append(workflow_filter)
        params.extend(workflow_params)

    row = conn.execute(
        f"""
        SELECT p.id
        FROM predictions p
        JOIN runs r ON r.id = p.run_id
        WHERE {" AND ".join(filters)}
        ORDER BY p.id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return int(row["id"]) if row else None


def _similar_pending_prediction_id(
    conn: sqlite3.Connection,
    *,
    workflow: str | None,
    item: dict[str, Any],
    condition_json: dict[str, Any] | None,
) -> int | None:
    if not condition_json:
        return None

    filters = [
        "p.status = 'pending'",
        "p.validation_status = 'valid'",
        "COALESCE(p.scope, '') = ?",
        "COALESCE(p.trade_date, '') = ?",
        "COALESCE(p.metric, '') = ?",
    ]
    params: list[Any] = [
        _as_text(item.get("scope")) or "",
        _as_text(item.get("trade_date")) or "",
        _as_text(item.get("metric")) or "",
    ]

    target_id = (_as_text(item.get("target_id")) or "").strip().upper()
    target = (_as_text(item.get("target")) or "").strip().upper()
    scope = (_as_text(item.get("scope")) or "").strip()
    if target_id:
        filters.append("UPPER(COALESCE(p.target_id, '')) = ?")
        params.append(target_id)
    elif scope != "market":
        filters.append("UPPER(COALESCE(p.target, '')) = ?")
        params.append(target)

    if workflow:
        workflow_filter, workflow_params = _workflow_filter_sql(
            workflow,
            run_alias="r",
            prediction_alias="p",
        )
        filters.append(workflow_filter)
        params.extend(workflow_params)

    rows = conn.execute(
        f"""
        SELECT p.id, p.target, p.target_id, p.condition_json
        FROM predictions p
        JOIN runs r ON r.id = p.run_id
        WHERE {" AND ".join(filters)}
        ORDER BY p.id DESC
        LIMIT 20
        """,
        tuple(params),
    ).fetchall()
    for row in rows:
        if not _prediction_targets_are_similar(
            scope=scope,
            target_id=target_id,
            target=target,
            stored_target_id=_as_text(row["target_id"]) or "",
            stored_target=_as_text(row["target"]) or "",
        ):
            continue
        stored_condition = _parse_condition_json(row["condition_json"])
        if _conditions_are_similar(stored_condition, condition_json):
            return int(row["id"])
    return None


def _prediction_targets_are_similar(
    *,
    scope: str,
    target_id: str,
    target: str,
    stored_target_id: str,
    stored_target: str,
) -> bool:
    normalized_stored_id = stored_target_id.strip().upper()
    normalized_stored_target = stored_target.strip().upper()
    if target_id:
        return normalized_stored_id == target_id
    if scope == "market":
        return (
            normalized_stored_target == target
            or (
                _is_broad_market_target(target)
                and _is_broad_market_target(normalized_stored_target)
            )
        )
    return normalized_stored_target == target


def _is_broad_market_target(value: str) -> bool:
    normalized = value.strip().upper()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "A-SHARE",
            "ASHARE",
            "A股",
            "全市场",
            "市场",
            "赚钱效应",
            "MARKET",
        )
    )


def _parse_condition_json(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _conditions_are_similar(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    if not left or not right:
        return False
    if _as_text(left.get("metric")) != _as_text(right.get("metric")):
        return False
    if _as_text(left.get("unit")) != _as_text(right.get("unit")):
        return False

    left_direction = _condition_direction(left)
    right_direction = _condition_direction(right)
    if not left_direction or left_direction != right_direction:
        return False

    left_interval = _condition_interval(left)
    right_interval = _condition_interval(right)
    if not left_interval or not right_interval:
        return False
    return _intervals_overlap(left_interval, right_interval)


def _condition_direction(condition: dict[str, Any]) -> str | None:
    operator = _as_text(condition.get("operator"))
    if operator in {"gt", "gte"}:
        return "above"
    if operator in {"lt", "lte"}:
        return "below"
    if operator == "between":
        return "range"
    if operator == "eq":
        return "exact"
    return None


def _condition_interval(condition: dict[str, Any]) -> tuple[float, float] | None:
    operator = _as_text(condition.get("operator"))
    if operator in {"gt", "gte", "lt", "lte", "eq"}:
        threshold = _as_float(condition.get("threshold"))
        if threshold is None:
            return None
        if operator in {"gt", "gte"}:
            return threshold, float("inf")
        if operator in {"lt", "lte"}:
            return float("-inf"), threshold
        return threshold, threshold
    if operator == "between":
        lower = _as_float(condition.get("lower"))
        upper = _as_float(condition.get("upper"))
        if lower is None or upper is None or lower > upper:
            return None
        return lower, upper
    return None


def _intervals_overlap(
    left: tuple[float, float],
    right: tuple[float, float],
) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def _question_similarity(left: Any, right: Any) -> float:
    left_text = _as_text(left) or ""
    right_text = _as_text(right) or ""
    left_normalized = _normalize_question_text(left_text)
    right_normalized = _normalize_question_text(right_text)
    if not left_normalized and not right_normalized:
        return 1.0
    if not left_normalized or not right_normalized:
        return 0.0
    if _question_codes_conflict(left_text, right_text):
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    shorter, longer = sorted(
        (left_normalized, right_normalized),
        key=len,
    )
    if len(shorter) >= 4 and shorter in longer:
        return 0.92

    sequence_score = SequenceMatcher(
        None,
        left_normalized,
        right_normalized,
    ).ratio()
    ngram_score = _jaccard_score(
        _question_ngrams(left_normalized),
        _question_ngrams(right_normalized),
    )
    return max(sequence_score, ngram_score)


def _normalize_question_text(value: str) -> str:
    normalized = value.strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


def _question_codes_conflict(left: str, right: str) -> bool:
    left_codes = _question_codes(left)
    right_codes = _question_codes(right)
    return bool(left_codes and right_codes and left_codes != right_codes)


def _question_codes(value: str) -> set[str]:
    return {
        match.upper().replace(".", "")
        for match in re.findall(r"\b\d{6}(?:\.(?:SH|SZ|BJ))?\b", value, re.IGNORECASE)
    }


def _question_ngrams(value: str) -> set[str]:
    if len(value) <= 2:
        return {value}
    grams = {value[index : index + 2] for index in range(len(value) - 1)}
    grams.update(value)
    return grams


def _jaccard_score(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _run_age_seconds(created_at: Any) -> int | None:
    created_at_text = _as_text(created_at)
    if not created_at_text:
        return None
    try:
        created_at_dt = datetime.fromisoformat(created_at_text)
    except ValueError:
        return None
    return max(
        0,
        int((datetime.now(ZoneInfo("Asia/Shanghai")) - created_at_dt).total_seconds()),
    )


def _duplicate_unknown_review_id(
    conn: sqlite3.Connection,
    review: dict[str, Any],
) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM reviews
        WHERE prediction_id = ?
          AND outcome = 'unknown'
          AND COALESCE(actual_trade_date, '') = ?
          AND COALESCE(actual_metric, '') = ?
          AND COALESCE(source_tool, '') = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            _as_int(review.get("prediction_id")),
            _as_text(review.get("actual_trade_date")) or "",
            _as_text(review.get("actual_metric")) or "",
            _as_text(review.get("source_tool")) or "",
        ),
    ).fetchone()
    return int(row["id"]) if row else None


def _workflow_filter_sql(
    workflow: str,
    *,
    run_alias: str,
    prediction_alias: str,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    workflow_names = sorted(_workflow_names_for_memory(workflow))
    if workflow_names:
        clauses.append(f"{run_alias}.workflow IN ({_placeholders(workflow_names)})")
        params.extend(workflow_names)
    scopes = sorted(WORKFLOW_SCOPE_FILTERS.get(workflow, set()))
    if scopes:
        clauses.append(f"{prediction_alias}.scope IN ({_placeholders(scopes)})")
        params.extend(scopes)
    return f"({' OR '.join(clauses)})", params


def _workflow_names_for_memory(workflow: str) -> set[str]:
    if workflow in MARKET_WORKFLOW_NAMES:
        return set(MARKET_WORKFLOW_NAMES)
    return {workflow}


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _normalize_target_hint(target_hint: str | None) -> str:
    if not target_hint:
        return ""
    return target_hint.strip().upper()


def _review_mismatch_error(
    review: dict[str, Any],
    prediction: dict[str, Any],
) -> str | None:
    actual_metric = _as_text(review.get("actual_metric"))
    predicted_metric = _as_text(prediction.get("metric"))
    if actual_metric != predicted_metric:
        return f"actual_metric {actual_metric} does not match prediction metric {predicted_metric}"

    actual_trade_date = _as_text(review.get("actual_trade_date"))
    prediction_trade_date = _as_text(prediction.get("trade_date"))
    if actual_trade_date and prediction_trade_date and actual_trade_date < prediction_trade_date:
        return (
            f"actual_trade_date {actual_trade_date} is earlier than "
            f"prediction trade_date {prediction_trade_date}"
        )

    return None


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
