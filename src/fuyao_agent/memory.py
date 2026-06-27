from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fuyao_agent.prediction_schema import validate_prediction_item, validate_review_item
from fuyao_agent.scoring import ScoreResult, score_condition


MEMORY_BLOCK_RE = re.compile(
    r"MEMORY_JSON\s*:\s*```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class MemoryWriteResult:
    run_id: int
    predictions_added: int
    reviews_added: int
    lessons_added: int
    invalid_predictions_added: int = 0
    invalid_reviews_added: int = 0


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
                    json.dumps(memory_payload, ensure_ascii=False) if memory_payload else None,
                ),
            )
            run_id = int(cursor.lastrowid)

            predictions_added = 0
            invalid_predictions_added = 0
            reviews_added = 0
            invalid_reviews_added = 0
            lessons_added = 0

            if memory_payload:
                predictions_added, invalid_predictions_added = self._insert_predictions(
                    conn,
                    run_id,
                    memory_payload,
                )
                reviews_added, review_lessons_added, invalid_reviews_added = self._insert_reviews(
                    conn,
                    run_id,
                    memory_payload,
                )
                lessons_added = review_lessons_added + self._insert_lessons(
                    conn,
                    run_id,
                    memory_payload,
                )

            if observations:
                self._insert_observations(conn, run_id, observations)

            return MemoryWriteResult(
                run_id=run_id,
                predictions_added=predictions_added,
                reviews_added=reviews_added,
                lessons_added=lessons_added,
                invalid_predictions_added=invalid_predictions_added,
                invalid_reviews_added=invalid_reviews_added,
            )

    def pending_predictions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, as_of_date, scope, target, target_id, horizon_days,
                       trade_date, metric, expected_direction, expected_range, confidence,
                       rationale, validation_query, condition_json, validation_status,
                       validation_errors, raw_json
                FROM predictions
                WHERE status = 'pending'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [_row_to_dict(row) for row in rows]

    def pending_predictions_json(self, limit: int = 20) -> str:
        return json.dumps(self.pending_predictions(limit), ensure_ascii=False, indent=2)

    def recent_lessons(self, limit: int = 10) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT lesson
                FROM lessons
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
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
        }

    def _insert_predictions(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        payload: dict[str, Any],
    ) -> tuple[int, int]:
        predictions = payload.get("predictions") or []
        if not isinstance(predictions, list):
            return 0, 0

        count = 0
        invalid_count = 0
        for item in predictions:
            if not isinstance(item, dict):
                continue
            condition_json, validation_errors = validate_prediction_item(item)
            validation_status = "valid" if not validation_errors else "invalid"
            status = "pending" if validation_status == "valid" else "invalid"
            conn.execute(
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
                    json.dumps(condition_json, ensure_ascii=False) if condition_json else None,
                    validation_status,
                    "; ".join(validation_errors),
                    json.dumps(item, ensure_ascii=False),
                    status,
                ),
            )
            if status == "pending":
                count += 1
            else:
                invalid_count += 1
        return count, invalid_count

    def _insert_reviews(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        payload: dict[str, Any],
    ) -> tuple[int, int, int]:
        reviews = payload.get("reviews") or []
        if not isinstance(reviews, list):
            return 0, 0, 0

        count = 0
        lessons_count = 0
        invalid_count = 0
        for item in reviews:
            if not isinstance(item, dict):
                continue
            validation_errors = validate_review_item(item)
            if validation_errors:
                invalid_count += 1
                continue
            prediction_id = _as_int(item.get("prediction_id"))
            if prediction_id is None:
                invalid_count += 1
                continue
            stored_prediction = self._reviewable_prediction(conn, prediction_id)
            if not stored_prediction:
                invalid_count += 1
                continue
            mismatch_error = _review_mismatch_error(item, stored_prediction)
            if mismatch_error:
                invalid_count += 1
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
        return count, lessons_count, invalid_count

    def _insert_observations(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        observations: list[dict[str, Any]],
    ) -> None:
        for index, observation in enumerate(observations, start=1):
            conn.execute(
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
                    _as_text(observation.get("tool_name")),
                    json.dumps(observation.get("arguments") or {}, ensure_ascii=False),
                    _as_text(observation.get("result")),
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
    ) -> int:
        lessons = payload.get("lessons") or []
        if not isinstance(lessons, list):
            return 0

        count = 0
        for item in lessons:
            lesson = item.get("lesson") if isinstance(item, dict) else item
            if lesson:
                self._insert_single_lesson(conn, run_id, None, str(lesson))
                count += 1
        return count

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

                CREATE INDEX IF NOT EXISTS idx_predictions_status
                ON predictions(status, validation_status, id);

                CREATE INDEX IF NOT EXISTS idx_reviews_prediction_id
                ON reviews(prediction_id);
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


def format_memory_context(store: MemoryStore, workflow_name: str, pending_limit: int) -> str:
    if workflow_name == "daily-review":
        pending = store.pending_predictions_json(limit=pending_limit)
        return f"待复盘预测记录如下，必须按 prediction_id 回填复盘：\n{pending}"

    if workflow_name == "daily-forecast":
        lessons = store.recent_lessons(limit=10)
        if not lessons:
            return "暂无历史复盘经验。"
        joined = "\n".join(f"- {lesson}" for lesson in lessons)
        return f"最近复盘经验，生成预测时需要参考：\n{joined}"

    return ""


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


def _grouped_counts(rows: list[sqlite3.Row], key: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        group_key = str(row[key] or "unknown")
        grouped.setdefault(group_key, {})[str(row["outcome"])] = int(row["count"])
    return grouped


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
