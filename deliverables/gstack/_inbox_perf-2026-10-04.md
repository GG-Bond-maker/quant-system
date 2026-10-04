# AQP 超时风险与性能根因取证（2026-10-04 上线前全检 · 调查员）

> 只读取证，未改动任何生产代码。范围：后端 `backend/app/**`、前端 `frontend/src/api/**`。
> 标注：**【实测】**= 本次命令/代码读取直接得到；**【推断】**= 由代码路径 + 数据规模保守推导。
> 数据规模实测：`data/parquet` 共 **77,907** 个 `.parquet`（daily_bar 5,223 个 symbol 目录 / 25,214 文件；predictions 263 个 `date=` 文件；universe_daily 1 项）。

---

## TL;DR

1. 历史上"前端 vs 后端超时"这条线**已基本修好**（`timeouts.ts` 集中表 + 双向闸门 + `_PATH_BUDGETS/_PATH_MAX_BUDGETS`）。
2. 真正的**剩余风险有三类**：① **预算 vs 真实工作量**仍有 1 处濒临（RT 15s vs 真实 ~14s）；② **"白烧方向"不变量只对 8 条路径登记**，其余 ≥12 条重活端点仍落回 240s 默认预算，而前端只等 15~180s；③ **重活线程池隔离只做了一半**——desk / screener / monitor / studio / report 仍跑在 22 槽**默认池**（与 `/health/ready` 共用），等于重开 2026-09-30 那个已定性 P0 的口子。

---

## 一、超时预算账本（后端常量 → 保护对象 → 真实工作量）

| # | 常量 | 值 | 文件:行 | 保护对象 | 真实工作量（证据） | 判定 |
|---|---|---|---|---|---|---|
| 1 | `RT_BUILD_TIMEOUT_SECONDS` | **15.0s** | `market.py:1065` | `/market/overview/rt` | 9 次 akshare 外呼（5 指数串行 + 3 资金源 + 1 快照），全局限速 `AKSHARE_RATE_LIMIT=1.2s` 串行化 ⇒ 正常 ~14s；指数每次 ~2.3s（注释 `market.py:1053-1064`）**【实测注释+推断】** | 🔴 7% 余量，濒临必然降级 |
| 2 | `GLOBAL_BUILD_TIMEOUT_SECONDS` | 25.0s | `market.py:1079` | `/market/overview/global` | 5 次新浪外呼串行 ≈12s | 🟢 ~2× 余量 |
| 3 | `DAILY_BUILD_TIMEOUT_SECONDS` | 20.0s | `market.py:214` | `/market/overview/daily` | 修复后冷 7.6s / 稳态 8.6s（`market.py:208-209`） | 🟢 ~2.3× 余量 |
| 4 | `OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS` | 6.0s | `market.py:1048` | `/market/overview`（DEPRECATED） | `_build_overview` 实测 23.5~113.5s ⇒ **请求路径 100% 超时**，靠无预算后台重建回填 | 🟡 有意设计，靠 background_build |
| 5 | `_ETF_ENDPOINT_BUDGET_SECONDS` | 4.5s | `etf.py:105` | `/etf/overview` | 冷路径实测 6.22s ⇒ 冷路径**必然超时**，靠 `_build_unbudgeted` + 预热 | 🟡 有意设计 |
| 6 | `_ETF_DETAIL_BLOCK_BUDGET_SECONDS` | 4.0s | `etf.py:106` | `/etf/detail/{code}` 各块 | 冷 6.22s | 🟡 有意设计 |
| 7 | `_WATCHLIST_BUDGET_SECONDS` | 5.5s | `watchlist.py:34` | `/watchlist/dashboard` | 多块并发 | 🟢 |
| 8 | `_PANEL_BLOCK_TIMEOUT` | 4.5s | `stock.py:68` | `/stock/{sym}/panels` 每块 | 每块独立预算 | 🟢 |
| 9 | `SINGLE_FLIGHT_WAIT_BUDGET_SECONDS` | 38.0s | `swr.py:227` | single-flight 等待方 | 必须 < `_PATH_MAX_BUDGETS` 最小 48s | 🟢 |
| 10 | `REQUEST_TIMEOUT_SECONDS` | 240.0s | `config.py:108` | 全局兜底（未登记路径） | 见第二节：**远大于**多条路径的前端超时 | 🔴 白烧源 |
| 11 | `SOCKET_DEFAULT_TIMEOUT_SECONDS` | 10.0s | `config.py:122` | akshare/requests 无 timeout 调用 | 兜住不可取消 worker | 🟢（但 10s × 重试可超小预算） |
| 12 | `COMPUTE_ACQUIRE_TIMEOUT_SECONDS` | 10.0s | `config.py:205` | 计算池排队 | — | 🟢 |

**结论**：账本上"预算 < 真实工作量 ⇒ 必然降级"的端点只剩 **#1（RT，濒临）**；#4/#5 是**已声明的有意设计**（靠无预算后台重建自愈），非缺陷但需监控。

