from __future__ import annotations

import unittest
from typing import Any

from fuyao_agent.memory import MemoryStore


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


def _review(prediction_id: int, actual_value: float | None, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "prediction_id": prediction_id,
        "actual_trade_date": "2026-06-29",
        "actual_metric": "limit_up_count",
        "actual_value": actual_value,
        "actual_summary": "Actual limit-up count came from tool data.",
        "source_tool": "get_a_share_special_data_limit_up_pool",
        "outcome": "unknown",
        "score": None,
        "error_reason": "",
        "lesson": "Use system scoring and let the model explain only errors.",
    }
    item.update(overrides)
    return item


class MemoryStoreTests(unittest.TestCase):
    def test_invalid_prediction_is_stored_but_not_pending(self) -> None:
        store = MemoryStore(":memory:")
        result = store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload={
                "predictions": [
                    _prediction(),
                    _prediction(confidence=0.9, target="invalid high-confidence prediction"),
                ],
            },
        )

        self.assertEqual(1, result.predictions_added)
        self.assertEqual(1, result.invalid_predictions_added)
        stats = store.stats()
        self.assertEqual(2, stats["prediction_total"])
        self.assertEqual(1, stats["valid_prediction_total"])
        self.assertEqual(1, stats["invalid_prediction_total"])
        self.assertEqual(1, stats["pending_total"])
        self.assertEqual("2026-06-26", store.pending_predictions()[0]["trade_date"])

    def test_review_scores_pending_valid_prediction_once(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload={"predictions": [_prediction()]},
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        first_review = store.add_run(
            workflow="daily-review",
            user_input="",
            output="",
            memory_payload={"reviews": [_review(prediction_id, 85)]},
        )
        duplicate_review = store.add_run(
            workflow="daily-review",
            user_input="",
            output="",
            memory_payload={"reviews": [_review(prediction_id, 90)]},
        )

        self.assertEqual(1, first_review.reviews_added)
        self.assertEqual(0, duplicate_review.reviews_added)
        stats = store.stats()
        self.assertEqual(0, stats["pending_total"])
        self.assertEqual(1, stats["reviewed_total"])
        self.assertEqual({"hit": 1}, stats["outcomes"])

    def test_unknown_review_keeps_prediction_pending(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload={"predictions": [_prediction()]},
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="daily-review",
            user_input="",
            output="",
            memory_payload={"reviews": [_review(prediction_id, None)]},
        )

        self.assertEqual(1, review_result.reviews_added)
        self.assertEqual(1, store.stats()["pending_total"])

    def test_metric_mismatch_review_is_rejected(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload={"predictions": [_prediction()]},
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="daily-review",
            user_input="",
            output="",
            memory_payload={
                "reviews": [
                    _review(prediction_id, 85, actual_metric="index_return_pct"),
                ],
            },
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, store.stats()["pending_total"])

    def test_review_before_prediction_trade_date_is_rejected(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload={"predictions": [_prediction()]},
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="daily-review",
            user_input="",
            output="",
            memory_payload={
                "reviews": [
                    _review(prediction_id, 85, actual_trade_date="2026-06-25"),
                ],
            },
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, store.stats()["pending_total"])


if __name__ == "__main__":
    unittest.main()
