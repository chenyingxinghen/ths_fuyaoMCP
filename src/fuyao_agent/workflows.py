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

工作流要求：
0. 遵循底层开源量化知识库：数据优先、可审计、区分分析/假设/策略，不输出投资建议。
1. 标的识别：如果输入不是标准 thscode，先调用 get_meta_tickers_search 消歧，确认股票名称、ticker、thscode、交易所。
2. 行情快照：调用 get_a_share_prices_snapshot 获取最新行情，说明数据时间、现价、涨跌幅、成交额等关键字段；字段缺失时明确写“数据未返回”。
3. 走势观察：调用 get_a_share_prices_historical 获取最近约 1 年日 K 数据；按工具 schema 计算 start/end 毫秒时间戳。概括区间涨跌、阶段高低点、近 20/60 日走势特征。只描述数据，不给买卖建议。
4. 财务概览：优先获取最近 4 期年报利润表；如工具和权限允许，再补充资产负债表与现金流量表。关注营业收入、归母净利润、资产负债结构、经营现金流。
5. 公司行动：如有必要，调用 get_a_share_corporate_actions_adjustment_factors 检查复权因子事件，说明是否影响长期价格比较。
6. 综合分析层：不要把行情、K 线、财务逐项平铺复述；必须提炼人力难以快速汇总的信息，包括但不限于：价格在 1 年区间的位置、20/60 日方向是否一致、回撤/反弹结构、价格变化与营收/利润/现金流趋势是否背离、财务趋势对行情解释的支持或矛盾、复权事件对长期比较的影响、关键缺失字段会改变哪些判断。
7. 输出结构固定为：分析结论摘要、标的确认、关键证据链、走势与财务的交叉验证、解释/假设、需要继续核验的数据。

约束：
- 使用中文回答。
- 不输出投资建议、目标价或确定性预测。
- 所有结论必须来自工具返回的数据；没有数据就说明缺口。
- 不要输出流水账式字段清单；每个数据点都必须服务于一个分析结论或证据链。
- 事实和计算结果必须使用中性语言，只报告数值、时间窗口、阈值、排名和缺口；解释性判断必须单独标注为“解释/假设”。
""",
    ),
    "market-movers": Workflow(
        name="market-movers",
        title="市场异动速览",
        description="快速汇总主要指数、涨停池、连板梯队和盘面异动线索。",
        prompt_template="""请按“市场异动速览”工作流执行：{target}

当前日期：{today}，时区：Asia/Shanghai。

工作流要求：
0. 遵循底层开源量化知识库：使用当前数据、说明缺口、只描述可观测事实，不把异动线索包装成交易建议。
1. 交易日确认：先调用 get_a_share_calendar_trading_days。该工具返回近一年交易日序列，必须在 data.item 中选择不晚于当前日期 {today} 的最大 date 作为目标交易日；如果用户指定日期，则必须确认该日期是否存在于 data.item。后续需要 date_ms 时，必须使用所选交易日条目的原始 date_ms，不得自行换算或使用列表首项。
2. 主要指数：调用 get_a_share_index_prices_snapshot 获取上证综指 000001.SH、深证成指 399001.SZ、创业板指 399006.SZ、沪深300 000300.SH 的行情快照；如某个代码无法返回，说明缺口。
3. 涨停股票池：调用 get_a_share_special_data_limit_up_pool 获取目标交易日涨停/连板股票池，提炼涨停数量、连板数量、代表性股票。
4. 连板天梯：调用 get_a_share_special_data_limit_up_ladder 获取近 30 个交易日连板梯队，概括最高连板高度、各板数股票数量和近 30 日数量变化。
5. 结构性分析：结合指数表现、涨停池、连板天梯，归纳 3-5 条人力难以快速汇总的盘面结构信息，例如指数与涨停数量是否背离、连板高度与涨停总数是否同步、梯队是否集中在少数高度、近 30 日连板矩阵相对当前交易日的变化、代表性股票是否集中于可识别名称线索。不能只列股票和涨跌幅。
6. 输出结构固定为：交易日、结构性结论摘要、指数-涨停-连板交叉验证、异常/背离线索、解释/假设、风险与数据缺口。