---

## 二、前端 ↔ 后端超时对齐（含"白烧方向"）

**已对齐（8 条，闸门双向校验）**：`/settings`(60/48)、`/ops/lineage`(60/48)、`/datacenter/datasets`(90/72)、`/datacenter/mirror/status`(120/96)、`/backtest/run`(120/96)、`/backtest/signal-analysis`(120/96)、`/research/optimize`(180/144)、`/research/stress-test`(180/144)；延长向 `/backtest/strategy-run`(600/660)、`/ops/dag/rerun`(300/330)。**【实测】**

**未对齐（白烧方向：服务端 240s ≫ 前端超时）—— 至少 12 条重活端点落回默认 240s**：

| 前端路径 | 前端超时 | 后端预算 | 潜在白烧 | 文件证据 |
|---|---|---|---|---|
| `/api/v1/market/quotes` | **20s** | 240s 默认 | 220s | `market.ts:91` |
| `/api/v1/screener` (GET "") | **30s** | 240s 默认 | 210s | `screener.ts:16` |
| `/api/v1/screener/stocks` | **30s** | 240s 默认 | 210s | `screener.ts` |
| `/api/v1/desk/*`(fills/run,account,orders,capacity,exclusion/screen) | **30s** | 240s 默认 | 210s | `production.ts:276-284` |
| `/api/v1/research/overview` | 30s | 240s 默认 | 210s | `research.ts:29` |
| `/api/v1/market/index/kline` | 15s | 240s 默认 | 225s | `market.ts:84` |
| `/api/v1/research/{factor-icir,factor-corr,factor-quantile,cv-folds,impact-sim}` | 120s | 240s 默认 | 120s | `research.ts:33-124` |
| `/api/v1/studio/{alpha-eval,factor-report,nl-to-factor,factors}` | 120s | 240s 默认 | 120s | `production.ts:56-67` |
| `/api/v1/monitor/run` | 120s | 240s 默认 | 120s | `monitor.ts:120` |
| `/api/v1/portfolio/backtest` | 120s | 240s 默认 | 120s | `portfolio.ts:10` |
| `/api/v1/ops/quality-scan` | 180s | 240s 默认 | 60s | `production.ts:177` |
| `/api/v1/datacenter/{quality,mirror/rebuild}` | 90/180s | 240s 默认 | 150/60s | `datacenter.ts:74,141` |

**根因**：`timeout_guard._PATH_MAX_BUDGETS`（`timeout_guard.py:151-185`）只登记了 8 条；未命中者一律取 `default_timeout_seconds()`=240s（`timeout_guard.py:216`）。`config.py:107` 的注释 **"其余路径前端最长 180s"是错的**——存在 15/20/30s 档路径。⇒ 一旦这些端点 hang，用户早已断开、服务端仍烧 210~225s 的 worker（且见 F1：烧的是默认池）。

> 与 2026-10-04 已修的 4 条同因同源，只是**登记面覆盖不足**。

---

## 三、阻塞 / 挂死源

### 🔴 F1 — 重活线程池隔离只做了一半：desk/screener/monitor/studio/report 仍跑默认池（22 槽，与探针共用）
- **类别**：线程池耗尽 / 级联挂死
- **位置**：`desk.py:65,86,102,135,155,166,195,208,252,260,294,503`；`screener.py:519,553,570,637,745,758,766,768`；`monitor.py:39`；`studio.py:73,83,209,211,231,276,336,354,436`；`report.py:500`（均 `asyncio.to_thread`）
- **对比**：`market.py:1003/1149/1192/1277/1452`、`etf.py:296/486/820`、`research.py:166-605`、`backtest.py:494/1005/1122`、`export.py:41/74/100`、`datacenter.py:1021`、`ops.py:406/575` **已迁移** `get_compute_pool()`。
- **证据**：`compute_pool.py` docstring（第 10-16 行）实测"22 路并发 ⇒ `/health/ready` 挂死 12008ms，容器判 unhealthy，且 22/22 返回 200、监控看不见"。desk `fills/run`、screener `_screen`（全市场扫描）、`/monitor/run`、`studio/*` 都是重活，却仍落默认池。**【实测：代码路径】**
- **建议**：把上述端点的 `asyncio.to_thread` 统一替换为 `run_in_compute_pool`（与 P-8 口径一致）；重活端点补 `Depends(compute_slot)`。

### 🟠 F4 — `/market/quotes` 冷缓存无 single-flight ⇒ 惊群 + 默认池占用
- **位置**：`quotes_hub.py:116-142`（`_QUOTES_CACHE_LOCK` 只护 cache 读/写，**不护 `_fetch_payload`**）
- **证据**：缓存 miss 时 N 个并发调用各自 `await asyncio.to_thread(_fetch_payload)`（第 135 行）⇒ 外部请求次数 = **并发 miss 数**，与注释声称的"= 过期次数"不符。分片 ≤4 片，单次 socket 兜底 10s ⇒ 最坏可超前端 20s。**【实测：代码】**
- **建议**：对 `cache_key` 加 single-flight（复用 `swr._single_flight` 模式）；`/market/quotes` 登记 `_PATH_MAX_BUDGETS`（如 16s = 20×0.8）。

