# Alpha Quant Platform · 后端架构审计报告

- 审计日期：2026-09-27
- 审计人：高见远（Gao）· 架构师（只读审计模式）
- 审计范围：`backend/app/`（FastAPI 后端）静态架构分析 + 超时/降级治理 + 日志取证；前端仅只读 `frontend/src/api/client.ts` 一个文件用于超时对齐判断。
- 审计性质：**只读**，未修改任何业务代码 / 配置 / 数据 / 服务。
- 基线口径：入口 `backend/app/main.py:261` `app.include_router(v1_router, prefix="/api/v1")`；模块前缀见 `backend/app/api/v1/router.py:26-44`。

> 说明：本报告端点的「文件:行」为**装饰器行号**（与 team-lead 的 114 条基线、`docs/audit-2026-09-27/backend-routes-baseline.csv` 同口径）；AST 解析的 `def` 行号比装饰器行号大 1。

---

## 0. TL;DR

| 指标 | 结果 |
|---|---|
| 业务端点总数 | **114**（`backend/app/api/v1/*.py`，与基线、`openapi.json` 118 条含 4 条根路由相互印证） |
| 隐藏/漏网路由 | **0**（无 `@router.api_route`、无空路径重复、无 `add_api_route` 动态注册） |
| 慢端点（需重点观察） | **35** 个（其中 **7** 个有服务端预算、**28** 个无预算） |
| 超时不一致处 | **5 处**（详见 §3.4），最严重：前端默认 15s == 后端 `overview/rt` 15s 预算（竞态）；LLM 60s vs 前端 15s |
| 全局请求超时中间件 | **不存在**（仅计时 + PanicGuard + CORS） |
| 未捕获异常致裸 500 | **基本已消除**（`core/errors.py` 统一转 HTTP 200 信封；`core/panic_guard.py` 兜 BaseException） |
| 运行时实测证据 | `overview/rt` 15s 超时降级 **已复现**；`overview` 预热冷构建 **64.1s**；ETF 冷路径 **8.1~8.4s**（> 4.5s 预算）；`ops/lineage` **32.3/38.2/33.2s**、`research/lab/yearly` **18.8/19.3s**（工程师 kou-engineer2 实测，§2 表） |

---

## 1. 完整端点清单（基准表）

> 鉴权口径：`core/auth.py` 的 `RBAC_ENFORCE` 默认 **False**（`config.py:195`），故 `require_role("viewer"/"researcher"/"admin")` 当前**仅等价于"需登录"**（`require_auth`），不再比较角色等级；置 True 即恢复分级。
> 「慢端点」列 `Y` 表示命中"外部 IO / 重计算 / 全市场扫描"三类高风险之一；「服务端预算」列给出请求路径 `asyncio.wait_for` 预算（无则空 = 无服务端超时保护）。

