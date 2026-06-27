from __future__ import annotations

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
MAX_CONFIDENCE = 0.75
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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
            elif self.lower > self.upper:
                errors.append("between lower must be <= upper")
        elif self.threshold is None:
            errors.append(f"{self.operator} requires threshold")
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
    _validate_allowed_text(item, "expected_direction", ALLOWED_DIRECTIONS, errors)
    _validate_required_text(item, "target", errors)
    _validate_required_text(item, "target_id", errors, allow_empty=True)
    _validate_required_text(item, "expected_range", errors)
    _validate_required_text(item, "rationale", errors)
    _validate_required_text(item, "validation_query", errors)

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
        if not condition.unit:
            errors.append("condition.unit is required")

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

    actual_value = item.get("actual_value")
    if actual_value not in (None, ""):
        try:
            float(actual_value)
        except (TypeError, ValueError):
            errors.append("actual_value must be numeric or null")

    return errors


def _validate_date_text(item: dict[str, Any], field: str, errors: list[str]) -> None:
    value = item.get(field)
    if not isinstance(value, str) or not DATE_RE.match(value):
        errors.append(f"{field} must use YYYY-MM-DD")


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


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
