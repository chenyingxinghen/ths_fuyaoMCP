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

项目内置两个基础 skill 工作流程，用固定分析框架约束模型调用工具和输出结构。

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

适合分析单只股票，会按顺序做标的识别、行情快照、最近一年走势、财务概览、公司行动和数据缺口说明。

```bash
fuyao-agent --skill stock-analysis "贵州茅台"
fuyao-agent --workflow stock-analysis "600519.SH"
```

固定输出结构：

- 标的确认
- 最新行情
- 走势观察
- 财务概览
- 需要继续核验的数据
- 结论摘要

### 市场异动速览

适合快速看当日或指定交易日市场热度，会汇总主要指数、涨停股票池、连板天梯和盘面异动线索。

```bash
fuyao-agent --skill market-movers
fuyao-agent --workflow market-movers "看 2026-06-26 的市场异动"
```

固定输出结构：

- 交易日
- 指数表现
- 涨停池
- 连板梯队
- 异动线索
- 风险与数据缺口

### 每日分析预测与结果复盘

系统内置一个 SQLite 记忆库，用于把每日预测、复盘结果、工具调用快照和复盘经验沉淀下来，默认路径是 `.fuyao-memory/memory.sqlite3`。可以通过 `FUYAO_MEMORY_DB` 修改。

每日预测：

```bash
fuyao-agent --skill daily-forecast
```

预测流程会拉取交易日、主要指数、涨停池和连板天梯，并在回答末尾生成 `MEMORY_JSON`。CLI 会自动解析并写入 `predictions` 表。

每条预测必须包含可执行的 `condition`：

```json
{
  "metric": "limit_up_count",
  "operator": "gte",
  "threshold": 80,
  "unit": "count"
}
```

没有合法 `condition` 的预测会被标记为 `invalid`，不会进入待复盘队列。

查看待复盘预测：

```bash
fuyao-agent --memory-pending
```

每日复盘：

```bash
fuyao-agent --skill daily-review
```

复盘流程会读取待复盘预测作为上下文，拉取实际数据，并要求模型提取数字型 `actual_value`。系统会根据预测里的 `condition` 自动计算 `hit` / `miss` 和分数；`unknown` 会保留为待复盘，避免数据尚未到齐时提前关闭。

查看记忆统计：

```bash
fuyao-agent --memory-stats
```

统计包含：

- 总预测数、有效预测数、无效预测数、待复盘数
- 总复盘数、平均分
- 按 `scope`、`metric`、置信度分桶的 outcome 分布
- 每次工作流运行的 MCP 工具调用快照保存在 `observations` 表，便于事后审计

临时禁用记忆读写：

```bash
fuyao-agent --skill daily-forecast --no-memory
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