| # | 模块 | METHOD | 完整路径 | 源(文件:行) | 鉴权 | 慢端点 | 服务端预算 |
|---|---|---|---|---|---|---|---|
| 1 | alerts | GET | `/api/v1/alerts/health` | alerts.py:134 | 登录 |  |  |
| 2 | alerts | GET | `/api/v1/alerts/rules` | alerts.py:146 | 登录 |  |  |
| 3 | alerts | POST | `/api/v1/alerts/rules` | alerts.py:161 | 登录 |  |  |
| 4 | alerts | PUT | `/api/v1/alerts/rules/{rule_id}` | alerts.py:190 | 登录 |  |  |
| 5 | alerts | DELETE | `/api/v1/alerts/rules/{rule_id}` | alerts.py:223 | 登录 |  |  |
| 6 | alerts | GET | `/api/v1/alerts/events` | alerts.py:245 | 登录 |  |  |
| 7 | alerts | POST | `/api/v1/alerts/events/read` | alerts.py:270 | 登录 |  |  |
| 8 | app_settings | GET | `/api/v1/settings` | app_settings.py:139 | 登录 |  |  |
| 9 | app_settings | PUT | `/api/v1/settings/preferences` | app_settings.py:180 | 登录 |  |  |
| 10 | app_settings | PUT | `/api/v1/settings/engine` | app_settings.py:200 | 登录 |  |  |
| 11 | app_settings | POST | `/api/v1/settings/connectors/test` | app_settings.py:239 | 登录 | Y | 20s(wait_for)+8s(httpx) |
| 12 | app_settings | POST | `/api/v1/settings/apikeys/rotate` | app_settings.py:257 | 登录 |  |  |
| 13 | app_settings | POST | `/api/v1/settings/data/sync` | app_settings.py:270 | 登录 |  |  |
| 14 | app_settings | POST | `/api/v1/settings/data/cache/clear` | app_settings.py:280 | 登录 |  |  |
| 15 | app_settings | POST | `/api/v1/settings/db/backup` | app_settings.py:310 | 登录 | Y |  |
| 16 | auth | POST | `/api/v1/auth/login` | auth.py:102 | 公开 |  |  |
| 17 | auth | GET | `/api/v1/auth/register/status` | auth.py:162 | 公开 |  |  |
| 18 | auth | POST | `/api/v1/auth/register` | auth.py:175 | 公开 |  |  |
| 19 | auth | GET | `/api/v1/auth/me` | auth.py:217 | 登录 |  |  |
| 20 | backtest | POST | `/api/v1/backtest/run` | backtest.py:452 | 登录 | Y |  |
| 21 | backtest | POST | `/api/v1/backtest/strategy-run` | backtest.py:891 | 登录 | Y |  |
| 22 | backtest | POST | `/api/v1/backtest/signal-analysis` | backtest.py:948 | 登录 | Y |  |
| 23 | datacenter | GET | `/api/v1/datacenter/overview` | datacenter.py:480 | 登录 | Y |  |
| 24 | datacenter | GET | `/api/v1/datacenter/datasets` | datacenter.py:636 | 登录 |  |  |
| 25 | datacenter | GET | `/api/v1/datacenter/quality` | datacenter.py:675 | 登录 |  |  |
| 26 | datacenter | GET | `/api/v1/datacenter/logs` | datacenter.py:720 | 登录 |  |  |
| 27 | datacenter | GET | `/api/v1/datacenter/task-stats` | datacenter.py:759 | 登录 |  |  |
| 28 | datacenter | POST | `/api/v1/datacenter/sync` | datacenter.py:807 | 登录 |  | 后台线程 |
| 29 | datacenter | GET | `/api/v1/datacenter/sync/status` | datacenter.py:861 | 登录 |  |  |
| 30 | datacenter | GET | `/api/v1/datacenter/sync/tasks/{task_id}` | datacenter.py:868 | 登录 |  |  |
| 31 | datacenter | GET | `/api/v1/datacenter/sync/auto` | datacenter.py:893 | 登录 |  |  |
| 32 | datacenter | POST | `/api/v1/datacenter/sync/auto` | datacenter.py:905 | 登录 |  |  |
| 33 | datacenter | POST | `/api/v1/datacenter/sync/cancel` | datacenter.py:919 | 登录 |  |  |
| 34 | datacenter | POST | `/api/v1/datacenter/sync/fetch` | datacenter.py:1199 | 登录 |  | 后台线程 |
| 35 | datacenter | GET | `/api/v1/datacenter/instruments` | datacenter.py:1282 | 登录 |  |  |
| 36 | datacenter | GET | `/api/v1/datacenter/text/status` | datacenter.py:1337 | 登录 |  |  |
| 37 | datacenter | POST | `/api/v1/datacenter/text/import` | datacenter.py:1346 | 登录 |  |  |
| 38 | datacenter | POST | `/api/v1/datacenter/text/build-factor` | datacenter.py:1359 | 登录 | Y |  |
| 39 | datacenter | GET | `/api/v1/datacenter/mirror/status` | datacenter.py:1374 | 登录 |  |  |
| 40 | datacenter | POST | `/api/v1/datacenter/mirror/rebuild` | datacenter.py:1392 | 登录 | Y |  |
| 41 | datacenter | GET | `/api/v1/datacenter/train/readiness` | datacenter.py:1429 | 登录 |  |  |
| 42 | datacenter | POST | `/api/v1/datacenter/train/start` | datacenter.py:1439 | 登录 | Y |  |
| 43 | datacenter | GET | `/api/v1/datacenter/train/status` | datacenter.py:1457 | 登录 |  |  |
| 44 | datacenter | POST | `/api/v1/datacenter/train/cancel` | datacenter.py:1467 | 登录 |  |  |
| 45 | desk | GET | `/api/v1/desk/kill-switch` | desk.py:53 | 登录 |  |  |
| 46 | desk | POST | `/api/v1/desk/kill-switch` | desk.py:73 | 登录 |  |  |
| 47 | desk | GET | `/api/v1/desk/exclusion` | desk.py:90 | 登录 |  |  |
| 48 | desk | POST | `/api/v1/desk/exclusion` | desk.py:112 | 登录 |  |  |
| 49 | desk | POST | `/api/v1/desk/exclusion/toggle` | desk.py:143 | 登录 |  |  |
| 50 | desk | GET | `/api/v1/desk/exclusion/screen` | desk.py:158 | 登录 |  |  |
| 51 | desk | POST | `/api/v1/desk/orders` | desk.py:179 | 登录 |  |  |
| 52 | desk | POST | `/api/v1/desk/fills/run` | desk.py:198 | 登录 |  |  |
| 53 | desk | GET | `/api/v1/desk/orders` | desk.py:211 | 登录 |  |  |
| 54 | desk | GET | `/api/v1/desk/account` | desk.py:254 | 登录 |  |  |
| 55 | desk | GET | `/api/v1/desk/capacity` | desk.py:269 | 登录 |  |  |
| 56 | desk | POST | `/api/v1/desk/attribution` | desk.py:311 | 登录 | Y |  |
| 57 | etf | GET | `/api/v1/etf/overview` | etf.py:407 | 登录 | Y | 4.5s |
| 58 | etf | GET | `/api/v1/etf/list` | etf.py:475 | 登录 |  |  |
| 59 | etf | GET | `/api/v1/etf/hot` | etf.py:524 | 登录 |  |  |
| 60 | etf | GET | `/api/v1/etf/performance` | etf.py:554 | 登录 |  |  |
| 61 | etf | GET | `/api/v1/etf/scale` | etf.py:628 | 登录 |  |  |
| 62 | etf | GET | `/api/v1/etf/flow` | etf.py:694 | 登录 | Y |  |
| 63 | etf | GET | `/api/v1/etf/detail/{code}` | etf.py:967 | 登录 | Y | 4s/块 |
| 64 | export | GET | `/api/v1/export/screener` | export.py:18 | 登录 | Y |  |
| 65 | export | POST | `/api/v1/export/backtest` | export.py:57 | 登录 | Y |  |
| 66 | export | POST | `/api/v1/export/strategy-backtest` | export.py:78 | 登录 | Y |  |
| 67 | market | GET | `/api/v1/market/index/kline` | market.py:643 | 登录 | Y |  |
| 68 | market | GET | `/api/v1/market/quotes` | market.py:675 | 登录 | Y |  |
| 69 | market | GET | `/api/v1/market/overview/rt` | market.py:766 | **公开(无依赖)** | Y | 15s |
| 70 | market | GET | `/api/v1/market/overview/daily` | market.py:830 | **公开(无依赖)** | Y | 5s |
| 71 | market | GET | `/api/v1/market/overview` | market.py:890 | **公开(无依赖)** | Y | 6s |
| 72 | monitor | GET | `/api/v1/monitor/health` | monitor.py:19 | 登录 |  |  |
| 73 | monitor | POST | `/api/v1/monitor/run` | monitor.py:34 | 登录 | Y |  |
| 74 | notify | POST | `/api/v1/notify/stream-ticket` | notify.py:75 | 登录 |  |  |
| 75 | notify | GET | `/api/v1/notify/stream` | notify.py:83 | 登录(SSE) |  | SSE 长连接 |
| 76 | notify | GET | `/api/v1/notify/recent` | notify.py:156 | 登录 |  |  |
| 77 | ops | POST | `/api/v1/ops/quality-scan` | ops.py:36 | 登录 | Y |  |
| 78 | ops | GET | `/api/v1/ops/lineage` | ops.py:296 | 登录 | **Y(实测32~38s)** |  |
| 79 | ops | GET | `/api/v1/ops/dag` | ops.py:390 | 登录 |  |  |
| 80 | ops | POST | `/api/v1/ops/dag/rerun` | ops.py:463 | 登录 |  |  |
| 81 | portfolio | POST | `/api/v1/portfolio/backtest` | portfolio.py:105 | 登录 | Y |  |
| 82 | portfolio | GET | `/api/v1/portfolio/search` | portfolio.py:135 | 登录 |  |  |
| 83 | report | GET | `/api/v1/report/daily` | report.py:464 | 登录 |  |  |
| 84 | report | POST | `/api/v1/report/daily/generate` | report.py:478 | 登录 | Y |  |
| 85 | research | GET | `/api/v1/research/overview` | research.py:115 | 登录 |  |  |
| 86 | research | POST | `/api/v1/research/factor-icir` | research.py:165 | 登录 | Y |  |
| 87 | research | POST | `/api/v1/research/factor-corr` | research.py:192 | 登录 | Y |  |
| 88 | research | POST | `/api/v1/research/factor-quantile` | research.py:213 | 登录 | Y |  |
| 89 | research | GET | `/api/v1/research/experiments` | research.py:229 | 登录 |  |  |
| 90 | research | POST | `/api/v1/research/cv-folds` | research.py:285 | 登录 | Y |  |
| 91 | research | GET | `/api/v1/research/feature-importance` | research.py:341 | 登录 |  |  |
| 92 | research | POST | `/api/v1/research/optimize` | research.py:399 | 登录 | Y |  |
| 93 | research | POST | `/api/v1/research/impact-sim` | research.py:481 | 登录 | Y |  |
| 94 | research | POST | `/api/v1/research/stress-test` | research.py:533 | 登录 | Y |  |
| 95 | research | GET | `/api/v1/research/lab/yearly` | research.py:584 | 登录 | **Y(实测18.8~19.3s)** |  |
| 96 | screener | GET | `/api/v1/screener/watchlist` | screener.py:507 | 登录 |  |  |
| 97 | screener | GET | `/api/v1/screener` | screener.py:523 | 登录 | Y |  |
| 98 | screener | GET | `/api/v1/screener/stocks` | screener.py:640 | 登录 | Y |  |
| 99 | stock | GET | `/api/v1/stock/search` | stock.py:78 | 登录 |  |  |
| 100 | stock | GET | `/api/v1/stock/{symbol}/profile` | stock.py:107 | 登录 |  |  |
| 101 | stock | GET | `/api/v1/stock/{symbol}/kline` | stock.py:150 | 登录 |  |  |
| 102 | stock | GET | `/api/v1/stock/{symbol}/predict` | stock.py:200 | 登录 |  |  |
| 103 | stock | GET | `/api/v1/stock/{symbol}/panels` | stock.py:256 | 登录 | Y | 4.5s/块 |
| 104 | studio | POST | `/api/v1/studio/mining/start` | studio.py:67 | 登录 | Y |  |
| 105 | studio | GET | `/api/v1/studio/mining/status/{task_id}` | studio.py:91 | 登录 |  |  |
| 106 | studio | POST | `/api/v1/studio/mining/cancel/{task_id}` | studio.py:116 | 登录 |  |  |
| 107 | studio | POST | `/api/v1/studio/nl-to-factor` | studio.py:193 | 登录 | Y |  |
| 108 | studio | POST | `/api/v1/studio/alpha-eval` | studio.py:243 | 登录 | Y |  |
| 109 | studio | GET | `/api/v1/studio/factors` | studio.py:298 | 登录 |  |  |
| 110 | studio | POST | `/api/v1/studio/factors` | studio.py:320 | 登录 | Y |  |
| 111 | studio | DELETE | `/api/v1/studio/factors/{factor_id}` | studio.py:384 | 登录 |  |  |
| 112 | studio | POST | `/api/v1/studio/factor-report` | studio.py:414 | 登录 | Y |  |
| 113 | watchlist | GET | `/api/v1/watchlist/dashboard` | watchlist.py:269 | 登录 | Y | 5.5s |
| 114 | watchlist | GET | `/api/v1/watchlist/correlation` | watchlist.py:389 | 登录 |  |  |

