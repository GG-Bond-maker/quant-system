# AQP 上线前代码审查 + 前后端接口契约覆盖审计

- **审计人**：gstack-product-reviewer（task #1）
- **日期**：2026-10-04
- **范围**：`D:/Python_Project/Alpha Quant Platform`（后端 `backend/app`，前端 `frontend/src`）
- **性质**：**只读审计**，未修改任何生产代码
- **可复现脚本**（本次新建，只读）：
  - `deliverables/gstack/_tmp/contract_diff.py`（方法感知的双向差集）
  - `deliverables/gstack/_tmp/contract_loose.py`（宽松交叉核对）
  - `deliverables/gstack/_tmp/be_scan.py` / `be_scan2.py`（后端反模式 AST 扫描）
  - 复用 `deliverables/gstack/_qa_route_scan.py`（后端 AST 路由提取 → `route_scan.json`）

---

## 0. TL;DR

| 维度 | 结论 |
|---|---|
| 接口契约覆盖 | **前端调用全部有后端实现（0 个 404）**；后端仅 **1 个真死接口**（`/market/overview`，已显式标注 DEPRECATED 并保留理由） |
| 严重缺陷 | **无 🔴P0**。最高为 🟠P1 级 0 条、🟡P2 级 5 条、🟢P3 级 4 条 |
| 历史反模式 | 裸 `except:` = 0；`except Exception: pass` 返回伪造成功 = 0；async 内同步阻塞 = 0（已逐一证伪）；`create_task` 未跟踪 = 0 |
| 主要可优化点 | `/desk/orders` N+1（每单一次 fills 查询）、OrderDesk 自动刷新并发 4 请求含 2 个重接口、`instrument` 全表入内存无缓存、前端 `.slice` 静默截断 2 处 |

---

## 1. 接口契约覆盖（双向，最高优先级）

### 1.1 比对方法（可复现）

1. **后端**：`python deliverables/gstack/_qa_route_scan.py` 用 AST 解析 `backend/app/api/v1/*.py` 的
   `@router.<method>` 装饰器 + `router.py` 的 `include_router(prefix=...)`，拼出全路径
   （`/api/v1` + include 前缀 + 模块 prefix + 路由路径）。结果落 `route_scan.json`。
2. **前端**：`contract_diff.py` 用正则扫描 `frontend/src/**/*.{ts,tsx}`，捕获
   `get/post/put/del/download/patch(...)` 包装器内的 `/api/v1/...` 字面量，**保留 HTTP 方法**。
3. **归一化**：`${...}`（TS 模板）与 `{...}`（FastAPI 路径参数）统一替换为 `{*}`，去查询串、去尾斜杠。
4. **差集**：`fe_paths - be_paths`（潜在 404）与 `be_paths - fe_paths`（死接口），并做**方法一致性**比对。

### 1.2 原始计数

```
后端已注册路由: 118 条 (唯一路径 111)
前端引用路径  : 109 条 (唯一 method+path 127)
交集路径      : 109
```

### 1.3 方向 A：前端调用了但后端未实现 —— **0 条** ✅

> `### A. 前端引用但后端未注册（潜在 404） ###  (无)`

宽松口径（`contract_loose.py`，抓取全部 `api/v1/*` token 含注释）交叉核对后，剩余 7 条“疑似缺失”**全部为误报**，逐条已核：

| 疑似路径 | 实际来源 | 判定 |
|---|---|---|
| `/api/v1/datacenter` | `pages/DataCenter/index.tsx:11` 注释 | 误报 |
| `/api/v1/etf` | `types/etf.ts:1` 注释 | 误报 |
| `/api/v1/research` | `api/research.ts:1` 注释 | 误报 |
| `/api/v1/watchlist` | `types/watchlist.ts:1` 注释 | 误报 |
| `/api/v1/nope` | `api/timeouts.test.ts:47` 测试夹具 | 误报 |
| `/api/v1/report/daily${date` | `monitor.ts:123` 模板字面量截断 | 真实调用 `/report/daily`，后端已实现 |
| `/api/v1/stock/${encodeURIComponent` | `stock.ts:22/25/33/42` 模板截断 | 真实调用 `/stock/{symbol}/*`，后端已实现 |

**结论：无 404 风险。**

### 1.4 方向 B：后端已实现但前端未调用（死接口）

严格差集列出 2 条，逐条核验后：

