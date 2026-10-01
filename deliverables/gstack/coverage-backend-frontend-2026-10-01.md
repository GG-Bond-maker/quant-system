# AQP 后端 → 前端 功能覆盖度矩阵

> 审计日期：2026-10-01　审计员：coverage-auditor
> 范围：`backend/app/api/v1/*.py`（19 路由模块 / **115 个端点**） × `frontend/src`（全量路径字面量扫描）
> 方法：AST 无关的文本提取 —— 后端正则抽 `@router.<method>("<path>")` 并结合 `router.py` 的 `include_router(prefix=...)` 还原完整路径；前端扫描**全 `src`** 所有 `/api/v1/...` 字符串字面量（含反引号模板串、`${...}` 变量、`encodeURIComponent`、`download()`/`del()` 二进制下载、`EventSource` SSE）。再做「端点 ↔ 前端引用 ↔ 页面消费」三层交叉验证。

---

## 0. 判定口径

| 状态 | 定义 |
|---|---|
| **已覆盖** | 前端有可点击入口，且页面真的取用/渲染该端点数据 |
| **部分覆盖** | 有入口但只展示部分能力；或功能可达但缺导航入口（只能手输 URL） |
| **缺失** | 前端无任何调用 / 页面 / 展示 |
| **内部端点(无需前端)** | 运维 / 内部 / 已被取代的端点，本就不该有前端入口 |

> 🔴 **纪律**：「注释里声明了入口」**不等于**「真的链过去了」。本次所有「已覆盖」判定均基于可 grep 的 `<Link to=…>` / `navigate(…)` / 导航项 / API 调用实证，不采纳任何注释声明。

---

## 1. 覆盖度汇总

| 状态 | 数量 | 占比 |
|---|---:|---:|
| 已覆盖 | **114** | 99.1% |
| 部分覆盖 | **0** | 0% |
| 缺失 | **0** | 0% |
| 内部端点(无需前端) | **1** | 0.9% |
| **合计** | **115** | 100% |

**幽灵调用（前端调了但后端没有）：0 个。**
前端共 **119 处** `/api/v1` 路径字面量（去重后 **114 个不同路径**，含 **8 处模板字符串**如 `` `/api/v1/etf/detail/${code}` ``），100% 命中后端真实路由。

> ⚠️ **口径澄清**：报告前言若出现「159 处 @ 130 个不同路径」的表述，系**早期口径污染，已废弃**。权威口径为 **119 处字面量 / 114 个去重路径**。差异来源：早期把 `api/` 目录外的重复引用与类型定义中的字符串一并计入，且未排除 `client.ts`/`swr.ts` 基础设施内的示例串。请以本表为准。

---

## 2. 逐模块覆盖度矩阵

### auth（4/4 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| POST /api/v1/auth/login | auth.ts:44 | Login/index.tsx:92 | 已覆盖 | |
| GET /api/v1/auth/me | auth.ts:57 | AuthBootstrap.tsx:35 | 已覆盖 | |
| POST /api/v1/auth/register | auth.ts:51 | Login/index.tsx:93 | 已覆盖 | |
| GET /api/v1/auth/register/status | auth.ts:54 | Login/index.tsx:41 | 已覆盖 | |

### alerts（7/7 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/alerts/rules | alerts.ts:71 | Alerts/index.tsx:387 | 已覆盖 | |
| POST /api/v1/alerts/rules | alerts.ts:77 | Alerts/index.tsx:436 | 已覆盖 | |
| PUT /api/v1/alerts/rules/{rule_id} | alerts.ts:80 | Alerts/index.tsx:451 | 已覆盖 | |
| DELETE /api/v1/alerts/rules/{rule_id} | alerts.ts:82 | Alerts/index.tsx:478 | 已覆盖 | |
| GET /api/v1/alerts/events | alerts.ts:85 | Alerts/index.tsx:377 | 已覆盖 | |
| POST /api/v1/alerts/events/read | alerts.ts:88 | Topbar.tsx:90 | 已覆盖 | 含 markAllRead（all:true） |
| GET /api/v1/alerts/health | alerts.ts:74 | FactorHealthCard.tsx:29 | 已覆盖 | |

### market（4/5 已覆盖，1 内部端点）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/market/overview/rt | market.ts:56 | MarketOverview/index.tsx:71 | 已覆盖 | |
| GET /api/v1/market/overview/daily | market.ts:62 | Screener/index.tsx:215 | 已覆盖 | |
| GET /api/v1/market/quotes | market.ts:76 | StockDetail/index.tsx:55 | 已覆盖 | |
| GET /api/v1/market/index/kline | market.ts:71 | Screener/index.tsx:306 | 已覆盖 | |
| **GET /api/v1/market/overview（裸）** | **无** | **无** | **内部端点(无需前端)** | 老的大一统聚合（指数+资金+异动+ML推荐），已被 `/overview/rt`+`/overview/daily` 取代；前端零调用。**P2 技术债**，功能无损失 |

