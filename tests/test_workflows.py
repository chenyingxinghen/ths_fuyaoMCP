from __future__ import annotations

import unittest

from fuyao_agent.agent import SYSTEM_PROMPT
from fuyao_agent.workflows import WORKFLOWS


class WorkflowBusinessLogicTests(unittest.TestCase):
    def test_system_prompt_requires_synthesis_not_tool_listing(self) -> None:
        self.assertIn("Do not stop at listing tool outputs", SYSTEM_PROMPT)
        self.assertIn("cross-tool synthesis", SYSTEM_PROMPT)
        self.assertIn("hard to assemble manually", SYSTEM_PROMPT)

    def test_stock_analysis_requires_cross_validation(self) -> None:
        prompt = WORKFLOWS["stock-analysis"].prompt_template

        self.assertIn("综合分析层", prompt)
        self.assertIn("走势与财务的交叉验证", prompt)
        self.assertIn("不要输出流水账式字段清单", prompt)

    def test_market_movers_requires_structural_analysis(self) -> None:
        prompt = WORKFLOWS["market-movers"].prompt_template

        self.assertIn("结构性分析", prompt)
        self.assertIn("指数-涨停-连板交叉验证", prompt)
        self.assertIn("异常/背离线索", prompt)

    def test_daily_forecast_requires_signal_synthesis_before_prediction(self) -> None:
        prompt = WORKFLOWS["daily-forecast"].prompt_template

        self.assertIn("先做信号归纳再预测", prompt)
        self.assertIn("信号合成摘要", prompt)
        self.assertIn("矛盾/背离信号", prompt)

    def test_daily_review_requires_method_correction(self) -> None:
        prompt = WORKFLOWS["daily-review"].prompt_template

        self.assertIn("信号失效/有效分析", prompt)
        self.assertIn("方法修正", prompt)
        self.assertIn("下一次可执行的方法修正", prompt)


if __name__ == "__main__":
    unittest.main()
