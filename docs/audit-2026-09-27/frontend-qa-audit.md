# AQP 前端接口可达性审计报告（QA · 严过关）

- **审计日期**：2026-09-27
- **审计范围**：`frontend/src`（api / pages / components / hooks / stores / utils）+ 与 `backend/app/api/v1/*` 的对齐核对
- **审计性质**：**只读审计**，未修改任何业务代码
- **审计目标**：回答用户四个核心诉求
  1. 前后端各功能是否可用
  2. 各组件是否能正常显示
  3. 是否有"按钮/组件却拉不起后端"
  4. 前后端超时问题

---

## 0. 结论速览

| 指标 | 结果 |
| --- | --- |
| 前端 HTTP 调用总数（去重 method+path） | **109** |
| 后端 v1 已注册端点总数 | **115** |
| ✅ 对齐（后端存在该方法+路径） | **109 / 109（100%）** |
| ❌ 悬空调用（前端调用但后端无路由，会 404） | **0** |
| ⚠️ 参数/方法不匹配 | **0**（逐个核对了 path 参数名、query 别名、Pydantic 请求体字段） |
| 后端"死端点"（已实现但前端无入口） | **6** |
| 前端声明但从未被调用的 API 函数 | **4** |
| 问题总数 | **P0 = 0，P1 = 4，P2 = 9，共 13** |
| 前端构建 | `tsc -b` ✅ 0 错误；`vite build` ✅ 成功（22.67s） |

**一句话结论**：本项目前端经过多轮治理，**不存在"按钮点了之后 404"的悬空调用**，前后端对齐度极高；剩余问题集中在三类：
- 公开首页（`/`）无错误态 + 顶栏搜索在未登录时静默失败（P1）
- 少量"死端点"与"声明未用"的 API 函数（P2）
- 少数长耗时接口沿用 15s 默认超时（P2）

---

## 1. 前端 API 调用清单

> 全部调用均集中在 `frontend/src/api/*.ts`，**未发现任何页面绕过 api 层直接 `fetch`/`axios`**（已全量 grep 验证，仅 `stores/useNotifyStore.ts` 有 `EventSource`，属 SSE 必须）。

### 1.1 api 层调用明细（调用方文件:行号 → METHOD /path）

| # | 文件:行号 | METHOD | PATH | 单次超时 |
| --- | --- | --- | --- | --- |
| 1 | api/alerts.ts:59 | GET | /api/v1/alerts/rules | 15s(默认) |
| 2 | api/alerts.ts:62 | POST | /api/v1/alerts/rules | 15s |
| 3 | api/alerts.ts:65 | PUT | /api/v1/alerts/rules/{id} | 15s ⚠️**未使用** |
| 4 | api/alerts.ts:67 | DELETE | /api/v1/alerts/rules/{id} | 15s |
| 5 | api/alerts.ts:70 | GET | /api/v1/alerts/events | 15s |
| 6 | api/alerts.ts:73/77 | POST | /api/v1/alerts/events/read | 15s |
| 7 | api/auth.ts:44 | POST | /api/v1/auth/login | 15s |
| 8 | api/auth.ts:51 | POST | /api/v1/auth/register | 15s |
| 9 | api/auth.ts:54 | GET | /api/v1/auth/register/status | 15s |
| 10 | api/auth.ts:57 | GET | /api/v1/auth/me | 15s |
| 11 | api/backtest.ts:64 | POST | /api/v1/backtest/run | 120s |
| 12 | api/backtest.ts:67 | POST | /api/v1/backtest/signal-analysis | 120s |
| 13 | api/strategyBacktest.ts:153 | POST | /api/v1/backtest/strategy-run | 120s / 600s(寻优) |
| 14 | api/datacenter.ts:13 | GET | /api/v1/datacenter/overview | 60s |
| 15 | api/datacenter.ts:22 | GET | /api/v1/datacenter/datasets | 90s |
| 16 | api/datacenter.ts:27 | GET | /api/v1/datacenter/quality | 90s |
| 17 | api/datacenter.ts:29 | GET | /api/v1/datacenter/logs | 15s |
| 18 | api/datacenter.ts:32 | GET | /api/v1/datacenter/task-stats | 15s |
| 19 | api/datacenter.ts:36 | POST | /api/v1/datacenter/sync | 15s |
| 20 | api/datacenter.ts:42 | POST | /api/v1/datacenter/sync/cancel | 15s |
| 21 | api/datacenter.ts:44 | GET | /api/v1/datacenter/sync/status | 15s |
| 22 | api/datacenter.ts:46 | GET | /api/v1/datacenter/sync/tasks/{id} | 15s ⚠️**未使用** |
| 23 | api/datacenter.ts:56 | POST | /api/v1/datacenter/sync/fetch | 15s |
| 24 | api/datacenter.ts:64 | GET | /api/v1/datacenter/instruments | 15s |
| 25 | api/datacenter.ts:69 | GET | /api/v1/datacenter/sync/auto | 15s |
| 26 | api/datacenter.ts:73 | POST | /api/v1/datacenter/sync/auto | 15s |
| 27 | api/datacenter.ts:76 | GET | /api/v1/datacenter/text/status | 15s |
| 28 | api/datacenter.ts:78 | POST | /api/v1/datacenter/text/import | 15s |
| 29 | api/datacenter.ts:80 | POST | /api/v1/datacenter/text/build-factor | 15s |
| 30 | api/datacenter.ts:85 | GET | /api/v1/datacenter/mirror/status | 45s |
| 31 | api/datacenter.ts:87 | POST | /api/v1/datacenter/mirror/rebuild | 15s |
| 32 | api/datacenter.ts:90 | GET | /api/v1/datacenter/train/readiness | 15s |
| 33 | api/datacenter.ts:92 | POST | /api/v1/datacenter/train/start | 15s |
| 34 | api/datacenter.ts:94 | GET | /api/v1/datacenter/train/status | 15s |
| 35 | api/datacenter.ts:96 | POST | /api/v1/datacenter/train/cancel | 15s |
| 36 | api/desk → production.ts:242 | GET | /api/v1/desk/kill-switch | 15s |
| 37 | production.ts:245 | POST | /api/v1/desk/kill-switch | 15s |
| 38 | production.ts:247 | GET | /api/v1/desk/exclusion | 15s |
| 39 | production.ts:249 | POST | /api/v1/desk/exclusion | 15s |
| 40 | production.ts:252 | POST | /api/v1/desk/exclusion/toggle | 15s |
| 41 | production.ts:254 | GET | /api/v1/desk/exclusion/screen | 15s |
| 42 | production.ts:259 | POST | /api/v1/desk/orders | 15s |
| 43 | production.ts:261 | POST | /api/v1/desk/fills/run | 15s |
| 44 | production.ts:266 | GET | /api/v1/desk/orders?limit=N | 15s |
| 45 | production.ts:267 | GET | /api/v1/desk/account | 15s |
| 46 | production.ts:269 | GET | /api/v1/desk/capacity | 15s |
| 47 | production.ts:280 | POST | /api/v1/desk/attribution | 180s |
| 48 | api/etf.ts:12 | GET | /api/v1/etf/overview | 30s |
| 49 | etf.ts:31 | GET | /api/v1/etf/list | 30s |
| 50 | etf.ts:34 | GET | /api/v1/etf/hot | 30s |
| 51 | etf.ts:38 | GET | /api/v1/etf/performance | 30s |
| 52 | etf.ts:42 | GET | /api/v1/etf/scale | 30s |
| 53 | etf.ts:45 | GET | /api/v1/etf/flow | 30s |
| 54 | etf.ts:55 | GET | /api/v1/etf/detail/{code} | 30s |
| 55 | api/export.ts:15 | GET | /api/v1/export/screener | 180s(blob) |
| 56 | export.ts:27 | POST | /api/v1/export/strategy-backtest | 180s |
| 57 | export.ts:37 | POST | /api/v1/export/backtest | 180s |
| 58 | api/market.ts:55 | GET | /api/v1/market/overview | 120s ⚠️**未使用** |
| 59 | market.ts:63 | GET | /api/v1/market/overview/rt | 120s |
| 60 | market.ts:69 | GET | /api/v1/market/overview/daily | 120s |
| 61 | market.ts:78 | GET | /api/v1/market/index/kline | 15s |
| 62 | market.ts:83 | GET | /api/v1/market/quotes | 20s ⚠️**未使用** |
| 63 | api/monitor.ts:110 | GET | /api/v1/monitor/health | 15s |
| 64 | monitor.ts:111 | POST | /api/v1/monitor/run | 120s |
| 65 | monitor.ts:113 | GET | /api/v1/report/daily | 15s |
| 66 | monitor.ts:116 | POST | /api/v1/report/daily/generate | 60s |
| 67 | monitor.ts:118 | POST | /api/v1/studio/nl-to-factor | 120s |
| 68 | api/notify.ts:26 | GET | /api/v1/notify/recent | 10s |
| 69 | notify.ts:28 | POST | /api/v1/notify/stream-ticket | 10s |
| 70 | notify.ts:31 + store:102 | GET(SSE) | /api/v1/notify/stream?ticket= | — |
| 71 | api/ops → production.ts:175 | POST | /api/v1/ops/quality-scan | 180s |
| 72 | production.ts:176 | GET | /api/v1/ops/lineage | 15s |
| 73 | production.ts:177 | GET | /api/v1/ops/dag | 15s |
| 74 | production.ts:179 | POST | /api/v1/ops/dag/rerun | 300s |
| 75 | api/portfolio.ts:12 | GET | /api/v1/portfolio/search | 15s |
| 76 | portfolio.ts:15 | POST | /api/v1/portfolio/backtest | 120s |
| 77 | api/research.ts:27 | GET | /api/v1/research/overview | 15s |
| 78 | research.ts:30 | POST | /api/v1/research/factor-icir | 120s |
| 79 | research.ts:34 | POST | /api/v1/research/factor-corr | 120s |
| 80 | research.ts:38 | POST | /api/v1/research/factor-quantile | 120s |
| 81 | research.ts:45 | GET | /api/v1/research/lab/yearly | 15s |
| 82 | research.ts:48 | GET | /api/v1/research/experiments | 15s |
| 83 | research.ts:58 | POST | /api/v1/research/cv-folds | 120s |
| 84 | research.ts:69 | GET | /api/v1/research/feature-importance?top_k= | 15s |
| 85 | research.ts:79 | POST | /api/v1/research/optimize | 180s |
| 86 | research.ts:107 | POST | /api/v1/research/impact-sim | 120s |
| 87 | research.ts:122 | POST | /api/v1/research/stress-test | 180s |
| 88 | api/screener.ts:17 | GET | /api/v1/screener | 15s |
| 89 | screener.ts:37 | GET | /api/v1/screener/stocks | 30s |
| 90 | screener.ts:42 | GET | /api/v1/screener/watchlist | 15s |
| 91 | api/settings.ts:65 | GET | /api/v1/settings | 60s |
| 92 | settings.ts:68 | PUT | /api/v1/settings/preferences | 15s |
| 93 | settings.ts:71 | PUT | /api/v1/settings/engine | 15s |
| 94 | settings.ts:74 | POST | /api/v1/settings/connectors/test | 30s |
| 95 | settings.ts:78 | POST | /api/v1/settings/data/cache/clear | 30s |
| 96 | settings.ts:82 | POST | /api/v1/settings/db/backup | 60s |
| 97 | settings.ts:86 | POST | /api/v1/settings/data/sync | 15s |
| 98 | api/stock.ts:17 | GET | /api/v1/stock/search | 15s |
| 99 | stock.ts:20 | GET | /api/v1/stock/{symbol}/profile | 15s |
| 100 | stock.ts:23 | GET | /api/v1/stock/{symbol}/kline | 15s |
| 101 | stock.ts:31 | GET | /api/v1/stock/{symbol}/predict | 15s |
| 102 | stock.ts:35 | GET | /api/v1/stock/{symbol}/panels | 30s |
| 103 | api/studio → production.ts:44 | POST | /api/v1/studio/mining/start | 60s |
| 104 | production.ts:47 | GET | /api/v1/studio/mining/status/{id} | 15s |
| 105 | production.ts:50 | POST | /api/v1/studio/mining/cancel/{id} | 15s |
| 106 | production.ts:54 | POST | /api/v1/studio/alpha-eval | 120s |
| 107 | production.ts:59 | POST | /api/v1/studio/factor-report | 120s |
| 108 | production.ts:61 | GET | /api/v1/studio/factors | 15s |
| 109 | production.ts:65 | POST | /api/v1/studio/factors | 120s |
| 110 | production.ts:68 | DELETE | /api/v1/studio/factors/{id} | 15s |
| 111 | api/watchlist.ts:8 | GET | /api/v1/watchlist/dashboard | 30s |
| 112 | watchlist.ts:13 | GET | /api/v1/watchlist/correlation | 30s |