| # | 方法 | 路径 | 定义 | 判定 |
|---|---|---|---|---|
| B1 | GET | `/api/v1/market/overview` | `market.py:1326 market_overview` | **真·死接口**，但**刻意保留**（见下） |
| B2 | GET | `/api/v1/notify/stream` | `notify.py:84 stream` | **误报**——前端经 `notifyApi.streamUrl()` 用 `EventSource` 消费（`notify.ts:33` + `useNotifyStore.ts:102`），非 `get()` 包装器故正则未命中 |

**B1 详情**（`market.py:1339` 内联文档原文）：

> ⚠️ DEPRECATED（2026-09-27 审计）：本端点的功能已被 `/market/overview/rt` + `/market/overview/daily` **完全覆盖**，前端早已改用拆分块，**新代码不要再用**。仅因两点保留：① `_build_overview` 被启动预热 `warm_overview_cache`（`main.py`）共用；② 有专用回归套件 `tests/test_overview_heal_chain.py`。

→ **不是缺陷**，是已登记的兼容性包袱。**建议**（🟢P3）：在 OpenAPI 层加 `deprecated=True` 并在 `docs/` 标注 sunset 计划，避免下一位读者再次触发同样告警。

### 1.5 方向 C：方法不一致 —— **0 条** ✅

`/export/*` 的 `download()`（GET/POST 二态）与后端逐一吻合：`/export/screener`=GET、`/export/backtest`=POST、`/export/strategy-backtest`=POST（`export.py:18/61/85` ↔ `export.ts:15/27/37`）。

---

## 2. 代码审查

### 2.1 反模式扫描（AST 精炼版，排除嵌套 def 误报）

| 检查项 | 命中 | 结论 |
|---|---|---|
| 裸 `except:` | 0 | ✅ |
| `except ...: pass/continue` 单行静默 | 0 | ✅ |
| handler 体为空（多行 AST 判定） | 47 | 见 §2.2，**无一是“伪造成功”**，多为显式降级 |
| async 直接体内的同步阻塞调用 | **0** | ✅（原始 5 条全部为**嵌套同步函数经 `asyncio.to_thread` 卸载**，见 §2.3 证伪） |
| 无 timeout 的 HTTP 调用 | **0** | ✅（`multi_source.py:80` 为跨行 `timeout=10.0` 误报） |
| `asyncio.create_task/ensure_future` 未跟踪 | **0** | ✅（`swr.py:207/281` 均有 `_bg_tasks.add`/`_inflight[key]` 跟踪 + done_callback 清理） |
| `TODO/FIXME/HACK` | 0（唯一命中是 docstring 里的 `XXX` 占位符字面量） | ✅ |

### 2.2 静默降级点抽样（🟡P2）

47 条空 handler 逐类抽检，**未发现“吞掉异常后返回编造数据”**；但以下 3 处**静默降级且无日志**，会掩盖真实故障：

| 位置 | 代码 | 风险 | 建议 |
|---|---|---|---|
| `datacenter.py:848` | `try: …MAX(finished_at…)… except Exception: pass` | `data_jobs` 读取失败时静默回退到 parquet mtime，运维看不到 DB 异常 | 加 `logger.warning` |
| `datacenter.py:908` | `except Exception: pass`（构造 health 前缀） | 健康度降级原因被吞，用户只看到 “yellow” 无解释 | 加日志 + 降级字段透出 |
| `research.py:142` | `except Exception: pass`（`model_registry` COUNT） | 库不可读时 `model_version_count` 静默显示 **0**，误导为“无模型” | 加日志，或返回 `null` + note |

其余（`etf.py:1046` fee 拉取失败→null、`report.py:296` 日报单节失败不中断、`screener.py:249` 日期解析跳过、`alerts.py:778` `CancelledError` 正常关闭）均为**有明确语义的 best-effort**，可接受。

### 2.3 “async 内同步阻塞”证伪记录（避免误伤）

原始 AST 命中 5 条 `sqlite3.connect`，全部位于**嵌套同步函数**内，经线程池卸载，**不阻塞事件循环**：

- `app_settings.py:351` → 嵌套 `_do()`，`await asyncio.to_thread(_do)`（app_settings.py:363）
- `ops.py:523` → 嵌套 `_run()`，`await asyncio.to_thread(_run)`（ops.py:536）
- `research.py:139` → 嵌套 `_sync()`
- `research.py:249` → 嵌套 `_run()`，`await run_in_compute_pool(_run)`

→ **无真实缺陷**。

### 2.4 配置校验缺口（🟡P2）

`PreferencesIn.refresh_freq: int | None = None`（`app_settings.py:188`）**无 `ge/le` 约束**；前端 `useRefreshIntervalMs` 仅做 `Math.max(1, …)`（`useRefreshInterval.ts:13`）。虽然 UI 是固定 `<select>`（`Settings/index.tsx:453`）挡住手输，但直连 API 可写入 `0 / 1 / 负数` → 令轮询页以 **1s** 频率打后端。建议后端加 `Field(ge=3, le=600)`。

