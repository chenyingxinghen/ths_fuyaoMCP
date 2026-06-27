from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fuyao_agent.prediction_schema import PredictionCondition


@dataclass(frozen=True)
class ScoreResult:
    outcome: str
    score: float | None
    actual_value: float | None
    reason: str


def score_condition(condition_data: dict[str, Any], actual_value: Any) -> ScoreResult:
    if actual_value is None or actual_value == "":
        return ScoreResult(
            outcome="unknown",
            score=None,
            actual_value=None,
            reason="actual_value is missing",
        )

    condition = PredictionCondition.from_dict(condition_data)
    try:
        value = float(actual_value)
    except (TypeError, ValueError):
        return ScoreResult(
            outcome="unknown",
            score=None,
            actual_value=None,
            reason=f"actual_value is not numeric: {actual_value}",
        )

    hit = _evaluate(condition, value)
    return ScoreResult(
        outcome="hit" if hit else "miss",
        score=1.0 if hit else 0.0,
        actual_value=value,
        reason=_format_reason(condition, value, hit),
    )


def _evaluate(condition: PredictionCondition, value: float) -> bool:
    if condition.operator == "gt":
        return value > _required_threshold(condition)
    if condition.operator == "gte":
        return value >= _required_threshold(condition)
    if condition.operator == "lt":
        return value < _required_threshold(condition)
    if condition.operator == "lte":
        return value <= _required_threshold(condition)
    if condition.operator == "eq":
        return value == _required_threshold(condition)
    if condition.operator == "neq":
        return value != _required_threshold(condition)
    if condition.operator == "between":
        if condition.lower is None or condition.upper is None:
            raise ValueError("between requires lower and upper")
        return condition.lower <= value <= condition.upper
    raise ValueError(f"unsupported operator: {condition.operator}")


def _required_threshold(condition: PredictionCondition) -> float:
    if condition.threshold is None:
        raise ValueError(f"{condition.operator} requires threshold")
    return condition.threshold


def _format_reason(condition: PredictionCondition, value: float, hit: bool) -> str:
    if condition.operator == "between":
        expression = f"{condition.lower} <= {value} <= {condition.upper}"
    else:
        expression = f"{value} {condition.operator} {condition.threshold}"
    return f"{expression}; outcome={'hit' if hit else 'miss'}"
