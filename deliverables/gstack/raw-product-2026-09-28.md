# AQP 产品/集成只读审查原始发现

- 审查日期：2026-09-28
- 范围：`frontend/src`、`backend/app` 的生产源码；按要求忽略 `backend/.tmp_*`、`backend/reports/`、`frontend/dist*`。
- 方法：静态抽取前端 HTTP 调用（含 `get/post/put/del/download`、SWR、SSE），对照 FastAPI 装饰器、`router.py` 与 `main.py` 的 prefix；未启动服务、未运行测试、未修改生产代码。
- 注意：端点“对齐”仅证明方法和路径注册相同，不能证明运行时数据源一定有数据。

## 0. 前缀与路由注册证据

| 项目 | 结论 | 证据 |
|---|---|---|
| 前端 baseURL | 默认 `/`，每个 API 模块已经写完整 `/api/v1/...` 路径 | `frontend/src/api/client.ts:150-154` |
| 开发代理 | `/api` 与 `/health` 原样转发到 `VITE_API_BASE` 或 `127.0.0.1:8000`，没有 rewrite | `frontend/vite.config.ts:16-26` |
| 后端顶层前缀 | `v1_router` 挂到 `/api/v1` | `backend/app/main.py:289` |
| 二级前缀 | 19 个模块由 `v1_router.include_router(..., prefix=...)` 聚合 | `backend/app/api/v1/router.py:26-44` |
| SSE 特例 | 前端不经 Axios，以临时 ticket 拼 `/api/v1/notify/stream?ticket=...`；后端注册 GET `/stream` | `frontend/src/api/notify.ts:24-32`；`backend/app/api/v1/notify.py:75-100` |

## A. 前后端端点契约一一对齐

### A1. 汇总

静态抽取到后端 **115** 个唯一 `(HTTP 方法, 归一化路径)` 路由，前端 **114** 个唯一调用；其中 **113 已对齐**、**1 个死链**、**2 个后端孤岛**。

- 前端调用命中率：`113 / 114 = 99.1%`
- 后端端点被前端覆盖率：`113 / 115 = 98.3%`
- 参数路径如 `{symbol}`、`${taskId}` 已归一化为 `{}`；查询串不计入路径匹配。

### A2. ✅ 已对齐（113）

下表按能力簇完整列出已命中端点。`FE` 给出调用模块行号；`BE` 给出真实 handler 装饰器行号。所有路径均已带最终 `/api/v1` 前缀。

