# AQP 架构与前后端模块对应性审查报告

> 审查日期：2026-10-03　审查人：高见远（架构师）
> 项目根目录：`D:\Python_Project\Alpha Quant Platform`
> 性质：纯静态审查（未修改任何源码、未启动服务）。所有结论均附**文件路径 + 行号**证据。
> 临时脚本目录：`docs/audit/2026-10-03/_scripts/`（可复跑验证，见附录 A）

---

## 0. 审查范围与方法

| 项 | 数量 | 取值方式 |
|---|---|---|
| 后端 HTTP 端点（`/api/v1`） | **115** | AST 静态解析 `backend/app/api/v1/*.py` 装饰器 + `router.py` 的 `include_router` prefix（`extract_routes.py`） |
| 后端根端点 | **5**（`/`、`/health`、`/health/ready`、`/health/live`、`/metrics`） | 人工核对 `backend/app/main.py:402/417/438/451/560` |
| 后端 API 模块 | **20 个业务模块** + `router.py` | `backend/app/api/v1/` |
| 前端页面 | **19** | `frontend/src/pages/` 一级目录 |
| 前端组件 | **14**（`components/` 9 + `charts/` 4 + `ui/` 1） | `frontend/src/components/` |
| 前端 hooks / stores / api / types | **7 / 5 / 21 / 8** | 同名目录 |
| 前端源码文件（ts/tsx） | **113** | 全量扫描 |
| 前端字面量端点调用 | **133** | 正则扫描（`extract_fe_calls.py`） |

**判定方法说明**

