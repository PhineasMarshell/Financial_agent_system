# Financial Agent System（金融智能体系统）

基于 **LangGraph** 的多智能体金融新闻分析与自动交易系统。系统实时监听市场新闻流，通过路由 Agent 判断新闻与各交易标的的相关性，在分发到具体领域分析前自动注入**宏观市场快照**（美元指数、美债收益率、VIX、BTC 资金费率等），再由并行分析 Agent（黄金 / 比特币）结合宏观上下文生成带止盈止损的交易信号并入库，最后由交易执行器在 **Hyperliquid** 上自动执行。同时提供 Web 仪表盘实时展示信号、持仓与绩效。（框架已写好，无论是切换货币还是要加 skill 直接写即可）

> ⚠️ **免责声明**：本项目仅用于学习与研究目的，不构成任何投资建议。加密货币交易风险极高，请务必先在小额资金或**测试网（testnet）**上验证，自行承担一切交易风险。

---

## ✨ 核心功能

- **多智能体协作**：路由 Agent（`Finance_Information_Router`） + 领域分析 Agent（`Gold_AnalystAgent`、`BTC_AnalystAgent`），一条新闻可触发多个 Agent 并行分析
- **宏观市场快照**：每次分析前自动拉取 DXY、US10Y、标普期货、VIX、BTC 资金费率，5 分钟 TTL 缓存，计算整体风险偏好（`risk_on` / `neutral` / `risk_off`）并注入下游 Agent
- **LangGraph 工作流**：基于状态机的条件分发，每篇新闻独立执行，支持并发处理
- **实时新闻接入**：WebSocket 监听市场新闻，异步缓冲队列（容量 1000）缓解高峰压力、保护 LLM 接口
- **结构化交易信号**：分析 Agent 输出 Markdown + JSON 混合报告，自动提取并清洗信号（方向、置信度、时间跨度、入场/止损/止盈价、仓位、关键驱动因素、风险因素）
- **自动交易执行**：`TradeExecutor` 每 30 秒轮询未执行信号，市价开仓 + 自动挂止盈/止损触发单，支持减仓、平仓、撤单
- **风控引擎**：`RiskEngine` 在真实下单前校验信号，规避无效或危险操作
- **Web 仪表盘**：FastAPI 提供 REST + WebSocket 实时推送，前端 `dashboard.html` 展示信号流、交易记录、账户与绩效

---

## 🏗️ 系统架构

```
                    ┌───────────────────────────────────────────────────────┐
   实时新闻流        │              LangGraph 工作流                        │
 WebSocket ───────► │                                                       │
 (ws_fetcher)       │   Router Agent ──► 是否相关？──不是──► End            │
      │             │                        │是                            │
      ▼             │           注入宏观快照 ▼                 ▼            │
 asyncio 队列 ─────►│   Macro Snapshot    Gold_Analyst     BTC_Analyst      │
 (容量1000)         │                      │                   │            │
                    │                      └────┬───────────── ┘            │
                    │                           ▼                           │
                    │                     信号提取 + 数据清洗 + SQLite 入库  │
                    └───────────────────────────────────────────────────────┘
                                                  ▼
                              ┌────────────────────────────────────────────┐
                              │   TradeExecutor（每 30s 轮询）             │
                              │    RiskEngine 风控 ──► Hyperliquid 下单    │
                              │    （市价单 + 止盈/止损触发单）             │
                              └────────────────────────────────────────────┘

   仪表盘：api_server.py（FastAPI REST + WebSocket） ──► dashboard.html
```

### 工作流节点（`main.py`）

| 节点 | 说明 |
|---|---|
| `router_node` | 路由 Agent 判断新闻相关性，输出 `dispatch` 决策与推理 |
| `macro_snapshot_node` | 自动拉取宏观指标（DXY、US10Y、标普期货、VIX、BTC 资金费率），计算风险偏好并注入 state |
| `gold_agent_node` | 黄金分析 Agent，结合宏观上下文生成报告 + 交易信号并入库 |
| `btc_agent_node` | 比特币分析 Agent，结合宏观上下文生成报告 + 交易信号并入库 |
| `end_node` | 汇总报告，结束流程 |

### 宏观快照覆盖的指标

| 指标 | Ticker | 含义 |
|---|---|---|
| DXY | `DX-Y.NYB` | ICE 美元指数 — 强美元压制风险资产 |
| US10Y | `^TNX` | CBOE 10 年期美债收益率 — 流动性风向标 |
| 标普 E-mini 期货 | `ES=F` | 全球权益情绪代理 |
| VIX | `^VIX` | 恐慌指数 — >25 触发风险降级 |
| BTC 资金费率 | Hyperliquid API | 多头/空头力量对比 |

系统根据上述指标打分，输出 `market_regime`（`risk_on` / `neutral` / `risk_off`）及判定依据，供下游 Agent 辅助判断。

---

## 🛠️ 技术栈

| 类别 | 依赖 |
|---|---|
| **LLM / Agent** | `openai`、`langgraph`、`langchain-core`、`langsmith` |
| **交易执行** | `hyperliquid-python-sdk`、`eth-account` |
| **数据接入** | `websockets`、`ddgs`（DuckDuckGo 搜索）、`requests`、`beautifulsoup4`、Yahoo Finance v8 chart API |
| **基础设施** | `python-dotenv`、`pydantic`、`aiohttp`、`httpx`、`numpy`、`pandas`、`cryptography`、`pycryptodome`、`tqdm` |