| 能力簇 | 已对齐方法 + 路径 | FE 证据 | BE 证据 |
|---|---|---|---|
| Auth | POST `/auth/login`；POST `/auth/register`；GET `/auth/register/status`；GET `/auth/me` | `api/auth.ts:44,51,54,57` | `auth.py:102,175,162,217` |
| Alerts | GET `/alerts/health`、`/rules`、`/events`；POST `/rules`、`/events/read`；PUT/DELETE `/rules/{}` | `api/alerts.ts:71,74,77,80,82,85,88,92` | `alerts.py:134,146,161,190,223,245,270` |
| Market | GET `/market/overview/rt`、`/overview/daily`、`/index/kline`、`/quotes` | `api/market.ts:56,62,71,76`；`pages/MarketOverview/index.tsx:47,54`；`pages/StockDetail/index.tsx:56` | `market.py:643,675,766,830` |
| Notify | POST `/notify/stream-ticket`；GET `/notify/recent`、`/notify/stream` | `api/notify.ts:26,28,31` | `notify.py:75,83,156` |
| Stock | GET `/stock/search`、`/stock/{}/profile`、`/stock/{}/kline`、`/stock/{}/predict`、`/stock/{}/panels` | `api/stock.ts:17,20,23,31,35` | `stock.py:78,107,150,200,256` |
| ETF | GET `/etf/overview`、`/overview/series`、`/list`、`/hot`、`/performance`、`/scale`、`/flow`、`/detail/{}` | `api/etf.ts:12,22,41,45,48,52,62,65` | `etf.py:497,565,614,655,756,822,864,1139` |
| DataCenter | GET `/datacenter/overview`、`/datasets`、`/quality`、`/logs`、`/task-stats`、`/sync/status`、`/sync/tasks/{}`、`/sync/auto`、`/instruments`、`/text/status`、`/mirror/status`、`/train/readiness`、`/train/status`；POST `/sync`、`/sync/auto`、`/sync/cancel`、`/sync/fetch`、`/text/import`、`/text/build-factor`、`/mirror/rebuild`、`/train/start`、`/train/cancel` | `api/datacenter.ts:28,37,42,44,47,51,57,59,62,66,76,79,83,86,88,92,97,102,104,107,108,111`；`pages/DataCenter/TrainPanel.tsx:44` | `datacenter.py:480,636,675,720,759,807,861,868,893,905,919,1199,1282,1337,1346,1359,1374,1392,1429,1439,1457,1467` |
| Watchlist | GET `/watchlist/dashboard`、`/watchlist/correlation` | `api/watchlist.ts:8,13` | `watchlist.py:269,389` |
| Settings | GET `/settings`；PUT `/settings/preferences`、`/settings/engine`；POST `/settings/connectors/test`、`/data/sync`、`/data/cache/clear`、`/db/backup` | `api/settings.ts:65,68,71,74,78,82,86` | `app_settings.py:139,180,200,239,257,267,297` |
| Screener | GET `/screener`、`/stats/series`、`/stocks`、`/watchlist` | `api/screener.ts:24,40,61,65` | `screener.py:507,523,604,702` |
| Backtest + Export | POST `/backtest/run`、`/strategy-run`、`/signal-analysis`；GET `/export/screener`；POST `/export/backtest`、`/export/strategy-backtest` | `api/backtest.ts:64,67`；`api/strategyBacktest.ts:153`；`api/export.ts:15,27,37` | `backtest.py:452,891,948`；`export.py:18,57,78` |
| Portfolio | POST `/portfolio/backtest`；GET `/portfolio/search` | `api/portfolio.ts:12,15` | `portfolio.py:114,166` |
| Research | GET `/research/overview`、`/experiments`、`/feature-importance`、`/lab/yearly`；POST `/factor-icir`、`/factor-corr`、`/factor-quantile`、`/cv-folds`、`/optimize`、`/impact-sim`、`/stress-test` | `api/research.ts:27,31,35,42,47,57,68,75,103,119,134` | `research.py:115,165,192,213,229,285,341,399,481,533,584` |
| Studio | POST `/studio/mining/start`、`/mining/cancel/{}`、`/nl-to-factor`、`/alpha-eval`、`/factor-report`、`/factors`；GET `/studio/mining/status/{}`、`/factors`；DELETE `/studio/factors/{}` | `api/production.ts:45,47,51,54,59,61,65,68`；`api/monitor.ts:119` | `studio.py:67,91,116,193,243,298,320,384,414` |
| Ops | POST `/ops/quality-scan`、`/ops/dag/rerun`；GET `/ops/lineage`、`/ops/dag` | `api/production.ts:175,181,184,187` | `ops.py:49,407,464,537` |
| Desk | GET `/desk/kill-switch`、`/exclusion`、`/exclusion/screen`、`/orders`、`/account`、`/capacity`；POST `/kill-switch`、`/exclusion`、`/exclusion/toggle`、`/orders`、`/fills/run`、`/attribution` | `api/production.ts:250,253,254,256,259,262,267,272,278,280,283,294` | `desk.py:53,73,90,112,143,158,179,198,211,255,270,312` |
| Monitor + Report | GET `/monitor/health`、`/report/daily`；POST `/monitor/run`、`/report/daily/generate` | `api/monitor.ts:110,112,115,117` | `monitor.py:19,34`；`report.py:464,478` |

