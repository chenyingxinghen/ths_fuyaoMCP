from __future__ import annotations

import json
import unittest
from typing import Any

from fuyao_agent.memory import MemoryStore, format_memory_context


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


def _review(prediction_id: int, actual_value: float | None, **overrides: Any) -> dict[str, Any]:
    if actual_value is None:
        actual_summary = "actual_value missing; limit_up_count unavailable from limit-up pool tool data."
    else:
        actual_summary = (
            f"actual_value={actual_value}; "
            "limit_up_count extracted from limit-up pool tool data."
        )
    item: dict[str, Any] = {
        "prediction_id": prediction_id,
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


def _memory_payload(
    *,
    predictions: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    lessons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "reviews": reviews or [],
        "predictions": predictions or [],
        "lessons": lessons or [],
    }


def _observations(*tool_names: str) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": tool_name,
            "arguments": {},
            "result": "{}",
        }
        for tool_name in tool_names
    ]


def _calendar_observation(*dates: str) -> dict[str, Any]:
    payload = {
        "data": {
            "item": [
                {
                    "date": date,
                    "date_ms": index,
                }
                for index, date in enumerate(dates, start=1)
            ],
        },
    }
    return {
        "tool_name": "get_a_share_calendar_trading_days",
        "arguments": {},
        "result": json.dumps(payload),
    }


def _index_prediction(**overrides: Any) -> dict[str, Any]:
    item = _prediction(
        scope="index",
        target="上证综指",
        target_id="000001.SH",
        metric="index_return_pct",
        expected_direction="increase",
        expected_range=">=0",
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
    )
    item.update(overrides)
    return item


def _ladder_prediction(**overrides: Any) -> dict[str, Any]:
    item = _prediction(
        target="连板天梯",
        metric="consecutive_limit_up_max",
        expected_direction="increase",
        expected_range=">=3",
        rationale="Ladder height metric from limit-up ladder tool is the breadth signal.",
        validation_query=(
            "Extract consecutive_limit_up_max from limit-up ladder tool on trade_date "
            "and compare with condition threshold."
        ),
        condition={
            "metric": "consecutive_limit_up_max",
            "operator": "gte",
            "threshold": 3,
            "lower": None,
            "upper": None,
            "unit": "count",
        },
    )
    item.update(overrides)
    return item


def _stock_prediction(**overrides: Any) -> dict[str, Any]:
    item = _prediction(
        scope="stock",
        target="贵州茅台",
        target_id="600519.SH",
        metric="stock_return_pct",
        expected_direction="increase",
        expected_range=">=0",
        rationale="Stock return metric from snapshot and historical price tool crossed threshold signal.",
        validation_query=(
            "Calculate stock_return_pct from price snapshot and historical tool on trade_date "
            "and compare with condition threshold."
        ),
        condition={
            "metric": "stock_return_pct",
            "operator": "gte",
            "threshold": 0,
            "lower": None,
            "upper": None,
            "unit": "pct",
        },
    )
    item.update(overrides)
    return item


def _stock_turnover_prediction(**overrides: Any) -> dict[str, Any]:
    item = _stock_prediction(
        metric="turnover_amount_change_pct",
        expected_range=">=0",
        rationale=(
            "Turnover_amount_change_pct metric from snapshot and historical price tool "
            "is the liquidity signal."
        ),
        validation_query=(
            "Calculate turnover_amount_change_pct from price snapshot and historical tool "
            "on trade_date and compare with condition threshold."
        ),
        condition={
            "metric": "turnover_amount_change_pct",
            "operator": "gte",
            "threshold": 0,
            "lower": None,
            "upper": None,
            "unit": "pct",
        },
    )
    item.update(overrides)
    return item


def _market_output(extra: str = "") -> str:
    sections = (
        "交易日",
        "复盘验证",
        "赚钱效应合成摘要",
        "支持信号",
        "矛盾/背离信号",
        "未来观察假设",
        "预测清单",
        "方法修正",
        "风险与数据缺口",
    )
    lines = [f"## {section}\n{section}内容。" for section in sections]
    if extra:
        lines.append(extra)
    return "\n\n".join(lines)


