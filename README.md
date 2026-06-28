# Fuyao ModelScope MCP Agent

这个项目是一个最小可运行的命令行和本地 Web 应用，用魔搭社区的 OpenAI 兼容接口作为大模型提供商，并通过同花顺金融数据 API 的远端 MCP 服务获取 A 股数据。

## 能力

- 连接同花顺远端 HTTP MCP 端点：
  - `https://fuyao.aicubes.cn/mcp/meta`
  - `https://fuyao.aicubes.cn/mcp/a-share`
  - `https://fuyao.aicubes.cn/mcp/a-share-index`
- 自动发现 MCP tools，并转换成 OpenAI-compatible `tools`。
- 使用魔搭社区 OpenAI 兼容 Chat Completions 完成多轮 tool calling。
- 支持自然语言提问，例如“查一下贵州茅台今天行情”。
- 提供本地 Web 控制台，支持问答、工作流、MCP 工具目录、记忆库、知识库和中性措辞检查。
- CLI 和 Web 输出会自动解码被转义或代码围栏包裹的 Markdown 结果。

## 准备 Key

需要两个 Key：

- `MODELSCOPE_API_KEY`：魔搭社区 API Key。
- `FUYAO_API_KEY`：同花顺金融数据 API Key，可在 <https://fuyao.aicubes.cn/admin> 创建。

同花顺接口鉴权使用 `X-api-key` 请求头；本项目会自动把 `FUYAO_API_KEY` 注入到 MCP HTTP 请求头。

## 安装

建议使用 Python 3.11+：

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
```

PowerShell 下也可以用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
MODELSCOPE_API_KEY=your-modelscope-api-key
MODELSCOPE_BASE_URL=https://api-inference.modelscope.cn/v1
MODELSCOPE_MODEL=Qwen/Qwen2.5-72B-Instruct

FUYAO_API_KEY=your-fuyao-api-key
FUYAO_BASE_URL=https://fuyao.aicubes.cn
FUYAO_MCP_SERVERS=meta,a-share,a-share-index

FUYAO_MEMORY_DB=.fuyao-memory/memory.sqlite3
```

`MODELSCOPE_MODEL` 可以换成你在魔搭社区开通且支持工具调用的模型。

## 使用

先验证 MCP tools 是否能列出：

```bash
fuyao-agent --list-tools
```

提问：

```bash
fuyao-agent "查一下贵州茅台今天的行情，并说明数据时间"
```

启动本地 Web 控制台：

```bash
fuyao-agent-web
```

默认访问地址是 <http://127.0.0.1:8765/>。如需改端口：

```bash
fuyao-agent-web --port 8780
```

更多示例：

```bash
fuyao-agent "茅台最近 4 期年报利润表里营业收入和归母净利润是多少？"
fuyao-agent "白酒概念板块有哪些成分股？"
fuyao-agent "最近一年有哪些 A 股交易日？"
```

## Skill 工作流程

项目内置两个业务工作流，用固定分析框架约束模型调用工具、输出结构和预测复盘闭环。

### 底层量化知识注入

系统内置一份从开源 AI/量化项目提炼的知识底座，来源包括：

- Microsoft Qlib
- AI4Finance FinRL
- AI4Finance FinGPT
- Microsoft RD-Agent

知识底座保存在 `docs/knowledge/open_source_quant_ai.md`，启动 agent 时会注入到 system prompt，用来约束模型遵循数据优先、可审计、可复盘、非投资建议、假设驱动的专业量化工作方式。

事实数据和计算结果要求使用中性语言：只报告数值、单位、时间窗口、阈值、排名和数据缺口；解释性判断必须单独标注为“解释”或“假设”。避免把“火爆、惨淡、疯狂、强势、弱势、恐慌、乐观、悲观”等主观词混入事实部分。

查看当前注入内容：

```bash
fuyao-agent --show-knowledge
```

检查一段文本是否包含高风险主观措辞：

```bash
fuyao-agent --check-neutrality "涨停池非常火爆，指数表现强势"
```

查看内置工作流：

```bash
fuyao-agent --list-workflows
```

### 个股分析

适合分析单只股票，会按顺序做标的识别、同标的待复盘预测验证、行情快照、最近一年走势、财务概览、公司行动、交叉证据链和数据缺口说明。数据足够时会生成 1-3 条可复盘个股假设；数据不足时会明确说明不生成预测的原因。

```bash
fuyao-agent --skill stock-analysis "贵州茅台"
fuyao-agent --workflow stock-analysis "600519.SH"
```

固定输出结构：