**核对结论**：AST 全量解析 = 114 条，与 team-lead 基线 114 条、`backend-routes-baseline.csv`（114 行）完全一致；`openapi.json` 118 条 = 114 业务 + 4 根路由（`/health`、`/`、`/health/ready`、`/health/live`；`/metrics` 为 `include_in_schema=False` 不计）。**无 `@router.api_route`、无动态 `add_api_route`、无重复空路径**——基线无需增补。

**端点分类统计**：GET 58 / POST 43 / PUT 4 / DELETE 3 / SSE 1（`notify/stream` 走 `require_stream_viewer`）。**需登录 108、公开 6**（公开：`auth/login`、`auth/register`、`auth/register/status` + `market/overview`、`market/overview/rt`、`market/overview/daily` 三条无任何依赖）。

---

## 2. 慢端点 / 超时风险表（含证据）

风险分级：**A=必然超前端 15s**（同步长计算，无服务端预算）；**B=可能超 15s**（外部 IO 或全市场扫描）；**C=已用预算保护但仍逼近 15s**（竞态）；**D=冷启动必降级**（有预热，但首个请求或预热失败时降级）。

| 风险 | 端点 | 耗时来源（证据） | 服务端保护 | 前端 15s |
|---|---|---|---|---|
| **A★实测** | `GET /ops/lineage` | **实测 32.3 / 38.2 / 33.2s（首测直接 20s 超时）**。`_run`（ops.py:306）对每个 dataset 节点调 `_scan_parquet_dataset`，全量遍历 parquet footer；`scan_cache` 仅**单次请求内**去重，**无跨请求缓存**、无预算 | 无 | 必然截断（最严重） |
| **A★实测** | `GET /research/lab/yearly` | **实测 18.8 / 19.3s**。`_yearly`（research.py:593）`pl.concat` 全量 `predictions` 分区 + 全市场 `daily_bar` 分区，再逐日 `group_by` 求 Spearman（Python 循环）；无缓存、无预算 | 无 | 必然截断 |
| **A★实测** | `POST /studio/factor-report` | **实测 11.3 / 12.5s**（逼近 15s） | 无 | 临界 |
| **A★实测** | `POST /studio/alpha-eval` | **实测 6.9s** | 无 | 一般 OK |
| **A** | `POST /backtest/run`、`/backtest/strategy-run`、`/backtest/signal-analysis` | 真实回测引擎 + 可选 grid/ga/optuna 寻优 + walk-forward；`asyncio.to_thread(_run)`（backtest.py:463、891+），**无 wait_for**；先 `compute_slot` 排队最多 10s | 无 | 必然截断 |
| **A** | `POST /research/factor-icir`/`factor-corr`/`factor-quantile`/`cv-folds`/`optimize`/`impact-sim`/`stress-test` | 全量 features（`_FEAT_TTL=600`，520 日 ~48MB）读入 + IC/协方差/LW 收缩/RMT 求解，`to_thread` 无预算 | 无 | 必然截断 |
| **A** | `POST /portfolio/backtest`、`/export/backtest`、`/export/strategy-backtest` | 重跑真实引擎（export.py:57 注释明确"CPU 密集长任务数十秒"）；导出走 180s 下载超时，但 `portfolio/backtest` 走 15s | 无（导出靠前端 180s） | portfolio 必然截断 |
| **A** | `POST /ops/quality-scan` | 逐标的读 parquet 跑 5 类 QC，`_SCAN_SYMBOL_LIMIT=200`（ops.py:27）个标的 + 每标的 `pl.read_parquet` | 无 | 大概率截断 |
| **A** | `POST /monitor/run` | 全量滚动 RankIC + 半衰期 + PSI 真实计算（monitor.py:41 `to_thread`） | 无 | 可能截断 |
| **A** | `POST /report/daily/generate` | 聚合 + **LLM 调用**（`LLM_TIMEOUT_SECONDS=60`，`core/llm.py:27`）+ 落库 | 无 | 必然截断（若 LLM 慢） |
| **A** | `POST /studio/nl-to-factor` | LLM 生成（60s 超时）+ AST 校验 + 真实截面 RankIC 评估（studio.py:209-231） | 无 | **必然截断** |
| **A** | `POST /studio/alpha-eval` | 读 520 日 features + `evaluate_expr_detail` 全截面评估（studio.py:256-276） | 无 | 必然截断 |
| **A** | `POST /studio/mining/start` | `_load_snapshot()` 全量 features（缓存未命中时读 48MB parquet + concat）+ `gp_miner.start_task`（studio.py:75-88） | 无 | 可能截断（首次） |
| **A** | `POST /studio/factors`、`POST /studio/factor-report` | 入库前真实截面评估 / 报告生成 | 无 | 可能截断 |
| **A** | `POST /datacenter/train/start` | 门禁检查 + `start_training`（`to_thread`，datacenter.py:1445） | 无 | 可能截断 |
| **A** | `POST /datacenter/text/build-factor`、`/mirror/rebuild` | 文本因子构建 / 截面镜像重建（全市场 parquet 重写） | 无 | 可能截断 |
| **A** | `POST /desk/attribution` | 持仓归因（读 hfq 日线 + 协方差），带 `compute_slot`（desk.py:311） | 无 | 可能截断 |
| **A** | `POST /app_settings/db/backup` | SQLite 全库备份（DB 大时秒级~十秒级） | 无 | 可能截断 |
| **B** | `GET /screener/stocks` | 全市场截面 + **外部实时快照分片抓取**（`_fetch_quotes_sharded`，200 只/片）；SWR TTL=60，无请求预算 | 无 | 可能截断（冷/refresh） |
| **B** | `GET /screener` | 全市场 `_screen` 富化（universe 关联 + 行情回填） | 无 | 可能截断 |
| **B** | `GET /datacenter/overview` | 数据集扫描 + DB 统计 | 无 | 可能截断 |
| **B** | `GET /market/quotes` | 腾讯批量一次 HTTP（200 只上限），`_TIMEOUT=3.0`/次；进程缓存 TTL=15s | 无（单次外呼） | 一般 OK |
| **B** | `GET /market/index/kline` | 腾讯指数据（`fetch_index_kline`，fetch 层 TTL 缓存） | 无 | 一般 OK |
| **B** | `GET /etf/flow` | 东财 `push2delay` 资金流榜（**仅东财、无备用**，etf.py:694） | 无 | 依赖熔断快速失败 |
| **C** | `GET /market/overview/rt` | **实测超时**：`backend-run.log:118`「实时块请求路径超时：预算 15.0s 内未完成，实际等待 15.0s → 返回降级载荷」。冷构建实测 ~14s，**逼近 15s 预算** | wait_for 15s | **竞态**（见 §3.4-①） |
| **C** | `GET /market/overview` | 请求路径 6s 预算；后台重建 **无预算**（`_build_unbudgeted`）。预热实测 `warm cache done in 19.0s`（backend-run.log:92）、**64.1s**（backend-kou.log） | wait_for 6s | 请求路径 OK；后台长跑 |
| **D** | `GET /etf/overview` | 冷路径实测 **8.1~8.4s**（backend-run.log:91、123）> 4.5s 预算 ⇒ **冷启动必降级**；靠启动预热 + 无预算后台重建回填 | wait_for 4.5s | 请求路径 OK（降级） |
| **D** | `GET /market/overview/daily` | 5s 硬编码预算；本地计算零外部依赖，正常应 <1s | wait_for 5.0 | OK |
| **C** | `GET /stock/{symbol}/panels` | 8 块并发（quote/money_flow/north/fundamentals/events/holders/chip/risk），每块 4.5s（`AQP_PANEL_BLOCK_TIMEOUT`）；日志实测出现过 `[panels] .SH block 超时` | wait_for 4.5s/块 | OK（单块降级） |
| **C** | `GET /watchlist/dashboard` | 本地 bars + 外部 PE/PB/资金流，`_WATCHLIST_BUDGET_SECONDS=5.5` | wait_for 5.5s | OK |
| **D** | `GET /etf/detail/{code}` | 9 块，每块 4s 预算 | wait_for 4s/块 | OK |
| **D** | `GET /etf/list`/`hot`/`performance`/`scale` | 目录/榜单聚合，部分经东财（不可达时兜底新浪+腾讯） | 无（依赖 SWR 300s） | 冷启动可能降级 |