### notify（3/3 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/notify/recent | notify.ts:26 | useNotifyStore.ts:66 | 已覆盖 | |
| POST /api/v1/notify/stream-ticket | notify.ts:28 | useNotifyStore.ts:97 | 已覆盖 | |
| GET /api/v1/notify/stream | notify.ts:31 `streamUrl()` | useNotifyStore.ts:102 `new EventSource(...)` | 已覆盖 | **SSE，非 axios**；正则需扫 `EventSource` 才不漏 |

### stock（5/5 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/stock/search | stock.ts:17 | Topbar.tsx:160 | 已覆盖 | |
| GET /api/v1/stock/{symbol}/profile | stock.ts:20 | StockDetail 页 | 已覆盖 | 路径含 `encodeURIComponent`，正则需覆盖 |
| GET /api/v1/stock/{symbol}/kline | stock.ts:23 | StockDetail 页 | 已覆盖 | 同上 |
| GET /api/v1/stock/{symbol}/predict | stock.ts:31 | StockDetail 页 | 已覆盖 | 同上 |
| GET /api/v1/stock/{symbol}/panels | stock.ts:40 | StockDetail 页 | 已覆盖 | 同上 |

### etf（8/8 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/etf/overview | etf.ts:12 | Etf/index.tsx:205 | 已覆盖 | |
| GET /api/v1/etf/overview/series | etf.ts:22 | Etf/index.tsx:357 | 已覆盖 | |
| GET /api/v1/etf/list | etf.ts:41 | Topbar.tsx:161 | 已覆盖 | |
| GET /api/v1/etf/hot | etf.ts:61 | Etf/index.tsx:266 | 已覆盖 | |
| GET /api/v1/etf/performance | etf.ts:67 | Etf/index.tsx:426 | 已覆盖 | |
| GET /api/v1/etf/scale | etf.ts:71 | Etf/index.tsx:347 | 已覆盖 | |
| GET /api/v1/etf/flow | etf.ts:81 | Etf/index.tsx:333 | 已覆盖 | |
| GET /api/v1/etf/detail/{code} | etf.ts:84 | EtfDetail 页 | 已覆盖 | 模板串路径 |

### datacenter（22/22 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/datacenter/overview | datacenter.ts:28 | DataCenter/index.tsx:492 | 已覆盖 | 60s 放宽超时 |
| GET /api/v1/datacenter/datasets | datacenter.ts:37 | Backtest/index.tsx:97 | 已覆盖 | |
| GET /api/v1/datacenter/quality | datacenter.ts:42 | DataCenter/index.tsx:493 | 已覆盖 | |
| GET /api/v1/datacenter/logs | datacenter.ts:44 | DataCenter/index.tsx:494 | 已覆盖 | |
| GET /api/v1/datacenter/task-stats | datacenter.ts:47 | DataCenter/index.tsx:495 | 已覆盖 | |
| POST /api/v1/datacenter/sync | datacenter.ts:52 | DataCenter/index.tsx:641 | 已覆盖 | |
| POST /api/v1/datacenter/sync/cancel | datacenter.ts:57 | DataCenter/index.tsx:678 | 已覆盖 | |
| GET /api/v1/datacenter/sync/status | datacenter.ts:62 | DataCenter/index.tsx:499 | 已覆盖 | 1.5s 级轮询 |
| GET /api/v1/datacenter/sync/tasks/{task_id} | datacenter.ts:65 | hooks/useAbortableTask.ts:18 | 已覆盖 | 模板串路径 |
| POST /api/v1/datacenter/sync/fetch | datacenter.ts:70 | DataCenter/index.tsx:685 | 已覆盖 | |
| GET /api/v1/datacenter/instruments | datacenter.ts:79 | DataCenter/index.tsx:319 | 已覆盖 | |
| GET /api/v1/datacenter/sync/auto | datacenter.ts:82 | DataCenter/index.tsx:500 | 已覆盖 | |
| POST /api/v1/datacenter/sync/auto | datacenter.ts:86 | DataCenter/index.tsx:500 | 已覆盖 | |
| GET /api/v1/datacenter/text/status | datacenter.ts:89 | TextDataPanel.tsx:53 | 已覆盖 | |
| POST /api/v1/datacenter/text/import | datacenter.ts:91 | TextDataPanel.tsx:90 | 已覆盖 | |
| POST /api/v1/datacenter/text/build-factor | datacenter.ts:95 | TextDataPanel.tsx:137 | 已覆盖 | |
| GET /api/v1/datacenter/mirror/status | datacenter.ts:103 | TextDataPanel.tsx:53 | 已覆盖 | |
| POST /api/v1/datacenter/mirror/rebuild | datacenter.ts:109 | TextDataPanel.tsx:192 | 已覆盖 | |
| GET /api/v1/datacenter/train/readiness | datacenter.ts:111 | TrainPanel.tsx:53 | 已覆盖 | |
| POST /api/v1/datacenter/train/start | datacenter.ts:114 | TrainPanel.tsx:76 | 已覆盖 | |
| GET /api/v1/datacenter/train/status | datacenter.ts:115 | TrainPanel.tsx:44 `useTaskPolling('/api/v1/…')` | 已覆盖 | 经通用 hook 传**字符串路径**消费，非方法调用 —— 单靠「方法名出现率」会漏判 |
| POST /api/v1/datacenter/train/cancel | datacenter.ts:118 | TrainPanel.tsx:86 | 已覆盖 | |