- 分析结论摘要
- 复盘验证
- 标的确认
- 关键证据链
- 走势与财务的交叉验证
- 解释/假设
- 预测清单
- 方法修正
- 需要继续核验的数据

### 大盘晴雨赚钱效应分析

适合分析最近一个或指定交易日的大盘晴雨和赚钱效应。工作流会先复盘本工作流待验证预测，再综合主要指数、涨停股票池、连板天梯、近 30 日梯队变化和历史 lessons，生成下一轮 3-6 条可执行验证条件的市场假设。

```bash
fuyao-agent --skill market-weather
fuyao-agent --workflow market-weather "看 2026-06-26 的大盘晴雨和赚钱效应"
```

固定输出结构：

- 交易日
- 复盘验证
- 赚钱效应合成摘要
- 支持信号
- 矛盾/背离信号
- 未来观察假设
- 预测清单
- 方法修正
- 风险与数据缺口

### 预测、复盘与经验沉淀闭环

系统内置一个 SQLite 记忆库，用于把两个工作流的预测、复盘结果、工具调用快照和复盘经验沉淀下来，默认路径是 `.fuyao-memory/memory.sqlite3`。可以通过 `FUYAO_MEMORY_DB` 修改。

每次运行 `stock-analysis` 或 `market-weather` 时，CLI/Web 会把同一工作流的待复盘预测、最近 lessons、历史系统评分反馈和近期结构化拒绝原因注入到 prompt。待复盘预测会按“最新可验证交易日”拆成三组：已到最新可验证交易日且本数据日尚未尝试 unknown 的记录、当前数据日已尝试 unknown 的暂缓复盘记录、尚未到最新可验证交易日的记录。最新可验证交易日优先来自最近一次日历 MCP observation 中的最大交易日；没有历史日历 observation 时，才退化为当前自然日前最近工作日。模型只能对第一组 prediction_id 写入 reviews，暂缓复盘和未到期记录只用于避免重复假设和管理观察队列，避免周末/假日把不可验证预测反复送入 LLM。系统也会拒绝同业务范围、同对象、同验证日、同指标、同 condition 的重复 pending 预测，防止观察队列被重复假设污染；预测入库时还会按 `metric` 校验本轮是否调用过可支撑该指标的 MCP 工具，并把通过校验的预测关联到本轮实际 observation 记录，避免无数据证据的假设进入闭环。工作流预测还必须满足核心工具完整性：`market-weather` 至少包含交易日历、指数快照、涨停池和连板天梯，且预测批次必须覆盖至少两个信号族（指数、涨停/连板广度、流动性/成交额）；`stock-analysis` 至少包含交易日历、行情快照和历史价格，若生成 2 条以上预测，必须同时覆盖收益率和成交额变化。真实工作流运行还会审计正文是否包含固定综合分析栏目，并把缺失栏目或高风险主观措辞写入 `validation_errors`。`market-weather` 运行中如果生成预测，记忆层会要求一次写入 3-6 条假设；`stock` 预测的 `target_id` 必须是 `600519.SH` 这类 A 股 thscode，`index` 预测的 `target_id` 必须是 `000001.SH` 这类指数代码。模型必须先处理可验证的待复盘记录，再结合历史命中率、指标表现、置信度分桶表现和上次结构拒绝原因生成新的可验证预测，并在回答末尾输出 `MEMORY_JSON`。CLI/Web 会自动解析并写入本地记忆库，并为每次写入生成 `run_audits` 记录，汇总工具、缺失工具、信号族、输出审计、MEMORY_JSON 状态和结构化拒绝数量。

每条预测必须包含可执行的 `condition`：

```json
{
  "metric": "limit_up_count",
  "operator": "gte",
  "threshold": 80,
  "unit": "count"
}
```

没有合法 `condition` 的预测会被标记为 `invalid`，不会进入待复盘队列；`scope` 和 `metric` 必须匹配，例如 `stock` 只能使用 `stock_return_pct` 或 `turnover_amount_change_pct`，`index` 只能使用指数类指标，`market/theme` 使用涨停、连板或成交额变化指标；条件阈值和区间上下限必须是有限数字，不能是 NaN 或无穷值。预测的 `rationale` 还必须包含指标、工具、信号、阈值、窗口、样本或条件等证据上下文；`validation_query` 必须说明复盘时如何提取或计算 `actual_value`，不能只写“继续观察”或“明天验证”。
预测的 `trade_date` 必须是日历工具明确返回的下一可验证交易日或指定验证日，不能填写已经用于生成分析结论的 `analysis_trade_date`。如果日历只返回到最新已开市交易日，没有返回未来下一交易日，工作流必须写空 `predictions` 并说明数据缺口，不能自行按自然日或星期推断。

