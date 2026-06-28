from __future__ import annotations

import unittest

from fuyao_agent.scoring import score_condition


CONDITION = {
    "metric": "limit_up_count",
    "operator": "gte",
    "threshold": 80,
    "lower": None,
    "upper": None,
    "unit": "count",
}


class ScoringTests(unittest.TestCase):
    def test_scores_hit(self) -> None:
        result = score_condition(CONDITION, 85)

        self.assertEqual("hit", result.outcome)
        self.assertEqual(1.0, result.score)
        self.assertEqual(85.0, result.actual_value)

    def test_scores_miss(self) -> None:
        result = score_condition(CONDITION, 79)

        self.assertEqual("miss", result.outcome)
        self.assertEqual(0.0, result.score)

    def test_non_numeric_actual_value_is_unknown(self) -> None:
        result = score_condition(CONDITION, "not available")

        self.assertEqual("unknown", result.outcome)
        self.assertIsNone(result.score)
        self.assertIsNone(result.actual_value)
        self.assertIn("not numeric", result.reason)

    def test_non_finite_actual_value_is_unknown(self) -> None:
        result = score_condition(CONDITION, float("nan"))

        self.assertEqual("unknown", result.outcome)
        self.assertIsNone(result.score)
        self.assertIsNone(result.actual_value)
        self.assertIn("not finite", result.reason)


if __name__ == "__main__":
    unittest.main()