### A3. 🔴 孤儿调用 / 死链（1）

| 方法 + 前端路径 | 问题 | 证据 | 严重度与动作 |
|---|---|---|---|
| GET `/api/v1/report/daily${date ? \`?date=${date}\` : ''}` | **静态抽取表面死链，运行时实际不是死链。** 模板字符串在 `${date...}` 内嵌反引号，简单路径抽取会截断；实际当 `date` 有/无值时均是已注册的 `GET /api/v1/report/daily`，查询参数不改变路由。 | 前端构造：`frontend/src/api/monitor.ts:114-115`；后端：`backend/app/api/v1/report.py:464` | 不构成运行时 404。建议把 URL 改为固定 path + `params: {date}`，让契约可被静态工具准确检查。 |

**结论：在考虑模板字符串后，运行时真实死链为 0。** 上表仍保留“原始扫描发现”，避免掩盖工具可观测性问题。

### A4. 🟡 后端孤岛端点（2）

| 方法 + 路径 | 后端证据 | 解释 |
|---|---|---|
| GET `/api/v1/market/overview` | `backend/app/api/v1/market.py:895` | 前端已拆分消费 `/overview/rt` 与 `/overview/daily`（`api/market.ts:56,62`），综合接口无页面调用，属重复/遗留聚合面。 |
| GET `/api/v1/report/daily` | `backend/app/api/v1/report.py:464` | 因 `monitor.ts:114-115` 的模板拼接被机械扫描漏配；运行时实际被 Report 页使用（`pages/Report/index.tsx:58-65`）。不是真孤岛。 |

**经人工复核后的真实孤岛：1 个，即 `GET /market/overview`。**

### A5. 请求/响应类型契约

#### 已确认的字段不匹配

| 接口 | 发现 | 证据 | 影响 |
|---|---|---|---|
| POST `/backtest/run` | 前端 `BacktestResultData.nav_tail` 是**必填且被渲染**，后端实际返回键为 `equity_curve`，没有 `nav_tail`。 | TS 定义：`frontend/src/types/p1.ts:170-184`；页面消费：`frontend/src/pages/Backtest/TopKPanel.tsx:171-175`；后端返回：`backend/app/api/v1/backtest.py:367-411`，其中曲线键在 `396`。 | “最近净值”区永远不显示；这是数据已算出、UI 静默丢失的典型“看起来可用”缺陷。P1。 |

#### 结构性契约风险（不是凭空宣称字段错；因后端未提供精确 response schema，无法静态证明字段逐项一致）

以下 6 个高价值面都以 `APIResponse[dict]` 或无 `response_model` 对外，不能由 OpenAPI/Pydantic 保证其返回键与 TypeScript 同步。它们应被视为“待约束”的典型清单；本次只读代码检视未找到第二个可确认的必填字段缺失。

| 接口/前端类型 | 前端证据 | 后端证据 | 风险 |
|---|---|---|---|
| `/portfolio/backtest` / `PortfolioBacktestResult` | `frontend/src/types/portfolio.ts`；调用 `api/portfolio.ts:15` | `backend/app/api/v1/portfolio.py:114` 使用 `APIResponse[dict]` | 回测曲线、metrics、drift 等返回键变更不会在后端编译期失败。 |
| `/desk/account` / `PaperAccount` | `frontend/src/api/production.ts:280` | `backend/app/api/v1/desk.py:255-266` 使用 `APIResponse[dict]`，实际由 `paper.account_summary` 生成 | 账户口径键（费用、NAV、回撤）跨 module 透传，无 schema 边界。 |
| `/datacenter/overview` / `DataOverview` | `frontend/src/types/datacenter.ts`；调用 `api/datacenter.ts:28` | `backend/app/api/v1/datacenter.py:480` 无 `response_model` | 数据盘指标大量动态汇总，前端必填字段无法被 OpenAPI 校验。 |
| `/etf/detail/{code}` / `EtfDetail` | `frontend/src/types/etf.ts`；调用 `api/etf.ts:65` | `backend/app/api/v1/etf.py:1139` 为 `APIResponse[dict]` | 深层 `holdings/tracking/sentiment` 块失败时需靠前端容错，字段漂移难发现。 |
| `/market/overview/rt` / `OverviewRt` | `frontend/src/api/market.ts:56` | `backend/app/api/v1/market.py:766` 为 `APIResponse[dict]`，且公开 | 关键实时面存在 `status/stale` 降级载荷，未用 discriminated schema 约束。 |
| `/research/*` 计算结果 | `frontend/src/api/research.ts:27-138` | `backend/app/api/v1/research.py:115,165,192,213,285,341,399,481,533,584` 多为 `APIResponse[dict]` | 高复杂度结果（矩阵、优化、冲击、压力测试）没有共享 DTO 或契约测试。 |