### desk（12/12 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/desk/kill-switch | production.ts:250 | OrderDesk/index.tsx:72 | 已覆盖 | |
| POST /api/v1/desk/kill-switch | production.ts:252 | OrderDesk/index.tsx:72 | 已覆盖 | |
| GET /api/v1/desk/exclusion | production.ts:254 | OrderDesk/index.tsx:73 | 已覆盖 | |
| POST /api/v1/desk/exclusion | production.ts:256 | OrderDesk/index.tsx:73 | 已覆盖 | |
| POST /api/v1/desk/exclusion/toggle | production.ts:259 | OrderDesk/index.tsx:174 | 已覆盖 | |
| GET /api/v1/desk/exclusion/screen | production.ts:262 | OrderDesk/index.tsx:565 | 已覆盖 | |
| POST /api/v1/desk/orders | production.ts:266 | OrderDesk/index.tsx:101 | 已覆盖 | |
| POST /api/v1/desk/fills/run | production.ts:270 | OrderDesk/index.tsx:110 | 已覆盖 | |
| GET /api/v1/desk/orders | production.ts:277 | OrderDesk/index.tsx:36 | 已覆盖 | 带 `?limit=` |
| GET /api/v1/desk/account | production.ts:280 | OrderDesk/index.tsx:35 | 已覆盖 | |
| GET /api/v1/desk/capacity | production.ts:283 | CapacityAttribution/index.tsx:53 | 已覆盖 | 多 query 参数 |
| POST /api/v1/desk/attribution | production.ts:294 | CapacityAttribution/index.tsx:73 | 已覆盖 | |

### export（3/3 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/export/screener | export.ts:15 | Screener/index.tsx:229 | 已覆盖 | **走 `download()` 二进制流**（非 get/post）—— 只扫 `get(`/`post(` 会漏 |
| POST /api/v1/export/strategy-backtest | export.ts:27 | Backtest/index.tsx:122 | 已覆盖 | 同上（download） |
| POST /api/v1/export/backtest | export.ts:37 | Backtest/TopKPanel.tsx:73 | 已覆盖 | 同上（download） |

### screener（4/4 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/screener | screener.ts:24 | Screener/index.tsx:207 | 已覆盖 | |
| GET /api/v1/screener/stats/series | screener.ts:40 | Screener/index.tsx:167 | 已覆盖 | |
| GET /api/v1/screener/stocks | screener.ts:61 | Screener/StockList.tsx:98 | 已覆盖 | |
| GET /api/v1/screener/watchlist | screener.ts:65 | Screener/index.tsx:193 | 已覆盖 | |

### backtest（3/3 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| POST /api/v1/backtest/run | backtest.ts:64 | Backtest/TopKPanel.tsx:54 | 已覆盖 | |
| POST /api/v1/backtest/signal-analysis | backtest.ts:67 | Backtest/SignalAnalysisPanel.tsx:44 | 已覆盖 | |
| POST /api/v1/backtest/strategy-run | strategyBacktest.ts:153 | Backtest/index.tsx | 已覆盖 | |

### portfolio（2/2 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/portfolio/search | portfolio.ts:12 | Topbar.tsx:160 | 已覆盖 | |
| POST /api/v1/portfolio/backtest | portfolio.ts:15 | Backtest/TopKPanel.tsx:73 | 已覆盖 | |