---

## 3. 性能可优化点

### 🟡P2-1 · `/desk/orders` N+1 查询

- **位置**：`backend/app/api/v1/desk.py:217-247`（`list_orders`），循环体 `desk.py:231`
- **证据**：
  ```python
  orders = session.execute(select(PaperOrder)…limit(limit)).scalars().all()
  out = []
  for o in orders:                                   # limit 默认 50，前端传 200，上限 1000
      fills = session.execute(                      # ← 每张母单一次独立查询
          sa.select(PaperFill).where(PaperFill.order_id == o.id)…).scalars().all()
  ```
- **影响**：1(COUNT)+1(orders)+N(fills)。前端 `ORDER_WINDOW=200` ⇒ 单次请求 **202 条 SQL**。
- **修复**：`selectinload(PaperOrder.fills)`（SQLAlchemy 2 原生）或 `WHERE order_id IN (...)` 一次取回后分组。

### 🟡P2-2 · OrderDesk 自动刷新并发 4 请求，含 2 个重接口

- **位置**：`frontend/src/pages/OrderDesk/index.tsx:71-73`（`Promise.all`）+ `:91-96`（`setInterval`）
- **证据**：每 tick 并发 `account()`（需重建逐日净值序列）、`orders(200)`（N+1 见上）、`killSwitch()`、`exclusionList()`；`refreshMs = useRefreshIntervalMs(2)`。
- **影响**：默认 `refresh_freq=3s`（后端）/`5s`（前端）× 2 ≈ 每 6~10s 触发一次“重接口风暴”，叠加 §2.4 无下限校验时可放大到 2s。
- **修复**：把轻量状态（killSwitch/exclusion）与重数据（account/orders）拆成不同刷新档；或复用 SWR `refreshInterval` 分级（轻 30s / 重 0）。

### 🟡P2-3 · `instrument` 全表入内存，无缓存

- **位置**：`watchlist.py:165 _load_instrument_names()`、`market.py:429`、`datacenter.py:1590`
- **证据**：`SELECT symbol[, name] FROM instrument` 无 LIMIT，每次调用全量拉取（docstring 自称“一次全量入内存”，但函数体**无 `lru_cache`/TTL 缓存**，仅由调用方 `asyncio.to_thread` 包装）。
- **修复**：加进程级 TTL 缓存（instrument 表变更频率低），或 `SELECT … WHERE symbol IN (?)` 按需取。

### 🟢P3-1 · 前端 `.slice()` 静默截断

| 位置 | 代码 | 问题 |
|---|---|---|
| `pages/MarketOverview/BreadthPanel.tsx:53` | `return out.slice(0, TARGET)` | 各桶 `Math.round` 后总和可能 > 240，slice **静默丢弃**尾部格子 → 广度热力图色块比例失真（无披露） |
| `components/Topbar.tsx:179` | `setResults(items.slice(0, 12))` | 搜索结果截断到 12 条**无“还有 N 条”提示** |

> 注：`EtfDetail/index.tsx:80/130/211` 的 `.slice` 已有明确语义/披露（前十大、`PIE_MAX_SLICES` + tail 聚合），**不是缺陷**。

### 🟢P3-2 · 其他

- `research.py:142` 的静默 0 值（同 §2.2）兼具数据准确性影响。
- `market/overview` 死接口（同 §1.4）。
- 前端 3 个 overview 端点（rt/daily/global）并发属**预期设计**（分块降级），非缺陷。

---

## 4. 结论与建议

1. **接口契约面非常干净**：118 后端路由 ↔ 109 前端调用路径，**零 404、零方法错配**；唯一死接口是已登记 DEPRECATED 的 `/market/overview`。这一项**可判定为 PASS**。
2. **代码健壮性**：历史高发的裸 except / 静默吞异常 / async 阻塞已基本清零；仅 3 处“静默降级无日志”建议补日志。
3. **上线前建议处理的 3 件事**（按性价比）：
   - 修 `/desk/orders` N+1（§3 P2-1）—— 改动小、收益明确；
   - 给 `refresh_freq` 加后端范围校验（§2.4）—— 一行 `Field(ge=3, le=600)`；
   - 拆 OrderDesk 刷新档位（§3 P2-2）—— 消除重接口高频轮询。
4. **已知未覆盖项**（超出本任务只读静态审计能力，交由 QA/安全）：**响应体字段级契约**（前端类型声明 vs 后端实际返回字段）需运行时校验；本报告仅覆盖**路径级**契约。