总体证据：后端 115 个装饰器中至少 **67** 个写为 `response_model=APIResponse[dict]`、**10** 个为 `APIResponse[list]`，另有一批无 response_model（例：`app_settings.py:139-297`、`datacenter.py:480-1467`）。这不是直接运行错误，但使 TS/Python 双边契约漂移高度隐蔽。

## B. 功能完整性（19 页面）

判定标准：✅ 完整 = 路由可进入、页面有真实 API 调用/操作闭环、未见 TODO/pass/硬编码业务表替代；🟡 半成品 = 关键业务块以非同类数据替代或已实现能力没有 UI 入口；🔴 空壳 = 无真实调用或明确 TODO/coming soon。静态审查不能验证本地数据是否已经同步，数据为空时标“待运行时确认”。

| 页面（路由） | 判定 | 真实接线/证据 | 审查结论 |
|---|---|---|---|
| Login (`/login`) | ✅ | 路由 `App.tsx:69`；登录/注册 `pages/Login/index.tsx` 调 `authApi`；API `api/auth.ts:44,51` | 完成认证入口。 |
| MarketOverview (`/`,`/market`) | ✅ | 路由 `App.tsx:87-88`；SWR 调实时/日频 API `pages/MarketOverview/index.tsx:47,54` | 有真实实时与日频拆分数据。注意后端两个市场端点公开，见风险。 |
| Report (`/report`) | ✅ | 路由 `App.tsx:91`；加载/重生成 `pages/Report/index.tsx:58-82`；API `monitor.ts:114-117` | 有历史回看、权限控制与生成闭环。 |
| StockDetail (`/stock/:symbol`) | 🟡 | 路由 `App.tsx:92`；profile/kline/predict/panels 调用 `pages/StockDetail/index.tsx`（API `stock.ts:17-36`） | 主功能真实；多项“主力净流入/北向/换手/内外盘”明确 `RowPlaceholder`，`StockDetail/index.tsx:400-450`，非真实数据块。 |
| Screener (`/screener`) | ✅ | 路由 `App.tsx:93`；筛选、历史、列表、自选行情、导出均接线：`pages/Screener/index.tsx` 调用点；`api/screener.ts:24-65` | 完整，且空数据时显示空态不造数。 |
| ETF Center (`/etf`) | ✅ | 路由 `App.tsx:94`；overview/hot/list/flow/scale/performance/series：`pages/Etf/index.tsx` 调用点，API `etf.ts:12-65` | 页面业务数据接线完整；数据源不可用时后端设计为结构化降级，运行时可用性待确认。 |
| ETF Detail (`/etf/:code`) | 🟡 | 路由 `App.tsx:95`；detail API `etf.ts:65`，页面请求 `EtfDetail/index.tsx` | “资金流动（基金特定）”实际拿估值/管理费/规模 `GaugeQuad` 替代，代码注释明确“占位”：`EtfDetail/index.tsx:655-668`。 |
| Backtest (`/backtest`) | 🟡 | 路由 `App.tsx:102`；策略回测、Top-K、信号分析、导出接线：`pages/Backtest/*`；API `backtest.ts:64,67`、`strategyBacktest.ts:153`、`export.ts:27,37` | 后端真实计算/导出均存在，但 Top-K 的 `nav_tail` 契约错误导致最近净值块不可用（A5）。 |
| Portfolio (`/portfolio`) | ✅ | 路由 `App.tsx:96`；标的搜索和组合回测 `pages/Portfolio/index.tsx`、`api/portfolio.ts:12-15` | 可形成搜索→配置→回测闭环。 |
| Research (`/research`) | ✅ | 路由 `App.tsx:103`；overview、ICIR、相关、分层、CV、特征、优化、冲击、压力测试都有调用：`pages/Research/index.tsx`；`api/research.ts:27-138` | 强计算页有真实后端面；应以队列/数据准备状态改善体验。 |
| FactorStudio (`/studio`) | ✅ | 路由 `App.tsx:105`；挖掘、状态、取消、表达式评估、因子库/报告：`pages/FactorStudio/*`；`api/production.ts:45-68` | 完整操作闭环。 |
| DataQuality (`/dataquality`) | ✅ | 路由 `App.tsx:106`；`qualityScan` + `lineage`：`pages/DataQuality/index.tsx`；`api/production.ts:175,181` | 非静态卡片，真实质量扫描与血缘。 |
| OrderDesk (`/desk`) | ✅ | 路由 `App.tsx:107`；账户、订单、撮合、kill switch、禁买池候选均接线：`pages/OrderDesk/index.tsx`；`api/production.ts:250-280` | 模拟盘下单闭环存在；下单 handler 声明 kill-switch/禁买池/行情/卖出持仓校验：`backend/app/api/v1/desk.py:179-194`。 |
| Pipeline (`/pipeline`) | ✅ | 路由 `App.tsx:108`；DAG 读取+重跑：`pages/Pipeline/index.tsx:48-80`；`api/production.ts:184,187` | 有真实产物状态和重跑。 |
| CapacityAttribution (`/capacity`) | ✅ | 路由 `App.tsx:109`；容量+归因请求 `pages/CapacityAttribution/index.tsx`；`api/production.ts:283,294` | 有真实端点。 |
| DataCenter (`/data`) | ✅ | 路由 `App.tsx:97`；overview/datasets/quality/log/sync/auto/text/mirror/train 等全接线：`pages/DataCenter/index.tsx`、`TrainPanel.tsx:44`、`TextDataPanel.tsx`；API `datacenter.ts:28-111` | 数据、任务、训练/文本扩展均非壳。首次全量统计可能很慢，见产品风险。 |
| Watchlist (`/watchlist`) | ✅ | 路由 `App.tsx:98`；dashboard/correlation：`pages/Watchlist/index.tsx`、`api/watchlist.ts:8-13` | 实时自选与相关性均接线。 |
| Settings (`/settings`) | ✅ | 路由 `App.tsx:99`；读取、偏好/引擎保存、源检测、同步、缓存、备份：`pages/Settings/index.tsx`；`api/settings.ts:65-87` | 完整运维/配置页。 |
| Alerts (`/alerts`) | ✅ | 路由 `App.tsx:104`；规则 CRUD、事件、已读、健康检查：`pages/Alerts/index.tsx`；`api/alerts.ts:71-92` | 有规则→调度→历史事件闭环。调度/渠道声明：`backend/app/api/v1/alerts.py:11-13,709-726`。 |

