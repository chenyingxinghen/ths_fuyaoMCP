from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal


Metric = Literal[
    "index_return_pct",
    "index_close",
    "stock_return_pct",
    "limit_up_count",
    "limit_up_count_change_pct",
    "consecutive_limit_up_max",
    "turnover_amount_change_pct",
]

Operator = Literal["gt", "gte", "lt", "lte", "between", "eq", "neq"]
Scope = Literal["market", "index", "theme", "stock"]
Direction = Literal["up", "down", "flat", "increase", "decrease", "mixed"]

ALLOWED_METRICS = {
    "index_return_pct",
    "index_close",
    "stock_return_pct",
    "limit_up_count",
    "limit_up_count_change_pct",
    "consecutive_limit_up_max",
    "turnover_amount_change_pct",
}

ALLOWED_OPERATORS = {"gt", "gte", "lt", "lte", "between", "eq", "neq"}
ALLOWED_SCOPES = {"market", "index", "theme", "stock"}
ALLOWED_DIRECTIONS = {"up", "down", "flat", "increase", "decrease", "mixed"}
ALLOWED_OUTCOMES = {"hit", "miss", "unknown"}
ALLOWED_METRICS_BY_SCOPE = {
    "stock": {"stock_return_pct", "turnover_amount_change_pct"},
    "index": {"index_return_pct", "index_close"},
    "market": {
        "limit_up_count",
        "limit_up_count_change_pct",
        "consecutive_limit_up_max",
        "turnover_amount_change_pct",
    },
    "theme": {
        "limit_up_count",
        "limit_up_count_change_pct",
        "consecutive_limit_up_max",
        "turnover_amount_change_pct",
    },
}
EXPECTED_UNITS_BY_METRIC = {
    "index_return_pct": "pct",
    "index_close": "points",
    "stock_return_pct": "pct",
    "limit_up_count": "count",
    "limit_up_count_change_pct": "pct",
    "consecutive_limit_up_max": "count",
    "turnover_amount_change_pct": "pct",
}
COUNT_METRICS = {"limit_up_count", "consecutive_limit_up_max"}
LESSON_METHOD_TERMS = (
    "阈值",
    "置信度",
    "权重",
    "threshold",
    "confidence",
    "weight",
)
LESSON_CONTEXT_TERMS = (
    "指标",
    "工具",
    "窗口",
    "条件",
    "样本",
    "信号",
    "metric",
    "tool",
    "window",
    "condition",
    "sample",
    "signal",
)
PREDICTION_EVIDENCE_CONTEXT_TERMS = (
    "指标",
    "工具",
    "信号",
    "阈值",
    "窗口",
    "样本",
    "条件",
    "涨停",
    "连板",
    "指数",
    "收益",
    "成交额",
    "价格",
    "metric",
    "tool",
    "signal",
    "threshold",
    "window",
    "sample",
    "condition",
    "limit-up",
    "limit_up",
    "index",
    "return",
    "turnover",
    "price",
    "close",
)
REVIEW_METRIC_CONTEXT_TERMS = (
    "指标",
    "涨停",
    "连板",
    "指数",
    "收益",
    "成交额",
    "价格",
    "收盘",
    "metric",
    "limit-up",
    "limit_up",
    "index",
    "return",
    "turnover",
    "price",
    "close",
)
VALIDATION_QUERY_ACTION_TERMS = (
    "提取",
    "计算",
    "比较",
    "统计",
    "查询",
    "count",
    "calculate",
    "compare",
    "extract",
    "query",
)
MAX_CONFIDENCE = 0.75
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
THSCODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.IGNORECASE)
INDEX_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)

REQUIRED_PREDICTION_FIELDS = (
    "as_of_date",
    "trade_date",
    "scope",
    "target",
    "target_id",
    "horizon_days",
    "metric",
    "expected_direction",
    "expected_range",
    "confidence",
    "rationale",
    "validation_query",
    "condition",
)