**关键证据摘录**（运行时日志，2026-09-27）：
- `backend-run.log:118` `[overview] aqp:market:overview_rt:20260924 实时块请求路径超时：预算 15.0s 内未完成，实际等待 15.0s → 返回降级载荷`
- `backend-run.log:92` `[overview] warm cache done in 19.0s`；`backend-kou.log` `[overview] warm cache done in 64.1s`
- `backend-run.log:91/123` `[etf] warm overview cache done in 8.4s / 8.1s`
- `backend-run.log:90` `[etf] 东财 clist 不可达（DataSourceUnavailable），切换新浪+腾讯兜底源`
- `backend-run.log:121` `[etf] 美股行情拉取失败（跨境 ETF 行情降级为 unavailable）：DataSourceUnavailable`

**运行时实测耗时**（工程师 kou-engineer2，复测 2~3 次，完整数据 `docs/audit-2026-09-27/smoke-results.csv`）：

| 端点 | 实测 | 判定 |
|---|---|---|
| `GET /ops/lineage` | **32.3 / 38.2 / 33.2s** | 持续慢，首测 20s 超时（**最严重**，见 §6-P1-9） |
| `GET /research/lab/yearly` | **18.8 / 19.3s** | 持续慢（**见 §6-P1-9**） |
| `POST /studio/factor-report` | 11.3 / 12.5s | 逼近 15s |
| `GET /market/overview/rt` | 11.1 / 11.9s | 与 §3.4-① 竞态一致 |
| `POST /studio/alpha-eval` | 6.9s | 正常 |

**冷启动注意**：`datacenter/datasets`、`datacenter/mirror/status`、`datacenter/quality` 首测 20s 超时、复测 0.01~0.02s ⇒ **非真慢**，但服务重启后首次点击会踩到（首次构建数据集索引 / 全量扫描），前端表现为"偶发超时"。

---

## 3. 超时配置全景表

### 3.1 请求路径预算（服务端 `asyncio.wait_for`）

| 端点/位置 | 常量 | 值 | 文件:行 |
|---|---|---|---|
| `market/overview/rt` | `RT_BUILD_TIMEOUT_SECONDS` | **15.0s** | market.py:728 |
| `market/overview`（兼容） | `OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS` | **6.0s** | market.py:711 |
| `market/overview/daily` | 硬编码字面量 | **5.0s** | market.py:865（**未常量化**） |
| `etf/overview` 等聚合 | `_ETF_ENDPOINT_BUDGET_SECONDS` | **4.5s** | etf.py:62 |
| `etf/detail` 各块 | `_ETF_DETAIL_BLOCK_BUDGET_SECONDS` | **4.0s** | etf.py:63 |
| `stock/panels` 各块 | `_PANEL_BLOCK_TIMEOUT`（env `AQP_PANEL_BLOCK_TIMEOUT`） | **4.5s** | stock.py:68 |
| `watchlist/dashboard` | `_WATCHLIST_BUDGET_SECONDS` | **5.5s** | watchlist.py:34 |
| `app_settings/connectors/test` | `asyncio.wait_for(_ping)` | **20s** + httpx 8s | app_settings.py:222-224 |
| 应用关闭 | `asyncio.wait(bg, timeout=10)` | 10s | main.py:162 |