**页面计数：✅ 16；🟡 3（StockDetail、ETF Detail、Backtest）；🔴 0。**

### B1. 后端“未完成 handler”扫描

在 `backend/app/api/v1/*.py` 中未找到路由 handler 为 `pass`、`return {}` 或 `raise NotImplementedError` 的情况。命中到的 `pass` 位于异常吞并/局部容错，而非端点空实现，例如：`alerts.py:729`、`datacenter.py:530,558`、`etf.py:945`、`report.py:280`、`research.py:130,266`。因此不把这些计为空壳端点。

## C. 产品级风险（按优先级）

| 优先级 | 风险与“看起来能用、实际不可用”场景 | 证据 | 建议 |
|---|---|---|---|
| P0 | **Top-K 回测“最近净值”静默失效。** 回测本身可成功、其它曲线可能显示，但页面依赖不存在的 `nav_tail`，所以该块永远不展示。 | `types/p1.ts:170-184`、`Backtest/TopKPanel.tsx:171-175` vs `backend/api/v1/backtest.py:367-411` | 统一为 `equity_curve.slice(-5)`，或后端补 `nav_tail`；新增前后端 JSON 契约测试。 |
| P1 | **ETF 详情的“资金流动”不是资金流。** 标题承诺基金资金流，页面却展示 PE/PB/费率/规模，用户会误判流入流出。 | `frontend/src/pages/EtfDetail/index.tsx:655-668`；尽管中心页已有真实 `/etf/flow`：`api/etf.ts:62`、`backend/api/v1/etf.py:822` | 在详情页接真实 flow（按 code）或直接改名“估值与费率”；不要用同维度错误的数据填空。 |
| P1 | **个股详情把关键微观数据固定为占位。** 视觉上像完整指标面板，但主力、北向、换手、内外盘没有数据接线。 | `frontend/src/pages/StockDetail/index.tsx:400-450` | 移除伪面板或标“暂未覆盖”；若确有数据源，新增受数据质量标记的 endpoint。 |
| P1 | **接口类型契约没有强约束。** 大量高价值接口返回 `dict`，字段删改不会让后端 schema 或前端编译报错；本次已经发现一例。 | `backtest.py:452`、`portfolio.py:114`、`desk.py:255`、`etf.py:1139` 等；A5 统计 | 为核心 GET/POST 结果提取 Pydantic DTO，CI 对 OpenAPI/TS 类型或固定 JSON 样例做 contract test。 |
| P1 | **市场概览端点公开，且可能触发昂贵实时/日频构建。** 这与需要登录才能进入前端主路由不等价，直接 HTTP 可绕过页面 guard。 | `backend/app/api/v1/market.py:766-770,830,895` 三个 handler 无 `Depends(require_role)`；对比受保护路由例 `backtest.py:452-455` | 明确产品策略：若只面向登录用户，给市场端点加 viewer 角色；若公开，增加 IP 限流/缓存和数据授权说明。 |
| P2 | **数据中心的冷路径把“慢/未同步”伪装成一般加载失败。** 前端已因扫描慢把 datasets/quality 放宽到 90 秒、overview 60 秒，说明实时操作体验高度依赖本地数据体量。 | `frontend/src/api/datacenter.ts:24-40`；后端 overview 注释也承认全量重扫：`backend/app/api/v1/datacenter.py:480-500` | 返回任务化进度/最近成功快照/数据新鲜度；不要让用户等同步 HTTP。 |
| P2 | **通知/告警是单进程内存态。** 单实例可用，但重启清空；多进程/多副本不能可靠广播或回放，可能错过关键告警。 | `backend/app/core/events.py:1-40` 明确“单进程内存态、服务重启即清空”；Alerts 调度 `alerts.py:709-726` | 告警事件落库、发布走 Redis/消息队列；SSE 仅做实时显示，不能作为唯一送达通道。 |

## D. 总结判断

AQP 不是“只有 UI 的壳”：端点接线覆盖很高、19 页均有路由、主能力（数据、选股、回测、组合、研究、模拟盘、告警、管线）大多已接到后端。真正需要优先修的不是扩新面，而是 **结果契约可验证性与不诚实的替代展示**：先修 Top-K 回测字段、ETF/个股详情伪指标，再给核心响应上 DTO/契约测试，然后处理公开市场接口和数据中心长请求的产品化状态。
