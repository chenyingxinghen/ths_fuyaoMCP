from __future__ import annotations

import unittest
from typing import Any

from fuyao_agent.prediction_schema import validate_prediction_item, validate_review_item


def _prediction(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "as_of_date": "2026-06-27",
        "trade_date": "2026-06-26",
        "scope": "market",
        "target": "A-share market",
        "target_id": "",
        "horizon_days": 1,
        "metric": "limit_up_count",
        "expected_direction": "increase",
        "expected_range": ">=80",
        "confidence": 0.55,
        "rationale": "Limit-up count can be checked from tool data.",
        "validation_query": "Count limit-up stocks on the next trading day.",
        "condition": {
            "metric": "limit_up_count",
            "operator": "gte",
            "threshold": 80,
            "lower": None,
            "upper": None,
            "unit": "count",
        },
    }
    item.update(overrides)
    return item


def _review(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "prediction_id": 1,
        "actual_trade_date": "2026-06-29",
        "actual_metric": "limit_up_count",
        "actual_value": 85,
        "actual_summary": "Actual limit-up count came from tool data.",
        "source_tool": "get_a_share_special_data_limit_up_pool",
        "outcome": "unknown",
        "score": None,
        "error_reason": "",
        "lesson": "Use system scoring and let the model explain only errors.",
    }
    item.update(overrides)
    return item


class PredictionSchemaTests(unittest.TestCase):
    def test_valid_prediction_returns_condition(self) -> None:
        condition, errors = validate_prediction_item(_prediction())

        self.assertEqual([], errors)
        self.assertEqual("limit_up_count", condition["metric"] if condition else None)

    def test_rejects_confidence_above_business_limit(self) -> None:
        _condition, errors = validate_prediction_item(_prediction(confidence=0.8))

        self.assertIn("confidence must be a number between 0 and 0.75", errors)

    def test_rejects_metric_mismatch(self) -> None:
        item = _prediction(metric="index_return_pct")
        _condition, errors = validate_prediction_item(item)

        self.assertIn("metric must match condition.metric", errors)

    def test_requires_trade_date(self) -> None:
        item = _prediction()
        del item["trade_date"]
        _condition, errors = validate_prediction_item(item)

        self.assertIn("missing field: trade_date", errors)

    def test_valid_review_has_no_schema_errors(self) -> None:
        self.assertEqual([], validate_review_item(_review()))

    def test_review_requires_actual_metric(self) -> None:
        item = _review()
        del item["actual_metric"]

        errors = validate_review_item(item)

        self.assertIn("missing field: actual_metric", errors)
        self.assertTrue(any("actual_metric must be one of" in error for error in errors))

    def test_review_rejects_non_numeric_actual_value(self) -> None:
        errors = validate_review_item(_review(actual_value="not available"))

        self.assertIn("actual_value must be numeric or null", errors)


if __name__ == "__main__":
    unittest.main()