### research（11/11 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/research/overview | research.ts:27 | Research/index.tsx:361+ | 已覆盖 | 渲染 factor_count / capacity_estimate_yi 等 |
| POST /api/v1/research/factor-icir | research.ts:31 | Research/index.tsx:210 | 已覆盖 | |
| POST /api/v1/research/factor-corr | research.ts:35 | Research/index.tsx:211 | 已覆盖 | |
| POST /api/v1/research/factor-quantile | research.ts:42 | Research/index.tsx:212 | 已覆盖 | |
| GET /api/v1/research/lab/yearly | research.ts:47 | Research/index.tsx:190 | 已覆盖 | |
| GET /api/v1/research/experiments | research.ts:57 | Research/index.tsx:176 | 已覆盖 | |
| POST /api/v1/research/cv-folds | research.ts:68 | Research/index.tsx:221 | 已覆盖 | |
| GET /api/v1/research/feature-importance | research.ts:75 | Research/index.tsx:179 | 已覆盖 | |
| POST /api/v1/research/optimize | research.ts:103 | Research/index.tsx:224 | 已覆盖 | |
| POST /api/v1/research/impact-sim | research.ts:119 | Research/index.tsx:231 | 已覆盖 | |
| POST /api/v1/research/stress-test | research.ts:134 | Research/index.tsx:235 | 已覆盖 | |

### studio（9/9 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| POST /api/v1/studio/mining/start | production.ts:45 | FactorStudio/index.tsx:114 | 已覆盖 | |
| GET /api/v1/studio/mining/status/{task_id} | production.ts:47 | FactorStudio/index.tsx:81 | 已覆盖 | 模板串 |
| POST /api/v1/studio/mining/cancel/{task_id} | production.ts:51 | FactorStudio/index.tsx:165 | 已覆盖 | 模板串 |
| POST /api/v1/studio/nl-to-factor | monitor.ts:119 | FactorStudio/NlFactorCard.tsx:37 | 已覆盖 | |
| POST /api/v1/studio/alpha-eval | production.ts:54 | FactorStudio/index.tsx:150 | 已覆盖 | |
| GET /api/v1/studio/factors | production.ts:61 | FactorStudio/FactorLab.tsx:38 | 已覆盖 | |
| POST /api/v1/studio/factors | production.ts:65 | FactorStudio/FactorLab.tsx:52 | 已覆盖 | |
| DELETE /api/v1/studio/factors/{factor_id} | production.ts:68 | FactorStudio/FactorLab.tsx:65 | 已覆盖 | `del()` + 模板串 |
| POST /api/v1/studio/factor-report | production.ts:59 | FactorStudio/FactorLab.tsx:73 | 已覆盖 | |

### ops（4/4 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| POST /api/v1/ops/quality-scan | production.ts:175 | DataQuality/index.tsx:138 | 已覆盖 | |
| GET /api/v1/ops/lineage | production.ts:181 | DataQuality/index.tsx:154 | 已覆盖 | |
| GET /api/v1/ops/dag | production.ts:184 | Pipeline/index.tsx:60 | 已覆盖 | |
| POST /api/v1/ops/dag/rerun | production.ts:187 | Pipeline/index.tsx:75 | 已覆盖 | |

### watchlist（2/2 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/watchlist/dashboard | watchlist.ts:8 | Watchlist/index.tsx | 已覆盖 | |
| GET /api/v1/watchlist/correlation | watchlist.ts:13 | Watchlist/index.tsx:265 | 已覆盖 | |

### settings（7/7 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/settings | settings.ts:65 | usePreferencesStore.ts:28 | 已覆盖 | |
| PUT /api/v1/settings/preferences | settings.ts:68 | Settings/index.tsx:242 | 已覆盖 | |
| PUT /api/v1/settings/engine | settings.ts:71 | Settings/index.tsx:248 | 已覆盖 | |
| POST /api/v1/settings/connectors/test | settings.ts:74 | Settings/index.tsx:203 | 已覆盖 | |
| POST /api/v1/settings/data/cache/clear | settings.ts:80 | Settings/index.tsx:318 | 已覆盖 | |
| POST /api/v1/settings/db/backup | settings.ts:84 | Settings/index.tsx:327 | 已覆盖 | |
| POST /api/v1/settings/data/sync | settings.ts:88 | Settings/index.tsx:284 | 已覆盖 | |

### monitor（2/2 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/monitor/health | monitor.ts:110 | FactorHealthCard.tsx:29 | 已覆盖 | |
| POST /api/v1/monitor/run | monitor.ts:112 | FactorHealthCard.tsx:39 | 已覆盖 | |

### report（2/2 已覆盖）