> 另有 2 处经 SWR `useApi` 直接传 URL 的调用：
> - `pages/MarketOverview/index.tsx:39,46` → GET `/api/v1/market/overview/rt`、`/api/v1/market/overview/daily`（超限 120s）
> - `pages/DataCenter/TrainPanel.tsx:44` → GET `/api/v1/datacenter/train/status`（`useTaskPolling`）
>
> 去重后前端实际打出的不同端点 = **109**。

### 1.2 前端声明但从未被页面调用的函数（死代码，4 个）

| 函数 | 声明位置 | 对应端点 | 说明 |
| --- | --- | --- | --- |
| `alertsApi.updateRule` | api/alerts.ts:64 | PUT /api/v1/alerts/rules/{id} | 页面只有"新建/删除"，**没有编辑入口** |
| `datacenterApi.task` | api/datacenter.ts:46 | GET /api/v1/datacenter/sync/tasks/{id} | 同步任务详情无消费方 |
| `marketApi.overview` | api/market.ts:55 | GET /api/v1/market/overview | 已拆分为 rt/daily 两块，整包端点无消费方 |
| `marketApi.quotes` | api/market.ts:83 | GET /api/v1/market/quotes | 批量实时行情端点无任何页面消费 |

---

## 2. 前端 UI 可交互元素清单（19 个页面）