---

## 5. 与 QA 交叉核验的代码级发现（合并结论）

> QA（task #3）回报 3 处与其数据一致性排查重叠的代码级问题。我已**独立复核**（读源码 + 追后端可达性），结论如下，并合并进本契约审查。

### 🟠P1（QA#1）· `EtfDetail` K 线把 null OHLC 兜成 0，渲染"价格 0"假 K 线

- **位置**：`frontend/src/pages/EtfDetail/index.tsx:814-825`（`klineBars` 的 `b.open ?? 0` / `close` / `high` / `low` / `volume`）
- **可达性证据链**：
  1. 类型 `EtfKlineBar`（`types/etf.ts:189-196`）明为 `open/close/high/low/volume: number | null`；
  2. 后端 `backend/app/data/etf.py:345 _num()`：`'-'`（停牌/无数据）⇒ `return None`；该函数**正是** `open/close/high/low/volume` 的赋值来源（`etf.py:549-551`）⇒ `null` **可达**；
  3. 同文件**自己**在 3 处明令禁止此写法：`:449`「禁止 `?? 0` 把缺失伪造成 0 偏离」、`:504`「不得用 `?? 0` 把未知染成『跌』绿」、`:660`「绝不 `?? 0` 染成『零净额』」——**唯独 `klineBars` 漏网**。
- **影响**：停牌/无数据 ETF 的 K 线会被画成价格 0 的假蜡烛（且方向判定 `close>=open` 恒真 ⇒ 假"涨"）。属团队明令禁止的"伪造数据"类。
- **修复**：`klineBars` 保持 `null` 透传（图表层按缺口处理，或过滤该 bar），与同文件其它 3 处口径一致。

### 🟢P3（QA#2）· `MarketOverview` 浅合并的双重断言 —— 已复核，**非"同名不同型"**

- **位置**：`frontend/src/pages/MarketOverview/index.tsx:88` `{...daily.data, ...rtData} as unknown as MarketOverviewData`
- **我的独立核验**：逐一比对 `OverviewRt`（`types/stock.ts:582-593`）与 `OverviewDaily`（`:596-604`）的**同名字段**：
  - `trade_date`：`string` vs `string` ✅ 一致
  - `from_cache`：`boolean | 'refreshed'` vs `boolean | 'refreshed'` ✅ 一致
  - 其余字段**无同名**（rt 有 `indices/money_flow/anomalies`，daily 有 `heat/sectors/recommend/ai_stats/sentiment/pred_dates`）
  - ⇒ QA 假设的「同名不同型」在当前类型定义下**不存在**。
- **真实问题（降级）**：`MarketOverviewData`（`:554`）把 `indices/heat/money_flow/anomalies/recommend` 声明为**必需**，而浅合并结果**必然无法满足**（rt 无 `heat`、daily 无 `indices`）⇒ 双重断言**掩盖了缺失的必需字段**。**该风险已在类型自身注释中登记**（`stock.ts:562-568`，引用 commit `e2283c9` 的 ErrorBoundary 事故）⇒ 属**已知**遗留，非新阻塞项。
- **建议**：把 `MarketOverviewData` 的这些块改为可选（`?`），或在合并处显式构造，去掉 `as unknown as` 断言。

### 🟢P3（QA#3）· `MarketHeat` 是死类型且声明与真实不符

- **位置**：`frontend/src/types/stock.ts:318-325`
- **证据**：`grep -rn "MarketHeat" frontend/src` 仅命中 `MarketHeatmap`（组件名）、`chartTheme.ts`/`test/setup.ts` 注释 —— **该 interface 零引用**；真实类型是 `HeatBlock`（`:459`），字段**全可选**（`up?/down?/flat?/…`，`total_amount_yi?: number|null`）。
- **影响**：死类型 + 把可选字段声明为必需，会误导后续取值（误以为可非空直取）。
- **修复**：删除 `MarketHeat`，统一用 `HeatBlock`。

---

### 附：后端路由全量清单（111 唯一路径）

<details>
<summary>展开</summary>