| 后端端点 | 前端调用点 | 入口 | 状态 | 备注 |
|---|---|---|---|---|
| GET /api/v1/report/daily | monitor.ts:115 | Report/index.tsx:57 | 已覆盖 | 模板串 `?date=` 拼接 |
| POST /api/v1/report/daily/generate | monitor.ts:117 | Report/index.tsx:75 | 已覆盖 | |

---

## 3. 「内部端点(无需前端)」清单（共 1 个）

| 端点 | 判定理由 |
|---|---|
| `GET /api/v1/market/overview` | market.py docstring 自述为「聚合：指数走势/涨跌分布/北向资金/异动/ML 推荐」的**老单体聚合**。前端已改走拆分后的 `/overview/rt`（实时）与 `/overview/daily`（日频+ML 推荐）两个新端点，裸 `overview` 前端**零调用**。属**已被取代的技术债**，非功能缺口（数据用户都看得到）。**建议 P2：确认无移动端/外部依赖后删除，或在 openapi 中标记 deprecated。** |

> 📌 说明：本次审计**未发现 10 个运维端点**。经全量核对，115 个端点中除上述 1 个聚合端点外，**其余 114 个均有前端消费证据**。若此前口径认为「有 10 个运维端点免报缺失」，请以本表为准 —— 这 10 个更可能是把 **`del()`/`download()`/`EventSource`/模板串/hook 字符串路径** 五类调用误判为「无前端调用」所致（见第 5 节）。

---

## 4. 「部分覆盖」端点

**经三轮交叉验证：本系统不存在「部分覆盖」端点（0 个）。**

此前疑似「字段未渲染」的 2 处，实测均已完整渲染，属**方法名启发式误报**：

| 曾被怀疑的端点 | 误报原因 | 实测结论 |
|---|---|---|
| `GET /api/v1/alerts/rules` | `AlertRule` 类型字段名（如 `cooldown_minutes`）在类型定义中出现，被字段级扫描误当成「接口方法未被调用」 | **完整覆盖**。`Alerts/index.tsx` 支持规则的增/改/删/启停，`cooldown_minutes`、`channels`、`scope`、`params` 均在表单中可编辑并渲染 |
| `GET /api/v1/research/overview` | 同上 —— `factor_universe`、`capacity_formula`、`model_version_count` 等字段名与页面变量重名，被误判 | **完整覆盖**。`Research/index.tsx:361-377` 逐个渲染 factor_count / expression_count / model_version_count / capacity_estimate_yi / capacity_formula / factor_universe |

**根因**：用「方法名字符串是否在页面出现」做启发式，会把 **TS 类型字段名** 与 **真实 API 方法调用** 混淆（两者都是小驼峰标识符）。任何基于该启发式的「部分覆盖」结论都不可信。本报告已改用「端点全路径字面量」为准绳。

---

## 5. 方法论：正则盲区（为什么「缺失」级结论必须 grep 复核）

机械匹配会漏掉以下 **5 类**真实调用，本次全部逐一 grep 校正：

| 类别 | 例子 | 只扫 `get(`/`post(` 的后果 |
|---|---|---|
| 模板字符串 | `` get(`/api/v1/etf/detail/${code}`) `` | 路径被 `${}` 截断 → 误判缺失 |
| `encodeURIComponent` 包裹 | `` get(`/api/v1/stock/${encodeURIComponent(sym)}/kline`) `` | 同上 |
| `download()` 二进制流 | `export.ts` 3 个导出端点 | 完全扫不到 → 3 个误判缺失 |
| `del()` 别名 | `del('/api/v1/studio/factors/${id}')` | DELETE 类误判缺失 |
| `EventSource` / hook 字符串路径 | `notify/stream`（SSE）、`train/status`（`useTaskPolling('/api/v1/…')`） | 误判缺失 |

**因此本报告的口径是：端点全路径字面量扫描（`norm()` 归一化变量段）+ 五类调用全覆盖 + 人工 grep 复核。** 任何「零缺失」结论都必须建立在此口径上才成立。

---

## 6. 结论与建议

- **总体覆盖度 99.1%（114/115）**，无缺失、无部分覆盖、无幽灵调用。该项目的前后端接线完整度非常高。
- **唯一动作项（P2）**：清理 / 标记废弃 `GET /api/v1/market/overview`。
- **不建议**为「运维端点」补前端入口 —— 本就不存在这类缺口；若他处报告列出「10 个运维端点」，请复核是否为上述 5 类正则盲区误报。
- **对下游审计方的建议**：任何「未渲染字段」结论，需给出「后端返回字段列表 vs 前端解构/渲染字段列表」的逐字段对照，不能仅凭方法名/字段名匹配。