约束：
- 使用中文回答。
- 不输出投资建议、荐股或确定性预测。
- 不臆造行业/概念归因；只有工具返回或可由股票名称明显识别时才写。
- 优先输出结构性结论，不要把工具返回列表改写成长表；原始数字只保留能支撑结论的关键值。
- 对涨停、连板、指数涨跌等事实只使用中性描述；避免使用“活跃、发酵、意愿、尚可、明显、中幅、大幅、小幅、热度、风险偏好”等主观标签，改用数量、比例、排名、区间和阈值。
- 交易日事实必须以日历工具返回的目标交易日为准；如果当前自然日不是交易日，不得写成“当前日期为交易日”。
- 不要自行补充星期几；除非工具返回星期字段。
""",
    ),
    "daily-forecast": Workflow(
        name="daily-forecast",
        title="每日分析预测",
        description="基于当日市场数据生成可复盘的结构化市场预测。",
        prompt_template="""请按“每日分析预测”工作流执行：{target}

当前日期：{today}，时区：Asia/Shanghai。

历史记忆上下文：
{memory_context}

工作流要求：
0. 遵循底层开源量化知识库：每日预测只能是可验证假设，必须保守表达不确定性，不能把假设写成结论。
1. 先调用 get_a_share_calendar_trading_days 确认最近一个 A 股交易日。必须在 data.item 中选择不晚于当前日期 {today} 的最大 date 作为目标交易日；后续需要 date_ms 时，必须使用所选交易日条目的原始 date_ms，不得自行换算。
2. 调用 get_a_share_index_prices_snapshot 获取上证综指 000001.SH、深证成指 399001.SZ、创业板指 399006.SZ、沪深300 000300.SH。
3. 调用 get_a_share_special_data_limit_up_pool 获取目标交易日涨停/连板股票池。
4. 调用 get_a_share_special_data_limit_up_ladder 获取近 30 个交易日连板梯队。
5. 先做信号归纳再预测：必须综合指数方向、涨停数量、连板高度、近 30 日梯队变化和历史复盘经验，提炼哪些信号互相支持、哪些信号互相矛盾、哪些缺口会降低预测可信度。不能直接从单个指标跳到预测。
6. 结合信号归纳形成 3-6 条可复盘预测。预测必须能在未来用行情、涨停池、连板高度或指数表现验证。
7. 输出结构固定为：交易日、信号合成摘要、支持信号、矛盾/背离信号、未来观察假设、预测清单、风险与数据缺口。

预测约束：
- 不输出投资建议、荐股、目标价或确定性结论。
- 预测前必须说明信号合成逻辑；禁止只罗列指数、涨停池和连板数据后直接给预测。
- 每条预测必须包含对象、周期、指标、方向/区间、置信度、依据、验证方式和 condition。
- MEMORY_JSON.predictions 必须包含 3-6 条记录。
- as_of_date 必须填写当前自然日期 {today}；trade_date 必须填写第 1 步选出的目标交易日，格式均为 YYYY-MM-DD。
- 顶层 metric 必须和 condition.metric 完全一致。
- condition 是系统自动复盘使用的可执行条件，不允许写自由文本。
- condition.metric 只能使用：index_return_pct、index_close、stock_return_pct、limit_up_count、limit_up_count_change_pct、consecutive_limit_up_max、turnover_amount_change_pct。
- condition.operator 只能使用：gt、gte、lt、lte、between、eq、neq。
- condition.unit 必须填写单位，例如 pct、points 或 count。
- 置信度使用 0 到 0.75 的小数，不得超过 0.75。
- 预测依据必须以中性事实表述，不使用“火爆、惨淡、疯狂、强势、弱势、活跃、发酵、意愿、尚可、明显、中幅、大幅、小幅”等主观词；如需表达市场状态，必须用可复盘指标和阈值定义。
- 交易日事实必须以日历工具返回的目标交易日为准；如果当前自然日不是交易日，不得写成“当前日期为交易日”。
- 不要自行补充星期几；除非工具返回星期字段。

