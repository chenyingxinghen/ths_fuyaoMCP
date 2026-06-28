from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Workflow:
    name: str
    title: str
    description: str
    prompt_template: str

    def render(self, user_input: str, memory_context: str = "") -> str:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        target = user_input.strip() or "按默认范围执行"
        return self.prompt_template.format(
            today=today,
            target=target,
            memory_context=memory_context.strip() or "无",
        )


WORKFLOWS: dict[str, Workflow] = {
    "stock-analysis": Workflow(
        name="stock-analysis",
        title="个股分析",
        description="围绕单只股票做标的识别、行情、趋势、财务和数据缺口梳理。",
        prompt_template="""请按“个股分析”工作流分析：{target}

当前日期：{today}，时区：Asia/Shanghai。

历史记忆上下文：
{memory_context}

工作流要求：
0. 遵循底层开源量化知识库：数据优先、可审计、区分事实/解释/假设，不输出投资建议。
1. 标的识别：如果输入不是标准 thscode，先调用 get_meta_tickers_search 消歧，确认股票名称、ticker、thscode、交易所。
2. 交易日与预测日期：调用 get_a_share_calendar_trading_days。选择不晚于当前日期 {today} 的最大 date 作为 analysis_trade_date；若日历中存在晚于 analysis_trade_date 的最小 date，则作为 prediction_trade_date。该日历通常只覆盖到最新已开市交易日，周末/假日可能没有未来交易日；如果无法从日历确定 prediction_trade_date，则根据 A 股交易日惯例（周一至周五，排除周末，注意实际节假日可能使该估计不准确）合理估计下一自然日作为 prediction_trade_date，并在该预测条目的 rationale 中标注“estimated_from_weekday”；复盘系统会通过 actual_trade_date 自动修正（actual_trade_date >= prediction_trade_date 即通过校验）后续预测的 trade_date 必须使用 prediction_trade_date，不能使用已经作为行情分析基准的 analysis_trade_date。
3. 闭环复盘优先：先读取历史记忆上下文里的“已到最新可验证交易日”待复盘预测；凡 target_id 与本次标的一致且目标交易日已可验证的记录，必须调用对应工具提取 actual_value，写入 reviews。历史记忆上下文里的“暂缓复盘”记录是当前最新可验证交易日已经尝试过 unknown 的记录，不得重复写 reviews；只有日历最新可验证交易日推进后才重新复盘。尚未到最新可验证交易日的待跟踪预测不得写入 reviews，只能用于避免重复假设；已到最新可验证交易日但数据缺失的记录必须说明缺口并以 unknown 留在待复盘队列。
4. 行情快照：调用 get_a_share_prices_snapshot 获取最新行情，说明数据时间、现价、涨跌幅、成交额等关键字段；字段缺失时明确写“数据未返回”。
5. 走势观察：调用 get_a_share_prices_historical 获取最近约 1 年日 K 数据；按工具 schema 计算 start/end 毫秒时间戳。概括区间涨跌、阶段高低点、近 20/60 日走势特征。只描述数据，不给买卖建议。
6. 财务概览：优先获取最近 4 期年报利润表；如工具和权限允许，再补充资产负债表与现金流量表。关注营业收入、归母净利润、资产负债结构、经营现金流。
7. 公司行动：如有必要，调用 get_a_share_corporate_actions_adjustment_factors 检查复权因子事件，说明是否影响长期价格比较。
8. 综合分析层：不要把行情、K 线、财务逐项平铺复述；必须提炼人力难以快速汇总的信息，包括但不限于：价格在 1 年区间的位置、20/60 日方向是否一致、回撤/反弹结构、价格变化与营收/利润/现金流趋势是否背离、财务趋势对行情解释的支持或矛盾、复权事件对长期比较的影响、历史复盘经验对本次判断的修正、关键缺失字段会改变哪些判断。
9. 预测分析：如果标的识别、行情数据和 prediction_trade_date 足够，形成 1-3 条下一可验证周期的个股假设；只能使用 stock_return_pct 或 turnover_amount_change_pct 等可由工具复盘的指标。如果生成 2 条以上预测，必须同时覆盖收益率和成交额变化两个信号族，避免单一指标复述。数据或 prediction_trade_date 不足时 predictions 使用空数组，并在正文说明不足原因。
10. 经验沉淀：复盘命中或未命中的原因必须沉淀成可迁移 lesson，明确下次要调整的阈值、置信度、信号权重或补充工具，并指明对应指标、工具、窗口、条件、样本或信号；不能写空泛经验。
11. 输出结构固定为：分析结论摘要、复盘验证、标的确认、关键证据链、走势与财务的交叉验证、解释/假设、预测清单、方法修正、需要继续核验的数据。

约束：
- 使用中文回答。
- 不输出投资建议、目标价或确定性预测。
- 所有结论必须来自工具返回的数据；没有数据就说明缺口。
- 不要输出流水账式字段清单；每个数据点都必须服务于一个分析结论或证据链。
- 事实和计算结果必须使用中性语言，只报告数值、时间窗口、阈值、排名和缺口；解释性判断必须单独标注为“解释/假设”。

预测与复盘约束：
- 每条预测必须包含对象、周期、指标、方向/区间、置信度、依据、验证方式和 condition。
- rationale 必须写清 metric/tool/signal/threshold/window/sample/condition 等证据上下文；validation_query 必须说明复盘时如何提取或计算 actual_value。
- 顶层 metric 必须和 condition.metric 完全一致。
- scope 必须和 metric 匹配：index 只能用 index_return_pct/index_close，target_id 必须是 000001.SH 这类指数代码；market/theme 只能用涨停、连板或成交额变化指标；stock 只能用 stock_return_pct/turnover_amount_change_pct，target_id 必须是 600519.SH 这类 A 股 thscode。
- condition 是系统自动复盘使用的可执行条件，不允许写自由文本。
- condition.metric 只能使用：stock_return_pct、turnover_amount_change_pct。
- condition.operator 只能使用：gt、gte、lt、lte、between、eq、neq。
- condition.unit 必须填写单位，例如 pct。
- MEMORY_JSON.predictions.trade_date 必须使用 prediction_trade_date，不能早于 as_of_date，不能填写 analysis_trade_date。
- 置信度使用 0 到 0.75 的小数，不得超过 0.75。
- 只基于工具返回的实际数据评分；模型只解释误差来源，系统会按 condition 自动计算 hit/miss 和 score。
- 只能复盘历史记忆上下文中“已到最新可验证交易日”待复盘预测的 prediction_id，不要自行编造、复盘“暂缓复盘”记录、复盘其他 id，或复盘尚未到最新可验证交易日的待跟踪预测。
- actual_value 必须是有限数字；count 类指标必须是非负整数，index_close 必须为正数；无法提取 actual_value 时使用 null，outcome 使用 unknown，并说明缺口。
- actual_metric 必须与原预测 metric 一致；source_tool 必须是本次实际调用过且能验证 actual_metric 的 MCP 工具名。
- actual_summary 必须写清 actual_metric、source_tool 或工具上下文，以及实际提取的 actual_value；不能只写“已验证”。
- actual_trade_date 不能早于原预测 trade_date，格式为 YYYY-MM-DD。

最终回答末尾必须追加一个机器可读区块，格式严格如下：

MEMORY_JSON:
```json
{{
  "reviews": [
    {{
      "prediction_id": 1,
      "actual_trade_date": "YYYY-MM-DD",
      "actual_metric": "stock_return_pct",
      "actual_value": 2.1,
      "actual_summary": "actual_value=2.1；从 get_a_share_prices_snapshot 提取 stock_return_pct",
      "source_tool": "get_a_share_prices_snapshot",
      "outcome": "unknown",
      "score": null,
      "error_reason": "无法提取 actual_value 时说明原因；能提取时可为空字符串",
      "lesson": "可迁移经验，不能是空泛套话"
    }}
  ],
  "predictions": [
    {{
      "as_of_date": "{today}",
      "trade_date": "YYYY-MM-DD",
      "scope": "stock",
      "target": "股票中文名",
      "target_id": "thscode",
      "horizon_days": 1,
      "metric": "stock_return_pct",
      "expected_direction": "up|down|flat|increase|decrease|mixed",
      "expected_range": "方向或数值区间",
      "confidence": 0.45,
      "rationale": "基于哪些工具数据",
      "validation_query": "复盘时应如何验证",
      "condition": {{
        "metric": "stock_return_pct",
        "operator": "between",
        "threshold": null,
        "lower": -2,
        "upper": 2,
        "unit": "pct"
      }}
    }}
  ],
  "lessons": [
    {{
      "lesson": "全局复盘经验或本次方法修正；没有新增经验则用空数组"
    }}
  ]
}}
```
""",
    ),
    "market-weather": Workflow(
        name="market-weather",
        title="大盘晴雨赚钱效应分析",
        description="复盘待验证假设，综合指数、涨停池和连板结构，生成下一轮可验证市场假设。",
        prompt_template="""请按“大盘晴雨赚钱效应分析”工作流执行：{target}

当前日期：{today}，时区：Asia/Shanghai。

历史记忆上下文：
{memory_context}

工作流要求：
0. 遵循底层开源量化知识库：使用当前数据、说明缺口、只描述可观测事实，把预测写成可验证假设，不把异动线索包装成交易建议。
1. 交易日与预测日期确认：先调用 get_a_share_calendar_trading_days。该工具返回近一年交易日序列，通常只覆盖到最新已开市交易日，必须在 data.item 中选择不晚于当前日期 {today} 的最大 date 作为 analysis_trade_date；如果用户指定日期，则必须确认该日期是否存在于 data.item 并把它作为 analysis_trade_date。后续需要 date_ms 时，必须使用所选 analysis_trade_date 条目的原始 date_ms，不得自行换算或使用列表首项。若日历中存在晚于 analysis_trade_date 的最小 date，则作为 prediction_trade_date；后续预测的 trade_date 必须使用 prediction_trade_date，不能使用已经作为盘面分析基准的 analysis_trade_date。周末/假日如日历未返回下一交易日，则根据 A 股交易日惯例（周一至周五，排除周末，注意实际节假日可能使该估计不准确）合理估计下一自然日作为 prediction_trade_date，并在该预测条目的 rationale 中标注“estimated_from_weekday”；复盘系统会通过 actual_trade_date 自动修正（actual_trade_date >= prediction_trade_date 即通过校验）
2. 闭环复盘优先：先读取历史记忆上下文里的“已到最新可验证交易日”待复盘预测；凡属于本工作流或 scope 为 market/index/theme 且目标交易日已可验证的记录，必须调用对应工具提取 actual_value，写入 reviews。历史记忆上下文里的“暂缓复盘”记录是当前最新可验证交易日已经尝试过 unknown 的记录，不得重复写 reviews；只有日历最新可验证交易日推进后才重新复盘。尚未到最新可验证交易日的待跟踪预测不得写入 reviews，只能用于避免重复假设；已到最新可验证交易日但数据缺失的记录必须说明缺口并以 unknown 留在待复盘队列。
3. 主要指数：调用 get_a_share_index_prices_snapshot 获取上证综指 000001.SH、深证成指 399001.SZ、创业板指 399006.SZ、沪深300 000300.SH 的行情快照；如某个代码无法返回，说明缺口。
4. 涨停股票池：调用 get_a_share_special_data_limit_up_pool 获取目标交易日涨停/连板股票池，提炼涨停数量、连板数量、代表性股票。
5. 连板天梯：调用 get_a_share_special_data_limit_up_ladder 获取近 30 个交易日连板梯队，概括最高连板高度、各板数股票数量和近 30 日数量变化。
6. 赚钱效应合成：不要把指数、涨停池和连板数据逐项平铺；必须综合指数方向、涨停数量、连板高度、近 30 日梯队变化、待复盘误差和历史 lessons，提炼 3-5 条人力难以快速汇总的结构信息，例如指数与涨停数量是否背离、连板高度与涨停总数是否同步、梯队是否集中在少数高度、近 30 日连板矩阵相对当前交易日的变化、旧预测中哪些阈值或权重需要修正。不能从单个指标直接跳到结论。
7. 预测分析：如果 prediction_trade_date 可确定，基于合成后的信号形成 3-6 条下一可验证交易日或指定 horizon 的假设。每条预测必须能用行情、涨停池、连板高度或指数表现验证，且必须包含 condition。如果无法确定 prediction_trade_date，则 MEMORY_JSON.predictions 使用空数组并说明原因。
8. 经验沉淀：复盘命中或未命中的原因必须沉淀成可迁移 lesson，明确下次要调整的阈值、置信度、信号权重或补充工具，并指明对应指标、工具、窗口、条件、样本或信号；不能写空泛经验。
9. 输出结构固定为：交易日、复盘验证、赚钱效应合成摘要、支持信号、矛盾/背离信号、未来观察假设、预测清单、方法修正、风险与数据缺口。

约束：
- 使用中文回答。
- 不输出投资建议、荐股、目标价或确定性结论。
- 不臆造行业/概念归因；只有工具返回或可由股票名称明显识别时才写。
- 优先输出结构性结论和可验证假设，不要把工具返回列表改写成长表；原始数字只保留能支撑结论的关键值。
- 对涨停、连板、指数涨跌等事实只使用中性描述；避免使用“活跃、发酵、意愿、尚可、明显、中幅、大幅、小幅、热度、风险偏好”等主观标签，改用数量、比例、排名、区间和阈值。
- 交易日事实必须以日历工具返回的目标交易日为准；如果当前自然日不是交易日，不得写成“当前日期为交易日”。
- 不要自行补充星期几；除非工具返回星期字段。

预测约束：
- 预测前必须说明赚钱效应合成逻辑；禁止只罗列指数、涨停池和连板数据后直接给预测。
- 每条预测必须包含对象、周期、指标、方向/区间、置信度、依据、验证方式和 condition。
- rationale 必须写清 metric/tool/signal/threshold/window/sample/condition 等证据上下文；validation_query 必须说明复盘时如何提取或计算 actual_value。
- prediction_trade_date 可确定时，MEMORY_JSON.predictions 必须包含 3-6 条记录；无法确定时必须使用空数组并在正文解释。
- 3-6 条预测必须覆盖至少两个信号族：指数方向、涨停/连板广度、流动性/成交额，不能全部围绕单一 metric。
- as_of_date 必须填写当前自然日期 {today}；trade_date 必须填写第 1 步选出的 prediction_trade_date，格式均为 YYYY-MM-DD，不能早于 as_of_date，不能填写 analysis_trade_date。
- 顶层 metric 必须和 condition.metric 完全一致。
- scope 必须和 metric 匹配：index 只能用 index_return_pct/index_close，target_id 必须是 000001.SH 这类指数代码；market/theme 只能用涨停、连板或成交额变化指标；stock 只能用 stock_return_pct/turnover_amount_change_pct，target_id 必须是 600519.SH 这类 A 股 thscode。
- condition 是系统自动复盘使用的可执行条件，不允许写自由文本。
- condition.metric 只能使用：index_return_pct、index_close、stock_return_pct、limit_up_count、limit_up_count_change_pct、consecutive_limit_up_max、turnover_amount_change_pct。
- condition.operator 只能使用：gt、gte、lt、lte、between、eq、neq。
- condition.unit 必须填写单位，例如 pct、points 或 count。
- 置信度使用 0 到 0.75 的小数，不得超过 0.75。
- 预测依据必须以中性事实表述，不使用“火爆、惨淡、疯狂、强势、弱势、活跃、发酵、意愿、尚可、明显、中幅、大幅、小幅”等主观词；如需表达市场状态，必须用可复盘指标和阈值定义。

复盘约束：
- 只基于工具返回的实际数据评分；模型只解释误差来源，系统会按 condition 自动计算 hit/miss 和 score。
- 只能复盘历史记忆上下文中“已到最新可验证交易日”待复盘预测的 prediction_id，不要自行编造、复盘“暂缓复盘”记录、复盘其他 id，或复盘尚未到最新可验证交易日的待跟踪预测。
- actual_value 必须是有限数字；count 类指标必须是非负整数，index_close 必须为正数；无法提取 actual_value 时使用 null，outcome 使用 unknown，并说明缺口。
- actual_metric 必须与原预测 metric 一致；source_tool 必须是本次实际调用过且能验证 actual_metric 的 MCP 工具名。
- actual_summary 必须写清 actual_metric、source_tool 或工具上下文，以及实际提取的 actual_value；不能只写“已验证”。
- actual_trade_date 不能早于原预测 trade_date，格式为 YYYY-MM-DD。
- 复盘事实、系统评分和误差解释分开写；事实部分不得加入情绪化或暗示性措辞。

最终回答末尾必须追加一个机器可读区块，格式严格如下：

MEMORY_JSON:
```json
{{
  "reviews": [
    {{
      "prediction_id": 1,
      "actual_trade_date": "YYYY-MM-DD",
      "actual_metric": "limit_up_count",
      "actual_value": 85,
      "actual_summary": "actual_value=85；从 get_a_share_special_data_limit_up_pool 提取 limit_up_count",
      "source_tool": "get_a_share_special_data_limit_up_pool",
      "outcome": "unknown",
      "score": null,
      "error_reason": "无法提取 actual_value 时说明原因；能提取时可为空字符串",
      "lesson": "可迁移经验，不能是空泛套话"
    }}
  ],
  "predictions": [
    {{
      "as_of_date": "{today}",
      "trade_date": "YYYY-MM-DD",
      "scope": "market|index|theme|stock",
      "target": "预测对象中文名",
      "target_id": "指数代码、thscode 或对象标识；index 必须如 000001.SH，stock 必须如 600519.SH",
      "horizon_days": 1,
      "metric": "limit_up_count",
      "expected_direction": "up|down|flat|increase|decrease|mixed",
      "expected_range": "方向或数值区间",
      "confidence": 0.55,
      "rationale": "基于哪些工具数据",
      "validation_query": "复盘时应如何验证",
      "condition": {{
        "metric": "limit_up_count",
        "operator": "gte",
        "threshold": 80,
        "lower": null,
        "upper": null,
        "unit": "count"
      }}
    }}
  ],
  "lessons": [
    {{
      "lesson": "全局复盘经验或本次方法修正；没有新增经验则用空数组"
    }}
  ]
}}
```
""",
    ),
}


def get_workflow(name: str) -> Workflow:
    try:
        return WORKFLOWS[name]
    except KeyError as exc:
        known = ", ".join(sorted(WORKFLOWS))
        raise RuntimeError(f"Unknown workflow: {name}. Known workflows: {known}") from exc