REQUIRED_REVIEW_FIELDS = (
    "prediction_id",
    "actual_trade_date",
    "actual_metric",
    "actual_value",
    "actual_summary",
    "source_tool",
    "outcome",
    "score",
    "error_reason",
    "lesson",
)


@dataclass(frozen=True)
class PredictionCondition:
    metric: str
    operator: str
    threshold: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PredictionCondition":
        condition = cls(
            metric=str(data.get("metric", "")),
            operator=str(data.get("operator", "")),
            threshold=_optional_float(data.get("threshold")),
            lower=_optional_float(data.get("lower")),
            upper=_optional_float(data.get("upper")),
            unit=str(data.get("unit", "")),
        )
        condition.validate()
        return condition

    def validate(self) -> None:
        errors: list[str] = []
        if self.metric not in ALLOWED_METRICS:
            errors.append(f"unsupported metric: {self.metric}")
        if self.operator not in ALLOWED_OPERATORS:
            errors.append(f"unsupported operator: {self.operator}")
        if self.operator == "between":
            if self.lower is None or self.upper is None:
                errors.append("between requires lower and upper")
            elif not math.isfinite(self.lower) or not math.isfinite(self.upper):
                errors.append("between lower and upper must be finite numbers")
            elif self.lower > self.upper:
                errors.append("between lower must be <= upper")
        else:
            if self.threshold is None:
                errors.append(f"{self.operator} requires threshold")
            elif not math.isfinite(self.threshold):
                errors.append(f"{self.operator} threshold must be a finite number")
        if self.metric in EXPECTED_UNITS_BY_METRIC:
            expected_unit = EXPECTED_UNITS_BY_METRIC[self.metric]
            if not self.unit:
                errors.append("condition.unit is required")
            elif self.unit != expected_unit:
                errors.append(
                    f"condition.unit for {self.metric} must be {expected_unit}",
                )
        if errors:
            raise ValueError("; ".join(errors))

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "lower": self.lower,
            "upper": self.upper,
            "unit": self.unit,
        }