### 3.2 外部数据源 HTTP timeout

| 数据源/封装 | 值 | 文件:行 |
|---|---|---|
| 交互外部请求（腾讯/新浪/东财 realtime） | `_TIMEOUT = 3.0s` | data/realtime.py:41 |
| ETF 直连（东财 clist/新浪/腾讯） | `_ETF_HTTP_TIMEOUT = 4.0s`（+`_ETF_HTTP_RETRIES`） | data/etf.py:47 |
| 多源日线（东财 HTTP） | `timeout=10.0` | data/ingest/multi_source.py:79 |
| alerts webhook | `httpx.AsyncClient(timeout=8)` | alerts.py:692 |
| settings 连接器测试 | httpx `timeout=8` | app_settings.py:224 |
| report webhook | `httpx.post(timeout=8)` | report.py:441 |
| LLM（ollama/openai） | `LLM_TIMEOUT_SECONDS = 60s` | core/config.py:253 / core/llm.py:27 |
| Redis | `REDIS_TIMEOUT = 1.0s`（+ 熔断 `OPEN_SECONDS=60`） | config.py:115 / redis_client.py:40 |

### 3.3 缓存 TTL 与 SWR

| 项 | 值 | 文件:行 |
|---|---|---|
| SWR degraded 落地 TTL 上限 | **15s**（`DEGRADED_TTL_SECONDS`），无影子键 | cache/swr.py:40-41 |
| `unavailable` 状态 | **不写缓存**（快速自愈） | cache/swr.py:44-52 |
| market overview 主键 / 影子窗口 | 300s / 1800s | market.py:698-699 |
| market rt 主键 / 影子窗口 | 45s / 600s | market.py:701-702 |
| overview 预热续期阈值 | 120s（预热间隔 240s） | market.py:734 / main.py:116 |
| etf 主键 / 影子 / 重建锁 | 300s / 1800s / 30s | etf.py:60-65 |
| screener stocks TTL | 60s | screener.py:43 |
| datacenter datasets/quality TTL | 1800s | datacenter.py:138-139 |
| research features TTL | 600s | research.py:50 |
| quotes_hub TTL | min/max/default = 15/120/30s | data/quotes_hub.py:25-27 |
| parquet 写锁等待 / 陈旧 | 60s / 300s | parquet_store.py:93-94 |

### 3.4 ⚠️ 超时不一致 / 缺失清单（核心发现）

**① 前端 15s == 后端 `overview/rt` 15s（竞态，最严重）**
`frontend/src/api/client.ts:92` 默认 `timeout: 15000`；`market.py:728` `RT_BUILD_TIMEOUT_SECONDS = 15.0`。后端在 15s 处降级返回，前端也在 15s 处 abort（`ECONNABORTED → "请求超时，请稍后重试"`）。**实测后端已用满 15.0s**（`backend-run.log:118`）⇒ 首页"大盘实时块"会间歇性在前端直接报超时，用户甚至看不到后端的降级黄条。**建议后端预算 ≤ 12s 或前端对该端点放宽至 20s。**

**② LLM 60s vs 前端 15s**
`LLM_TIMEOUT_SECONDS=60`（config.py:253）用于 `studio/nl-to-factor`、`report/daily/generate`；前端默认 15s。LLM 一旦 >15s，前端报"请求超时"，后端仍在跑并在 60s 内占用线程/连接。**60s 与 15s 无任何一方对齐。**

**③ 无全局请求超时中间件**
`main.py` 仅注册 `PanicGuardMiddleware` + CORS + 计时中间件（`add_response_time_middleware`）。**不存在**任何 `REQUEST_TIMEOUT` 配置或超时中间件（全仓 grep 为空）⇒ §2 表中 28 个"无预算"端点的耗时完全由业务代码自觉，重计算端点无兜底。

**④ `market/overview/daily` 超时预算未常量化**
`market.py:865` 直接写 `timeout=5.0` 字面量，而同类端点均用命名常量（15.0/6.0/4.5…）。调参时易漏改，且与 `RT_BUILD_TIMEOUT_SECONDS`/`OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS` 的风格不一致。

**⑤ 外部 HTTP timeout 梯度不统一**：3.0（realtime）/ 4.0（etf）/ 8（alerts·settings·report）/ 10.0（multi_source）/ 60（LLM），同一"外部源"语义却 5 档取值，缺乏统一预算域。

**⑥ 降级载荷 TTL 收敛缺口（非超时但同族）**：`market/overview`（market.py:941-971）与 `market/overview/daily`（market.py:867-880）的超时降级载荷**没有顶层 `status` 字段**，`swr.default_cacheable`（swr.py:44-52）判定为"可缓存"⇒ 按**正常 TTL 落地**（overview 300s、daily 最长 **86400×3s≈3 天**）。`/overview/rt` 与 ETF 系列已补顶层 `status=degraded`（15s 短 TTL），**唯独这两个未收敛**。daily 块虽是纯本地计算（正常 <1s），一旦超时降级会被缓存到次日 15:30（周五可到周一），期间用户持续看到"unavailable"。

---

## 4. 日志与历史审计取证

### 4.1 当前日志（`backend-run.log`，2026-09-27 02:04~02:08；`backend-kou.log` 15:14）

| 级别 | 现象 | 频次 | 影响 |
|---|---|---|---|
| WARNING | `[etf] 东财 clist 不可达（DataSourceUnavailable），切换新浪+腾讯兜底源` | 反复（backend.out.log 28 次） | 东财 ETF 目录**持续不可达**，已走兜底（功能可用但口径/字段可能缺失） |
| WARNING | `[etf] 美股行情拉取失败（跨境 ETF 行情降级为 unavailable）` | 3 次 | 跨境 ETF 行情恒缺 |
| WARNING | `[overview] 实时块请求路径超时：预算 15.0s…实际等待 15.0s` | 每次冷访问 | **首页实时块降级**（见 §3.4-①） |
| WARNING | `[settings] 数据盘水位 93.3%（warning），接近写满将导致抓取/落库失败，请及时清理` | 2 次 | **磁盘水位 93.3%**，写入型任务（sync/train/mirror）有失败风险 |
| WARNING | `[overview] indices...`（指数块降级） | 3 次 | 新浪指数串行拉取慢（§2 C） |
| WARNING | `[panels] .SH block 超时（>4.5s）` | 2 次 | 个股面板单块降级 |
| WARNING | `[graph] 3 个行业桶超过 150 只，已跳过同业边（疑似分类脏桶）` | 1 次 | 行业分类数据质量 |
| INFO | `[etf] warm overview cache done in 8.4s / 8.1s` | 每次预热 | ETF 冷构建 > 4.5s 预算（设计上靠后台重建） |
| INFO | `[overview] warm cache done in 19.0s`（09-27）/ **64.1s**（15:14） | 每次预热 | **overview 全量重建 19~64s**，与 6s 请求预算严重错配（靠 `_build_unbudgeted` 后台路径规避） |
| WARNING | `FutureWarning: default fill_method='pad' in DataFrame.pct_change is deprecated`（`desk.py:337`） | 1 次 | 未来 pandas 版本将变更默认行为，需显式 `fill_method=None` |