```
DELETE  /api/v1/alerts/rules/{*}
DELETE  /api/v1/studio/factors/{*}
GET     /api/v1/alerts/events
GET     /api/v1/alerts/health
GET     /api/v1/alerts/rules
GET     /api/v1/auth/me
GET     /api/v1/auth/register/status
GET     /api/v1/datacenter/datasets
GET     /api/v1/datacenter/instruments
GET     /api/v1/datacenter/logs
GET     /api/v1/datacenter/mirror/status
GET     /api/v1/datacenter/overview
GET     /api/v1/datacenter/quality
GET     /api/v1/datacenter/sync/auto
GET     /api/v1/datacenter/sync/status
GET     /api/v1/datacenter/sync/tasks/{*}
GET     /api/v1/datacenter/task-stats
GET     /api/v1/datacenter/text/status
GET     /api/v1/datacenter/train/readiness
GET     /api/v1/datacenter/train/status
GET     /api/v1/desk/account
GET     /api/v1/desk/capacity
GET     /api/v1/desk/exclusion
GET     /api/v1/desk/exclusion/screen
GET     /api/v1/desk/kill-switch
GET     /api/v1/desk/orders
GET     /api/v1/etf/detail/{*}
GET     /api/v1/etf/flow
GET     /api/v1/etf/hot
GET     /api/v1/etf/list
GET     /api/v1/etf/overview
GET     /api/v1/etf/overview/series
GET     /api/v1/etf/performance
GET     /api/v1/etf/scale
GET     /api/v1/export/screener
GET     /api/v1/market/index/kline
GET     /api/v1/market/overview          ← 死接口（DEPRECATED，保留）
GET     /api/v1/market/overview/daily
GET     /api/v1/market/overview/global
GET     /api/v1/market/overview/rt
GET     /api/v1/market/quotes
GET     /api/v1/monitor/health
GET     /api/v1/notify/recent
GET     /api/v1/notify/stream
GET     /api/v1/ops/dag
GET     /api/v1/ops/lineage
GET     /api/v1/portfolio/search
GET     /api/v1/report/daily
GET     /api/v1/research/experiments
GET     /api/v1/research/feature-importance
GET     /api/v1/research/lab/yearly
GET     /api/v1/research/overview
GET     /api/v1/screener
GET     /api/v1/screener/stats/series
GET     /api/v1/screener/stocks
GET     /api/v1/screener/watchlist
GET     /api/v1/settings
GET     /api/v1/stock/search
GET     /api/v1/stock/{*}/kline
GET     /api/v1/stock/{*}/panels
GET     /api/v1/stock/{*}/predict
GET     /api/v1/stock/{*}/profile
GET     /api/v1/studio/factors
GET     /api/v1/watchlist/correlation
GET     /api/v1/watchlist/dashboard
POST    /api/v1/alerts/events/read
POST    /api/v1/alerts/rules
POST    /api/v1/auth/login
POST    /api/v1/auth/register
POST    /api/v1/backtest/run
POST    /api/v1/backtest/signal-analysis
POST    /api/v1/backtest/strategy-run
POST    /api/v1/backtest/strategy-run/cancel
POST    /api/v1/backtest/strategy-run/opt-status
POST    /api/v1/datacenter/mirror/rebuild
POST    /api/v1/datacenter/sync
POST    /api/v1/datacenter/sync/auto
POST    /api/v1/datacenter/sync/cancel
POST    /api/v1/datacenter/sync/fetch
POST    /api/v1/datacenter/text/build-factor
POST    /api/v1/datacenter/text/import
POST    /api/v1/datacenter/train/cancel
POST    /api/v1/datacenter/train/start
POST    /api/v1/desk/attribution
POST    /api/v1/desk/exclusion
POST    /api/v1/desk/exclusion/toggle
POST    /api/v1/desk/fills/run
POST    /api/v1/desk/kill-switch
POST    /api/v1/desk/orders
POST    /api/v1/export/backtest
POST    /api/v1/export/strategy-backtest
POST    /api/v1/monitor/run
POST    /api/v1/notify/stream-ticket
POST    /api/v1/ops/dag/rerun
POST    /api/v1/ops/quality-scan
POST    /api/v1/portfolio/backtest
POST    /api/v1/report/daily/generate
POST    /api/v1/research/cv-folds
POST    /api/v1/research/factor-corr
POST    /api/v1/research/factor-icir
POST    /api/v1/research/factor-quantile
POST    /api/v1/research/impact-sim
POST    /api/v1/research/optimize
POST    /api/v1/research/stress-test
POST    /api/v1/settings/connectors/test
POST    /api/v1/settings/data/cache/clear
POST    /api/v1/settings/data/sync
POST    /api/v1/settings/db/backup
POST    /api/v1/studio/alpha-eval
POST    /api/v1/studio/factor-report
POST    /api/v1/studio/factors
POST    /api/v1/studio/mining/cancel/{*}
POST    /api/v1/studio/mining/start
POST    /api/v1/studio/nl-to-factor
PUT     /api/v1/settings/engine
PUT     /api/v1/settings/preferences
```
</details>