### 🟢 已正确处理（无需动）
- akshare 无 timeout：`socket.setdefaulttimeout(10)`（`main.py:61-64`）兜住；熔断（`akshare_adapter.py:120-184`）把快速失败压到毫秒级。
- SSE 长连接豁免超时（`timeout_guard.py:107-113`）。

---

## 四、性能热点

### 🟠 F5 — `/desk/orders` N+1 查询
- **位置**：`desk.py:230-233`：对每个 order 单独 `SELECT fills`，循环上限 `limit≤1000` ⇒ **1+N 次查询**（N 最多 1000）。
- **建议**：改 `selectinload`/一次 `IN` 批量取 fills 后内存分组。

### 🟡 F8 — 日频块每轮 glob 5,223 个 symbol 目录 × 2
- **位置**：`market.py:317`（`_heat_from_local`）、`market.py:413`（`_sectors_from_local`），均 `glob("symbol=*/year={year}.snappy.parquet")` 并 `read_parquet_columns` 全量读。
- **实测规模**：`daily_bar` 5,223 symbol 目录 / 25,214 文件。两函数**并行**跑 ⇒ 约 2×5,223 文件打开/轮（缓存未命中时）。
- **建议**：改用按 `date=` 分区的宽表或 manifest 快路径（`datacenter` 已有 `_gated_scan`+manifest 先例），避免每次冷算重扫全年。

### 🟡 F9 — 预警成交量扫描串行 N 次读
- **位置**：`alerts.py:607-622`：`for sym: await asyncio.to_thread(read_symbol_dataset, "daily_bar", sym)`，并发上限 6。
- **建议**：批量读或复用 screener 的宽表。

### 🟢 F10 — `_build_indices` 串行 5 次外呼
- **位置**：`market.py:55-57`。5 指数在 `for` 循环内串行，共享全局限速 ⇒ 约占 RT 15s 预算中的 ~11.5s。属 F3 的**根因**，可作独立优化项（换更轻指数据源 / 独立限速域），但改动面大，本轮不建议。

### 轮询频率 vs 后端成本（无致命不匹配，仅登记）
- `MarketOverview` rt 轮询 30s（`swr.ts:31`），RT 冷算 ~14s + TTL 45s ⇒ 稳态命中缓存，OK。
- `strategyBacktest` opt-status 轮询用 `TIMEOUT.probe`(10s)（`strategyBacktest.ts:215-220`），后端为轻查询，OK。

---

## 五、按严重度汇总

| 级别 | ID | 一句话 | 位置 |
|---|---|---|---|
| 🔴 P0 | F1 | 重活仍跑 22 槽默认池（desk/screener/monitor/studio/report），重开已定性 P0 | 见 §3-F1 |
| 🟠 P1 | F2 | 白烧不变量只登记 8 条，≥12 条重活端点落回 240s（前端仅 15~180s） | `timeout_guard.py:151`；`config.py:107` 注释有误 |
| 🟠 P1 | F3 | RT 预算 15s vs 真实 ~14s（7% 余量），濒临必然降级 | `market.py:1065` |
| 🟠 P1 | F4 | `/market/quotes` 冷缓存无 single-flight + 无预算 | `quotes_hub.py:116-142` |
| 🟠 P1 | F5 | `/desk/orders` N+1（≤1000 次查询） | `desk.py:230-233` |
| 🟡 P2 | F8 | 日频块每轮 glob 5,223×2 目录 | `market.py:317,413` |
| 🟡 P2 | F9 | 预警成交量串行 N 读 | `alerts.py:607-622` |
| 🟡 P2 | F6 | ETF/overview 预算<冷算（靠后台重建，有意设计，需监控） | `etf.py:105`；`market.py:1048` |
| 🟡 P2 | F7 | 前端注释漂移（`market.ts:11` 仍写日频冷 22.2s，实际已 7.6s） | `market.ts:11` |
| 🟢 P3 | F10 | `_build_indices` 串行 5 外呼（F3 根因） | `market.py:55-57` |

## 六、修复优先级建议（只读取证，未改代码）
1. **F1**：desk/screener/monitor/studio/report → `run_in_compute_pool` + `compute_slot`（与 P-8 同口径，改动机械）。
2. **F2**：把 30s 档及以下的重活路径补登 `CAPPED_PATHS` + `_PATH_MAX_BUDGETS`（0.8×）；同时修正 `config.py:107` 的错误注释。
3. **F3**：RT 预算按前端 120s 上限上调（如 25s），或拆分指数/资金源为独立限速域；**新数据块继续禁止并入 RT 预算**（现 `global` 已拆出，做法正确）。
4. **F4/F5**：quotes single-flight + 登记预算；desk/orders 批量取 fills。