### 4.2 历史日志（`backend-run.before-fix.log`，2026-09-23，**修复前**）

| 现象 | 端点 | 说明 |
|---|---|---|
| `unhandled error ... GET /api/v1/screener/stocks` → `polars ComputeError: could not append value: 91.33 of type: f64 to the builder` | screener/stocks | polars 列类型漂移致 50000；**当前 09-27 日志未见复现，判定已修** |
| `unhandled error ... POST /api/v1/backtest/strategy-run` / `signal-analysis` | backtest | 修复前未捕获异常；当前未复现 |
| `[etf] 东财 clist 不可达` 21 次、`DataSourceUnavailable` 21 次、`Traceback` 54 次、`ERROR` 18 次 | 多 | 历史修复轮次背景噪声 |

### 4.3 历史审计结论交叉引用（`docs/`）

- `docs/audit-datasource-2026-09-26/数据源审计报告.md`（**同一位架构师**）：系统梳理东财单点依赖。**当前仍然成立的"仅东财无备用"清单**：个股主力资金流、大盘主力净流入、行业板块资金结构、北向资金汇总、股东户数/十大流通股东、个股财报关键指标（ROE/毛利率/净利率）、ETF 全市场资金净流入榜/单只资金流/行业配置/费率/公告/持仓、乐咕乐股估值。另记录 **Tushare 残留**（`data/ingest/multi_source.py`，未接线但违反用户"绝对禁止 Tushare"），**需确认是否已清理**。
- `docs/audit-2026-09-18/AQP-全栈审核报告.md`：P0 六条已全部处置；P1 已完成 35 条（含 P1-43 `/health*` 恒 200、P1-50 `/market/index/kline` 无鉴权——**本报告实测该端点已挂 `require_role("viewer")`，已修**）。
- `docs/audit-2026-09-27/backend-routes-baseline.csv` + `openapi.json`：工程师（kou-engineer2）产出，与本报告端点表一致，可作 QA 对齐基准。

---

## 5. 端点实现健壮性逐模块结论

### 5.1 未捕获异常 → 裸 500
**结论：基本已消除。** `core/errors.py:153-213` 注册了 `StarletteHTTPException / AQPException / RequestValidationError / Exception` 四类处理器，全部转 **HTTP 200 + `{code,message,data,trace_id,ts}` 信封**（通用异常 → `ERR_SYSTEM=50000`，不泄露类名/堆栈）。`core/panic_guard.py` 作为**原始 ASGI 中间件**兜住 polars `pyo3_runtime.PanicException` 等 `BaseException`（Starlette 三层中间件均只 `except Exception`），返回 `ERR_PANIC_CONTAINED` 信封。
- 残留风险：异常若在 `BaseHTTPMiddleware`（计时中间件）**响应已开始后**抛出，或 `PanicGuard` 中 `response_started=True`（SSE 流中途），只能上抛 → uvicorn 裸 500。SSE 端点 `notify/stream` 属此类，但属框架语义限制。
- 业务侧：多数端点对"数据缺失/外部不可达"用 `AQPException` + 业务码显式降级（如 `ops/quality-scan` 对目录不存在抛 `ERR_DATA_EMPTY` 而非 `FileNotFoundError`），规范良好。

### 5.2 外部数据源降级路径
- **健全**：`data/realtime._first_source` 统一降级链（腾讯→新浪→degraded）；ETF 目录东财→新浪+腾讯兜底（`data/etf.py`，日志已见实际切换）；`market` 概览失败走 `_heat_from_local`/`_sectors_from_local` 本地日线聚合。
- **薄弱**：§4.3 所列"仅东财无备用"功能失败时只能返回结构化 `unavailable`（诚实降级，非造假，但功能不可用）；ETF 资金流/跨境 ETF 行情实测持续不可用。
- **⚠️ 备源路径缺陷（工程师 kou-engineer2 实测 + 代码复核）**：`portfolio/backtest` 的**备源**分支（`data/portfolio_source.py:146` `_market_symbol(code)` → `domain/a_share_rules.py:59-60` `market_prefix`）对**带交易所后缀**的代码（`600519.SH`）抛 `ValueError: code 必须 6 位数字`；仅当**东财主源降级到腾讯备源**时触发。接口层（`portfolio.py:106`）无代码格式归一化/校验，错误深到数据层才炸。实测响应为 `{"code":51001,...}`——`portfolio.py:126-127` 用**同一个 `except ValueError`** 把两类完全不同原因合并进 `ERR_DATA_EMPTY=51001`（`core/errors.py:98`）：① 入参格式错（`600519.SH`）本应 `ERR_PARAMS=40000`（`core/errors.py:74`）；② 真·本地无数据（`domain/portfolio.py:279`「对齐后有效交易日不足」等）才是 51001。前端只会展示"本地无数据"，**无法区分"用户填错"还是"数据没同步"**。纯数字 `600519` 正常。见 §6-P1-10。
- **告警缺口**：`data/etf.py` 注释声称"同花顺 `stock_fund_flow_individual`"备用但**未接线**（09-26 报告 §8.6 已登记），属"永不兑现的降级承诺"。

### 5.3 同步阻塞长耗时计算
- **正确**：所有重计算均经 `asyncio.to_thread` 卸载（backtest/research/studio/monitor/ops 共 100+ 处），**未发现直接在事件循环内跑 CPU 密集**的端点（export.py:57 的历史缺陷已修，注释明确）。
- **问题**：卸载后**未施加请求预算**（§2 A 类 28 个端点），线程池任务无法取消 ⇒ 前端 15s 断开后后端线程仍跑满，叠加 `COMPUTE_CONCURRENCY=2` + `COMPUTE_ACQUIRE_TIMEOUT_SECONDS=10`（compute_guard.py:11,26）会形成"请求排队→超时→线程仍占用"的拥塞放大。

### 5.4 DB / parquet 缺失表现
- **健全**：SQLite 统一 `busy_timeout=30000`（init_db.py:43、db/session.py:65、kv.py:22/38、task_store.py:19 对齐）；parquet 写锁 60s 等待 + 300s 陈旧接管（parquet_store.py:93-94）；读侧对"数据集目录不存在/分区缺列"有显式业务码降级（backtest.py:529-533 `missing_columns`、ops.py:48）。
- **薄弱**：`/health/ready` 会检查 `core_snapshot`，但多数数据端点未统一检查 `.manifest.json` 一致性；**磁盘水位 93.3%** 无自动清理，写入型任务（sync/train/mirror/backup）存在失败风险，且当前仅在 `/settings` 被动告警。