最终回答末尾必须追加一个机器可读区块，格式严格如下：

MEMORY_JSON:
```json
{{
  "predictions": [
    {{
      "as_of_date": "{today}",
      "trade_date": "YYYY-MM-DD",
      "scope": "market|index|theme|stock",
      "target": "预测对象中文名",
      "target_id": "thscode 或对象标识，无法确定则为空字符串",
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
  ]
}}
```
""",
    ),
    "daily-review": Workflow(
        name="daily-review",
        title="每日结果复盘",
        description="读取待复盘预测，拉取实际数据，生成评分和经验沉淀。",
        prompt_template="""请按“每日结果复盘”工作流执行：{target}

当前日期：{today}，时区：Asia/Shanghai。

历史记忆上下文：
{memory_context}

工作流要求：
0. 遵循底层开源量化知识库：复盘以实际数据和系统自动评分为准，模型只解释误差来源，不自行改写结果。
1. 逐条查看待复盘预测记录；如果没有待复盘记录，直接说明无需复盘。
2. 调用 get_a_share_calendar_trading_days 确认可验证的最近交易日。必须在 data.item 中选择不晚于当前日期 {today} 的最大 date 作为目标交易日；后续需要 date_ms 时，必须使用所选交易日条目的原始 date_ms，不得自行换算。
3. 根据每条预测的 validation_query、target_id、metric 选择合适工具获取实际结果。
4. 对每条能验证的预测提取 actual_value，actual_value 必须是数字，actual_metric 必须等于原预测 metric，actual_trade_date 必须填写实际数据对应交易日，source_tool 必须填写实际使用的 MCP 工具名；系统会按 prediction.condition 自动判定 hit/miss 和 score。
5. 如果无法提取 actual_value，actual_value 使用 null，outcome 使用 unknown。
6. 误差归因必须比较“预测依据中的信号”与“实际结果中的信号”：找出哪些信号失效、哪些指标被高估/低估、哪些数据缺口导致误判；不要只写命中/未命中。
7. 提炼可迁移的 lesson，后续每日预测会读取这些经验。lesson 必须能改变下次预测方法，例如阈值、置信度、信号权重或需要补充的验证工具。
8. 输出结构固定为：复盘范围、命中统计、信号失效/有效分析、逐条预测复盘、方法修正、仍需等待的数据。

复盘约束：
- 只基于工具返回的实际数据评分。
- 不为了迎合原预测而事后解释。
- 不要只复述 hit/miss；必须解释预测信号与实际信号之间的差异，并沉淀为下一次可执行的方法修正。
- 只能输出历史记忆上下文中待复盘预测的 prediction_id，不要自行编造或复盘其他 id。
- actual_metric 必须与原预测 metric 一致；source_tool 必须是本次实际调用过的工具名。
- actual_trade_date 不能早于原预测 trade_date，格式为 YYYY-MM-DD。
- 数据不足时 outcome 必须是 unknown，并说明缺口。
- 复盘事实、系统评分和误差解释分开写；事实部分不得加入情绪化或暗示性措辞。
- 交易日事实必须以日历工具返回的目标交易日为准；如果当前自然日不是交易日，不得写成“当前日期为交易日”。
- 不要自行补充星期几；除非工具返回星期字段。

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
      "actual_summary": "实际结果摘要",
      "source_tool": "get_a_share_special_data_limit_up_pool",
      "outcome": "unknown",
      "score": null,
      "error_reason": "无法提取 actual_value 时说明原因；能提取时可为空字符串",
      "lesson": "可迁移经验，不能是空泛套话"
    }}
  ],
  "lessons": [
    {{
      "lesson": "全局复盘经验"
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
