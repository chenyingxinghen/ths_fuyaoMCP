from __future__ import annotations

import unittest

from fuyao_agent.agent import SYSTEM_PROMPT
from fuyao_agent.workflows import WORKFLOWS


class WorkflowBusinessLogicTests(unittest.TestCase):
    def test_only_two_business_workflows_are_exposed(self) -> None:
        self.assertEqual({"stock-analysis", "market-weather"}, set(WORKFLOWS))

    def test_system_prompt_requires_synthesis_not_tool_listing(self) -> None:
        self.assertIn("Do not stop at listing tool outputs", SYSTEM_PROMPT)
        self.assertIn("cross-tool synthesis", SYSTEM_PROMPT)
        self.assertIn("hard to assemble manually", SYSTEM_PROMPT)

    def test_stock_analysis_requires_cross_validation(self) -> None:
        prompt = WORKFLOWS["stock-analysis"].prompt_template

        self.assertIn("综合分析层", prompt)
        self.assertIn("走势与财务的交叉验证", prompt)
        self.assertIn("不要输出流水账式字段清单", prompt)
        self.assertIn("闭环复盘优先", prompt)
        self.assertIn("预测分析", prompt)
        self.assertIn("MEMORY_JSON", prompt)

    def test_market_weather_requires_profit_effect_synthesis(self) -> None:
        prompt = WORKFLOWS["market-weather"].prompt_template

        self.assertIn("赚钱效应合成", prompt)
        self.assertIn("支持信号", prompt)
        self.assertIn("矛盾/背离信号", prompt)

    def test_market_weather_requires_signal_synthesis_before_prediction(self) -> None:
        prompt = WORKFLOWS["market-weather"].prompt_template

        self.assertIn("不能从单个指标直接跳到结论", prompt)
        self.assertIn("赚钱效应合成摘要", prompt)
        self.assertIn("矛盾/背离信号", prompt)
        self.assertIn("MEMORY_JSON.predictions 必须包含 3-6 条记录", prompt)
        self.assertIn("至少两个信号族", prompt)
        self.assertIn("scope 必须和 metric 匹配", prompt)

    def test_workflows_separate_analysis_date_from_prediction_date(self) -> None:
        prompts = "\n".join(workflow.prompt_template for workflow in WORKFLOWS.values())

        self.assertIn("analysis_trade_date", prompts)
        self.assertIn("prediction_trade_date", prompts)
        self.assertIn("不能填写 analysis_trade_date", prompts)

    def test_workflows_do_not_review_future_tracking_predictions(self) -> None:
        prompts = "\n".join(workflow.prompt_template for workflow in WORKFLOWS.values())

        self.assertIn("已到最新可验证交易日", prompts)
        self.assertIn("尚未到最新可验证交易日", prompts)
        self.assertIn("暂缓复盘", prompts)
        self.assertIn("不得写入 reviews", prompts)
        self.assertIn("不得自行按自然日或星期推断", prompts)

    def test_workflows_require_review_lessons_and_method_correction(self) -> None:
        prompts = "\n".join(workflow.prompt_template for workflow in WORKFLOWS.values())

        self.assertIn("闭环复盘优先", prompts)
        self.assertIn("方法修正", prompts)
        self.assertIn("可迁移 lesson", prompts)
        self.assertIn("指明对应指标、工具、窗口、条件、样本或信号", prompts)
        self.assertIn("validation_query 必须说明复盘时如何提取或计算 actual_value", prompts)
        self.assertIn("count 类指标必须是非负整数", prompts)
        self.assertIn("source_tool 必须是本次实际调用过且能验证 actual_metric 的 MCP 工具名", prompts)


if __name__ == "__main__":
    unittest.main()