参见 [requirements.txt](requirements.txt)。

---

## 📁 项目结构

```
Financial_agent_system/
├── main.py                          # 主入口：LangGraph 工作流 + 队列 + WebSocket + 执行器
├── api_server.py                    # FastAPI 仪表盘服务（REST + WebSocket 实时推送）
├── dashboard.html                   # Web 仪表盘前端
├── fas-architecture.html / .json    # 系统架构图（可视化）
├── financial_agent_db.db            # SQLite 数据库（运行时生成）
├── requirements.txt                 # Python 依赖
├── .env.example                     # 环境变量模板（见下方配置）
└── src/
    ├── agents/
    │   ├── Finance_Information_Router.py   # 路由 Agent：相关性判断与领域分发
    │   ├── Gold_analyst.py                 # 黄金分析 Agent（集成宏观快照）
    │   └── BTC_analyst.py                  # 比特币分析 Agent（集成宏观快照）
    ├── database/
    │   └── db_manager.py                   # SQLite 数据管理（signals / trades 表）
    ├── execution/
    │   └── trade_executor.py               # 交易执行（TradeConfig / HyperliquidClient / RiskEngine / TradeExecutor）
    ├── gateway/
    │   └── ws_fetcher.py                   # WebSocket 新闻接入与格式标准化
    └── tools/
        ├── hy_finance_tools.py             # Hyperliquid 行情查询工具
        ├── macro_tools.py                  # 宏观快照工具（DXY / US10Y / VIX / BTC funding rate）
        ├── search_tools.py                 # 新闻舆情搜索工具
        ├── registry.py                     # 工具注册表
        └── tool_schemas.py                 # 工具 Schema 定义
```

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 建议使用 `venv` 虚拟环境

### 1. 安装依赖

```bash
# Windows（PowerShell）
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | LLM 服务的 API Key |
| `LLM_BASE_URL` | LLM 服务的 Base URL（OpenAI 兼容接口） |
| `FEED_API_BASE_URL` | 新闻数据源 API 地址 |
| `FEED_API_KEY` | 新闻数据源 API Key |
| `HYPERLIQUID_PRIVATE_KEY` | Hyperliquid 钱包私钥 |
| `HYPERLIQUID_MAIN_ADDRESS` | Hyperliquid 主账户地址 |
| `HYPERLIQUID_AGENT_WALLET_ADDRESS` | Hyperliquid Agent 钱包地址 |
| `HYPERLIQUID_NETWORK` | 网络环境（如 `mainnet` / `testnet`），**建议先用 testnet 验证** |

> 🔒 `.env` 已加入 `.gitignore`，请勿提交任何密钥。

### 3. 启动交易流水线

```bash
python main.py
```

启动后会同时运行三个后台任务：

1. **WebSocket 新闻监听** — 实时接收市场新闻并入队
2. **新闻处理循环** — 消费队列，逐篇执行 LangGraph 工作流（含宏观快照 + Agent 并行分析）并入库信号
3. **交易执行器轮询** — 每 30 秒扫描未执行信号，经风控后在 Hyperliquid 执行

### 4. 启动仪表盘

另开一个终端：

```bash
python api_server.py
```

浏览器访问 `http://localhost:8000` 查看实时仪表盘。

---

## 📡 API 接口（api_server.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 仪表盘首页（dashboard.html） |
| `GET` | `/api/signals` | 交易信号列表 |
| `GET` | `/api/trades` | 交易记录列表 |
| `GET` | `/api/account/summary` | 账户概览 |
| `GET` | `/api/performance/{agent_name}` | 指定 Agent 的绩效数据 |
| `WebSocket` | `/ws` | 实时推送信号 / 交易更新 |

---

## 💾 数据模型

### signals（交易信号）

由分析 Agent 生成，核心字段：

| 字段 | 说明 |
|---|---|
| `agent_name` | 来源 Agent（`gold` / `btc`） |
| `signal` | 交易方向（`long` / `short` / `neutral`） |
| `confidence` | 置信度（0–10） |
| `timeframe` | 时间跨度（`SCALP` / `INTRADAY` / `SWING` / `TREND`） |
| `entry_price` / `stop_loss` / `take_profit` | 入场价 / 止损价 / 止盈价 |
| `position_size` | 建议仓位 |
| `key_drivers` / `risk_factors` | 关键驱动因素 / 风险因素 |
| `tools_used` | 使用的工具列表 |
| `raw_report` | Agent 原始分析报告 |
| `latency_ms` | 分析耗时（毫秒） |

### trades（交易记录）

由 `TradeExecutor` 写入，追踪开仓、平仓、止盈止损等状态。

---

## 🔧 常见问题

- **信号一直不入库？** 检查 `.env` 中 `LLM_API_KEY` / `LLM_BASE_URL` 是否可用，以及日志中 Router 节点输出的相关性判断。
- **交易未执行？** 确认 `HYPERLIQUID_NETWORK` 配置正确、账户有足够余额，且信号通过 `RiskEngine` 校验；日志中 `[ExecutorLoop]` 会打印扫描结果。
- **宏观快照失败？** 默认使用 Yahoo Finance v8 chart API 和 Hyperliquid 公开接口，受网络环境影响较大；失败时日志提示 `[Macro] 宏观数据获取失败`，不会中断分析流程，只是本次不注入宏观约束。
- **数据库文件** `financial_agent_db.db` 会在运行时自动创建/更新，如需重置请先备份。