class MemoryStoreTests(unittest.TestCase):
    def test_workflow_run_without_memory_json_is_a_validation_error(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="answer without memory block",
            memory_payload=None,
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(0, result.reviews_added)
        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("memory_json", result.validation_errors[0]["item_type"])
        self.assertIn("MEMORY_JSON block is missing", result.validation_errors[0]["errors"][0])
        self.assertEqual({"memory_json": 1}, store.stats()["validation_errors_by_type"])
        self.assertEqual("memory_json", store.recent_validation_errors()[0]["item_type"])

    def test_workflow_run_with_memory_json_parse_error_is_a_validation_error(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="answer with broken memory block",
            memory_payload=None,
            memory_payload_error="MEMORY_JSON parse failed: bad json",
        )

        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("memory_json", result.validation_errors[0]["item_type"])
        self.assertEqual(
            "MEMORY_JSON parse failed: bad json",
            result.validation_errors[0]["errors"][0],
        )

    def test_memory_json_missing_top_level_arrays_is_a_validation_error(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="partial memory json",
            memory_payload={"predictions": []},
        )

        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("memory_json", result.validation_errors[0]["item_type"])
        self.assertIn("missing MEMORY_JSON field: reviews", result.validation_errors[0]["errors"])
        self.assertIn("missing MEMORY_JSON field: lessons", result.validation_errors[0]["errors"])

    def test_workflow_output_missing_synthesis_sections_is_a_validation_error(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="只列出指数、涨停池和连板数据。",
            memory_payload=_memory_payload(),
            observations=[],
        )

        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("output", result.validation_errors[0]["item_type"])
        self.assertIn("missing required synthesis section", result.validation_errors[0]["errors"][0])
        self.assertIn("赚钱效应合成摘要", result.validation_errors[0]["errors"][0])
        self.assertEqual({"output": 1}, store.stats()["validation_errors_by_type"])

    def test_workflow_output_subjective_wording_is_a_validation_error(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output=_market_output("涨停池非常火爆。"),
            memory_payload=_memory_payload(),
            observations=[],
        )

        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("output", result.validation_errors[0]["item_type"])
        self.assertIn("subjective wording", result.validation_errors[0]["errors"][0])
        self.assertIn("火爆", result.validation_errors[0]["errors"][0])

    def test_workflow_output_with_required_sections_is_accepted(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output=_market_output(),
            memory_payload=_memory_payload(),
            observations=[],
        )

        self.assertEqual([], result.validation_errors)

    def test_global_lesson_requires_actionable_method_adjustment(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="generic lesson",
            memory_payload=_memory_payload(lessons=[{"lesson": "继续观察，提高准确率。"}]),
        )

        self.assertEqual(0, result.lessons_added)
        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("lesson", result.validation_errors[0]["item_type"])
        self.assertIn(
            "lesson must include an actionable method adjustment",
            result.validation_errors[0]["errors"][0],
        )

    def test_global_lesson_with_method_adjustment_is_stored(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="specific lesson",
            memory_payload=_memory_payload(
                lessons=[
                    {
                        "lesson": (
                            "Raise limit_up_count threshold after low-sample signal miss."
                        ),
                    },
                ],
            ),
        )

        self.assertEqual(1, result.lessons_added)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(
            ["Raise limit_up_count threshold after low-sample signal miss."],
            store.recent_lessons_for_workflow("market-weather"),
        )

    def test_invalid_prediction_is_stored_but_not_pending(self) -> None:
        store = MemoryStore(":memory:")
        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(),
                    _prediction(confidence=0.9, target="invalid high-confidence prediction"),
                ],
            ),
        )

        self.assertEqual(1, result.predictions_added)
        self.assertEqual(1, result.invalid_predictions_added)
        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("prediction", result.validation_errors[0]["item_type"])
        self.assertIn("confidence must be", result.validation_errors[0]["errors"][0])
        stats = store.stats()
        self.assertEqual(2, stats["prediction_total"])
        self.assertEqual(1, stats["valid_prediction_total"])
        self.assertEqual(1, stats["invalid_prediction_total"])
        self.assertEqual(1, stats["pending_total"])
        self.assertEqual(1, stats["validation_error_total"])
        self.assertEqual({"prediction": 1}, stats["validation_errors_by_type"])
        recent_errors = store.recent_validation_errors()
        self.assertEqual(1, len(recent_errors))
        self.assertEqual("prediction", recent_errors[0]["item_type"])
        self.assertIn("confidence must be", recent_errors[0]["errors"][0])
        self.assertEqual("invalid high-confidence prediction", recent_errors[0]["item_summary"]["target"])
        self.assertEqual("2026-06-29", store.pending_predictions()[0]["trade_date"])

    def test_duplicate_pending_prediction_is_rejected_across_market_workflows(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[_prediction(target="duplicate market hypothesis")],
            ),
        )

        duplicate_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[_prediction(target="duplicate market hypothesis")],
            ),
        )

        self.assertEqual(0, duplicate_result.predictions_added)
        self.assertEqual(1, duplicate_result.invalid_predictions_added)
        self.assertEqual(1, len(duplicate_result.validation_errors))
        self.assertEqual("prediction", duplicate_result.validation_errors[0]["item_type"])
        self.assertIn("duplicate pending prediction", duplicate_result.validation_errors[0]["errors"][0])
        self.assertEqual(1, store.stats()["pending_total"])

    def test_similar_pending_prediction_is_rejected_for_market_question_variants(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[_prediction(target="A-share market")],
            ),
        )

        similar_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        target="市场赚钱效应",
                        expected_range=">85",
                        condition={
                            "metric": "limit_up_count",
                            "operator": "gt",
                            "threshold": 85,
                            "lower": None,
                            "upper": None,
                            "unit": "count",
                        },
                    ),
                ],
            ),
        )

        self.assertEqual(0, similar_result.predictions_added)
        self.assertEqual(1, similar_result.invalid_predictions_added)
        self.assertEqual(1, len(similar_result.validation_errors))
        self.assertEqual("prediction", similar_result.validation_errors[0]["item_type"])
        self.assertIn("similar pending prediction", similar_result.validation_errors[0]["errors"][0])
        self.assertEqual(1, store.stats()["pending_total"])

    def test_opposite_condition_direction_is_not_rejected_as_similar(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[_prediction(target="A-share market")],
            ),
        )

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        target="A-share market",
                        expected_direction="decrease",
                        expected_range="<=60",
                        condition={
                            "metric": "limit_up_count",
                            "operator": "lte",
                            "threshold": 60,
                            "lower": None,
                            "upper": None,
                            "unit": "count",
                        },
                    ),
                ],
            ),
        )

        self.assertEqual(1, result.predictions_added)
        self.assertEqual(0, result.invalid_predictions_added)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(2, store.stats()["pending_total"])

    def test_cached_report_matches_similar_question_within_ttl(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="stock-analysis",
            user_input="贵州茅台",
            output="first cached report",
            memory_payload=_memory_payload(),
        )

        cached = store.find_cached_report(
            workflow="stock-analysis",
            user_input="请分析贵州茅台",
            ttl_seconds=1800,
            similarity_threshold=0.78,
        )

        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual("first cached report", cached["answer"])
        self.assertEqual("贵州茅台", cached["user_input"])
        self.assertGreaterEqual(cached["similarity"], 0.78)

    def test_cached_report_rejects_conflicting_stock_codes(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="stock-analysis",
            user_input="分析 600519.SH",
            output="maotai report",
            memory_payload=_memory_payload(),
        )

        cached = store.find_cached_report(
            workflow="stock-analysis",
            user_input="分析 000858.SZ",
            ttl_seconds=1800,
            similarity_threshold=0.5,
        )

        self.assertIsNone(cached)

    def test_prediction_metric_requires_supporting_current_tool_call(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow=None,
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
            observations=[
                {
                    "tool_name": "get_a_share_index_prices_snapshot",
                    "arguments": {},
                    "result": "{}",
                },
            ],
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(1, result.invalid_predictions_added)
        self.assertEqual(1, len(result.validation_errors))
        self.assertIn("requires evidence", result.validation_errors[0]["errors"][0])
        self.assertEqual(0, store.stats()["pending_total"])

    def test_prediction_metric_accepts_supporting_current_tool_call(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow=None,
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
            observations=[
                {
                    "tool_name": "get_a_share_special_data_limit_up_pool",
                    "arguments": {},
                    "result": "{}",
                },
            ],
        )

        self.assertEqual(1, result.predictions_added)
        self.assertEqual(0, result.invalid_predictions_added)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(1, store.stats()["pending_total"])
        pending = store.pending_predictions()
        self.assertEqual(
            [
                {
                    "observation_id": 1,
                    "tool_name": "get_a_share_special_data_limit_up_pool",
                },
            ],
            pending[0]["evidence"],
        )
        self.assertEqual(1, store.stats()["predictions_with_evidence_total"])
        self.assertEqual(1, store.stats()["evidence_trace_total"])

    def test_market_weather_rejects_partial_prediction_batch_with_observations(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="first market hypothesis"),
                    _prediction(target="second market hypothesis"),
                ],
            ),
            observations=[
                {
                    "tool_name": "get_a_share_special_data_limit_up_pool",
                    "arguments": {},
                    "result": "{}",
                },
            ],
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(2, result.invalid_predictions_added)
        self.assertEqual(1, len(result.validation_errors))
        self.assertEqual("predictions", result.validation_errors[0]["item_type"])
        self.assertIn("3-6 records", result.validation_errors[0]["errors"][0])
        self.assertEqual(0, store.stats()["pending_total"])

    def test_market_weather_requires_core_synthesis_tools_for_predictions(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="market hypothesis one"),
                    _prediction(target="market hypothesis two"),
                    _prediction(target="market hypothesis three"),
                ],
            ),
            observations=_observations("get_a_share_special_data_limit_up_pool"),
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(3, result.invalid_predictions_added)
        self.assertEqual("predictions", result.validation_errors[0]["item_type"])
        self.assertIn("multi-tool synthesis evidence", result.validation_errors[0]["errors"][0])
        self.assertIn("get_a_share_index_prices_snapshot", result.validation_errors[0]["errors"][0])
        self.assertIn("get_a_share_special_data_limit_up_ladder", result.validation_errors[0]["errors"][0])

    def test_market_weather_accepts_predictions_with_core_synthesis_tools(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="market hypothesis one"),
                    _index_prediction(target="market hypothesis two"),
                    _ladder_prediction(target="market hypothesis three"),
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_index_prices_snapshot",
                "get_a_share_special_data_limit_up_pool",
                "get_a_share_special_data_limit_up_ladder",
            ),
        )

        self.assertEqual(3, result.predictions_added)
        self.assertEqual(0, result.invalid_predictions_added)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(3, store.stats()["pending_total"])

    def test_run_audit_records_tools_families_and_validation_status(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output=_market_output(),
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="market hypothesis one"),
                    _index_prediction(target="market hypothesis two"),
                    _ladder_prediction(target="market hypothesis three"),
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_index_prices_snapshot",
                "get_a_share_special_data_limit_up_pool",
                "get_a_share_special_data_limit_up_ladder",
            ),
        )

        self.assertEqual([], result.validation_errors)
        self.assertEqual("passed", result.run_audit["output_audit_status"])
        self.assertEqual("passed", result.run_audit["memory_json_status"])
        self.assertEqual([], result.run_audit["missing_tools"])
        self.assertEqual(["breadth", "index"], result.run_audit["signal_families"])
        self.assertEqual(3, result.run_audit["accepted_prediction_count"])
        self.assertEqual(1, store.stats()["run_audit_total"])

        recent_audit = store.recent_run_audits()[0]
        self.assertEqual(result.run_id, recent_audit["run_id"])
        self.assertEqual(["breadth", "index"], recent_audit["signal_families"])
        detailed_audit = store.run_audit(result.run_id)
        self.assertIsNotNone(detailed_audit)
        self.assertEqual([], detailed_audit["validation_errors"])

    def test_run_audit_records_memory_json_failures(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="answer without memory block",
            memory_payload=None,
        )

        audit = store.run_audit(result.run_id)
        self.assertIsNotNone(audit)
        self.assertEqual("failed", audit["memory_json_status"])
        self.assertEqual(1, audit["validation_error_count"])
        self.assertEqual({"memory_json": 1}, audit["audit"]["validation_errors_by_type"])

    def test_market_weather_requires_prediction_signal_family_diversity(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="market hypothesis one"),
                    _prediction(target="market hypothesis two"),
                    _prediction(target="market hypothesis three"),
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_index_prices_snapshot",
                "get_a_share_special_data_limit_up_pool",
                "get_a_share_special_data_limit_up_ladder",
            ),
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(3, result.invalid_predictions_added)
        self.assertEqual("predictions", result.validation_errors[0]["item_type"])
        self.assertIn("at least two signal families", result.validation_errors[0]["errors"][0])
        self.assertIn("limit_up_count", result.validation_errors[0]["errors"][0])

    def test_stock_analysis_multiple_predictions_require_return_and_turnover(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="stock-analysis",
            user_input="600519.SH",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _stock_prediction(target="return hypothesis one"),
                    _stock_prediction(target="return hypothesis two"),
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_prices_snapshot",
                "get_a_share_prices_historical",
            ),
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(2, result.invalid_predictions_added)
        self.assertEqual("predictions", result.validation_errors[0]["item_type"])
        self.assertIn("both return and turnover", result.validation_errors[0]["errors"][0])

    def test_stock_analysis_accepts_return_and_turnover_prediction_mix(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="stock-analysis",
            user_input="600519.SH",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _stock_prediction(target="return hypothesis"),
                    _stock_turnover_prediction(target="turnover hypothesis"),
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_prices_snapshot",
                "get_a_share_prices_historical",
            ),
        )

        self.assertEqual(2, result.predictions_added)
        self.assertEqual(0, result.invalid_predictions_added)
        self.assertEqual([], result.validation_errors)
        self.assertEqual(["liquidity", "return"], result.run_audit["signal_families"])

    def test_stock_analysis_requires_core_synthesis_tools_for_predictions(self) -> None:
        store = MemoryStore(":memory:")

        result = store.add_run(
            workflow="stock-analysis",
            user_input="600519.SH",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        scope="stock",
                        target="贵州茅台",
                        target_id="600519.SH",
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
                ],
            ),
            observations=_observations(
                "get_a_share_calendar_trading_days",
                "get_a_share_prices_snapshot",
            ),
        )

        self.assertEqual(0, result.predictions_added)
        self.assertEqual(1, result.invalid_predictions_added)
        self.assertEqual("predictions", result.validation_errors[0]["item_type"])
        self.assertIn("multi-tool synthesis evidence", result.validation_errors[0]["errors"][0])
        self.assertIn("get_a_share_prices_historical", result.validation_errors[0]["errors"][0])

    def test_review_scores_pending_valid_prediction_once(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        first_review = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(reviews=[_review(prediction_id, 85)]),
        )
        duplicate_review = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(reviews=[_review(prediction_id, 90)]),
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
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(
                        prediction_id,
                        None,
                        error_reason="Tool data did not include a numeric actual_value.",
                    ),
                ],
            ),
        )

        self.assertEqual(1, review_result.reviews_added)
        self.assertEqual(1, store.stats()["pending_total"])

    def test_duplicate_unknown_review_is_rejected(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])
        unknown_review = _review(
            prediction_id,
            None,
            error_reason="Tool data did not include a numeric actual_value.",
        )

        first_review = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(reviews=[unknown_review]),
        )
        duplicate_review = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(reviews=[unknown_review]),
        )

        self.assertEqual(1, first_review.reviews_added)
        self.assertEqual(0, duplicate_review.reviews_added)
        self.assertEqual(1, duplicate_review.invalid_reviews_added)
        self.assertEqual(1, len(duplicate_review.validation_errors))
        self.assertIn("duplicate unknown review", duplicate_review.validation_errors[0]["errors"][0])
        stats = store.stats()
        self.assertEqual(1, stats["pending_total"])
        self.assertEqual({"unknown": 1}, stats["outcomes"])

    def test_metric_mismatch_review_is_rejected(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(prediction_id, 85, actual_metric="index_return_pct"),
                ],
            ),
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, len(review_result.validation_errors))
        self.assertIn("actual_metric", review_result.validation_errors[0]["errors"][0])
        self.assertEqual("review", store.recent_validation_errors()[0]["item_type"])
        self.assertEqual(1, store.stats()["pending_total"])

    def test_review_before_prediction_trade_date_is_rejected(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(prediction_id, 85, actual_trade_date="2026-06-28"),
                ],
            ),
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, len(review_result.validation_errors))
        self.assertIn("actual_trade_date", review_result.validation_errors[0]["errors"][0])
        self.assertEqual(1, store.stats()["pending_total"])

    def test_workflow_context_filters_pending_predictions(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction(target="market item")]),
        )
        store.add_run(
            workflow="stock-analysis",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        scope="stock",
                        target="stock item",
                        target_id="600519.SH",
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
                ],
            ),
        )

        market_pending = store.pending_predictions_for_workflow("market-weather")
        stock_pending = store.pending_predictions_for_workflow("stock-analysis")

        self.assertEqual(["market item"], [row["target"] for row in market_pending])
        self.assertEqual(["stock item"], [row["target"] for row in stock_pending])

    def test_pending_predictions_can_be_split_by_review_date(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="due item", trade_date="2026-06-28"),
                    _prediction(target="future item", trade_date="2026-06-30"),
                ],
            ),
        )

        due_rows = store.pending_predictions_for_workflow(
            "market-weather",
            due_on_or_before="2026-06-29",
        )
        future_rows = store.pending_predictions_for_workflow(
            "market-weather",
            due_after="2026-06-29",
        )

        self.assertEqual(["due item"], [row["target"] for row in due_rows])
        self.assertEqual(["future item"], [row["target"] for row in future_rows])

    def test_memory_context_separates_due_reviews_from_future_tracking(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="due item", trade_date="2026-06-28"),
                    _prediction(target="future item", trade_date="2026-06-30"),
                ],
            ),
        )

        context = format_memory_context(
            store,
            "market-weather",
            20,
            current_date="2026-06-29",
        )

        due_heading = context.index("已到最新可验证交易日")
        future_heading = context.index("尚未到最新可验证交易日")
        self.assertLess(due_heading, context.index("due item"))
        self.assertLess(context.index("due item"), future_heading)
        self.assertLess(future_heading, context.index("future item"))
        self.assertIn("不得写入 reviews", context)

    def test_memory_context_uses_latest_available_trade_date_for_weekends(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="calendar observation",
            memory_payload=_memory_payload(),
            observations=[
                _calendar_observation(
                    "2026-06-24",
                    "2026-06-25",
                    "2026-06-26",
                ),
            ],
        )
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        target="friday item",
                        as_of_date="2026-06-26",
                        trade_date="2026-06-26",
                    ),
                    _prediction(target="monday item", trade_date="2026-06-29"),
                ],
            ),
        )

        context = format_memory_context(
            store,
            "market-weather",
            20,
            current_date="2026-06-28",
        )

        due_heading = context.index("待复盘预测如下")
        future_heading = context.index("待跟踪预测如下")
        self.assertIn("最新可验证交易日：2026-06-26", context)
        self.assertLess(due_heading, context.index("friday item"))
        self.assertLess(context.index("friday item"), future_heading)
        self.assertLess(future_heading, context.index("monday item"))

    def test_unknown_review_is_deferred_until_available_trade_date_advances(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="calendar observation",
            memory_payload=_memory_payload(),
            observations=[_calendar_observation("2026-06-26")],
        )
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        target="temporarily unavailable item",
                        as_of_date="2026-06-26",
                        trade_date="2026-06-26",
                    ),
                ],
            ),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(
                        prediction_id,
                        None,
                        actual_trade_date="2026-06-26",
                        error_reason="Tool data did not include a numeric actual_value.",
                    ),
                ],
            ),
        )

        deferred_context = format_memory_context(
            store,
            "market-weather",
            20,
            current_date="2026-06-28",
        )
        due_heading = deferred_context.index("待复盘预测如下")
        deferred_heading = deferred_context.index("暂缓复盘预测")
        future_heading = deferred_context.index("待跟踪预测如下")
        item_index = deferred_context.index("temporarily unavailable item")
        self.assertLess(deferred_heading, item_index)
        self.assertLess(item_index, future_heading)
        self.assertLess(due_heading, deferred_heading)

        store.add_run(
            workflow="market-weather",
            user_input="",
            output="calendar advanced",
            memory_payload=_memory_payload(),
            observations=[_calendar_observation("2026-06-26", "2026-06-29")],
        )

        advanced_context = format_memory_context(
            store,
            "market-weather",
            20,
            current_date="2026-06-29",
        )
        due_heading = advanced_context.index("待复盘预测如下")
        deferred_heading = advanced_context.index("暂缓复盘预测")
        item_index = advanced_context.index("temporarily unavailable item")
        self.assertLess(due_heading, item_index)
        self.assertLess(item_index, deferred_heading)

    def test_market_weather_context_includes_legacy_market_predictions(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[_prediction(target="legacy market item")],
            ),
        )

        context = format_memory_context(store, "market-weather", 20)

        self.assertIn("legacy market item", context)
        self.assertIn("暂无历史系统评分反馈", context)

    def test_stock_context_filters_by_target_hint(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="stock-analysis",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        scope="stock",
                        target="贵州茅台",
                        target_id="600519.SH",
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
                    _prediction(
                        scope="stock",
                        target="五粮液",
                        target_id="000858.SZ",
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
                ],
            ),
        )

        context = format_memory_context(store, "stock-analysis", 20, target_hint="600519.SH")

        self.assertIn("贵州茅台", context)
        self.assertNotIn("五粮液", context)

    def test_memory_context_includes_system_scoring_feedback(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="hit item"),
                    _prediction(target="miss item", confidence=0.35),
                ],
            ),
        )
        ids_by_target = {
            str(row["target"]): int(row["id"])
            for row in store.pending_predictions_for_workflow("market-weather")
        }
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(ids_by_target["hit item"], 85),
                    _review(ids_by_target["miss item"], 70),
                ],
            ),
        )

        context = format_memory_context(store, "market-weather", 20)

        self.assertIn("历史系统评分反馈", context)
        self.assertIn("命中率=50.00%", context)
        self.assertIn("limit_up_count", context)
        self.assertIn("<0.4", context)
        self.assertIn("0.4-0.6", context)

    def test_memory_context_adds_calibration_recommendations_for_weak_history(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="miss one", confidence=0.55),
                    _prediction(target="miss two", confidence=0.56),
                ],
            ),
        )
        ids_by_target = {
            row["target"]: int(row["id"])
            for row in store.pending_predictions(limit=10)
        }
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(ids_by_target["miss one"], 70),
                    _review(ids_by_target["miss two"], 72),
                ],
            ),
        )

        context = format_memory_context(store, "market-weather", 20)

        self.assertIn("校准建议", context)
        self.assertIn("指标 limit_up_count 命中率低于 50%", context)
        self.assertIn("置信度桶 0.4-0.6 命中率低于 50%", context)

    def test_memory_context_includes_recent_validation_rejections(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="daily-forecast",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(target="invalid market item", confidence=0.8),
                ],
            ),
        )

        context = format_memory_context(store, "market-weather", 20)

        self.assertIn("最近结构化记忆拒绝", context)
        self.assertIn("confidence must be", context)
        self.assertIn("invalid market item", context)

    def test_stock_memory_context_filters_validation_rejections_by_target_hint(self) -> None:
        store = MemoryStore(":memory:")
        stock_condition = {
            "metric": "stock_return_pct",
            "operator": "gte",
            "threshold": 0,
            "lower": None,
            "upper": None,
            "unit": "pct",
        }
        store.add_run(
            workflow="stock-analysis",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                predictions=[
                    _prediction(
                        scope="stock",
                        target="贵州茅台",
                        target_id="600519.SH",
                        metric="stock_return_pct",
                        condition=stock_condition,
                        confidence=0.8,
                    ),
                    _prediction(
                        scope="stock",
                        target="五粮液",
                        target_id="000858.SZ",
                        metric="stock_return_pct",
                        condition=stock_condition,
                        confidence=0.8,
                    ),
                ],
            ),
        )

        context = format_memory_context(store, "stock-analysis", 20, target_hint="600519.SH")

        self.assertIn("贵州茅台", context)
        self.assertNotIn("五粮液", context)

    def test_review_source_tool_must_come_from_current_observations(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(reviews=[_review(prediction_id, 85)]),
            observations=[
                {
                    "tool_name": "get_a_share_index_prices_snapshot",
                    "arguments": {},
                    "result": "{}",
                },
            ],
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, len(review_result.validation_errors))
        self.assertIn("source_tool", review_result.validation_errors[0]["errors"][0])

    def test_review_source_tool_must_support_actual_metric(self) -> None:
        store = MemoryStore(":memory:")
        store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(predictions=[_prediction()]),
        )
        prediction_id = int(store.pending_predictions()[0]["id"])

        review_result = store.add_run(
            workflow="market-weather",
            user_input="",
            output="",
            memory_payload=_memory_payload(
                reviews=[
                    _review(
                        prediction_id,
                        85,
                        source_tool="get_a_share_index_prices_snapshot",
                    ),
                ],
            ),
            observations=[
                {
                    "tool_name": "get_a_share_index_prices_snapshot",
                    "arguments": {},
                    "result": "{}",
                },
            ],
        )

        self.assertEqual(0, review_result.reviews_added)
        self.assertEqual(1, review_result.invalid_reviews_added)
        self.assertEqual(1, len(review_result.validation_errors))
        self.assertIn("cannot verify actual_metric", review_result.validation_errors[0]["errors"][0])


if __name__ == "__main__":
    unittest.main()
