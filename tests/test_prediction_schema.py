from __future__ import annotations

import unittest
from typing import Any

from fuyao_agent.prediction_schema import validate_prediction_item, validate_review_item


def _prediction(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "as_of_date": "2026-06-27",
        "trade_date": "2026-06-29",
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
    actual_value = overrides.get("actual_value", 85)
    if actual_value is None:
        actual_summary = "actual_value missing; limit_up_count unavailable from limit-up pool tool data."
    else:
        actual_summary = (
            f"actual_value={actual_value}; "
            "limit_up_count extracted from limit-up pool tool data."
        )
    item: dict[str, Any] = {
        "prediction_id": 1,
        "actual_trade_date": "2026-06-29",
        "actual_metric": "limit_up_count",
        "actual_value": actual_value,
        "actual_summary": actual_summary,
        "source_tool": "get_a_share_special_data_limit_up_pool",
        "outcome": "unknown",
        "score": None,
        "error_reason": "",
        "lesson": "Use system scoring to adjust confidence threshold and signal weight.",
    }
    item.update(overrides)
    return item


class PredictionSchemaTests(unittest.TestCase):
    def test_valid_prediction_returns_condition(self) -> None:
        condition, errors = validate_prediction_item(_prediction())

        self.assertEqual([], errors)
        self.assertEqual("limit_up_count", condition["metric"] if condition else None)

    def test_prediction_rejects_generic_rationale(self) -> None:
        _condition, errors = validate_prediction_item(_prediction(rationale="Looks likely."))

        self.assertIn(
            "rationale must reference metric/tool/signal/threshold/window/sample/condition evidence",
            errors,
        )

    def test_prediction_rejects_generic_validation_query(self) -> None:
        _condition, errors = validate_prediction_item(_prediction(validation_query="Check tomorrow."))

        self.assertIn(
            "validation_query must describe how actual_value will be extracted or computed",
            errors,
        )
        self.assertIn(
            "validation_query must reference the metric/tool/signal/condition to verify",
            errors,
        )

    def test_prediction_accepts_concrete_validation_query(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(
                validation_query=(
                    "Count limit_up_count from limit-up pool tool on trade_date "
                    "and compare with condition threshold."
                ),
            ),
        )

        self.assertEqual([], errors)

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

    def test_rejects_trade_date_before_as_of_date(self) -> None:
        _condition, errors = validate_prediction_item(_prediction(trade_date="2026-06-26"))

        self.assertIn("trade_date must not be earlier than as_of_date", errors)

    def test_rejects_wrong_condition_unit_for_metric(self) -> None:
        item = _prediction()
        item["condition"] = {
            **item["condition"],
            "unit": "pct",
        }
        _condition, errors = validate_prediction_item(item)

        self.assertIn("condition.unit for limit_up_count must be count", errors)

    def test_rejects_non_finite_condition_threshold(self) -> None:
        item = _prediction()
        item["condition"] = {
            **item["condition"],
            "threshold": float("nan"),
        }
        _condition, errors = validate_prediction_item(item)

        self.assertTrue(any("threshold must be a finite number" in error for error in errors))

    def test_rejects_non_finite_between_condition_bound(self) -> None:
        item = _prediction()
        item["condition"] = {
            **item["condition"],
            "operator": "between",
            "threshold": None,
            "lower": float("-inf"),
            "upper": 100,
        }
        _condition, errors = validate_prediction_item(item)

        self.assertTrue(any("between lower and upper must be finite numbers" in error for error in errors))

    def test_stock_prediction_requires_target_id(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(
                scope="stock",
                target_id="",
                metric="stock_return_pct",
                condition={
                    "metric": "stock_return_pct",
                    "operator": "gte",
                    "threshold": 0,
                    "lower": None,
                    "upper": None,
                    "unit": "pct",
                },
            ),
        )

        self.assertIn("target_id must not be empty", errors)

    def test_stock_prediction_rejects_market_breadth_metric(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(scope="stock", target_id="600519.SH"),
        )

        self.assertIn(
            "metric limit_up_count is not compatible with scope stock",
            "; ".join(errors),
        )

    def test_index_prediction_rejects_market_breadth_metric(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(scope="index", target_id="000001.SH"),
        )

        self.assertIn(
            "metric limit_up_count is not compatible with scope index",
            "; ".join(errors),
        )

    def test_market_prediction_rejects_stock_return_metric(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(
                metric="stock_return_pct",
                condition={
                    "metric": "stock_return_pct",
                    "operator": "gte",
                    "threshold": 0,
                    "lower": None,
                    "upper": None,
                    "unit": "pct",
                },
            ),
        )

        self.assertIn(
            "metric stock_return_pct is not compatible with scope market",
            "; ".join(errors),
        )

    def test_stock_prediction_requires_thscode_target_id(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(
                scope="stock",
                target_id="600519",
                metric="stock_return_pct",
                condition={
                    "metric": "stock_return_pct",
                    "operator": "gte",
                    "threshold": 0,
                    "lower": None,
                    "upper": None,
                    "unit": "pct",
                },
            ),
        )

        self.assertIn("target_id must be an A-share thscode like 600519.SH", errors)

    def test_index_prediction_requires_index_code_target_id(self) -> None:
        _condition, errors = validate_prediction_item(
            _prediction(
                scope="index",
                target_id="上证综指",
                metric="index_return_pct",
                rationale="Index return metric from index snapshot tool crossed threshold signal.",
                validation_query=(
                    "Calculate index_return_pct from index snapshot tool on trade_date "
                    "and compare with condition threshold."
                ),
                condition={
                    "metric": "index_return_pct",
                    "operator": "gte",
                    "threshold": 0,
                    "lower": None,
                    "upper": None,
                    "unit": "pct",
                },
            ),
        )

        self.assertIn("target_id must be an A-share index code like 000001.SH", errors)

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

    def test_review_rejects_non_finite_actual_value(self) -> None:
        errors = validate_review_item(_review(actual_value=float("nan")))

        self.assertIn("actual_value must be numeric or null", errors)

    def test_review_rejects_fractional_count_actual_value(self) -> None:
        errors = validate_review_item(_review(actual_value=85.5))

        self.assertIn(
            "actual_value for limit_up_count must be a non-negative integer",
            errors,
        )

    def test_review_rejects_non_positive_index_close_actual_value(self) -> None:
        errors = validate_review_item(_review(actual_metric="index_close", actual_value=0))

        self.assertIn("actual_value for index_close must be positive", errors)

    def test_review_rejects_model_supplied_outcome_and_score(self) -> None:
        errors = validate_review_item(_review(outcome="hit", score=1.0))

        self.assertIn("outcome must be unknown; system computes hit/miss", errors)
        self.assertIn("score must be null; system computes score", errors)

    def test_review_rejects_generic_actual_summary(self) -> None:
        errors = validate_review_item(_review(actual_summary="已验证。"))

        self.assertIn("actual_summary must reference the actual_metric or metric context", errors)
        self.assertIn("actual_summary must reference the source_tool or tool context", errors)
        self.assertIn("actual_summary must mention actual_value", errors)

    def test_review_summary_accepts_metric_tool_and_actual_value(self) -> None:
        errors = validate_review_item(
            _review(
                actual_summary=(
                    "actual_value=85; limit_up_count was extracted from "
                    "get_a_share_special_data_limit_up_pool."
                ),
            ),
        )

        self.assertEqual([], errors)

    def test_review_missing_actual_value_requires_error_reason(self) -> None:
        errors = validate_review_item(_review(actual_value=None, error_reason=""))

        self.assertIn("error_reason must not be empty", errors)

    def test_review_rejects_generic_lesson(self) -> None:
        errors = validate_review_item(_review(lesson="继续观察，后续提高准确率。"))

        self.assertTrue(any("lesson must include an actionable method adjustment" in error for error in errors))

    def test_review_lesson_requires_context_anchor(self) -> None:
        errors = validate_review_item(_review(lesson="Adjust confidence threshold next time."))

        self.assertIn(
            "lesson must identify the metric/tool/window/condition/sample/signal context",
            errors,
        )

    def test_review_lesson_accepts_method_and_metric_context(self) -> None:
        errors = validate_review_item(
            _review(lesson="Raise limit_up_count threshold when sample signal is weak."),
        )

        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