---

## 6. 架构级分级修复建议

> 原则：最小改动、可回滚、优先消除"用户可见的超时/假死"；不改业务语义、不引入新框架。

### P0（影响可用性，建议本轮处理）

1. **消除 `overview/rt` 前端/后端 15s 竞态**
   - 文件：`backend/app/api/v1/market.py:728`（`RT_BUILD_TIMEOUT_SECONDS`）或前端 `client.ts` 调用处。
   - 做法：二选一——① 后端预算下调至 **12.0s**（留 3s 给序列化/网络）；② 前端对该端点显式传 `timeout: 20000`。**推荐 ①**（后端是唯一能诚实降级的一方）。
   - 依据：`backend-run.log:118` 实测 15.0s 用满。

2. **统一重计算端点的服务端预算 + 前端放宽**
   - 文件：`backend/app/core/compute_guard.py`（新增 `COMPUTE_REQUEST_BUDGET_SECONDS`）或各端点 `to_thread` 外包 `asyncio.wait_for`；前端对 A 类端点传 `timeout`（30~60s）。
   - 做法：为 §2 A 类端点引入统一 `await asyncio.wait_for(asyncio.to_thread(...), timeout=...)`，超时返回 `ERR_RATE_LIMITED`/结构化降级；前端对 `/backtest/*`、`/research/*`、`/studio/*`、`/ops/quality-scan`、`/monitor/run`、`/report/daily/generate`、`/portfolio/backtest` 显式放宽超时。
   - 依据：无预算 + 前端 15s ⇒ 必然截断且后端线程空转。

3. **LLM 端点超时对齐**
   - 文件：`backend/app/core/config.py:253`（`LLM_TIMEOUT_SECONDS=60`）与 `studio.py:193`、`report.py:478`。
   - 做法：`nl-to-factor` / `daily/generate` 前端超时 ≥ `LLM_TIMEOUT_SECONDS + 余量`（建议 70s），或后端 LLM 超时降至 20s 并返回"LLM 超时"业务码。

### P1（稳健性与一致性）

4. **补齐降级载荷顶层 `status`**
   - 文件：`backend/app/api/v1/market.py:941-971`（`/overview` 超时/异常降级）、`market.py:867-880`（`/overview/daily` 超时降级）。
   - 做法：给两处降级 dict 加顶层 `"status": "degraded"`，使 `swr._effective_ttl` 收敛为 **≤15s 短 TTL + 无影子键**（与 `/overview/rt`、ETF 系列对齐），避免 daily 降级被缓存到次日 15:30。

5. **新增全局请求超时中间件**
   - 文件：`backend/app/main.py`（中间件注册区）。
   - 做法：对**未显式声明预算**的路由设全局兜底超时（如 60s），超时返回统一信封；作为 §2 无预算端点的最后防线。需注意 SSE（`notify/stream`）与下载端点（`export/*`）应豁免。

6. **`market/overview/daily` 预算常量化**
   - 文件：`backend/app/api/v1/market.py:865`。
   - 做法：`timeout=5.0` → 模块级 `OVERVIEW_DAILY_BUILD_TIMEOUT_SECONDS = 5.0`，与同族常量风格一致、便于测试 monkeypatch。

7. **统一外部 HTTP 预算域**
   - 文件：`data/realtime.py:41`(3.0) / `data/etf.py:47`(4.0) / `alerts.py:692`·`app_settings.py:224`·`report.py:441`(8) / `data/ingest/multi_source.py:79`(10.0)。
   - 做法：在 `core/config.py` 收敛为 `EXTERNAL_HTTP_TIMEOUT_SECONDS`（默认 5s）+ 各源可选覆盖，避免同语义 5 档取值。

8. **磁盘水位治理与前置告警**
   - 现象：`数据盘水位 93.3%`（backend-run.log:100）。
   - 做法：`/health/ready` 增加 `disk_watermark` 检查项（>90% → degraded），并在写入型任务（sync/train/mirror/backup）入口前置校验水位，避免"抓取/落库中途失败"。

9. **`ops/lineage`（32~38s）与 `research/lab/yearly`（18.8~19.3s）加缓存/下推**
   - 文件：`backend/app/api/v1/ops.py:306`（`_run` 内 `_scan_parquet_dataset`）、`backend/app/api/v1/research.py:593`（`_yearly`）。
   - 做法：① 二者均走 `cache.swr.cached_or_build`（lineage TTL 建议 300s、lab/yearly TTL 600s），把 32s 冷扫挪到后台无预算重建（复用 `background_build` 先例）；② `lab/yearly` 的逐日 Spearman Python 循环改为 polars 分组聚合下推，或只读近 N 年分区；③ `lineage` 的 parquet footer 扫描结果落进程缓存（当前 `scan_cache` 仅单请求内有效）。
   - 依据：`smoke-results.csv` 实测；两处均**无任何服务端预算**，前端 15s 必然截断。

10. **`portfolio/backtest` 入参代码归一化 + 备源健壮性**
   - 文件：`backend/app/api/v1/portfolio.py:106-127`（入参层）、`backend/app/data/portfolio_source.py:146`（备源）、`backend/app/domain/a_share_rules.py:59-60`（抛错点）。
   - 做法：① 在接口层（或 `PortfolioBacktestRequest` 校验器）把 `600519.SH` 统一归一化为 6 位数字后再下传；② `_market_symbol` 对带后缀输入做 `code.split(".")[0]` 兜底而非抛错；③ **拆分 `portfolio.py:126` 的 `except ValueError`**：入参格式错 → `ERR_PARAMS(40000)`，真·本地无数据（`domain/portfolio.py:279`「对齐后有效交易日不足」等）→ `ERR_DATA_EMPTY(51001)`，避免两类原因共用 51001 导致前端无法区分"填错"与"数据没同步"。
   - 依据：kou-engineer2 实测（响应 `code=51001`）+ `core/errors.py:74/98` 码表 + `domain/portfolio.py:279`；仅东财降级时触发，属"降级路径上的二次故障"。

### P2（体验与可观测性）

11. **清理东财单点依赖的"降级承诺"**：`data/etf.py` 注释中未接线的同花顺备用源——要么接线，要么修正注释（09-26 报告 §8.6 已登记）；确认 `data/ingest/multi_source.py` 的 Tushare 残留是否已按用户要求删除。
12. **`desk.py:337` pandas `pct_change` 显式 `fill_method=None`**，消除 FutureWarning 与未来版本行为变更风险。
13. **补齐 `/health/ready` 的 `core_snapshot` 与各数据端点一致性**：将 `.manifest.json` 校验下沉为统一工具，避免"目录存在但索引缺失"的静默降级。
14. **为无预算慢端点补 `X-Response-Time-MS` 观测看板**：`main.py` 已写入该头，建议在 Prometheus（`HTTP_REQUEST_DURATION`）按 endpoint 设 P95 告警阈值，持续监控 §2 表。
15. **HTTP 层监控盲区（实测）**：后端**未鉴权也返回 HTTP 200**（错误码在 body 信封），基于 HTTP 状态码的探针/网关监控**会漏报**鉴权失败与业务异常；建议监控改为解析 body `code`，或将鉴权失败在网关层另加规则。