def validate_prediction_item(item: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []

    for field in REQUIRED_PREDICTION_FIELDS:
        if field not in item:
            errors.append(f"missing field: {field}")

    _validate_date_text(item, "as_of_date", errors)
    _validate_date_text(item, "trade_date", errors)
    _validate_allowed_text(item, "scope", ALLOWED_SCOPES, errors)
    _validate_allowed_text(item, "metric", ALLOWED_METRICS, errors)
    _validate_metric_scope_compatibility(item, errors)
    _validate_allowed_text(item, "expected_direction", ALLOWED_DIRECTIONS, errors)
    _validate_required_text(item, "target", errors)
    _validate_required_text(item, "target_id", errors, allow_empty=True)
    _validate_required_text(item, "expected_range", errors)
    _validate_required_text(item, "rationale", errors)
    _validate_required_text(item, "validation_query", errors)
    _validate_prediction_text_quality(item, errors)

    if _valid_date_text(item.get("as_of_date")) and _valid_date_text(item.get("trade_date")):
        if str(item["trade_date"]) < str(item["as_of_date"]):
            errors.append("trade_date must not be earlier than as_of_date")

    if item.get("scope") == "stock":
        _validate_required_text(item, "target_id", errors)
        target_id = item.get("target_id")
        if isinstance(target_id, str) and target_id.strip():
            if not THSCODE_RE.match(target_id.strip()):
                errors.append("target_id must be an A-share thscode like 600519.SH")
    elif item.get("scope") == "index":
        _validate_required_text(item, "target_id", errors)
        target_id = item.get("target_id")
        if isinstance(target_id, str) and target_id.strip():
            if not INDEX_CODE_RE.match(target_id.strip()):
                errors.append("target_id must be an A-share index code like 000001.SH")

    confidence = _optional_float(item.get("confidence"))
    if confidence is None or not 0 <= confidence <= MAX_CONFIDENCE:
        errors.append(f"confidence must be a number between 0 and {MAX_CONFIDENCE}")

    horizon_days = item.get("horizon_days")
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days <= 0:
        errors.append("horizon_days must be a positive integer")

    condition_dict = item.get("condition")
    condition: PredictionCondition | None = None
    if isinstance(condition_dict, dict):
        try:
            condition = PredictionCondition.from_dict(condition_dict)
        except ValueError as exc:
            errors.append(str(exc))
    else:
        errors.append("condition must be an object")

    if condition:
        metric = item.get("metric")
        if metric != condition.metric:
            errors.append("metric must match condition.metric")

    return (condition.as_dict() if condition else None), errors


def validate_review_item(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_REVIEW_FIELDS:
        if field not in item:
            errors.append(f"missing field: {field}")

    prediction_id = item.get("prediction_id")
    if not isinstance(prediction_id, int) or prediction_id <= 0:
        errors.append("prediction_id must be a positive integer")

    _validate_date_text(item, "actual_trade_date", errors)
    _validate_allowed_text(item, "actual_metric", ALLOWED_METRICS, errors)
    _validate_required_text(item, "source_tool", errors)
    _validate_required_text(item, "actual_summary", errors)
    _validate_review_summary_quality(item, errors)
    _validate_required_text(item, "lesson", errors)
    if isinstance(item.get("lesson"), str) and str(item.get("lesson")).strip():
        errors.extend(validate_lesson_text(item.get("lesson")))

    outcome = item.get("outcome")
    if not isinstance(outcome, str) or outcome not in ALLOWED_OUTCOMES:
        joined = ", ".join(sorted(ALLOWED_OUTCOMES))
        errors.append(f"outcome must be one of: {joined}")
    elif outcome != "unknown":
        errors.append("outcome must be unknown; system computes hit/miss")

    if item.get("score") is not None:
        errors.append("score must be null; system computes score")

    error_reason = item.get("error_reason")
    if error_reason is not None and not isinstance(error_reason, str):
        errors.append("error_reason must be a string")

    actual_value = item.get("actual_value")
    if actual_value in (None, ""):
        _validate_required_text(item, "error_reason", errors)
    else:
        numeric_value = _actual_value_float(actual_value)
        if numeric_value is None:
            errors.append("actual_value must be numeric or null")
        else:
            actual_metric = item.get("actual_metric")
            if isinstance(actual_metric, str):
                _validate_actual_value_domain(actual_metric, numeric_value, errors)

    return errors


def validate_lesson_text(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, str):
        errors.append("lesson must be a string")
        return errors
    if not value.strip():
        errors.append("lesson must not be empty")
        return errors
    _validate_lesson_quality(value, errors)
    return errors


def _validate_date_text(item: dict[str, Any], field: str, errors: list[str]) -> None:
    value = item.get(field)
    if not _valid_date_text(value):
        errors.append(f"{field} must use YYYY-MM-DD")


def _valid_date_text(value: Any) -> bool:
    return isinstance(value, str) and bool(DATE_RE.match(value))


def _validate_allowed_text(
    item: dict[str, Any],
    field: str,
    allowed: set[str],
    errors: list[str],
) -> None:
    value = item.get(field)
    if not isinstance(value, str) or value not in allowed:
        joined = ", ".join(sorted(allowed))
        errors.append(f"{field} must be one of: {joined}")


def _validate_metric_scope_compatibility(item: dict[str, Any], errors: list[str]) -> None:
    scope = item.get("scope")
    metric = item.get("metric")
    if not isinstance(scope, str) or not isinstance(metric, str):
        return
    allowed = ALLOWED_METRICS_BY_SCOPE.get(scope)
    if not allowed or metric in allowed:
        return
    joined = ", ".join(sorted(allowed))
    errors.append(f"metric {metric} is not compatible with scope {scope}; expected one of: {joined}")


def _validate_required_text(
    item: dict[str, Any],
    field: str,
    errors: list[str],
    *,
    allow_empty: bool = False,
) -> None:
    value = item.get(field)
    if not isinstance(value, str):
        errors.append(f"{field} must be a string")
    elif not allow_empty and not value.strip():
        errors.append(f"{field} must not be empty")


def _validate_lesson_quality(value: Any, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    normalized = value.strip().lower()
    if not any(term.lower() in normalized for term in LESSON_METHOD_TERMS):
        errors.append(
            "lesson must include an actionable method adjustment "
            "(threshold, confidence, or weight)",
        )
    if not _lesson_has_context_anchor(normalized):
        errors.append(
            "lesson must identify the metric/tool/window/condition/sample/signal context",
        )


def _actual_value_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _validate_actual_value_domain(metric: str, value: float, errors: list[str]) -> None:
    if metric in COUNT_METRICS:
        if value < 0 or not value.is_integer():
            errors.append(f"actual_value for {metric} must be a non-negative integer")
    elif metric == "index_close" and value <= 0:
        errors.append("actual_value for index_close must be positive")


def _validate_prediction_text_quality(item: dict[str, Any], errors: list[str]) -> None:
    rationale = item.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        normalized = rationale.strip().lower()
        if len(normalized) < 16 or not _prediction_text_has_context_anchor(normalized):
            errors.append(
                "rationale must reference metric/tool/signal/threshold/window/sample/condition evidence",
            )

    validation_query = item.get("validation_query")
    if isinstance(validation_query, str) and validation_query.strip():
        normalized = validation_query.strip().lower()
        if not any(term.lower() in normalized for term in VALIDATION_QUERY_ACTION_TERMS):
            errors.append("validation_query must describe how actual_value will be extracted or computed")
        if not _prediction_text_has_context_anchor(normalized):
            errors.append("validation_query must reference the metric/tool/signal/condition to verify")


def _validate_review_summary_quality(item: dict[str, Any], errors: list[str]) -> None:
    summary = item.get("actual_summary")
    if not isinstance(summary, str) or not summary.strip():
        return
    normalized = summary.strip().lower()
    actual_metric = item.get("actual_metric")
    source_tool = item.get("source_tool")
    actual_value = item.get("actual_value")

    if not _review_summary_has_metric_context(normalized, actual_metric):
        errors.append("actual_summary must reference the actual_metric or metric context")
    if not _review_summary_has_tool_context(normalized, source_tool):
        errors.append("actual_summary must reference the source_tool or tool context")
    if actual_value not in (None, "") and not _review_summary_mentions_actual_value(
        normalized,
        actual_value,
    ):
        errors.append("actual_summary must mention actual_value")


def _review_summary_has_metric_context(normalized: str, actual_metric: Any) -> bool:
    if isinstance(actual_metric, str) and actual_metric.lower() in normalized:
        return True
    if any(term.lower() in normalized for term in REVIEW_METRIC_CONTEXT_TERMS):
        return True
    return any(metric.lower() in normalized for metric in ALLOWED_METRICS)


def _review_summary_has_tool_context(normalized: str, source_tool: Any) -> bool:
    if "tool" in normalized or "工具" in normalized:
        return True
    if isinstance(source_tool, str) and source_tool.strip():
        return source_tool.strip().lower() in normalized
    return False


def _review_summary_mentions_actual_value(normalized: str, actual_value: Any) -> bool:
    if "actual_value" in normalized:
        return True
    numeric_value = _actual_value_float(actual_value)
    if numeric_value is None:
        return False
    for match in re.finditer(r"-?\d+(?:\.\d+)?", normalized):
        try:
            if math.isclose(float(match.group()), numeric_value, rel_tol=1e-9, abs_tol=1e-9):
                return True
        except ValueError:
            continue
    return False


def _prediction_text_has_context_anchor(normalized: str) -> bool:
    if any(term.lower() in normalized for term in PREDICTION_EVIDENCE_CONTEXT_TERMS):
        return True
    return any(metric.lower() in normalized for metric in ALLOWED_METRICS)


def _lesson_has_context_anchor(normalized: str) -> bool:
    if any(term.lower() in normalized for term in LESSON_CONTEXT_TERMS):
        return True
    return any(metric.lower() in normalized for metric in ALLOWED_METRICS)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