查看待复盘预测：

```bash
fuyao-agent --memory-pending
```

复盘流程会读取待复盘预测作为上下文，拉取实际数据，并要求模型提取数字型 `actual_value`。`MEMORY_JSON` 必须包含 `reviews`、`predictions`、`lessons` 三个顶层数组。模型在 `MEMORY_JSON.reviews` 中只能把 `outcome` 写为 `unknown`、`score` 写为 `null`；系统会根据预测里的 `condition` 自动计算 `hit` / `miss` 和分数。`actual_value` 必须是有限数字，count 类指标必须是非负整数，`index_close` 必须为正数；`actual_summary` 必须同时说明 `actual_metric` 或指标上下文、`source_tool` 或工具上下文，以及提取到的 `actual_value`。`unknown` 会保留为待复盘，避免数据尚未到齐时提前关闭；同一 prediction、同一 actual_trade_date、同一 metric、同一 source_tool 的重复 unknown 复盘会被拒绝，避免重复缺口记录污染统计和 lessons。运行时还会校验 `source_tool` 必须来自本次真实 MCP 调用链，且必须能验证 `actual_metric`，防止复盘记录声明未调用过或指标不兼容的工具；review 和全局 `lessons` 必须同时包含可执行方法修正（阈值、置信度或权重）和适用上下文（指标、工具、窗口、条件、样本或信号）。缺失或解析失败的 `MEMORY_JSON`、不完整顶层结构、无效预测、无效复盘或空泛经验都会返回结构化 `validation_errors`，CLI 会打印拒绝原因，Web API 会在 `memory_write.validation_errors` 中返回，并持久写入 `validation_errors` 表，便于检查并修正模型输出。

查看最近结构化记忆拒绝原因：

```bash
fuyao-agent --memory-errors
```

查看记忆统计：

```bash
fuyao-agent --memory-stats
```

查看某个工作流的系统评分反馈：

```bash
fuyao-agent --memory-performance market-weather
```

查看最近运行审计，或查看指定 run_id 的审计详情：

```bash
fuyao-agent --memory-audits
fuyao-agent --memory-audit-run 12
```

查看某个工作流下一次运行会注入的完整记忆上下文：

```bash
fuyao-agent --memory-context stock-analysis "600519.SH"
```

统计包含：

- 总预测数、有效预测数、无效预测数、待复盘数
- 总复盘数、平均分
- 按 `scope`、`metric`、置信度分桶的 outcome 分布
- 结构化记忆拒绝总数和按记录类型分布
- 预测证据链覆盖数和证据记录数
- 运行审计总数、每次运行的工具缺口、信号族、输出审计状态和 MEMORY_JSON 状态
- 工作流运行时还会把同业务范围的命中率、按指标表现、按置信度表现、弱项校准建议和近期结构化拒绝原因注入上下文，用于校准下一轮阈值、置信度和 `MEMORY_JSON` 结构
- 每次工作流运行的 MCP 工具调用快照保存在 `observations` 表，通过校验的预测证据关系保存在 `prediction_evidence` 表，运行级汇总保存在 `run_audits` 表，便于事后审计

临时禁用记忆读写：

```bash
fuyao-agent --skill market-weather --no-memory
```

也可以直接用模块方式运行：

```bash
python -m fuyao_agent "上证综指和白酒概念今天表现如何？"
```

## 工作流

1. CLI 读取 `.env`。
2. 连接同花顺 MCP 端点并执行 `list_tools`。
3. 把 MCP tool schema 转为 OpenAI-compatible tool schema。
4. 调用魔搭 Chat Completions。
5. 如果模型返回 `tool_calls`，执行对应 MCP tool，把结果作为 `tool` message 回传给模型。
6. 重复直到模型输出最终回答。

## 常见问题

`Missing required environment variable`

检查 `.env` 是否存在，并确认 `MODELSCOPE_API_KEY`、`FUYAO_API_KEY` 已填写。

`code=2001` 或 `code=2003`

同花顺 API Key 缺失、失效或没有对应 capability 权限。重新签发 Key 或确认权限。

模型不调用工具

确认选择的魔搭模型支持 OpenAI-compatible tool calling。不同模型对工具调用的支持程度不同。

连接 MCP 失败

确认网络可以访问 `https://fuyao.aicubes.cn`，并检查 `FUYAO_BASE_URL` 和 `FUYAO_MCP_SERVERS`。