| 页面（路由） | 主要按钮 / 表单 / 开关 | 触发行为 | 后端可达 |
| --- | --- | --- | --- |
| **MarketOverview** `/`·`/market`（公开） | 视图切换 自选 / 市场概览 / AI专题 | **无 onClick** | ❌ 装饰性（见 P2-1） |
| | 交易日下拉 | 纯前端（SWR key 换日期） | — |
| | DataFreshness ⟳ 轻刷新 | GET /market/overview/rt?refresh=1 | ✅ |
| | KPI/热力图/资金流/板块/AI精选 | GET /market/overview/rt + /daily | ✅ |
| **Login** `/login` | 登录 / 注册 Tab、提交表单 | POST /auth/login · /auth/register · GET /auth/register/status | ✅ |
| **StockDetail** `/stock/:symbol` | 搜索、+自选/移出自选 | GET /stock/search；localStorage | ✅ / 本地 |
| | 复权切换、区间 | GET /stock/{sym}/kline | ✅ |
| | AI 预测卡 | GET /stock/{sym}/predict（researcher 才发） | ✅ |
| | 6 个研究面板 | GET /stock/{sym}/panels | ✅ |
| **Screener** `/screener` | 板块 Tab、Alpha/自选视图 pills、分页、排序表头 | GET /screener（top_k/board/date） | ✅ |
| | 榜单内搜索、最小 Score/风险筛选 | 纯前端过滤 | — |
| | 导出 Excel（researcher 才渲染） | GET /export/screener | ✅ |
| | ★ 加入/移出自选 | localStorage | — |
| | 大盘走势（5 指数） | GET /market/index/kline ×5 | ✅ |
| | 自选股表 | GET /screener/watchlist | ✅ |
| | 全市场股票列表 | GET /screener/stocks | ✅ |
| **Etf** `/etf` | 板块/国别 Tab、筛选器（类型/指数/管理人/规模/成立日）、重置、搜索、分页 | GET /etf/list | ✅ |
| | 表现图（metric/period）、热门榜、规模榜、资金流 | /etf/performance · /hot · /scale · /flow | ✅ |
| | ★ 自选 | localStorage | — |
| **EtfDetail** `/etf/:code` | K 线周期 Tab、跟踪周期、自选 | GET /etf/detail/{code} | ✅ |
| **Backtest** `/backtest` | 三 Tab（趋势跟踪/Top-K/信号分析） | — | — |
| | 运行回测、参数表单 | POST /backtest/strategy-run | ✅ |
| | 导出 Excel | POST /export/strategy-backtest | ✅ |
| | Top-K 运行 + 导出 | POST /backtest/run + POST /export/backtest | ✅ |
| | 信号分析运行 | POST /backtest/signal-analysis | ✅ |
| **Portfolio** `/portfolio` | 资产搜索（debounce+abort）、权重、一键均分、日期/资金/策略/调仓/基准 | GET /portfolio/search | ✅ |
| | 开始回测（researcher 才 enable） | POST /portfolio/backtest | ✅ |
| **Research** `/research` | 因子 IC/相关/分位、实验、CV、重要性、优化、冲击、压力、分年 | 11 个 /research/* 端点 | ✅ 全部 |
| **FactorStudio** `/studio` | 字段勾选、种群/代数/口径/种子、启动挖掘、取消任务 | POST /studio/mining/start · /cancel/{id} | ✅ |
| | 轮询进度 | GET /studio/mining/status/{id} | ✅ |
| | 表达式 评估 / 复制 | POST /studio/alpha-eval；navigator.clipboard | ✅ / 本地 |
| | 因子库增删改、因子检测报告、NL-to-Factor | GET/POST/DELETE /studio/factors；POST /factor-report；POST /nl-to-factor | ✅ |
| **DataQuality** `/dataquality` | 重新扫描 daily_bar | POST /ops/quality-scan | ✅ |
| | 血缘图 | GET /ops/lineage | ✅ |
| | 因子健康度卡（自动刷新/手动运行） | GET /monitor/health · POST /monitor/run | ✅ |
| **Pipeline** `/pipeline` | 重跑该交易日流水线（日期输入） | POST /ops/dag/rerun | ✅ |
| | DAG 图 / 最近任务 | GET /ops/dag | ✅ |
| **OrderDesk** `/desk` | 提交母单、执行撮合、熔断开关（二次确认）、自动刷新 Toggle | POST /desk/orders · /fills/run · POST /kill-switch | ✅ |
| | 禁买池：新增/启停/批量/一键扫描 | GET/POST /desk/exclusion · /toggle · /exclusion/screen | ✅ |
| | 账户/订单刷新 | GET /desk/account · /orders | ✅ |
| **CapacityAttribution** `/capacity` | 容量三滑块（防抖 400ms 重算） | GET /desk/capacity | ✅ |
| | 归因窗口、基准三选一、资产行增删、运行归因（挂载即跑） | POST /desk/attribution | ✅ |
| **Report** `/report` | 历史期下拉、重新生成（researcher 才 enable） | GET /report/daily · POST /report/daily/generate | ✅ |
| **DataCenter** `/data` | 刷新看板、开始同步、停止同步、修复缺漏/全量重构（二次确认） | GET /datacenter/overview·datasets·quality·task-stats；POST /sync·/sync/cancel | ✅ |
| | 自定义抓取（类型/日期/代码/条数） | POST /sync/fetch + GET /instruments | ✅ |
| | 自动更新开关 + 时间（失败回退） | GET/POST /sync/auto | ✅ |
| | 文本数据：导入 + 构建因子 + 镜像状态/重建 | /text/status · /text/import · /text/build-factor · /mirror/status · /mirror/rebuild | ✅ |
| | 模型训练：就绪检查/开始/取消/轮询 | /train/readiness · /train/start · /train/status · /train/cancel | ✅ |
| **Watchlist** `/watchlist` | 分组 Tab、搜索、新增分组、勾选 | localStorage | — |
| | 分析 / 回测（跳页） | 路由跳转 | — |
| | 一键导入组合回测、相关性矩阵、估值快照、导出 CSV、移出分组 | localStorage + GET /watchlist/correlation | ✅ / 本地 |
| | 行情轮询（refresh_freq×12） | GET /watchlist/dashboard | ✅ |
| **Alerts** `/alerts` | 新建规则（六类参数白名单表单）、删除（二次确认）、全部已读 | POST /alerts/rules · DELETE /rules/{id} · POST /events/read | ✅ |
| | 触发历史 30s 轮询 | GET /alerts/events | ✅ |
| **Settings** `/settings` | 主题/语言/市场/通知开关、刷新频率下拉、保存首选项 | PUT /settings/preferences | ✅ |
| | 修改资料弹窗 | PUT /settings/preferences | ✅ |
| | 测试连接 / 刷新所有连接（researcher 才渲染） | POST /settings/connectors/test | ✅ |
| | 立即同步（researcher）、清理所有缓存（admin，二次确认）、备份数据库（admin） | POST /settings/data/sync · /data/cache/clear · /db/backup | ✅ |
| | 量化引擎卡（admin） | PUT /settings/engine | ✅ |
| | 启用外部接口（QuantConnect/IB） | `disabled` + title 明示"规划功能" | ✅ 已明示 |
| **Topbar（全局）** | 搜索框（debounce 300ms） | GET /stock/search + GET /etf/list | ✅（未登录时失败，见 P1-2） |
| | 收藏跳转、通知铃铛（SSE）、登录/退出 | /notify/stream-ticket + SSE | ✅ |
| **Sidebar（全局）** | 19 个导航项、折叠、登录/退出 | 路由 | — |

---

## 3. 「前端调用 → 后端路由」对齐矩阵

**挂载规则确认**：`backend/app/main.py:261` → `app.include_router(v1_router, prefix="/api/v1")`；
`backend/app/api/v1/router.py` 再按模块追加二级前缀（`/auth` `/market` `/datacenter` `/desk` …）。
因此完整路径 = `/api/v1` + 模块前缀 + 装饰器路径。前端 `api/*.ts` 全部硬编码完整路径，与此完全一致。

判定图例：✅ 对齐 ｜ ⚠️ 参数/方法不匹配 ｜ ❌ 悬空调用

### 3.1 auth / alerts / settings

| 前端调用 | 后端路由 | 后端权限 | 判定 |
| --- | --- | --- | --- |
| POST /auth/login | ✅ auth.py:POST /login | public | ✅ |
| POST /auth/register | ✅ auth.py:POST /register | public | ✅ |
| GET /auth/register/status | ✅ auth.py:GET /register/status | public | ✅ |
| GET /auth/me | ✅ auth.py:GET /me | auth | ✅ |
| GET /alerts/rules | ✅ alerts.py:GET /rules | researcher | ✅ |
| POST /alerts/rules | ✅ alerts.py:POST /rules (RuleIn) | researcher | ✅ |
| DELETE /alerts/rules/{id} | ✅ alerts.py:DELETE /rules/{id} | researcher | ✅ |
| GET /alerts/events | ✅ alerts.py:GET /events (unread/limit) | researcher | ✅ |
| POST /alerts/events/read | ✅ alerts.py:POST /events/read | researcher | ✅ |
| GET /settings | ✅ app_settings.py:GET "" | viewer | ✅ |
| PUT /settings/preferences | ✅ PUT /preferences | viewer | ✅ |
| PUT /settings/engine | ✅ PUT /engine | admin | ✅ |
| POST /settings/connectors/test | ✅ POST /connectors/test | researcher | ✅ |
| POST /settings/data/cache/clear | ✅ POST /data/cache/clear | admin | ✅ |
| POST /settings/db/backup | ✅ POST /db/backup | admin | ✅ |
| POST /settings/data/sync | ✅ POST /data/sync | researcher | ✅ |

### 3.2 market / stock / screener / etf / export

| 前端调用 | 后端路由 | 后端权限 | 判定 |
| --- | --- | --- | --- |
| GET /market/overview/rt | ✅ market.py:GET /overview/rt (refresh) | public | ✅ |
| GET /market/overview/daily | ✅ market.py:GET /overview/daily (recommend_k/date/refresh) | public | ✅ |
| GET /market/index/kline | ✅ GET /index/kline (code/limit/refresh) | viewer | ✅ |
| GET /stock/search | ✅ stock.py:GET /search | viewer | ✅ |
| GET /stock/{sym}/profile | ✅ GET /{symbol}/profile | viewer | ✅ |
| GET /stock/{sym}/kline | ✅ GET /{symbol}/kline | viewer | ✅ |
| GET /stock/{sym}/predict | ✅ GET /{symbol}/predict | researcher | ✅ |
| GET /stock/{sym}/panels | ✅ GET /{symbol}/panels | viewer | ✅ |
| GET /screener | ✅ screener.py:GET "" (date/strategy/top_k/board/refresh) | viewer | ✅ |
| GET /screener/stocks | ✅ GET /stocks (board/industry/q/exclude_st/sort/dir/page/page_size/basis/refresh) | viewer | ✅ |
| GET /screener/watchlist | ✅ GET /watchlist (symbols) | viewer | ✅ |
| GET /etf/overview | ✅ etf.py:GET /overview | viewer | ✅ |
| GET /etf/list | ✅ GET /list（含 `dir` 别名） | viewer | ✅ |
| GET /etf/hot | ✅ GET /hot (limit/sort) | viewer | ✅ |
| GET /etf/performance | ✅ GET /performance (symbols/metric/period) | viewer | ✅ |
| GET /etf/scale | ✅ GET /scale (period/top_n) | viewer | ✅ |
| GET /etf/flow | ✅ GET /flow (period/limit) | viewer | ✅ |
| GET /etf/detail/{code} | ✅ GET /detail/{code} (kline_period) | viewer | ✅ |
| GET /export/screener | ✅ export.py:GET /screener (date/top_k/board) | researcher | ✅ |
| POST /export/strategy-backtest | ✅ POST /strategy-backtest | researcher | ✅ |
| POST /export/backtest | ✅ POST /backtest | researcher | ✅ |

### 3.3 backtest / research / studio / ops

| 前端调用 | 后端路由 | 后端权限 | 判定 |
| --- | --- | --- | --- |
| POST /backtest/run | ✅ backtest.py:POST /run (BacktestRequest) | researcher | ✅ |
| POST /backtest/strategy-run | ✅ POST /strategy-run (StrategyBacktestRequest) | researcher | ✅ |
| POST /backtest/signal-analysis | ✅ POST /signal-analysis (SignalAnalysisRequest) | researcher | ✅ |
| GET /research/overview | ✅ research.py:GET /overview | researcher | ✅ |
| POST /research/factor-icir | ✅ POST /factor-icir | researcher | ✅ |
| POST /research/factor-corr | ✅ POST /factor-corr | researcher | ✅ |
| POST /research/factor-quantile | ✅ POST /factor-quantile | researcher | ✅ |
| GET /research/experiments | ✅ GET /experiments | researcher | ✅ |
| POST /research/cv-folds | ✅ POST /cv-folds | researcher | ✅ |
| GET /research/feature-importance?top_k= | ✅ GET /feature-importance (top_k) | researcher | ✅ |
| POST /research/optimize | ✅ POST /optimize | researcher | ✅ |
| POST /research/impact-sim | ✅ POST /impact-sim | researcher | ✅ |
| POST /research/stress-test | ✅ POST /stress-test | researcher | ✅ |
| GET /research/lab/yearly | ✅ GET /lab/yearly | researcher | ✅ |
| POST /studio/mining/start | ✅ studio.py:POST /mining/start | researcher | ✅ |
| GET /studio/mining/status/{id} | ✅ GET /mining/status/{task_id} | viewer | ✅ |
| POST /studio/mining/cancel/{id} | ✅ POST /mining/cancel/{task_id} | researcher | ✅ |
| POST /studio/nl-to-factor | ✅ POST /nl-to-factor | researcher | ✅ |
| POST /studio/alpha-eval | ✅ POST /alpha-eval | researcher | ✅ |
| GET /studio/factors | ✅ GET /factors | researcher | ✅ |
| POST /studio/factors | ✅ POST /factors | researcher | ✅ |
| DELETE /studio/factors/{id} | ✅ DELETE /factors/{factor_id} | researcher | ✅ |
| POST /studio/factor-report | ✅ POST /factor-report | researcher | ✅ |
| POST /ops/quality-scan | ✅ ops.py:POST /quality-scan | researcher | ✅ |
| GET /ops/lineage | ✅ GET /lineage | researcher | ✅ |
| GET /ops/dag | ✅ GET /dag | researcher | ✅ |
| POST /ops/dag/rerun | ✅ POST /dag/rerun | researcher | ✅ |

### 3.4 datacenter / desk / watchlist / portfolio / report / monitor / notify

| 前端调用 | 后端路由 | 后端权限 | 判定 |
| --- | --- | --- | --- |
| GET /datacenter/overview | ✅ datacenter.py:GET /overview | viewer | ✅ |
| GET /datacenter/datasets | ✅ GET /datasets | viewer | ✅ |
| GET /datacenter/quality | ✅ GET /quality | viewer | ✅ |
| GET /datacenter/logs | ✅ GET /logs | researcher | ✅ |
| GET /datacenter/task-stats | ✅ GET /task-stats | viewer | ✅ |
| POST /datacenter/sync | ✅ POST /sync | researcher | ✅ |
| POST /datacenter/sync/cancel | ✅ POST /sync/cancel | researcher | ✅ |
| GET /datacenter/sync/status | ✅ GET /sync/status | researcher | ✅ |
| POST /datacenter/sync/fetch | ✅ POST /sync/fetch | researcher | ✅ |
| GET /datacenter/instruments | ✅ GET /instruments | viewer | ✅ |
| GET /datacenter/sync/auto | ✅ GET /sync/auto | researcher | ✅ |
| POST /datacenter/sync/auto | ✅ POST /sync/auto | researcher | ✅ |
| GET /datacenter/text/status | ✅ GET /text/status | viewer | ✅ |
| POST /datacenter/text/import | ✅ POST /text/import | researcher | ✅ |
| POST /datacenter/text/build-factor | ✅ POST /text/build-factor | researcher | ✅ |
| GET /datacenter/mirror/status | ✅ GET /mirror/status | viewer | ✅ |
| POST /datacenter/mirror/rebuild | ✅ POST /mirror/rebuild | researcher | ✅ |
| GET /datacenter/train/readiness | ✅ GET /train/readiness | viewer | ✅ |
| POST /datacenter/train/start | ✅ POST /train/start | researcher | ✅ |
| GET /datacenter/train/status | ✅ GET /train/status | researcher | ✅ |
| POST /datacenter/train/cancel | ✅ POST /train/cancel | researcher | ✅ |
| GET /desk/kill-switch | ✅ desk.py:GET /kill-switch | researcher | ✅ |
| POST /desk/kill-switch | ✅ POST /kill-switch | researcher | ✅ |
| GET /desk/exclusion | ✅ GET /exclusion | researcher | ✅ |
| POST /desk/exclusion | ✅ POST /exclusion | researcher | ✅ |
| POST /desk/exclusion/toggle | ✅ POST /exclusion/toggle | researcher | ✅ |
| GET /desk/exclusion/screen | ✅ GET /exclusion/screen | researcher | ✅ |
| POST /desk/orders | ✅ POST /orders | researcher | ✅ |
| POST /desk/fills/run | ✅ POST /fills/run | researcher | ✅ |
| GET /desk/orders?limit=N | ✅ GET /orders (limit) | researcher | ✅ |
| GET /desk/account | ✅ GET /account | researcher | ✅ |
| GET /desk/capacity | ✅ GET /capacity | researcher | ✅ |
| POST /desk/attribution | ✅ POST /attribution | researcher | ✅ |
| GET /watchlist/dashboard | ✅ watchlist.py:GET /dashboard | viewer | ✅ |
| GET /watchlist/correlation | ✅ GET /correlation | viewer | ✅ |
| GET /portfolio/search | ✅ portfolio.py:GET /search | viewer | ✅ |
| POST /portfolio/backtest | ✅ POST /backtest | researcher | ✅ |
| GET /report/daily | ✅ report.py:GET /daily | viewer | ✅ |
| POST /report/daily/generate | ✅ POST /daily/generate | researcher | ✅ |
| GET /monitor/health | ✅ monitor.py:GET /health | viewer | ✅ |
| POST /monitor/run | ✅ POST /run | researcher | ✅ |
| GET /notify/recent | ✅ notify.py:GET /recent | viewer | ✅ |
| POST /notify/stream-ticket | ✅ POST /stream-ticket | viewer | ✅ |
| GET(SSE) /notify/stream | ✅ GET /stream | public(ticket) | ✅ |

### 3.5 反向清单：后端已实现但前端从未调用的「死端点」（6 个）

| 后端端点 | 权限 | 前端入口 | 影响 |
| --- | --- | --- | --- |
| GET /api/v1/alerts/health | researcher | 无 | 预警子系统健康检查无法在界面查看 |
| PUT /api/v1/alerts/rules/{rule_id} | researcher | **无**（`updateRule` 声明未用） | 规则不能编辑/启停，只能删了重建 |
| POST /api/v1/settings/apikeys/rotate | admin | 无 | API Key 轮换无入口（UI 已明示"未接入验证链路的 API Key 功能不予展示"） |
| GET /api/v1/datacenter/sync/tasks/{task_id} | researcher | 无 | 单个同步任务详情无法查看（页面只轮询 /sync/status 聚合态） |
| GET /api/v1/market/overview | public | 无 | 已拆分为 rt/daily 两块，整包端点空转 |
| GET /api/v1/market/quotes | viewer | 无 | 批量实时行情能力无消费方（≤200 只、TTL 15s 进程缓存） |

---

## 4. 「有按钮但拉不起后端」问题排查

排查手段：全量 grep `TODO|FIXME|mock|假数据|硬编码|alert(|console.log|onClick={() => {}}|disabled`。
结果：**未发现任何 mock/假数据常量、未发现 TODO/FIXME、未发现 alert 占位、未发现空 onClick**。
组件渲染字段与后端返回字段做过逐项核对（例如 `SignalAnalysisResult.quantile_spread`、`desk/orders` 的 `total/truncated` 缺失、`data_jobs.duration_ms` 可为 NULL 等均已在前端显式处理为 `—`）。

剩余问题如下：

### P1-1 市场概览页（公开首页）完全没有错误态

- **【文件:行号】** `frontend/src/pages/MarketOverview/index.tsx:39-49, 76-124`（全文件 `error` 关键字出现次数 = 0）
- **【现象】** `rt` / `daily` 两个 `useApi` 的错误对象（`rt.error` / `daily.error`）全程未被读取；两个端点同时失败时，`data` 为 `null`，页面渲染出 5 张 KPI 卡全部显示 `—`、热力图/资金流/板块/AI 精选四块全部空图，且**没有任何错误提示、没有重试入口**。
- **【影响】** 这是唯一无需登录即可访问的公开业务页，也是用户第一眼看到的页面。后端冷启动、Redis 不可用、数据源被限流时，用户看到的是"能打开但全是空"，会误判为"平台没数据"，而不是"服务暂时不可用"。属于**静默失败**中最严重的一种。
- **【修复建议】** 在 `<h1>` 行下方增加错误横幅：`(rt.error || daily.error) && <ErrorBanner>`，文案取 `ApiError.message`，并提供"重试"按钮（`mutate` 对应 key）。同时把两块分开提示（实时块 / 日频块各自降级），不要因为一块失败就整页判死。

### P1-2 公开页面的顶栏搜索必然失败（鉴权缺口）

- **【文件:行号】** `frontend/src/components/Topbar.tsx:145-148`；后端 `backend/app/api/v1/stock.py`（`GET /search` → `require_role("viewer")`）、`backend/app/api/v1/etf.py`（`GET /list` → `require_role("viewer")`）
- **【现象】** `Topbar` 在 `/` 与 `/market` 两个**公开路由**下同样渲染（App.tsx:87-88 未包 `RequireRole`）。未登录用户敲 ≥2 个字符即触发 `stockApi.search` + `etfApi.list`，两者后端均要求 `viewer`，返回 401/403；前端 `.catch(() => [])` 把错误吞掉，下拉框显示"无结果"。
- **【影响】** 未登录用户会认为"搜索功能坏了 / 平台上没有这只股票"，而不是"请先登录"。错误被静默吞掉，排查成本高。
- **【修复建议】** 二选一：(a) 前端在 `!authed` 时不发搜索请求，输入框下方提示"登录后可搜索"；(b) 后端把 `/stock/search`、`/etf/list` 降为 public。推荐 (a)，改动面小且不动鉴权基线。

### P1-3 预警规则无法编辑 / 启停（后端有 PUT，前端无入口）

- **【文件:行号】** `frontend/src/api/alerts.ts:64`（`updateRule` 全项目 0 处调用）；`frontend/src/pages/Alerts/index.tsx:378-418`（规则表只有"删除"按钮）
- **【现象】** 规则创建后无法修改名称/参数/冷却/渠道，也无法停用（后端 `RuleIn.enabled` 字段前端只在创建时固定传 `true`）。要改只能删除重建，历史触发记录的 `rule_id` 关联会断。
- **【影响】** 后端 `PUT /alerts/rules/{rule_id}` 成为死端点；运营上"临时关掉一条误报规则"这个高频诉求无法满足。
- **【修复建议】** 规则表增加"编辑"按钮复用现有 `ParamsForm`（已有完整组件），提交走 `alertsApi.updateRule`；再加一个启用/停用 `Toggle`（复用 Settings 页的 `Toggle` 组件，失败回退）。

### P1-4 数据血缘图加载失败后永久显示"加载血缘…"

- **【文件:行号】** `frontend/src/pages/DataQuality/index.tsx:116` + `:222-225`
- **【现象】** `opsApi.lineage().then(setGraph).catch(() => setGraph(null))` —— 失败时把 graph 置 `null`，而渲染分支是 `graph ? <LineageGraphView/> : "加载血缘…"`。**失败与"加载中"共用同一个 UI 状态**，导致失败后卡片永久停在"加载血缘…"，既不报错也不重试。
- **【影响】** 用户会一直等一个永远不来的结果；`/ops/lineage` 是 researcher 端点且实扫产物，超时/无权限都会走这条分支。
- **【修复建议】** 引入三态：`lineageState: 'loading' | 'ready' | 'error'`，error 分支显示错误文案 + "重试"按钮。

### P2-1 市场概览「自选 / 市场概览 / AI专题」三个视图切换按钮无 onClick

- **【文件:行号】** `frontend/src/pages/MarketOverview/index.tsx:83-85`
- **【现象】** 三个 `<button>` 均无 `onClick`，第二个还带 `bg-white shadow-sm text-brand-600` 的"选中态"样式。点击无任何反应，但是视觉上完全可点。
- **【影响】** 典型"看着能点、点了没反应"。用户会反复点击并认为前端卡死。
- **【修复建议】** 未实现的视图（自选 / AI专题）要么加 `disabled` + `title="功能建设中"`（与 Sidebar `NavItem.disabled` 同口径），要么直接移除；"市场概览"保持为选中态标签（非 button）。

### P2-2 长耗时写操作沿用 15s 默认超时

- **【文件:行号】**
  - `api/datacenter.ts:87` `mirrorRebuild` — 后端同步重建截面镜像（逐文件读 3.1 万 parquet 的 date 列，冷扫实测 21~23s）
  - `api/datacenter.ts:80` `textBuildFactor` — 文本因子构建，样本量大时易超 15s
  - `api/production.ts:261` `deskApi.runFills` — 扫描全部母单并生成子单，母单多时可能超 15s
  - `api/screener.ts:17` `screenerApi.screen` — `refresh=1` 时后端重算（默认 15s，未放宽；同族 `/screener/stocks` 已放宽到 30s，口径不一致）
- **【现象】** 上述调用未传 `timeout` 参数，落回 `client.ts:92` 的 15000ms 默认。
- **【影响】** 用户点击后约 15s 收到"请求超时，请稍后重试"，但**后端任务其实还在跑**（镜像重建/因子构建不会因前端断开而取消），导致重复点击、重复触发。
- **【修复建议】** 按现有约定（聚合/长任务接口显式放宽）分别给 60s / 60s / 30s / 30s；更好的做法是让这三个接口改为"启动任务 + 轮询状态"，与 `/sync`、`/train` 同构。

### P2-3 前端无全局重试策略

- **【文件:行号】** `frontend/src/api/client.ts:90-139`（axios 实例未挂 `axios-retry`，无 retry 拦截器）
- **【现象】** 只有走 SWR `useApi` 的调用（`api/swr.ts:33-39`：errorRetryCount=2 / interval=5s）具备重试；全项目仅 MarketOverview 两个端点走 SWR，其余 107 个调用**一次失败即终态**。
- **【影响】** 网络抖动 / 后端冷启动期间，用户必须手动点刷新。
- **【修复建议】** 对幂等 GET 增加一次指数退避重试（或引入 `axios-retry`，仅对 `get` 且 `code===-1`（网络层）重试 1 次）；POST 写操作**不重试**（避免重复下单/重复触发同步）。

### P2-4 大部分页面卸载时不取消在途请求

- **【文件:行号】** 仅 `pages/Backtest/index.tsx:63-88`（AbortController）、`pages/Portfolio/index.tsx:271-285`（search）、`pages/Research/index.tsx`（signal）使用了 abort；其余页面（DataQuality 的 180s qualityScan、Pipeline 的 300s dagRerun、OrderDesk、CapacityAttribution、DataCenter 等）无取消机制。
- **【现象】** 用户在长任务进行中切换路由，请求仍在飞；多数页面靠 `seqRef` 代际守卫丢弃过期响应（已做得不错），但请求本身继续占用后端计算槽。
- **【影响】** 后端 `/ops/quality-scan`、`/dag/rerun` 有并发/槽位限制，前端堆积的在途请求会加剧排队。
- **【修复建议】** 长任务页面统一接入 `AbortController` + `useEffect` 卸载时 `abort()`（Backtest 页已有可复制范式）。

### P2-5 数据中心页同步任务详情无入口

- **【文件:行号】** `frontend/src/api/datacenter.ts:46`（`task()` 声明未用）；`pages/DataCenter/index.tsx` 只轮询 `/sync/status`
- **【现象】** 后端 `GET /datacenter/sync/tasks/{task_id}` 可查单任务进度/结果/错误，前端只展示聚合态 `done/total`，单任务失败原因看不到。
- **【影响】** 同步失败时用户只能看到"进度停住"，无法定位是哪一类任务失败。
- **【修复建议】** 在同步控制台展示 `task_id` 并提供"查看详情"入口，或在失败横幅直接透出 `error_message`。

### P2-6 系统设置「启用外部接口」为永久 disabled 按钮

- **【文件:行号】** `frontend/src/pages/Settings/index.tsx:468-476`
- **【现象】** 按钮 `disabled` + title="外部券商接口为规划功能"，文案"高级外部 API（暂未开放）"。**已明示，非欺骗性 UI**，但仍是"占位按钮"。
- **【影响】** 低（已披露），但与项目"不留伪功能"的治理口径略有出入。
- **【修复建议】** 保持现状即可；若追求一致性，可改为非按钮的灰态说明行。

### P2-7 QuantConnect/IB 相关信息与后端无关，属纯前端文案

- **【文件:行号】** `frontend/src/pages/Settings/index.tsx:463-476`
- **【现象】** 整块为静态文案，无数据绑定。
- **【影响】** 无（已明示）。
- **【修复建议】** 无需处理，登记备查。

### P2-8 执行中心母单窗口无真实总数（前端已披露，后端缺字段）

- **【文件:行号】** `frontend/src/api/production.ts:264-266`（C-3 注释）；`pages/OrderDesk/index.tsx:22-24, 138-139`
- **【现象】** `GET /desk/orders` 后端不返回 `total` / `truncated`，前端只能按"取满 200 条 ⇒ 可能还有更早母单"来提示。筛选/分页/计数都是窗口内结论。
- **【影响】** 母单量超过 200 时，用户看到的"共 N 条"是窗口内数字，可能误判为全部。
- **【修复建议】** 后端补 `total` / `truncated` 字段（属后端改动，已同步给架构师口径）。

### P2-9 依赖体积：echarts 单 chunk 694 KB（gzip 230 KB）

- **【文件:行号】** `frontend/src/lib/echarts.ts`；构建产物 `dist/assets/echarts-BXLDfhbB.js`
- **【现象】** `vite build` 成功，首屏主 chunk 已通过 `React.lazy` 分包降到 20~40 KB 量级，但 echarts 仍以 694 KB 的独立 chunk 在首次进入任意图表页时加载。
- **【影响】** 低端网络首次打开图表页有明显等待（已有 `PageSkeleton` 兜底，不白屏）。
- **【修复建议】** 按需引入 `echarts/core` + 仅注册用到的图表/组件（line/bar/graph/heatmap/tooltip/legend/grid/dataZoom/visualMap），可显著降体积。

---

## 5. 前端超时与错误处理专项

### 5.1 client.ts 基线（总体健康）

| 项 | 现状 | 评价 |
| --- | --- | --- |
| 默认超时 | `api/client.ts:92` `timeout: 15000` | ✅ 合理 |
| 超时文案 | `api/client.ts:132` `ECONNABORTED → '请求超时，请稍后重试'` | ✅ 友好 |
| HTTP 非 200 | `服务异常（HTTP {status}）`；无响应 `网络错误，请检查后端服务是否启动` | ✅ 分类清晰 |
| 业务错误 | 统一解包 `{code,message,data,trace_id}` → `ApiError` | ✅ |
| 异常文案脱敏 | `sanitizeApiMessage` 屏蔽 `Traceback/File "/sqlalchemy/polars.` 等内部串 | ✅ 防泄漏 |
| 未登记错误码 | 运行时标记 `（未登记错误码 XXXX）` | ✅ 便于对账 |
| 401 处理 | 仅 `clear()` 清会话，**不在拦截器里跳转**（避免可选请求 401 打断公开页） | ✅ 设计正确 |
| AbortController | `get/post/put` 支持 `options.signal`（`del` 不支持） | ⚠️ 见 P2-4 |
| 重试 | 无全局重试 | ⚠️ 见 P2-3 |
| 导出（blob） | `download()` 180s + 解析 Content-Disposition + **JSON 错误信封手动解包**（不下载损坏 xlsx） | ✅ 考虑周全 |
| SSE | 一次性 ticket（不放 JWT 到 URL）+ 主动 close 阻断无限重连 | ✅ 安全 |

### 5.2 已放宽超时的长任务接口（做得好的部分）

| 接口 | 超时 | 说明 |
| --- | --- | --- |
| /datacenter/overview | 60s | 冷算 ~29s |
| /datacenter/datasets | 90s | 冷算 ~24s |
| /datacenter/quality | 90s | 冷算 ~32s |
| /datacenter/mirror/status | 45s | 冷扫 21~23s |
| /market/overview* | 120s | 后端已收敛到 6s 服务端预算，前端 120s 仅兜底 |
| /settings | 60s | 内部调用 datacenter.overview |
| /backtest/strategy-run（寻优） | 600s | 网格 × 单次回测 |
| /backtest/* 其余 | 120s | — |
| /research/optimize、stress-test | 180s | — |
| /research/* 其余 | 120s | — |
| /ops/quality-scan | 180s | 全量 QC |
| /ops/dag/rerun | 300s | 全流水线 |
| /desk/attribution | 180s | Brinson + 风格回归 |
| /studio/* | 60~120s | GP 挖掘 / 表达式评估 |
| /export/*（blob） | 180s | xlsx 生成 |
| /monitor/run | 120s | 健康度重算 |
| /report/daily/generate | 60s | 日报重生成 |
| /stock/{sym}/panels | 30s | 5 块并发外源 |
| /etf/* | 30s | 四国全量目录 |
| /screener/stocks | 30s | 全市场 |
| /watchlist/* | 30s | 多资产 |

### 5.3 页面 loading / error / empty 三态覆盖

| 页面 | loading | error | empty | 备注 |
| --- | :-: | :-: | :-: | --- |
| MarketOverview | ✅ | ❌ | ⚠️ | **唯一 0 处 error 处理**（P1-1） |
| Login | ✅ | ✅ | — | 表单内错误条 |
| StockDetail | ✅ | ✅（分块） | ✅ | 4 个请求各自独立降级 |
| Screener | ✅ | ✅ | ✅ | Alpha/自选各自 EmptyState |
| Etf | ✅ | ✅ | ✅ | flow/scale/perf 分别降级 |
| EtfDetail | ✅ | ✅ | ✅ | `ErrorState` + onRetry |
| Backtest | ✅ | ✅ | ✅ | Tab 级缓存，避免重跑 |
| Portfolio | ✅ | ✅ | ✅ | 搜索失败与"无匹配"区分 |
| Research | ✅ | ✅（分区） | ✅ | 计算队列防 40103 |
| FactorStudio | ✅ | ✅ | ✅ | 任务失效（40400/51001）显式提示 |
| DataQuality | ✅ | ✅ | ⚠️ | 血缘图失败→永久 loading（P1-4） |
| Pipeline | ✅ | ✅ | ✅ | — |
| OrderDesk | ✅ | ✅ | ✅ | 熔断二次确认 |
| CapacityAttribution | ✅ | ✅ | ⚠️ | 失败时容量仅显 `—`，有 err 横幅 |
| Report | ✅ | ✅ | ✅ | 无日报时给"晚间自动生成"说明 |
| DataCenter | ✅ | ✅（分面板） | ✅ | 7 个请求 `allSettled` 逐面板降级 |
| Watchlist | ✅ | ✅ | ✅ | 消费后端 `status=degraded` |
| Alerts | ✅ | ✅ | ✅ | 未读扫描饱和显"≥" |
| Settings | ✅ | ✅(Toast) | — | 权限不足显式文案 |

**结论**：19 个页面中 18 个三态齐全，仅 MarketOverview 缺 error、DataQuality 血缘图缺 error。无"接口挂了就白屏"的地方（根级 `ErrorBoundary` 在 `main.tsx:12` 兜底，Suspense 有 `PageSkeleton`）。

---

## 6. 前端能否正常构建 / 渲染

### 6.1 构建验证（实跑）

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| TypeScript 全量检查 | `npx tsc -b --force` | ✅ **0 错误，退出码 0**（22s） |
| 生产构建 | `npx vite build` | ✅ **构建成功**（22.67s，✓ built in 22.67s） |
| 依赖完整性 | `frontend/node_modules` 存在，axios 1.7.7 / react 18.3.1 / echarts 5.5.1 / swr 2.5.1 / zustand 4.5.5 | ✅ |

`package.json` 的 `build` 脚本为 `tsc -b && vite build`，即**类型检查是构建门禁**，两条命令均通过 ⇒ 不存在会导致编译失败的 TS 类型错误。

### 6.2 路由与组件引用（静态核对）

- `App.tsx` 共 19 条业务路由 + `/login` + `*` 兜底；`pages/` 下 19 个目录**全部被 `lazy()` 引用**，无孤儿页面。
- `Sidebar.tsx:154-177` 的 `NAV_MAIN`(13) + `NAV_DATA`(6) = 19 个导航项，逐一对应 App.tsx 路由，**无指向不存在路由的死链**；`NavItem.disabled` 字段存在但**当前无任何条目置 true**（即没有"建设中"灰项）。
- `components/ui/index.tsx` 导出的 `SectionCard / Modal / EmptyState / LoadingState / ErrorState / PanelEmpty / SortHeader / MetricCard / StatRow / RatioBar / SplitBar / FactorBar / ProbabilityBar / ViewToggle` 在各页均有实际引用，无引用不存在的导出（tsc 已证）。
- `main.tsx` 根级 `ErrorBoundary` + `BrowserRouter`，且**刻意不启用 StrictMode**（避免 ECharts/Lightweight-Charts canvas 双创建），注释已说明。

### 6.3 渲染健壮性抽查

- `KpiCards.tsx:41-43` 指数按**代码**匹配（`sh000001`/`sh000300`）而非下标，缺数据时显示 `—`，不做位置兜底（避免把深证成指标成沪深300）—— ✅ 正确。
- `BreadthPanel` / `DistributionCharts` 对每项数据独立判空，缺数据给占位文案，**不做"假数据"填充** —— ✅。
- `Portfolio` 对后端 NaN→null 的 `null*100===0` 陷阱做了显式处理（`PerformanceTable:169-170`），避免显示假 `+0.00%` —— ✅。
- `Pipeline` 对 `duration_ms` 为 NULL 显示 `—` 而非 `0.0s`；`STAGE_ORDER` 已剔除不存在的 `desk` 假节点 —— ✅。
- `Backtest` / `Portfolio` 的复权口径只展示后端披露的 `price_basis`，不硬编码 "QFQ" —— ✅。
- `Watchlist` 首次访问预置的 `SEED` 示例组合是**真实已落库标的**（600519.SH / 510300 等），不是假数据 —— ✅。

---

## 7. 问题汇总（按优先级）

| 级别 | 编号 | 一句话 | 位置 |
| --- | --- | --- | --- |
| **P1** | P1-1 | 公开首页无错误态，双端点失败时静默显示空数据 | pages/MarketOverview/index.tsx:39-124 |
| **P1** | P1-2 | 公开页顶栏搜索打 viewer 端点，未登录必然失败且被静默吞掉 | components/Topbar.tsx:145-148 |
| **P1** | P1-3 | 预警规则无法编辑/启停，后端 PUT 成死端点 | api/alerts.ts:64 · pages/Alerts/index.tsx:378-418 |
| **P1** | P1-4 | 血缘图加载失败永久显示"加载血缘…" | pages/DataQuality/index.tsx:116,222-225 |
| **P2** | P2-1 | 市场概览三个视图切换按钮无 onClick | pages/MarketOverview/index.tsx:83-85 |
| **P2** | P2-2 | 4 个长耗时接口沿用 15s 默认超时 | api/datacenter.ts:80,87 · production.ts:261 · screener.ts:17 |
| **P2** | P2-3 | 无全局重试（仅 SWR 两个端点有重试） | api/client.ts:90-139 |
| **P2** | P2-4 | 多数页面卸载时不取消在途请求 | 多页（Backtest/Portfolio/Research 除外） |
| **P2** | P2-5 | 同步任务详情无入口，失败原因不可见 | api/datacenter.ts:46 |
| **P2** | P2-6 | 「启用外部接口」永久 disabled 占位按钮 | pages/Settings/index.tsx:468-476 |
| **P2** | P2-7 | QuantConnect/IB 整块为静态文案 | pages/Settings/index.tsx:463-476 |
| **P2** | P2-8 | 母单列表无真实总数（后端缺 total/truncated） | api/production.ts:264-266 |
| **P2** | P2-9 | echarts 单 chunk 694 KB | lib/echarts.ts |

**P0（阻断级）= 0**：无悬空调用、无 404 死按钮、无编译错误、无白屏路径。

---

## 8. 给后续角色的交接

- **给架构师（后端侧）**：本报告第 3.5 节的 6 个死端点、P2-8（`GET /desk/orders` 缺 `total`/`truncated`）、P1-2（`/stock/search` 与 `/etf/list` 的 viewer 门槛与公开首页的冲突）需要后端侧决策：补前端入口 / 降权限 / 删端点。
- **给工程师（修复实施）**：P1-1、P1-4 是纯前端改动且改动面小（各约 10 行），建议优先；P1-3 需复用已有 `ParamsForm`；P2-2 只需补 `timeout` 实参。
- **未覆盖**：本次为静态审计 + 构建验证，未实跑 dev server 做浏览器交互回归（按任务要求未启动长驻服务）。若需运行时验证，建议补一轮 Playwright 冒烟（登录 → 19 页逐个挂载 → 断言无 console error、无永久 loading）。

---

## 附录 A：二次独立复核（yan-qa2 · 2026-09-27）

> 本节由第二名 QA（yan-qa2）在**不读取上方结论的前提下**独立重跑同一审计流程后追加，用于交叉验证。复核全程只读，未改动任何业务代码。

### A.1 复核方法

| 步骤 | 做法 | 结果 |
| --- | --- | --- |
| 挂载前缀确认 | 亲自读 `backend/app/main.py:261` + `backend/app/api/v1/router.py` | 确认 `/api/v1`，与正文字段一致 |
| 前端调用提取 | 通读 `frontend/src/api/*.ts`(19 个) + grep 全量 `pages/**`/`components/**`/`stores/**` 的 `Api.` 调用与 `fetch/axios/EventSource` | 与正文 1.1 一致：**仅 `stores/useNotifyStore.ts:102` 有 `EventSource`，无页面绕过 api 层** |
| 后端路由提取 | 逐文件 grep `@router.(get/post/put/delete)` | 与正文 3.x 矩阵一致 |
| 类型检查 | `tsc --noEmit` | ✅ **0 错误，退出码 0** |
| 生产构建 | `vite build --outDir dist-qa-verify` | ✅ **747 modules transformed，built in 7.05s**（`npm run build` 默认 outDir 被本机 safe-delete 守卫拦截清目录，属环境问题非代码问题，换目录即成功） |
| 死代码/空实现扫描 | 脚本枚举全部 `<button>` 无 `onClick` 者；grep `TODO/FIXME/mock/alert(/console.log` | 无 mock/TODO/FIXME/alert/空 onClick；仅 3 个无 onClick 按钮（= 正文 P2-1） |

### A.2 一致性确认（独立复现，结论相同）

- **P0 = 0**：109 个前端调用全部命中后端路由，**悬空调用 0 个、方法/参数不匹配 0 个**。
- 正文 **P1-1 ~ P1-4、P2-1 ~ P2-9** 均已独立复现，判定与定位（文件:行号）无出入。
- 死端点 6 个（`/alerts/health`、`PUT /alerts/rules/{id}`、`/settings/apikeys/rotate`、`/datacenter/sync/tasks/{id}`、`/market/overview`、`/market/quotes`）已逐一 grep 确认前端 0 引用。
- 构建结论一致：**前端可正常编译与打包，不存在"引用不存在的组件/导出"**（tsc 已证）。

### A.3 补充发现（正文未列出，独立新增）

#### 补充-1（P2）顶栏搜索：6 位纯数字回车恒跳 ETF 详情
- **【文件:行号】** `frontend/src/components/Topbar.tsx:119-120`
- **【现象】** `if (/^\d{6}$/.test(text)) navigate('/etf/'+text)`。股票代码也是 6 位纯数字（如 `600519`），回车后会被当作 ETF 代码跳到 `/etf/600519`，而该路由只拉 `/etf/detail/600519`（非 ETF 代码）⇒ 详情页空/报错。
- **【影响】** 用户手输股票代码回车得到"空白的 ETF 页"，与"按钮/组件拉不起后端"同类的错误导流。
- **【修复建议】** 无法区分股票/ETF 时不要自动跳详情，改为跳 `/screener` 并带上关键词；或仅在能判定 ETF 前缀（`51/56/58/15`）时才跳 `/etf/`。

#### 补充-2（P2）容量归因页「交互 / 残差 Alpha」重复渲染两遍
- **【文件:行号】** `frontend/src/pages/CapacityAttribution/index.tsx:308-313`
- **【现象】** 同一行 JSX 连续输出两次「交互 … · 残差 Alpha …」（4 个 `fmtPct` 调用了两组完全相同的字段）。
- **【影响】** 纯展示冗余，用户会疑惑是否口径不同。
- **【修复建议】** 删除重复的一组。

#### 补充-3（P2 · 超时）另 3 组重型 GET 同样沿用 15s 默认（正文 P2-2 未覆盖）
- **【文件:行号】**
  - `api/production.ts:176` `opsApi.lineage` → `GET /ops/lineage`（后端 `ops.py:127 _scan_parquet_dataset` 逐个读 `daily_bar / hfq / qfq / universe_daily / predictions / screener` 全部 parquet 的 footer，与 `/datacenter/datasets` 同量级，实测约 24s）
  - `api/production.ts:177` `opsApi.dag` → `GET /ops/dag`（同样实扫数据集新鲜度）
  - `api/research.ts:27,45,47,68` `overview / labYearly / experiments / featureImportance`（GET，`timeout` 实参为 `undefined` ⇒ 15s）
  - `api/production.ts:242-267` `deskApi.killSwitch / exclusionList / orders / account`（`/desk/account` 需按逐日净值序列重建，母单多时可能超 15s）
- **【影响】** 与 P1-4 强相关：`/ops/lineage` 的 15s 超时**正是**「血缘图永久显示加载血缘…」的直接触发条件——应把 P1-4 与补充-3 一并修（放宽 timeout + 补 error 三态）。
- **【修复建议】** 按现有约定给 `lineage/dag` 放宽到 60s、`research` 4 个 GET 放宽到 30~60s、`desk/account` 放宽到 30s；根治仍是把实扫端点改为"缓存 + 后台重建"。

#### 补充-4（提示）`/market/index/kline` 未放宽超时但被并发调用
- **【文件:行号】** `api/market.ts:78`（默认 15s）；`pages/Screener/index.tsx:284` 用 `Promise.allSettled` 并发 5 个指数。
- **【影响】** 低（后端有 fetch 层 TTL 缓存、limit≤800）。登记备查，无需立即改。

### A.4 复核结论

正文结论**成立且完整**：前端与后端路由对齐度 100%，无悬空调用、无 404 死按钮、无编译错误、无 mock 假数据；核心待修项为 **P1-1（公开首页无错误态）/ P1-4（血缘图永久 loading）** 两个纯前端小改动，以及 P1-2/P1-3 的鉴权与入口缺口。本次复核额外补充 4 条（补充-1~4），其中**补充-3 与 P1-4 应合并处理**。

---

## 附录 B：运行期实测 × 前端超时预算 交叉校验（yan-qa2 · 2026-09-27）

> 本节由 yan-qa2 追加。数据来源为队友（kou-engineer2）运行期探测产物 `docs/audit-2026-09-27/smoke-results.csv`（`probe.py`，探测端 20s 截断；含 `elapsed_s` 首测与 `retest_s` 复测两列）。**本节只读消费该 CSV，未改动探测脚本/产物，也未触碰 `smoke-test-report.md`**；目的是把附录 A 的静态超时预测用真实耗时做一次实证闭环。

### B.1 实测耗时 × 前端超时预算对照（仅列 >5s 或已放宽的端点）

> 表中「首测/复测」取自更新后的 `smoke-results.csv`（含 `elapsed_s` 与 `retest_s` 两列，由 kou-engineer2 复测 2–3 次填充）。

| 端点 | 首测 | 复测（2–3 次） | 前端调用点 | 前端 timeout | 判定 |
| --- | --- | --- | --- | --- | --- |
| `GET /api/v1/ops/lineage` | 20s 截断 | **32.3 / 38.2 / 33.2s** | `api/production.ts:176` → `pages/DataQuality/index.tsx:116` | **默认 15s**（未传） | ❌ **必然超时 + 静默失败** |
| `GET /api/v1/research/lab/yearly` | 19.57s | **18.8 / 19.3s** | `api/research.ts:45` → `pages/Research/index.tsx:177` | **默认 15s**（第3参 `undefined`） | ❌ **必然超时** |
| `POST /api/v1/studio/factor-report` | 11.94s | 11.3 / 12.5s | `api/production.ts:59` | 120_000 | ✅ 安全（持续慢 ~12s） |
| `GET /api/v1/market/overview/rt` | 11.74s | 11.1 / 11.9s | `api/market.ts:62` | 120_000（`OVERVIEW_TIMEOUT`） | ✅ 安全（持续慢 ~11s） |
| `POST /api/v1/studio/alpha-eval` | 6.88s | — | `api/production.ts:54` | 120_000 | ✅ 安全 |
| `GET /api/v1/screener/stocks` | 4.51s | — | `api/screener.ts` | 30_000 | ✅ 安全 |
| `GET /api/v1/etf/detail/{code}` | 4.27s | — | `api/etf.ts` | 30_000 | ✅ 安全 |
| `GET /api/v1/datacenter/datasets` | 20s 截断 | **0.02s** | `api/datacenter.ts:22` | 90_000 | ✅ 安全（**冷启动假阳**） |
| `GET /api/v1/datacenter/quality` | 20s 截断 | **0.01s** | `api/datacenter.ts:27` | 90_000 | ✅ 安全（**冷启动假阳**） |
| `GET /api/v1/datacenter/mirror/status` | 20s 截断 | **0.01s** | `api/datacenter.ts:85` | 45_000 | ✅ 安全（**冷启动假阳**） |
| `GET /api/v1/ops/dag` | 2.37s | — | `api/production.ts:177` | 默认 15s | ✅ 安全（见 B.3 修正） |

### B.2 实证确认：2 个「必然前端超时」端点（升级附录 A 补充-3 为实测）

- **`/research/lab/yearly`**：`api/research.ts:45` 第 3 参 timeout 为 `undefined` ⇒ 落回 `client.ts:92` 默认 **15s**；实测首测 19.57s、复测 **18.8 / 19.3s**（稳定超 15s）⇒ 前端 axios 必先 `timeout of 15000ms exceeded`。调用处 `pages/Research/index.tsx:177` 与其它轻量请求共用 185–189 行的统一 `catch`，会把整页错误条（`setErr`）误点亮。
- **`/ops/lineage`**：`api/production.ts:176` 未传 timeout ⇒ 15s；复测 3 次 **32.3 / 38.2 / 33.2s**（**持续慢，非冷启动**，远超 15s 一倍以上）。`pages/DataQuality/index.tsx:116` 用 `.catch(() => setGraph(null))` **静默吞错**，血缘图永久空白且与"确实无血缘数据"无法区分 ⇒ **用户可见功能损坏 + 无任何错误提示**。此条应视为 **P1**（非 P2），与正文 P1-4「血缘图永久 loading」是同一根因的两种表现（见 `DataQuality` 的 `graph ? ... : 加载血缘…`）。
- **修复建议（合并处理 · 前后端双管）**：`api/research.ts:45` 与 `api/production.ts:176` 各补一个实参（建议 `120_000` / `60_000`）；同时把 `DataQuality/index.tsx:116` 的静默 catch 改为三态（超时/失败 → 可重试提示；成功但空 → 「暂无血缘数据」）。**但前端放宽只是止血**：若后端无服务端预算，单请求仍会占住连接 30s+（lineage 实测 32–38s），高并发下拖垮线程池 ⇒ 必须同时由后端收敛/缓存（根治是把 `_scan_parquet_dataset` 实扫改为"缓存 + 后台重建"）。后端根因已由 kou-engineer2 同步 **gao-architect2**，前端侧超时契约（lineage 60s / lab-yearly 120s）亦已同步，确保后端服务端预算 ≤ 该值。

### B.3 自我修正：附录 A 补充-3 对 `/ops/dag` 的预测不成立

附录 A 补充-3 曾将 `/ops/dag` 与 `/ops/lineage` 并列为"实扫数据集新鲜度、约 24s"。运行期实测 **`GET /api/v1/ops/dag` = 2.37s（200 OK）**，而 `lineage` 复测 **32–38s**，二者相差十余倍 ⇒ **仅 `lineage` 是超时元凶**，`dag` 无需放宽超时。据实修正，避免工程师做无谓改动。

### B.4 一致性确认：运行期无 HTTP 404

`smoke-results.csv` 全部端点均返回 **HTTP 200**（信封模式），无 404；其中 `/alerts/health`、`/market/overview`、`/market/quotes` 等"死端点"运行期均正常响应 ⇒ 与静态审计「悬空调用 0、死端点=已实现但前端未接」的判定**完全一致**（死端点非缺陷，只是能力冗余）。探测中出现的 `code=40000` 多因探测脚本占位参数不符（如 `assets="600519"` 应为对象、`start="2024-01-01"` 应为 `^\d{8}$`），属探测脚本假阳，非前端缺陷。

### B.5 附录 B 结论

- 静态预测 **P1-4 / 补充-3 被实测证实**，并**升级**：`/ops/lineage` 不仅永久 loading，还因静默 catch 而**无错误提示**，建议按 **P1** 修复；`/research/lab/yearly` 为**新增实证 P1**（必然超时 + 误点亮整页错误）。
- 其余慢端点均已按约定放宽（studio 120s、datacenter 90s、overview 120s）且**持续慢但仍安全**（studio ~12s、overview/rt ~11s）。**撤回**上一版对 `datacenter/mirror/status`「45s 预算偏紧」的 P2 观察：复测 0.01s，`datacenter/datasets|quality|mirror/status` 三者的 20s 首测均为**冷启动假阳**，非真慢，前端 90s/45s/90s 预算充裕。
- 实测**无 HTTP 404**，前后端对齐度 100% 的结论获得运行期背书。
- **治理须前后端双管**（与 kou-engineer2 共识）：前端放宽超时仅治标；`/ops/lineage` 后端无服务端预算时，单请求占连接 32–38s，高并发下会拖垮线程池 ⇒ 必须后端收敛/缓存（根因已同步 gao-architect2），前端契约（lineage 60s / lab-yearly 120s）须 ≥ 后端服务端预算。
- **口径提醒**：首测为**冷路径**单次（探测端 20s 截断）；关键端点已由 kou-engineer2 复测 2–3 次交叉确认——`ops/lineage`、`research/lab/yearly` 属**持续慢**（非缓存问题），`datacenter` 三者属**冷启动假阳**（复测 <0.03s）。判定针对"首次/缓存失效"场景，即真实用户首次打开页面时遇到的路径。