1. **A 类（僵尸端点）**：后端路由的完整路径字面量在 `frontend/src/**` 中**不可匹配**（正则要求路径后紧跟 `'`/`"`/`` ` ``/`?`/空白/行尾，避免 `/overview` 误命中 `/overview/rt`）。
2. **B 类（前端无后端）**：前端出现的 `/api/v1/*` 字面量反查后端路由表，全部命中 → **B 类 = 0**。
3. **C / D 类**：跨模块命名与实现比对（grep + 人工判读）。
4. **兜底掩盖**：正则扫描 `catch { return … }`、`?? []`、`?? {}`、`?? 0`、`mock/MOCK`（`fe_fallback.py`，203 处命中后逐条人工判读，剔除注释中的"禁止 `?? 0`"反例）。

---

## ① 后端端点全景表

> **「消费入口」列为模块粒度**：指 import 了对应 `frontend/src/api/*.ts` 封装模块的页面/组件，非逐端点精确调用点。已确认的例外在下表「备注」中逐条标注。

### 1.1 alerts（7）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 | 备注 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/alerts/health` | alerts.py:136 | api/alerts.ts | Alerts、StockDetail | 真实（predictions + universe 快照） | |
| GET | `/api/v1/alerts/rules` | alerts.py:148 | api/alerts.ts | Alerts、StockDetail | 真实（SQLite alert_rules） | |
| POST | `/api/v1/alerts/rules` | alerts.py:163 | api/alerts.ts | Alerts、StockDetail | 真实 | params/channels 白名单校验 |
| PUT | `/api/v1/alerts/rules/{rule_id}` | alerts.py:192 | api/alerts.ts | Alerts、StockDetail | 真实 | |
| DELETE | `/api/v1/alerts/rules/{rule_id}` | alerts.py:225 | api/alerts.ts | Alerts、StockDetail | 真实 | |
| GET | `/api/v1/alerts/events` | alerts.py:247 | api/alerts.ts | Alerts、StockDetail | 真实 | |
| POST | `/api/v1/alerts/events/read` | alerts.py:272 | api/alerts.ts | Alerts、Topbar、useNotifyStore | 真实 | |

⚠️ **隐含缺陷**：`_rule_symbols()` 在 `scope == "watchlist"` 时读 SQLite `watchlist` 表（alerts.py:301-305），失败/空表时 `alerts.py:306` 直接 `return []`，**无任何告警日志**。见 ③ -A-01。

### 1.2 app_settings（7）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 | 备注 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/settings` | app_settings.py:153 | api/settings.ts | Settings、usePreferencesStore | 真实 | 内部复用 `/datacenter/overview`（:173） |
| PUT | `/api/v1/settings/preferences` | app_settings.py:194 | api/settings.ts | Settings、usePreferencesStore | 真实写入 | |
| PUT | `/api/v1/settings/engine` | app_settings.py:214 | api/settings.ts | Settings | **写入有效、消费为零** | 见 ③ -A-02（P0） |
| POST | `/api/v1/settings/connectors/test` | app_settings.py:253 | api/settings.ts | Settings | 真实探测 | |
| POST | `/api/v1/settings/data/sync` | app_settings.py:271 | api/settings.ts | Settings | 真实（复用 datacenter.trigger_sync） | |
| POST | `/api/v1/settings/data/cache/clear` | app_settings.py:281 | api/settings.ts | Settings | 真实 | 有 `PERSISTENT_KEYS` 保护 |
| POST | `/api/v1/settings/db/backup` | app_settings.py:338 | api/settings.ts | Settings | 真实 | admin |

### 1.3 auth（4）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | auth.py:108 | api/auth.ts | AuthBootstrap、RequireAuth、Sidebar、Topbar、Login | 真实 |
| GET | `/api/v1/auth/register/status` | auth.py:181 | api/auth.ts | Login | 真实 |
| POST | `/api/v1/auth/register` | auth.py:194 | api/auth.ts | Login、useAuthStore | 真实 |
| GET | `/api/v1/auth/me` | auth.py:239 | api/auth.ts | AuthBootstrap、useAuthStore、App | 真实 |

### 1.4 backtest（3）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/backtest/run` | backtest.py:481 | api/backtest.ts | Backtest/TopKPanel | 真实引擎 |
| POST | `/api/v1/backtest/strategy-run` | backtest.py:929 | api/strategyBacktest.ts | Backtest/index | 真实引擎 |
| POST | `/api/v1/backtest/signal-analysis` | backtest.py:986 | api/backtest.ts | Backtest/SignalAnalysisPanel | 真实（IC 衰减 + 分层） |

### 1.5 datacenter（21）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/datacenter/overview` | datacenter.py:643 | api/datacenter.ts | DataCenter、Settings、Backtest | 真实 |
| GET | `/api/v1/datacenter/datasets` | datacenter.py:853 | api/datacenter.ts | DataCenter | 真实（parquet 扫描） |
| GET | `/api/v1/datacenter/quality` | datacenter.py:893 | api/datacenter.ts | DataCenter | 真实 |
| GET | `/api/v1/datacenter/logs` | datacenter.py:940 | api/datacenter.ts | DataCenter | 真实（app.log 尾部） |
| GET | `/api/v1/datacenter/task-stats` | datacenter.py:979 | api/datacenter.ts | DataCenter | 真实；SQLite 异常时 `return []`（:997，有 warning） |
| POST | `/api/v1/datacenter/sync` | datacenter.py:1027 | api/datacenter.ts | DataCenter、Settings | 真实 |
| GET | `/api/v1/datacenter/sync/status` | datacenter.py:1081 | api/datacenter.ts | DataCenter | 真实 |
| GET | `/api/v1/datacenter/sync/tasks/{task_id}` | datacenter.py:1088 | api/datacenter.ts | DataCenter | 真实 |
| GET | `/api/v1/datacenter/sync/auto` | datacenter.py:1113 | api/datacenter.ts | DataCenter | 真实 |
| POST | `/api/v1/datacenter/sync/auto` | datacenter.py:1125 | api/datacenter.ts | DataCenter | 真实 |
| POST | `/api/v1/datacenter/sync/cancel` | datacenter.py:1139 | api/datacenter.ts | DataCenter | 真实 |
| POST | `/api/v1/datacenter/sync/fetch` | datacenter.py:1419 | api/datacenter.ts | DataCenter | 真实 |
| GET | `/api/v1/datacenter/instruments` | datacenter.py:1502 | api/datacenter.ts | DataCenter | 真实；异常 `return []`（:1324，有 warning） |
| GET | `/api/v1/datacenter/text/status` | datacenter.py:1557 | api/datacenter.ts | DataCenter/TextDataPanel | 真实 |
| POST | `/api/v1/datacenter/text/import` | datacenter.py:1566 | api/datacenter.ts | DataCenter/TextDataPanel | 真实 |
| POST | `/api/v1/datacenter/text/build-factor` | datacenter.py:1579 | api/datacenter.ts | DataCenter/TextDataPanel | 真实 |
| GET | `/api/v1/datacenter/mirror/status` | datacenter.py:1594 | api/datacenter.ts | DataCenter/TextDataPanel | 真实（走 `_gated_scan`） |
| POST | `/api/v1/datacenter/mirror/rebuild` | datacenter.py:1630 | api/datacenter.ts | DataCenter/TextDataPanel | 真实 |
| GET | `/api/v1/datacenter/train/readiness` | datacenter.py:1673 | api/datacenter.ts | DataCenter/TrainPanel | 真实（四项门禁） |
| POST | `/api/v1/datacenter/train/start` | datacenter.py:1683 | api/datacenter.ts | DataCenter/TrainPanel | 真实 |
| GET | `/api/v1/datacenter/train/status` | datacenter.py:1701 | api/datacenter.ts | **封装存在但无调用者** | 真实 | 见 ③ -A-03 |
| POST | `/api/v1/datacenter/train/cancel` | datacenter.py:1711 | api/datacenter.ts | DataCenter/TrainPanel | 真实 |

### 1.6 desk（11）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET/POST | `/api/v1/desk/kill-switch` | desk.py:54 / :74 | api/production.ts | OrderDesk | 真实 |
| GET/POST | `/api/v1/desk/exclusion` | desk.py:91 / :113 | api/production.ts | OrderDesk | 真实 |
| POST | `/api/v1/desk/exclusion/toggle` | desk.py:144 | api/production.ts | OrderDesk | 真实 |
| GET | `/api/v1/desk/exclusion/screen` | desk.py:159 | api/production.ts | OrderDesk | 真实（is_st + 成交额） |
| POST | `/api/v1/desk/orders` | desk.py:180 | api/production.ts | OrderDesk | 真实 |
| POST | `/api/v1/desk/fills/run` | desk.py:199 | api/production.ts | OrderDesk | 真实 |
| GET | `/api/v1/desk/orders` | desk.py:212 | api/production.ts | OrderDesk | 真实 |
| GET | `/api/v1/desk/account` | desk.py:256 | api/production.ts | OrderDesk | 真实 |
| GET | `/api/v1/desk/capacity` | desk.py:271 | api/production.ts | CapacityAttribution | 真实；**与 research/overview 口径并存** → ③ -D-01 |
| POST | `/api/v1/desk/attribution` | desk.py:313 | api/production.ts | CapacityAttribution | 真实 |

### 1.7 etf（8）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/etf/overview` | etf.py:542 | api/etf.ts | Etf、Topbar | 真实（可结构化降级） |
| GET | `/api/v1/etf/list` | etf.py:610 | api/etf.ts | Etf | 真实 |
| GET | `/api/v1/etf/hot` | etf.py:664 | api/etf.ts | Etf | 真实 |
| GET | `/api/v1/etf/performance` | etf.py:724 | api/etf.ts | Etf | 真实；单序列失败 `return []`（:774） |
| GET | `/api/v1/etf/scale` | etf.py:826 | api/etf.ts | Etf | **估算口径**（份额×收盘，docstring 已声明） |
| GET | `/api/v1/etf/flow` | etf.py:895 | api/etf.ts | Etf | 真实（东财主力净流入） |
| GET | `/api/v1/etf/overview/series` | etf.py:941 | api/etf.ts | Etf | 真实（kpi_series） |
| GET | `/api/v1/etf/detail/{code}` | etf.py:1218 | api/etf.ts | EtfDetail | 真实（块级并发，块超时降级） |

### 1.8 export（3）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/export/screener` | export.py:19 | api/export.ts | Screener | 真实 |
| POST | `/api/v1/export/backtest` | export.py:58 | api/export.ts | Backtest | 真实（重跑引擎） |
| POST | `/api/v1/export/strategy-backtest` | export.py:79 | api/export.ts | Backtest | 真实 |

### 1.9 market（5）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 | 备注 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/market/index/kline` | market.py:858 | api/market.ts | Screener | 真实 | |
| GET | `/api/v1/market/quotes` | market.py:895 | api/market.ts | StockDetail（绕过封装直连 :62） | 真实 | |
| GET | `/api/v1/market/overview/rt` | market.py:997 | api/market.ts | MarketOverview | 真实 | |
| GET | `/api/v1/market/overview/daily` | market.py:1073 | api/market.ts | MarketOverview、Screener | 真实 | |
| GET | `/api/v1/market/overview` | market.py:1152 | **无封装** | **无任何调用** | 真实但 **DEPRECATED** | 见 ③ -A-04 |

### 1.10 monitor（2）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/monitor/health` | monitor.py:20 | api/monitor.ts | FactorHealthCard | 真实；未运行时返回 `state=unknown`（:25-28，前端显式渲染"尚未运行"，诚实） |
| POST | `/api/v1/monitor/run` | monitor.py:35 | api/monitor.ts | FactorHealthCard | 真实 |

### 1.11 notify（3）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/notify/stream-ticket` | notify.py:76 | api/notify.ts | useNotifyStore | 真实 |
| GET | `/api/v1/notify/stream` | notify.py:84 | api/notify.ts（`streamUrl`） | useNotifyStore | 真实 SSE |
| GET | `/api/v1/notify/recent` | notify.py:157 | api/notify.ts | useNotifyStore | 真实 |

### 1.12 ops（4）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/ops/quality-scan` | ops.py:50 | api/production.ts | DataQuality | 真实 QC |
| GET | `/api/v1/ops/lineage` | ops.py:408 | api/production.ts | DataQuality | 真实（拓扑静态 + 节点状态真实扫描） |
| GET | `/api/v1/ops/dag` | ops.py:465 | api/production.ts | Pipeline | 真实（产物新鲜度推导） |
| POST | `/api/v1/ops/dag/rerun` | ops.py:538 | api/production.ts | Pipeline | 真实（FULL_STEPS） |

### 1.13 portfolio（2）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/portfolio/backtest` | portfolio.py:115 | api/portfolio.ts | Portfolio | 真实（domain/portfolio） |
| GET | `/api/v1/portfolio/search` | portfolio.py:167 | api/portfolio.ts | Portfolio | 真实；**DB 缺失/异常静默 `return []`** → ③ -B-01（P1） |

### 1.14 report（2）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/report/daily` | report.py:465 | **api/monitor.ts**（模块归属错位） | Report | 真实 |
| POST | `/api/v1/report/daily/generate` | report.py:479 | api/monitor.ts | Report | 真实 |

### 1.15 research（11）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/research/overview` | research.py:116 | api/research.ts | Research | 真实；`capacity_estimate_yi` 公式**硬编码 1%×20**（:139-148）→ ③ -D-01 |
| POST | `/api/v1/research/factor-icir` | research.py:166 | api/research.ts | Research | 真实 |
| POST | `/api/v1/research/factor-corr` | research.py:193 | api/research.ts | Research | 真实 |
| POST | `/api/v1/research/factor-quantile` | research.py:214 | api/research.ts | Research | 真实 |
| GET | `/api/v1/research/experiments` | research.py:230 | api/research.ts | Research | 真实 |
| POST | `/api/v1/research/cv-folds` | research.py:286 | api/research.ts | Research | 真实 |
| GET | `/api/v1/research/feature-importance` | research.py:342 | api/research.ts | Research | 真实 |
| POST | `/api/v1/research/optimize` | research.py:400 | api/research.ts | Research | 真实 |
| POST | `/api/v1/research/impact-sim` | research.py:482 | api/research.ts | Research | 真实；**第三份冲击模型** → ③ -D-02 |
| POST | `/api/v1/research/stress-test` | research.py:534 | api/research.ts | Research | 真实 |
| GET | `/api/v1/research/lab/yearly` | research.py:614 | api/research.ts | Research | 真实 |

### 1.16 screener（4）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 | 备注 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/screener/watchlist` | screener.py:508 | api/screener.ts | Screener | 真实 | **第二套自选实现** → ③ -D-03 |
| GET | `/api/v1/screener` | screener.py:524 | api/screener.ts | Screener | 真实 | 智能选股榜单 |
| GET | `/api/v1/screener/stats/series` | screener.py:605 | api/screener.ts | Screener | 真实（kpi_series） | |
| GET | `/api/v1/screener/stocks` | screener.py:703 | api/screener.ts | Screener/StockList | 真实；降级时整表切本地口径并标 `degraded` | 语义与 `/screener` 完全不同 → ③ -C-01 |

### 1.17 stock（5）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/stock/search` | stock.py:79 | api/stock.ts | Topbar、StockDetail | 真实 |
| GET | `/api/v1/stock/{symbol}/profile` | stock.py:108 | api/stock.ts | StockDetail | 真实 |
| GET | `/api/v1/stock/{symbol}/kline` | stock.py:152 | api/stock.ts | StockDetail | 真实 |
| GET | `/api/v1/stock/{symbol}/predict` | stock.py:202 | api/stock.ts | StockDetail | 真实；`confidence` 为**模型级占位**（ml/predict.py:10 已声明） |
| GET | `/api/v1/stock/{symbol}/panels` | stock.py:258 | api/stock.ts | StockDetail | 真实（块级 status） |

### 1.18 studio（8）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| POST | `/api/v1/studio/mining/start` | studio.py:68 | api/production.ts | FactorStudio | 真实（GP 进化） |
| GET | `/api/v1/studio/mining/status/{task_id}` | studio.py:92 | api/production.ts | FactorStudio | 真实 |
| POST | `/api/v1/studio/mining/cancel/{task_id}` | studio.py:117 | api/production.ts | FactorStudio | 真实 |
| POST | `/api/v1/studio/nl-to-factor` | studio.py:194 | **api/monitor.ts**（模块归属错位） | FactorStudio/NlFactorCard | 真实（LLM 产 DSL + AST 白名单） |
| POST | `/api/v1/studio/alpha-eval` | studio.py:244 | api/production.ts | FactorStudio | 真实 |
| GET | `/api/v1/studio/factors` | studio.py:299 | api/production.ts | FactorStudio/FactorLab | 真实 |
| POST | `/api/v1/studio/factors` | studio.py:321 | api/production.ts | FactorStudio/FactorLab | 真实 |
| DELETE | `/api/v1/studio/factors/{factor_id}` | studio.py:385 | api/production.ts | FactorStudio/FactorLab | 真实 |
| POST | `/api/v1/studio/factor-report` | studio.py:415 | api/production.ts | FactorStudio/FactorLab | 真实 |

### 1.19 watchlist（2）

| Method | 路径 | 位置 | 前端封装 | 消费入口 | 数据源判定 |
|---|---|---|---|---|---|
| GET | `/api/v1/watchlist/dashboard` | watchlist.py:270 | api/watchlist.ts | Watchlist | 真实；超时降级为全 `unavailable`（:364-386，诚实） |
| GET | `/api/v1/watchlist/correlation` | watchlist.py:395 | api/watchlist.ts | Watchlist | 真实 |

### 1.20 根端点（不在 `/api/v1`）

| Method | 路径 | 位置 | 消费方 | 说明 |
|---|---|---|---|---|
| GET | `/health` | main.py:402 | nginx / Docker（非 SPA） | `frontend/nginx.conf:114` 反代 |
| GET | `/health/ready` | main.py:451 | K8s readiness | 未就绪返回 HTTP 503（main.py:483） |
| GET | `/health/live` | main.py:560 | Docker HEALTHCHECK（`frontend/Dockerfile:33` → `/healthz` → `/health/live`） | |
| GET | `/metrics` | main.py:438 | Prometheus；`include_in_schema=False` | prod 默认需 Bearer |
| GET | `/` | main.py:417 | — | 提示文案 |

---

## ② 前端模块全景表

### 2.1 页面（19）

| 页面 | 目录 | 主要消费端点 | 备注 |
|---|---|---|---|
| 市场概览 | `pages/MarketOverview` | `market/overview/rt`、`market/overview/daily`、`market/quotes` | **绕过 `api/market.ts` 封装，直接用 `useApi('/api/v1/...')` 字面量**（index.tsx:43/:50/:69） |
| 自选池 | `pages/Watchlist` | `watchlist/dashboard`、`watchlist/correlation` | 分组存 localStorage（`useWatchlistStore.ts:63`）；首访 SEED 写死 8 只标的（index.tsx:32-36） |
| 个股分析 | `pages/StockDetail` | `stock/{sym}/profile|kline|predict|panels`、`market/quotes`、`alerts/*` | `market/quotes` 亦为字面量直连（:62） |
| 选股中心 | `pages/Screener` | `screener`、`screener/stocks`、`screener/stats/series`、`screener/watchlist`、`market/overview/daily`、`market/index/kline`、`export/screener` | 一个页面消费 4 个后端模块 |
| ETF 中心 | `pages/Etf` | `etf/overview|list|hot|performance|scale|flow|overview/series`、`export/screener` | |
| ETF 分析 | `pages/EtfDetail` | `etf/detail/{code}` | |
| 因子实验室 | `pages/FactorStudio` | `studio/mining/*`、`studio/factors*`、`studio/alpha-eval`、`studio/factor-report`、`studio/nl-to-factor` | |
| 回测中心 | `pages/Backtest` | `backtest/run`、`backtest/strategy-run`、`backtest/signal-analysis`、`datacenter/overview|datasets|quality`、`export/*` | |
| 组合回测 | `pages/Portfolio` | `portfolio/backtest`、`portfolio/search` | |
| 容量与归因 | `pages/CapacityAttribution` | `desk/capacity`、`desk/attribution` | |
| 订单台 | `pages/OrderDesk` | `desk/*`（11 个中的 10 个） | |
| 研究中心 | `pages/Research` | `research/*`（11 个全量） | |
| 数据中心 | `pages/DataCenter` | `datacenter/*`（21 个中的 20 个） | 7 端点 `Promise.allSettled` + 逐面板错误收集（index.tsx:491-536） |
| 数据质量 | `pages/DataQuality` | `ops/quality-scan`、`ops/lineage` | |
| 流水线 | `pages/Pipeline` | `ops/dag`、`ops/dag/rerun` | |
| 预警中心 | `pages/Alerts` | `alerts/*`（全量） | |
| AI 日报 | `pages/Report` | `report/daily`、`report/daily/generate` | |
| 设置 | `pages/Settings` | `settings*`、`datacenter/overview`、`datacenter/sync` | **引擎参数区无后端消费者** |
| 登录/注册 | `pages/Login` | `auth/login`、`auth/register`、`auth/register/status` | |

### 2.2 组件 / hooks / stores / api

| 层 | 模块 | 说明 |
|---|---|---|
| components | Topbar、Sidebar、AuthBootstrap、RequireAuth、DataFreshness、FactorHealthCard、HotkeyHelp、ResearchDisclaimer、ErrorBoundary | `FactorHealthCard` 是 `monitor/*` 的唯一消费方；`Sidebar` 写死 `DEFAULT_ETF_CODE='510300'`（:266） |
| components/charts | KLineChart、KpiBits、MarketHeatmap、Sparkline | 纯展示；`MarketHeatmap` 对 `heat.up/down/flat` 用 `?? 0`（:31-33） |
| hooks | useTaskPolling、useAbortableTask、useRefreshInterval、useHotkeys、useMarketSession、useTheme、useWatchlistQuotes | `useWatchlistQuotes` 被 Screener 与 Watchlist 共用，但注入的 fetcher 指向**两个不同端点** |
| stores | useAuthStore、useNotifyStore、usePreferencesStore、useUiStore、useWatchlistStore | 仅 `useWatchlistStore` 是纯本地（localStorage），无后端对应 |
| api | alerts、auth、backtest、datacenter、etf、export、market、monitor、notify、portfolio、production、research、screener、settings、stock、strategyBacktest、watchlist、swr、client、timeouts（+1 test） | **`production.ts` 一个文件封装 3 个后端模块（ops/desk/studio）；`monitor.ts` 封装 3 个（monitor/report/studio·nl）** |
| types | api、stock、etf、kpi、p1、portfolio、datacenter、watchlist | 类型与后端字段对齐度较高，注释中大量"禁止 `?? 0`"约束 |

---

## ③ 对应矩阵与缺口清单

### A 类：后端有端点，前端未消费 / 消费为零（4 条）

#### A-01 【P0】`watchlist` 表零写入 —— "我的自选"预警规则永远作用不到任何标的

- **现象**：后端 `Watchlist` 表（`backend/app/db/models.py:78-93`）在整个后端**只有一处读、零处写**；前端自选分组完全存 localStorage。用户在自选池里增删标的，与预警规则的 `scope=watchlist` 集合**永久脱节**。
- **证据**：
  - 唯一读取点：`backend/app/api/v1/alerts.py:304` `select(Watchlist.symbol)`
  - 全仓 `Watchlist` 命中仅 3 处（models.py:78 定义、alerts.py:36 import、alerts.py:304 读取）
  - 无写入端点：后端 115 个端点中无任何 `POST/PUT/DELETE /watchlist/*`
  - 空表静默：`backend/app/api/v1/alerts.py:301-306` —— `scope=="watchlist"` 时查表，无结果走 `:306 return []`，**无 logger.warning**
  - 前端 UI 暴露该选项：`frontend/src/pages/Alerts/index.tsx:29` `watchlist: '我的自选'`、`:256 <option value="watchlist">`
  - 前端自选存本地：`frontend/src/stores/useWatchlistStore.ts:63` `{ name: 'aqp-watchlist' }`
- **影响范围**：预警中心「作用范围=我的自选」的全部规则类型（price / kline / factor_quantile / data_health）均静默失效；用户看到"规则已创建且启用"，但永远不触发事件，且**无任何提示**。
- **严重度**：**P0**（功能不可用 + 用户无感知）

#### A-02 【P0】`PUT /settings/engine` 是"只写不读"的死控件

- **现象**：设置页的「量化引擎」区（引擎名 / 风险指标 / 佣金率 / 滑点）可保存并返回"引擎参数已保存"，但后端**没有任何代码读取这些值**；回测的摩擦参数走的是**每次请求随请求体传入**的另一套字段（且单位不同：`pct` vs `bps`）。
- **证据**：
  - 写入：`backend/app/api/v1/app_settings.py:213-223`（`put_engine` → `data["engine"][field] = value` → `_save_settings`）
  - 默认值定义：`backend/app/api/v1/app_settings.py:64-65`（`commission_pct: 0.03`、`slippage_pct: 0.1`）
  - **全后端 grep `slippage_pct|commission_pct` 仅命中 app_settings.py:64/65/209/210（定义与入参），无任何消费点**
  - 真实回测入口用的是另一套字段：`backend/app/api/v1/backtest.py:54` `slippage_bps: float = Field(5.0)`、`:59-60` `impact_model` / `impact_sqrt_coef_bps`；`BacktestRequest`（backtest.py:46-80）**根本没有 commission 字段**
  - 实际生效的佣金是**模块级常量**：`backend/app/domain/trading_rules.py:25` `COMMISSION_RATE_DEFAULT = 0.0003`，被 `backtest/engine.py:360/536`、`broker.py:109`、`ma_cross.py:56`、`strategy_base.py:308`、`trading/paper.py:299`、`domain/portfolio.py:401/422/429` 直接引用 —— 设置页的 `commission_pct: 0.03`（即 0.03% = 0.0003）**数值恰好等价却完全不连通**，极具迷惑性
  - `risk_indicators` / `engine.name` 同样零消费（全仓 grep 仅 app_settings.py:208 与前端 Settings 渲染）
  - 前端 UI：`frontend/src/pages/Settings/index.tsx:535-543`（佣金率 / 滑点输入框）、`:248` `settingsApi.saveEngine(engine)`
- **影响范围**：用户在设置页调整佣金/滑点后，回测结果**完全不变**。用户会以为"自定义费率下的回测结果"成立 → 基于错误前提做决策。
- **严重度**：**P0**（静默忽略用户输入 = 错误数据认知且无感知）

#### A-03 【P2】`datacenterApi.trainStatus` 封装无人调用（页面绕过封装直连）

- **证据**：`frontend/src/api/datacenter.ts:118` 定义 `trainStatus`，而 `frontend/src/pages/DataCenter/TrainPanel.tsx:44` 用 `useTaskPolling<TrainStatus>('/api/v1/datacenter/train/status')` 直连字面量。
- **影响**：同一端点两条调用路径，超时/重试策略不一致（`useTaskPolling` 默认 2s 轮询，封装走 `TIMEOUT.heavy`）。
- **严重度**：P2

#### A-04 【P2】`GET /api/v1/market/overview` 僵尸端点（已有 DEPRECATED 声明但未在 OpenAPI 标注）

- **证据**：`backend/app/api/v1/market.py:1162` docstring 明确写"⚠️ DEPRECATED（2026-09-27 审计）…前端早已改用拆分块"，但：
  - 装饰器仍是 `@router.get("/overview", ...)`（market.py:1151），**未加 `deprecated=True`**
  - 前端 `frontend/src/api/market.ts:61-86` 的 `marketApi` 只有 `overviewRt` / `overviewDaily` / `indexKline` / `quotes`，**无 overview 封装**
  - 全前端 grep `market/overview` 仅命中 `/rt`、`/daily` 与 `types/stock.ts:513` 的类型注释
- **影响**：保留理由是"内部 `_build_overview` 被 `warm_overview_cache` 复用"（market.py:1164-1168）——即**用 HTTP 端点承载内部预热入口**，端点与内部函数职责混用；且 `/openapi.json` 仍把该端点输出给调用方。
- **严重度**：P2（有意为之，但建议下沉为内部函数 + 加 `deprecated=True`，或抽出 `warm_overview_cache` 直接调 `_build_overview`）

### B 类：前端有模块但后端无支撑（1 条）

#### B-01 【P1】`/portfolio/search` 在 SQLite 不可用时静默返回空数组

- **现象**：资产搜索在「DB 文件不存在」与「SQLite/OSError 异常」两种情况下都返回 `[]` 并 HTTP 200，前端表现为"没有匹配的资产"。
- **证据**：
  - `backend/app/api/v1/portfolio.py:70-71` `if not db_path.exists(): return []`（**连日志都没有**）
  - `backend/app/api/v1/portfolio.py:100-102` `except (sqlite3.Error, OSError) as exc: logger.warning(...); return []`（仅日志，响应体无任何降级标记）
  - 响应体只有 `ok(results)`（portfolio.py:182），**无 `status` / `degraded` 字段**
  - 前端：`frontend/src/api/portfolio.ts:14` 直接 `get<AssetSearchItem[]>`，页面按空数组渲染"无结果"（`pages/Portfolio/index.tsx`）
- **影响范围**：DB 挂载失败 / 只读 / 锁冲突时，组合同步构建页的资产搜索框表现为"查无此股"，用户会以为是标的未上市。
- **严重度**：**P1**（把基础设施故障伪装成"没数据"）

> 说明：除 B-01 外，**未发现前端调用不存在端点**的情况（133 处字面量全部命中后端路由表，见 `_scripts/fe_paths.txt`，仅 4 条注释/测试误报）。

### C 类：前后端模块划分 / 命名不一致（4 条）

#### C-01 【P1】`/screener` 前缀下塞了三个语义无关的能力

- **证据**：`backend/app/api/v1/screener.py` 同时提供
  - `GET /screener`（:524）—— 智能选股榜单（ML score 排序 Top-K）
  - `GET /screener/stocks`（:703）—— 全市场在册证券列表（服务端排序分页，与"选股"无关）
  - `GET /screener/watchlist`（:508）—— 自选股行情快照（与"选股"无关）
- 而真正的"自选"主能力在 `backend/app/api/v1/watchlist.py:270` `GET /watchlist/dashboard`。
- **影响**：前端 `pages/Screener/index.tsx` 一个页面要理解三种语义；`useWatchlistQuotes`（`frontend/src/hooks/useWatchlistQuotes.ts:5-8`）的注释本身就承认"接口层保留两套领域端点"，需要靠注释解释。
- **严重度**：P1（可维护性 + 新人误用风险；`watchlist` 能力分散在两个后端模块是 C/D 交叉问题）

#### C-02 【P2】前端 api 封装模块与后端模块不是 1:1

- **证据**：
  - `frontend/src/api/production.ts` 同时封装 `/ops/*`（:176-191）、`/desk/*`（:253-298）、`/studio/*`（:44-70）—— 对应 3 个后端模块
  - `frontend/src/api/monitor.ts` 同时封装 `/monitor/*`（:118-120）、`/report/daily*`（:123-125）、`/studio/nl-to-factor`（:127）—— 跨 3 个后端模块
  - `frontend/src/api/market.ts` 缺 `/market/overview` 封装
  - 后端有 `watchlist.py` / `screener.py` / `stock.py` / `etf.py` 等 20 个模块，前端只有 18 个 api 文件
- **严重度**：P2

#### C-03 【P2】"默认 ETF 标的"在三处各写一份，仅靠注释同步

- **证据**：
  - 后端 `backend/app/api/v1/etf.py:77` `DEFAULT_PERF = "510300,510500,159915,513500,512100"`
  - 前端 `frontend/src/components/Sidebar.tsx:266` `const DEFAULT_ETF_CODE = '510300'`
  - 前端 `frontend/src/pages/Watchlist/index.tsx:33` SEED 含 `510300`
  - 三处均靠注释互指（`Sidebar.tsx:259-261`、`:299-302`）
- **严重度**：P2（无单一数据源，改一处静默漂移）

#### C-04 【P2】页面绕过 api 层直接用 URL 字面量

- **证据**：`frontend/src/pages/MarketOverview/index.tsx:43/:50/:69`、`frontend/src/pages/StockDetail/index.tsx:62`、`frontend/src/pages/DataCenter/TrainPanel.tsx:44`
- **影响**：这 4 处不享受 `api/*.ts` 里的 timeout 分级（如 `OVERVIEW_TIMEOUT = TIMEOUT.heavy`），`StockDetail:62` 的 `market/quotes` 用的是封装里的 20s，页面直连会落回 `client.ts:154` 的 15s 默认。
- **严重度**：P2

### D 类：重复实现（4 条）

#### D-01 【P1】"策略容量"两套公式并存，前端两页各显示一个数

- **证据**：
  - 口径 A：`backend/app/api/v1/research.py:139-148` —— `med_amt × 0.01 × 20`，返回 `capacity_estimate_yi`，`capacity_formula = "universe日成交额中位数 × 1%参与率 × 20只持仓"`（**硬编码，不可调**）
  - 口径 B：`backend/app/api/v1/desk.py:270-294` → `domain/attribution.py:122-146` `strategy_capacity(adv_median, participation_cap, holdings, rebalance_per_year)`，公式含 `÷ 调仓换手比例`（attribution.py:138-139），**参数可由请求调整**
  - 字段命名也不一致：`capacity_estimate_yi` vs `aum_threshold` / `aum_yi`
  - 前端两个消费者互不感知：`pages/Research/index.tsx:372-373`（显示 A 的数值 + 公式串）、`pages/CapacityAttribution/index.tsx:140`（显示 B 的 `capacity.formula`）
- **影响**：默认参数下两者数值恰好相同（`turnover_ratio = min(1.0, 12/12) = 1.0`），但用户在容量页改了参与率/持仓数/年调仓次数后，**研究中心顶栏的"策略容量"仍是老数字**，两页同时可见、无交叉标注。
- **严重度**：**P1**（口径存疑）

#### D-02 【P1】平方根冲击模型写了三份，常量各写一份且边界处理不一致

- **证据**：
  - `backend/app/backtest/broker.py:64` `impact_sqrt_coef_bps: float = 10.0`（回测引擎；默认 `impact_model = "linear"`，broker.py:63）
  - `backend/app/trading/paper.py:38` `IMPACT_COEF_BPS = 10.0`；`:291` `math.sqrt(min(part, 1.0))` —— **对参与率做了 ≤1 的 clamp**
  - `backend/app/domain/research.py:232` `impact_coef_bps: float = 10.0`；`:270` `np.sqrt(part)` —— **没有 clamp**
- **影响**：三处常量独立维护（没有共享定义），任一侧调参都会造成"回测摩擦成本 / 订单台成交冲击 / 研究页冲击曲线"三者口径漂移；`part > 1` 时 research 侧冲击成本会被高估。
- **严重度**：**P1**

#### D-03 【P1】自选行情两套实现，各自算一遍 close/pct/amount

- **证据**：
  - `backend/app/api/v1/screener.py:100` `_watchlist_quotes()`（供 `GET /screener/watchlist`）—— 自带预测分 / `signal_strength` / 行业
  - `backend/app/api/v1/watchlist.py:120` `_bars_for()` + `:65 _kline_state()` + `:178 _batch_valuation()` + `_fund_flow_total()`（供 `GET /watchlist/dashboard`）—— 自带 PE/PB/迷你 K/形态/资金流
  - 重复的取数基础：两边都读 `daily_bar` 最新 bar 算 `close / pct / amount`
  - 连"加载标的名称"都各写一份：`screener.py:49 _load_instrument_info()` vs `watchlist.py:165 _load_instrument_names()`
  - 单位口径曾在两侧不一致（watchlist.py:296-302 的注释记录了"旧实现固定 ×100 对 daily_bar 路径会放大 100 倍"）
- **缓解**：`frontend/src/hooks/useWatchlistQuotes.ts:4-8` 已把"两套端点"作为有意设计记录（字段互补，合并会丢字段）。
- **严重度**：P1（口径漂移高风险；单位/缺失处理逻辑没有共享）

#### D-04 【P2】绩效指标与年度化常量多处重复定义

- **证据**：
  - `backend/app/backtest/strategy_base.py:263-297` 与 `backend/app/backtest/ma_cross.py:255-290` 各自算一遍 `alpha / beta / sortino / info_ratio / annual_return`（strategy_base.py:263 注释自陈"与 ma_cross 口径一致"，即靠注释维持一致）
  - `TRADING_DAYS = 252` 在 7 个模块各写一份：`backtest/ma_cross.py:38`、`backtest/strategy_base.py:41`、`ml/signal_analysis.py:16`、`domain/optimizer.py:18`、`domain/research.py:22`，另加 `domain/chip.py:26`、`domain/metrics.py:21`、`domain/risk.py:26` 的 `TRADING_DAYS_PER_YEAR = 252`
- **严重度**：P2

---

## ④ 前端兜底 / 降级掩盖问题

> 扫描 203 处候选（`_scripts/fe_fallback.csv`），逐条判读后**大部分是注释中的"禁止 `?? 0`"反例**（团队已建立明确纪律，如 `Backtest/index.tsx:170`、`Etf/index.tsx:676`、`Watchlist/index.tsx:334-345`、`StockDetail/index.tsx:807/834/926`、`BreadthPanel.tsx:101`）。以下为**真实的掩盖点**。

| # | 位置 | 现象 | 影响 | 严重度 |
|---|---|---|---|---|
| F-01 | `frontend/src/pages/EtfDetail/index.tsx:109,118` | `const ratio = it.ratio ?? 0;` 随后 `{ratio.toFixed(2)}%` —— 持仓占比未知渲染成 **"0.00%"**；同一行的 `mv_yi` 却显示 `—` | 同一条记录内两个字段的缺失处理不一致；用户读到"占比 0.00%"而非"未知" | **P1** |
| F-02 | `frontend/src/pages/MarketOverview/BreadthPanel.tsx:72` | `Donut` 组件仍用 `heat.up ?? 0, heat.down ?? 0, heat.flat ?? 0` | 与同文件 :104-108 已修正的 `up/down → null`（渲染 `—`）**口径相反**：文字显示"红盘 —"，环形图显示 0/0/0 的空饼 | **P1** |
| F-03 | `frontend/src/components/charts/MarketHeatmap.tsx:31-33` | `value: heat.up ?? 0` 等三处 | 涨跌家数未知时饼图三个扇区全 0（ECharts 渲染空白），用户看到空白图表而非"无数据"提示 | P2 |
| F-04 | `frontend/src/pages/EtfDetail/index.tsx:137` | `.map((it) => ({ value: it.ratio ?? 0 }))` 后 `.filter((d) => d.value > 0)`（:138） | 与 F-01 相反：饼图丢弃 null，列表显示 0.00% —— **同一字段两种缺失语义** | P2 |
| F-05 | `frontend/src/pages/StockDetail/index.tsx:551` | `fill={Math.min(100, Math.abs(mainRatio ?? 0) / FULL_SCALE_PCT * 100)}` | `mainRatio`（占成交额比）为 null 时进度条长度 0，而同格数值正常显示金额 → 视觉上像"占比为 0" | P2 |
| F-06 | `frontend/src/pages/DataCenter/index.tsx:505-508` | `if (results[i].status === 'fulfilled') setXxx(...)` | rejected 的面板**保留上一次成功的旧值**，仅靠 `setPanelErrors` 横幅提示（:534-536） | P2（有横幅，不算完全静默） |
| F-07 | 全局 | 前端**无** mock 数据文件、无 `MOCK_*` 常量（grep `mock|MOCK|fake|DEMO` 仅命中 vitest 测试桩 `pages/Etf/FilterPanel.test.tsx:58/73`、`utils/useChart.test.tsx:36`） | 生产代码零 mock，良好 | — |

**结论**：前端的"未知值纪律"整体执行得相当好（大量显式 `!= null ? … : '—'` 与"禁止 `?? 0`"注释），**残余问题集中在 ETF 详情页与图表组件**（F-01 ~ F-05），共同模式是「数值文本层已修、图形层没跟上」。

---

## ⑤ 架构层面的系统性风险

### R1 【高】业务逻辑大量驻留在路由层，`services/` 层名存实亡

- 证据：`backend/app/services/` 仅 5 个文件（`task_store`、`stats_cache`、`market_service`、`sync_service`、`__init__`），而 `backend/app/api/v1/` 合计 **11,567 行**，其中 `datacenter.py` 1794 行、`market.py` 1379 行、`etf.py` 1285 行、`backtest.py` 1001 行、`screener.py` 793 行、`alerts.py` 759 行。
- grep `from ...services` 在 `api/v1` 下仅命中 3 处（`datacenter.py:61/62/83`、`market.py:41`）；`from ...domain` / `...backtest` / `...trading` 合计约 20 处。
- 后果：同一业务概念的实现分散在「路由文件内的私有函数」与「domain 层」两侧（如自选行情 D-03、容量 D-01、冲击 D-02），**没有强制的单一实现入口**，重复实现只能靠注释约束。

### R2 【高】"配置" 与 "执行参数" 双轨制，配置轨无消费者

- `/settings/engine`（持久化配置）与 `BacktestRequest`/`StrategyBacktestRequest`（请求体参数）是两套互不相干的参数体系，且**单位都不同**（pct vs bps）。
- 同类：`/settings/preferences.refresh_freq` 被 `useRefreshInterval` 消费（有效），而 `engine.*` 全无效 —— 用户无法从 UI 区分哪些设置"真的生效"。

### R3 【中高】本地状态与服务端状态混用，缺同步契约

- 自选股分组纯 localStorage（`useWatchlistStore.ts:63`），服务端 `watchlist` 表另有其主（且为空），二者无同步（A-01）。
- 自选看板首访 SEED 写死 8 只标的（`pages/Watchlist/index.tsx:32-36`），用户删掉后 `SEED_FLAG` 阻止再次播种，但服务端永远不知道用户的自选集合 → 任何需要"服务端知道自选"的能力（预警、日报、组合导入）都不可用。

### R4 【中】端点命名与领域边界错位已形成惯性

- `/screener/stocks`（全市场列表）/ `/screener/watchlist`（自选快照）挂在"选股"前缀下（C-01）；
- `/studio/nl-to-factor` 被前端塞进 `api/monitor.ts`（C-02）；
- `/market/overview` 以 HTTP 端点身份承担内部预热入口（A-04）。
- 后果：新增能力时"放哪个模块"没有可判定的规则，僵尸端点与重复实现会持续累积。

### R5 【中】跨模块常量无单一数据源

- 冲击系数 10.0 ×3（D-02）、`TRADING_DAYS=252` ×7（D-04）、默认 ETF `510300` ×3（C-03）、`COMMISSION_RATE_DEFAULT`（`domain/trading_rules.py`，被 `backtest.py:41` 引入）与 `settings.engine.commission_pct` 并存。
- 这些常量一旦需要调整，必然出现"改了一处漏了两处"的口径漂移。

### R6 【低-中】前端部分页面绕过 api 层（C-04）

- 4 处直接 URL 字面量，绕开了 `api/*.ts` 中集中维护的 timeout 分级（`api/timeouts.ts`）与 `LONG_PATHS` 白名单（`api/timeouts.ts:77`），长请求有落回 15s 默认超时的风险。

---

## 附录 A：临时脚本清单（可复跑）

| 脚本 | 作用 | 输出 |
|---|---|---|
| `_scripts/extract_routes.py` | AST 解析后端全部 v1 路由 | `_scripts/routes.csv`（115 条） |
| `_scripts/extract_fe_calls.py` | 扫描前端 `/api/v1/*` 字面量 | `_scripts/fe_calls.csv`（133 条） |
| `_scripts/match_matrix.py` | 后端↔前端路径严格匹配 | `_scripts/matrix2.txt` |
| `_scripts/validate_fe_paths.py` | 前端路径反查后端（B 类） | `_scripts/fe_paths.txt`（0 条真实缺口） |
| `_scripts/unused_api.py` | api 封装符号是否被页面引用 | `_scripts/unused_api2.txt` |
| `_scripts/fe_fallback.py` | 兜底 / mock 扫描 | `_scripts/fe_fallback.csv`（203 条候选） |
| `_scripts/short_handlers.py` | 路由处理器体量（筛空壳） | `_scripts/handlers.csv` |
| `_scripts/counts.py` / `line_counts.py` | 模块与行数统计 | `_scripts/counts.txt` / `lines.txt` |
| `_scripts/gen_tables.py` | 生成 ① 表 | `_scripts/tables.md` |

复跑方式（PowerShell）：

```powershell
cd 'D:\Python_Project\Alpha Quant Platform\docs\audit\2026-10-03'
$env:PYTHONIOENCODING='utf-8'
python _scripts\extract_routes.py _scripts\routes.csv
python _scripts\match_matrix.py _scripts\routes.csv
```

## 附录 B：缺口汇总计数

| 类别 | 条数 | P0 | P1 | P2 |
|---|---|---|---|---|
| A 类（后端有 / 前端未消费或消费为零） | 4 | **2**（A-01、A-02） | 0 | 2（A-03、A-04） |
| B 类（前端有 / 后端无支撑） | 1 | 0 | **1**（B-01） | 0 |
| C 类（命名 / 模块划分不一致） | 4 | 0 | **1**（C-01） | 3（C-02、C-03、C-04） |
| D 类（重复实现） | 4 | 0 | **3**（D-01、D-02、D-03） | 1（D-04） |
| F 类（前端兜底掩盖） | 5 | 0 | **2**（F-01、F-02） | 3（F-03、F-04、F-05） |
| **合计** | **18** | **2** | **7** | **9** |

**P0 清单（按处置优先级）**

1. **A-01** —— `backend/app/db/models.py:78` `Watchlist` 表零写入；`backend/app/api/v1/alerts.py:304` 唯一读取、`:306` 空集合静默 `return []`；前端 `frontend/src/stores/useWatchlistStore.ts:63` 自选存 localStorage。
2. **A-02** —— `backend/app/api/v1/app_settings.py:213-223` `put_engine` 写入的 `commission_pct` / `slippage_pct` / `risk_indicators` / `name` 全后端零消费者；真实回测参数在 `backend/app/api/v1/backtest.py:54/59/60` 随请求传入。