---

## 附录 A：审计方法与可复现命令

- 路由全量解析：AST 遍历 `backend/app/api/v1/*.py` 的 `@router.<method>` 装饰器（含 `api_route`），拼接 `router.py` 前缀 ⇒ 114 条。
- 鉴权判定：解析函数签名的 `Depends(...)` 默认值；结合 `core/auth.py:222-244`（`RBAC_ENFORCE` 默认 False ⇒ `require_role` 退化为登录门）。
- 超时：`grep -rn "timeout\|BUDGET\|TTL\|SECONDS" backend/app`。
- 日志：`backend-run.log`、`backend.out.log`、`backend_qa.log`、`backend-run.before-fix.log`（GBK/UTF-8 双解码）。
- 交叉核对：`docs/audit-2026-09-27/backend-routes-baseline.csv`（114 行）、`openapi.json`（118 操作 = 114 + 4 根路由）。

## 附录 B：与 team-lead 基线的差异说明

| 项 | team-lead 基线 | 本报告 | 说明 |
|---|---|---|---|
| 端点数 | 114 | 114 | **完全一致** |
| `screener.py` 空路径 | 记作 `screener.py:523 get ""` | `screener.py:523` → `/api/v1/screener` | 同一端点，无差异 |
| 行号口径 | 装饰器行号 | 装饰器行号 | 一致 |
| 未覆盖形式 | 担心漏 `api_route`/动态注册 | 全仓无 `api_route`、无 `add_api_route` | 无漏项 |
| 新增发现 | — | `market/overview*` 三条**无鉴权**（公开）；`auth/login`·`register`·`register/status` 公开 | 基线未标注鉴权列 |

---

---

## 附：修复阶段新增发现（补录）

> 以下两条在初版审计（15:31）之后由修复与验证阶段发现，补录于此。

### 附-1 基准取数无异常守卫（已修复）

`domain/portfolio.py` 的**基准（benchmark）取数原先没有任何 `except` 守卫**，与标的资产取数不对称：
标的取数失败会被收敛为「部分资产数据获取失败」，而基准取数失败会**直接逃逸成 `ERR_SYSTEM`(50000)**，
表现为"系统错误"，与"数据未同步"混淆，误导排查方向。

**已修复**：基准取数纳入与标的取数同一套守卫，失败归 `ERR_DATA_EMPTY`(51001)，
对外文案不含内部类名。QA 已实测「未知基准 `999999` → 51001」端到端生效。

### 附-2 `/ops/lineage` 冷启动 —— ⚠️ 本条原描述已证伪，真实问题是「冷启动惊群」（已修复）

> **⚠️ 勘误（2026-09-27 晚）**：本条原记载「冷启动返回 `ERR_UNIFIED_TASK_CONFLICT`(40951)，
> 8 秒内 4 次并发全部命中」。该描述**经查证为错误，40951 在全仓及全部 git 历史中均不存在**：
> - 全仓搜索 `40951` / `ERR_UNIFIED_TASK_CONFLICT` / `TASK_CONFLICT` / `unified` → **0 命中**
> - `git log --all -S` 全历史搜索 → **0 命中**
> - 实测复现（冷缓存 + 4 路并发）→ **4/4 全部 HTTP 200 code=0**，无一例 40951
> - 早期取证中该端点的失败记录**只有客户端 20s 超时**
>
> 完整证据见 `docs/audit-2026-09-27/lineage-40951-evidence.md`。
> 以下保留真实的架构问题与已实施的修复。

**真实问题：冷启动惊群（thundering herd）**

缓存未命中时，并发请求各自触发一次全量 parquet 冷扫：
- 单请求冷扫 29.34s
- **4 路并发因磁盘争用劣化到 60.89 ~ 63.05s**，且**重复 4 倍 IO**
- 后果：超过前端 60s 预算 ⇒ **实际全部超时**，用户看到血缘图加载失败

**已实施的修复：single-flight**

`cache/swr.py` 新增**可选** `single_flight` 参数（默认 False，对其他调用方零影响）：
- 同 key 只创建一个重建 Task，其余请求 `await asyncio.shield(task)` **共享结果**
- 回写 / after_build 在任务内完成 ⇒ 发起方断连也不丢冷扫成果
- `ops.py` 端点与 `warm_lineage_cache()` 均置 True（预热与请求共享同一次重建）

**实测效果**

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 冷启动 4 路并发 | 60.89 ~ 63.05s | **28.70s** |
| 单请求冷扫 | 29.34s | 29.34s |
| 稳态（6 轮） | 12.6 ~ 40.6ms | **8.1 ~ 17.3ms（avg 10.3ms）** |

monkeypatch 计数验证：预热进行中发请求，`_compute_lineage` **真实调用次数 = 1**。

> 未采纳「冷启动无 stale 返回 202」方案：前端 `get<LineageGraph>` 会把非 0 code 当 `ApiError`，
> 202 只会把"冲突"换成"报错"反而劣化。single-flight 让冷启动直接返回 200 真实数据，是更稳的等价解。

**架构建议（三选一，按推荐度排序）** —— 以下为原建议，供后续其他慢端点参考：

1. **首选：有 stale 缓存则返回 stale + 后台重建**
   复用仓内既有的 `cache.swr.cached_or_build`（stale-while-revalidate）语义：
   只要影子键里有上一版数据，就返回旧值并后台重建，**完全不产生冲突响应**。
   冷启动期（无任何历史数据）是唯一例外。

2. **冷启动且无 stale：返回 `202 Accepted` + 任务进行中**
   而非 40951。前端据此显示"正在构建，请稍候"并轮询，
   语义清晰且可操作。比抛一个冲突码友好得多。

3. **缓存击穿保护（single-flight）**
   并发请求应**共享同一次后台重建**，而不是各自去抢锁再失败。
   当前"每次并发都抢锁、抢不到就报冲突"的做法，本质是把并发控制泄漏成了 API 语义。
   建议引入 single-flight（同 key 只放行一个重建，其余等待并共享结果）。

**冷路径耗时不可预测的兜底**：
`_compute_lineage()` 实测 33.68s，且取决于 parquet 扫描量，随数据量增长可能更久。
现有启动预热（`WARM_OVERVIEW_ON_STARTUP` 开关约束）是主要防线，
建议：① 确保预热在生产默认开启；② 预热失败时告警而非静默；
③ 前端 timeout 保持 60s 兜底（当前值），但**不应依赖它**——真正的解法是上述 1+3。

---

**报告结束**（只读审计，未修改任何业务代码；新写入仅本文件）。
