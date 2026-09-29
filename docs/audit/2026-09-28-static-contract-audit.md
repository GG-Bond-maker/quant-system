# AQP 静态契约审查报告（前后端对账 / 死按钮 / 超时 / 性能）

- 审查人：高见远（架构师）
- 日期：2026-09-28
- 范围：`backend/app/**`（FastAPI）、`frontend/src/**`（Vite + React + TS）
- 方式：**只读静态审查 + 命令取证**，未修改、未创建任何源码文件
- 唯一写入物：本报告 `docs/audit/2026-09-28-static-contract-audit.md`

---

## 0. 取证方法（可复现）

所有结论均由下列命令产出，脚本落在临时目录 `C:\Users\HY\AppData\Local\Temp\aqp_audit\`（不在项目内）。

### 0.1 后端真实注册路由（运行时遍历，非肉眼 grep）

```bash
cd backend
./.venv/Scripts/python.exe -c "
from app.main import app
rows=[]
for r in app.routes:
    if hasattr(r,'methods'):
        for m in sorted(r.methods):
            if m in ('HEAD','OPTIONS'): continue
            rows.append((m, r.path))
rows.sort(key=lambda x:(x[1],x[0]))
for m,p in rows: print(m,p)
"
```
> 说明：系统默认 `python` 无 fastapi，`backend\.venv\Scripts\python.exe` 有（fastapi 0.115.0），用它导入成功。
> 该方式直接读 `app.routes`，**含 `main.py` 的全局前缀与所有 `include_router` 前缀**，可杜绝假阳性。

**结果：124 条路由（method+path 全展开），其中 `/api/v1/*` 115 条，基础设施 9 条**（`/`、`/docs`、`/docs/oauth2-redirect`、`/health`、`/health/live`、`/health/ready`、`/metrics`、`/openapi.json`、`/redoc`）。
已确认后端**没有**除 `/api/v1` 之外的额外业务前缀，前端 URL 均自带 `/api/v1`，比对口径一致。

### 0.2 前端真实请求 URL（AST 级扫描，非简单 grep）

脚本：`extract_fe2.py` — 遍历 `frontend/src/**/*.ts(x)`，匹配 `get|post|put|del|download` 调用
（支持泛型写法 `get<T>(`、跨行实参、模板串、模板串内嵌 `${...}` 与三元表达式），
再按顶层逗号切分实参取第 1 个（URL）。

**结果：120 个调用点**，其中 `api/client.ts`(5) 与 `api/swr.ts`(1) 是封装自身定义，
剔除后 **114 个业务调用点** 参与对账。

### 0.3 双向 diff

脚本：`diff2.py` — 把前端 URL 归一化（模板占位 → `{p}`，剥离 query）后，
对后端路由做「精确匹配 → 路径参数模式匹配」，且**强制 method 一致**
（`download` 因 `client.ts` 内按 payload 决定 GET/POST，故 method 放宽为任意）。

### 0.4 超时实参提取

脚本：`timeouts.py` — 手工扫描泛型尖括号 + 配对括号，按调用签名取 timeout 实参索引
（`get/post/put` 第 3 个、`del` 第 2 个、`download` 无此参数走 `client.ts` 内固定 180s），
并解析 `*_TIMEOUT` 常量。

---

## A. 接口契约双向对账表

### A.0 总览

| 指标 | 数值 |
|---|---|
| 后端注册路由（method+path） | **124**（`/api/v1/*` 115） |
| 前端业务调用点 | **114** |
| 前端调用 → 后端缺失（必然 404） | **0** ✅ |
| 后端存在 → 前端从未调用 | **2**（1 真 / 1 假阳性） |
| 动态 / 无法静态判定 | **0** |

> **核心结论：前端没有任何"点了必然 404"的死接口。** 用户担心的第 2 类问题（有按钮拉不起后端）
> 在本项目**不成立**——契约层是干净的。真正的问题集中在**超时**（C 节）与**能力未暴露**（A.2）。

### A.1 前端调用 → 后端缺失（P0 致命）

**空表。** 114 个前端调用点全部命中后端已注册路由，且 method 一致。

（取证过程中的 3 次"疑似缺失"均已定位为工具误判，列入下表以免复查时重复踩坑：）

| 曾报出的缺失 | 真实情况 |
|---|---|
| `GET /api/v1/export/backtest`、`/export/strategy-backtest` | 后端是 **POST**；前端走 `download()`，client.ts 内按 payload 走 POST。属 method 放宽后匹配 |
| `GET /api/v1/report/daily{p}` | `monitor.ts:114` 模板串 `` `/api/v1/report/daily${date ? `?date=${date}` : ''}` ``，三元用于拼 **query**，路径实为 `/api/v1/report/daily` ✅ |
| `POST /api/v1/settings/connectors/test` 等 35 条 | 首版脚本漏抓跨行实参导致误报；修正后全部命中 |

### A.2 后端存在 → 前端从未调用（P2 能力未暴露）

| # | Method | Path | 判定 | 说明 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/market/overview` | **真·未调用（P2）** | 兼容聚合端点。页面 `MarketOverview/index.tsx:47,54` 只调 `/overview/rt` 与 `/overview/daily` 两个拆分块；后端 `_build_overview()` 仍完整实现并供后台预热 `warm_overview_cache()` 共用。**对用户的可见影响：无**（功能已由拆分块覆盖），属可清理的兼容债务 |
| 2 | GET | `/api/v1/notify/stream` | **假阳性** | 该端点是 SSE 长连接，不走 axios：`stores/useNotifyStore.ts:102` 用 `new EventSource(notifyApi.streamUrl(ticket))` 调用（`api/notify.ts:31`）。后端 `timeout_guard.py:89` 已将其列入豁免路径，语义一致 |

### A.3 动态 / 无法静态判定 URL

**空表。**（模板串 11 处全部为路径参数或 query 拼接，均已静态解析成功；
全仓无 `fetch(`/`axios(`/WebSocket 绕过封装的直连调用，仅 `DataCenter/index.tsx:597` 的
`datacenterApi.fetch(req)` 是封装内的业务方法名，非浏览器 fetch。）

---

## B. 前端死按钮 / 死组件清单

### B.1 扫描方法

1. 占位文案：`TODO|FIXME|敬请期待|即将上线|开发中|暂未开放|coming soon|未实现`
2. 空 handler：`onClick={() => {}}` / `onClick={undefined}`
3. 仅打日志：`onClick.*console.`
4. 恒真 disabled：`disabled={true}` / `disabled={1}`
5. 无 handler 的 `<button>`：扫描全部 `<button` 开标签，检查 `onClick` / `type="submit"`
6. 死组件：组件文件从未被 import、页面目录内子组件未被渲染
7. 死链：Sidebar 导航项 ↔ `App.tsx` 路由表

### B.2 结论

| 检查项 | 结果 |
|---|---|
| 占位文案（敬请期待等） | **0 处** ✅（仅 `Sidebar.tsx:150` 是 disabled 机制的注释） |
| 空 / 仅 console 的 handler | **0 处** ✅ |
| 恒真 disabled 按钮 | **0 处** ✅（3 处 `disabled={!!testing.xxx}` 均为真实 loading 态） |
| 无 handler 的 `<button>` | **0 处** ✅ |
| 侧边栏死链 | **0 处** ✅（18 个导航项 ↔ 19 条路由全覆盖；`/login` 独立渲染） |
| 页面目录内未渲染子组件 | **0 处** ✅（`MarketOverview/pieces.tsx` 由 `KpiCards.tsx` 间接引用，非死文件） |
| **从未被 import 的组件** | **1 处（P2）** |

### B.3 唯一死组件（P2）

| 文件 | 组件 | 症状 | 判定依据 | 建议 |
|---|---|---|---|---|
| `frontend/src/components/charts/MarketHeatmap.tsx:97` | `MarketHeatmap`（导出 `default`，147 行完整实现） | **全仓 0 处 import**，涨跌分布玫瑰图 + 北向净流入柱永不渲染 | `grep -rn "MarketHeatmap" frontend/src` 仅命中自身第 97 行；同目录 `KLineChart`(2) / `KpiBits`(2) / `Sparkline`(6) 均有引用 | ① 若要该能力：`MarketOverview/index.tsx` 已渲染 `BreadthPanel`/`MoneyFlowPanel`，二者数据同源（`data?.heat`），可直接替换或并列；② 若不要：删除文件（含 2 个 ECharts 实例，减少死代码与打包体积） |

### B.4 附带排除项（已核实为误报，避免重复排查）

- `pages/Settings/index.tsx:422/450/482` 三个"测试连接"按钮 → 真实调用 `settingsApi.testConnector()` → `POST /api/v1/settings/connectors/test`（`api/settings.ts:74`，30s），**非死按钮**。
- `components/ui/index.tsx` 被 8+ 页面以 `from '@/components/ui'` 引用（目录导入，工具按文件名匹配故报 0），**非死文件**。
- `Sidebar.tsx:158` 的 `NavItem.disabled` 能力存在但当前 18 项导航**无一使用**，属预留机制，不算缺陷。

---

## C. 超时链路分析（用户最关心的第 3 个问题）

### C.1 全链路时序与阈值

```
[浏览器] axios timeout (per-call 或默认 15000ms)
   │  超时 → axios abort → client.ts:124 isRetryableNetworkError 明确"不重试"
   │       → ApiError(code=-1, "请求超时，请稍后重试")
   ▼
[ASGI] TimeoutGuardMiddleware (core/timeout_guard.py)
   │  默认预算 REQUEST_TIMEOUT_SECONDS = 240s
   │  _PATH_BUDGETS: /backtest/strategy-run = 660s, /ops/dag/rerun = 330s
   │  _EXEMPT_PATHS: /api/v1/notify/stream（SSE 豁免）
   │  超时 → 未 start 回 HTTP 504 + ERR_REQUEST_TIMEOUT(50400) 统一信封
   ▼
[业务] compute_slot (core/compute_guard.py)  ← 仅部分端点接入
   │  threading.BoundedSemaphore(COMPUTE_CONCURRENCY = 2)
   │  等待上限 COMPUTE_ACQUIRE_TIMEOUT_SECONDS = 10s，超时 → ERR_RATE_LIMITED(40103)
   ▼
[执行] asyncio.to_thread(...)  → 不可取消（线程跑完才释放）
```

### C.2 前端 timeout 全景（114 个调用点）

| 前端超时 | 调用点数 | 说明 |
|---|---:|---|
| **15s（client 默认）** | **49** | ⚠️ 最大风险面 |
| 30s | 22 | ETF 全系 / screener / watchlist / desk 账户、成交 / research overview、experiments、feature-importance / settings 连接器与清缓存 / panels |
| 60s | 9 | datacenter overview、text/build-factor、mirror/rebuild；report/daily/generate；studio/mining/start；ops/lineage、ops/dag；settings、settings/db/backup |
| 90s | 2 | `datacenter/datasets`、`datacenter/quality` |
| 120s | 16 | backtest/run、signal-analysis；market/overview/rt、/daily；monitor/run；studio/nl-to-factor、alpha-eval、factor-report、factors(POST)；portfolio/backtest；research factor-icir/corr/quantile、lab/yearly、cv-folds、impact-sim |
| 180s | 4 | ops/quality-scan、desk/attribution、research/optimize、research/stress-test |
| 180s（download 固定） | 3 | `export/screener`、`export/strategy-backtest`、`export/backtest`（`client.ts:299` 硬编码） |
| 300s | 1 | `ops/dag/rerun` |
| 600s / 120s（三元） | 1 | `backtest/strategy-run`（`optimize_params` 时 600s，否则 120s） |
| 10s / 15s / 20s / 45s | 6 | notify(10s)、monitor/health(15s)、report/daily(15s)、portfolio/search(15s)、settings/data/sync(15s)、market/quotes(20s)、datacenter/mirror/status(45s) |

### C.3 后端自身保护能力

| 机制 | 位置 | 阈值 | 覆盖 |
|---|---|---|---|
| 全局请求预算 | `core/timeout_guard.py` | 240s（`/backtest/strategy-run` 660s、`/ops/dag/rerun` 330s；SSE 豁免） | **全部 HTTP**（ASGI 中间件） |
| 重计算并发闸门 | `core/compute_guard.py` | `COMPUTE_CONCURRENCY=2`，等待 10s 后 40103 | **仅** backtest(3) / desk(1) / export(2) / portfolio(1) / research(7) 共 **14 处** `Depends(compute_slot)` |
| 外部数据源熔断 | `config.py` AKSHARE_BREAKER_* | 连续 1 次失败即熔断，冷却 15s | akshare 数据源 |
| Redis 超时 | `REDIS_TIMEOUT=1.0s` | 超时自动降级内存缓存 | 缓存层 |
| 面板分块超时 | `stock.py:68` `_PANEL_BLOCK_TIMEOUT=4.5s` | 单块 4.5s，超时回 SWR 旧值 | `/stock/{symbol}/panels` 的 8 个块 |
| 后台循环韧性 | `core/resilience.py` | 兜住 PanicException | **仅后台协程**，不覆盖请求 |

### C.4 ⚠️ 超时不匹配风险点（前端 timeout < 后端典型耗时）

**方向说明：本项目不存在"后端先 504、前端还在等"的误杀**——唯一长期路径预算 240s 严格大于
前端最大 180s，两条长路径（600s/300s）也已在 `_PATH_BUDGETS` 单独延长到 660s/330s，
不变量「服务端预算 > 前端超时」成立。
**真实风险是反方向：前端先断，后端继续烧 CPU 并占用仅有的 2 个计算 slot。**

#### P1（明确会复现的用户可见超时）

| # | 端点 | 前端 | 后端实测/注释耗时 | 证据 | 后果 |
|---|---|---|---|---|---|
| C-1 | `GET /api/v1/datacenter/datasets` | 90s | 代码注释 23.8s；**实测 >240s 触发 504** | `backend-run.log:235`、`backend-run.log:606` 两条 `[timeout-guard] GET /api/v1/datacenter/datasets 超时（>240s）` | 冷算阶段前端 90s 必然超时，后端继续占用到 240s 才回 504。冷算根因：`api/v1/datacenter.py:641` 走 `_scan_all_datasets()` 遍历 **3.3 万个 parquet**；而同文件已存在快路径 `_manifest_dataset_summary()`（:389）只被 `/overview`（:505）使用，**`/datasets` 没用** |
| C-2 | `GET /api/v1/datacenter/quality` | 90s | 代码注释 31.6s（与 datasets 同族全扫） | `api/v1/datacenter.py:114-116` 注释 | 同 C-1 代码路径，日志暂未命中但风险等价 |
| C-3 | `GET /api/v1/datacenter/sync/status`（轮询） | 15s，且 `DataCenter/index.tsx:534` 每 **1.5s** 一次 | 同步进行中每次都要聚合任务状态 | `api/datacenter.ts:59` 无显式 timeout | 单次若 >1.5s，请求**无限堆积**（无 AbortController、无 in-flight 去重），叠加 datacenter 冷算会把后端拖垮并放大 C-1 |
| C-4 | `POST /api/v1/datacenter/sync` | 15s | 启动全市场同步（2499 只标的），需等互斥锁与初始化 | `api/datacenter.ts:51` 无显式 timeout；日志 `sync incremental ... done=0/2499` | 冷启动阶段 15s 极易超时；前端 `startSync` 捕获后提示"同步任务启动失败"，但**后端任务可能已真的启动**（前端不知道 task_id）→ 状态不一致 |
| C-5 | `GET /api/v1/stock/{symbol}/predict` | 15s | `asyncio.to_thread(predict_symbol)`：因子构建 + LightGBM 推理 + SHAP，**无任何服务端预算注释**，缓存 6h 仅覆盖热路径 | `api/v1/stock.py:201-217` | 冷路径无超时契约，15s 断线后线程仍跑完并写缓存（结果被丢弃），下一次还要重算一遍——**超时不但没省算力，反而重复消耗** |
| C-6 | `GET /api/v1/stock/{symbol}/kline` | 15s | parquet 读取 + `enrich_kline` 指标计算，缓存 10min | `api/v1/stock.py:151-197` | 冷路径（长区间 + 多指标）风险中等 |
| C-7 | `GET /api/v1/desk/capacity` | 15s | `rglob("*.parquet")` **全目录递归**后只读最后 1 个文件 + `read_parquet` | `api/v1/desk.py:279-294` | 目录越大越慢，且 `rglob` 是纯浪费（见 D-2） |
| C-8 | `GET /api/v1/screener/watchlist` | 15s | 每次请求 `pl.read_parquet(最新 predictions 分区)`（全截面），**无缓存** | `api/v1/screener.py:100-130, 508-519` | 自选股越多越慢，且 Watchlist 页有轮询钩子 `useWatchlistQuotes.ts:74` → 反复重读同一分区 |
| C-9 | `GET /api/v1/datacenter/train/readiness`、`/train/status` | 15s | 训练就绪度检查需扫描特征分区 | `api/datacenter.ts:104,108` 无显式 timeout | 与训练任务同机竞争 CPU，15s 偏紧 |

#### P2（建议预防性放宽）

| 端点 | 前端 | 理由 |
|---|---|---|
| `GET /api/v1/market/index/kline` | 15s | 外部数据源（腾讯源），无熔断保护，抖动时可达数秒~十几秒 |
| `GET /api/v1/desk/exclusion/screen` | 15s | `asyncio.to_thread` 内 SQL 全表筛选（`desk.py:159-166`） |
| `GET /api/v1/studio/factors`（GET） | 15s | 因子列表含表达式校验，条目多时偏慢 |
| `GET /api/v1/studio/mining/status/{id}` | 15s，且 `FactorStudio/index.tsx:119` 每 **2s** 轮询 | 同 C-3 的堆积风险 |
| `PUT /api/v1/settings/engine` | 15s | 引擎配置保存可能触发缓存失效/重建 |

### C.5 超时设计的正确之处（不要改）

- `client.ts:109-126`：**ECONNABORTED（前端超时）明确不重试**，且注释说明了理由——
  后端 HTTP 恒 200、长任务真在跑，重试等于重复提交长计算。**这个决策是对的，请勿"优化"成重试。**
- `timeout_guard.py` 对 504 用统一信封（`fail(50400)`），使前端响应拦截器走 `isApiEnvelope`
  分支拿到真实 code，同时**短路**了 `isRetryableNetworkError` 对 504 的自动重试——避免了雪崩。
- `swr.ts:27` 已 `retry: false` opt-out，避免 SWR 重试 × client 重试叠加放大到 6 次。

---

## D. 性能风险清单（静态层面）

### D.1 后端

| # | 位置 | 影响 | 具体优化建议 | 优先级 |
|---|---|---|---|---|
| D-1 | `main.py:104-118` `_overview_warmer` + `api/v1/market.py:1025` | 后台预热每 **240s** 一轮，每轮 **23~27s**（日志实测 20+ 次：`warm cache done in 22.2s / 23.7s / 24.7s / 25.5s / 26.4s / 26.6s / 27.3s`），约 **10% 常驻 CPU**；且直接 `asyncio.to_thread(_build_overview)`，**绕过 `compute_slot` 闸门**，与用户重计算抢 GIL 与 CPU | ① 纳入 `compute_slot` 或独立低优先级线程池；② 把 240s 间隔按"是否真有请求"自适应（空闲时降频到 600s）；③ 预热的 `ThreadPoolExecutor(max_workers=3)`（`market.py:563`）降到 2，与 `COMPUTE_CONCURRENCY` 对齐 | **P1** |
| D-2 | `api/v1/desk.py:279`、`:343` | `sorted((DATA_ROOT/"universe_daily").rglob("*.parquet"))` **全目录递归遍历**后只用 `files[-1]` 一个文件；目录含数千文件时，遍历成本 >> 实际读取成本 | 改为按日期分区直接定位：`(root/f"date={td}").glob(...)` 或从 manifest 取最新分区路径，去掉 `rglob` | **P1** |
| D-3 | `api/v1/datacenter.py:641` | `/datasets` 走 `_scan_all_datasets()` 遍历 **3.3 万 parquet**，冷算 23.8s~>240s（见 C-1） | **复用本文件已有的快路径**：`_manifest_dataset_summary()`（:389，读 `.manifest.json`，零 parquet I/O），把 `/datasets` 的 `_cached(..., _scan_all_datasets)` 换成 `_cached(..., _manifest_dataset_summary)`；仅在 manifest 缺失时回退全扫 | **P1** |
| D-4 | `api/v1/datacenter.py:428` `_storage_stats` | `os.scandir` 全树遍历 + **逐文件 `stat()`**（3.3 万次系统调用）统计磁盘占用，供 `/overview` 与 `/settings` 使用 | 已缓存，但冷算仍贵：① 改为抽样/增量（只统计当日新增分区 + 上轮基线）；② 或直接读 manifest 中已维护的字节数 | P2 |
| D-5 | `config.py:181` `COMPUTE_CONCURRENCY` | `Field(default=2, ge=1, le=2)` —— **`le=2` 把上限硬编码为 2**，单进程最多 2 个重计算，第 3 个请求排队 10s 后直接 40103。UI 上表现为"计算资源繁忙"（如同时开回测 + 研究页） | 放开 `le` 上限（如 `le=8`），默认取 `min(4, cpu_count//2)`；并在 `/monitor/health` 暴露 slot 使用率，前端在排队时显示进度而非裸 40103 | P2 |
| D-6 | `data/parquet_store.py:154` | 跨进程写锁等待用 `time.sleep(_LOCK_POLL_SECONDS)` 轮询，占满一个线程池线程（默认 `asyncio` 线程池 = min(32, cpu+4)，但重计算已被 slot 限流，实际争抢严重） | 改为指数退避（如 0.05→0.5s 封顶）或使用 `threading.Event` + 文件变更通知，减少空转 | P2 |
| D-7 | `data/realtime.py:95-103` `_throttle`、`:141`、 `data/etf.py:260`、 `data/ingest/akshare_adapter.py:80`、 `multi_source.py:120` | 同步 `time.sleep` 限速/退避。均在 `to_thread` 内，不阻塞事件循环（可接受），但会长时间占用工作者线程 | 保持现状即可；若要优化，改用 `asyncio` 侧限速后再入线程 | P3 |
| D-8 | `api/v1/etf.py:709,781` | 每次请求新建 `ThreadPoolExecutor`（`max_workers=min(4, len(requests))`），线程池创建/销毁有固定开销，且并发数不受 `COMPUTE_CONCURRENCY` 约束 | 复用模块级线程池；或统一走 `compute_slot` | P2 |
| D-9 | 日志 | loguru 三 sink 全 `enqueue=True`（`core/logging.py:46/59/72`），**异步写盘** ✅；但 `sqlalchemy.engine` INFO 级 SQL 回显出现在日志中（`backend-run.log:314/435/521`） | 生产把 `sqlalchemy.engine` 设为 WARNING，减少 I/O 与日志体积 | P2 |

### D.2 前端

| # | 位置 | 影响 | 具体优化建议 | 优先级 |
|---|---|---|---|---|
| D-10 | 49 个调用点走 15s 默认 | 见 C.4：重计算端点必然超时 | 仿照已有 `REFRESH` 分级表（`api/swr.ts:32`）建立 **`TIMEOUT` 分级常量表**（light 15s / normal 30s / heavy 120s / long 300s），`api/*.ts` 全部显式引用，禁止裸默认值；并加一条 lint/单测断言"重端点必须显式传 timeout" | **P1** |
| D-11 | `pages/DataCenter/index.tsx:534`（1.5s 轮询 `datacenterApi.status()`）、`pages/FactorStudio/index.tsx:119,129`（2s 轮询）、`hooks/useWatchlistQuotes.ts:74`、`pages/OrderDesk/index.tsx:90`、`pages/Alerts/index.tsx:402` | 轮询**均不带 `AbortSignal`**，也无 in-flight 去重；单次请求慢于轮询间隔时请求无限堆积（1.5s/2s 场景最危险），且切换路由后旧响应仍会 `setState` | 统一用 `AbortController`：每次发新请求前 `abort()` 上一个；`useEffect` cleanup 里 abort。已有 `hooks/useAbortableTask.ts`（11 个页面在用）——把轮询也收敛到它 | **P1** |
| D-12 | `pages/FactorStudio/index.tsx:129` `resumeTask` | `timer.current = setInterval(...)` **未先 `clearInterval(timer.current)`** 就覆盖赋值 → 连点"查看"多次会叠加多个 2s 轮询（定时器泄漏） | 在 `resumeTask` 开头加 `if (timer.current) clearInterval(timer.current);`（同文件 :87/:103 已有该写法，:129 漏了） | **P1** |
| D-13 | `hooks/useRefreshInterval.ts`、`hooks/useTaskPolling.ts` | 两个轮询钩子**各自只被 1 个页面使用**（`OrderDesk`、`DataCenter/TrainPanel`），其余 5 处轮询全是手写 `setInterval`，行为不一致 | 把所有手写轮询迁移到 `useTaskPolling` / `useAbortableTask`，统一可见性判断 + abort + 清理 | P2 |
| D-14 | `pages/Etf/index.tsx:151` `Chart`、`pages/DataCenter/index.tsx:84` `useChart` | effect 依赖 `[option]`，**option 一变就 `dispose()` + `init()` 重建实例**（ECharts init 开销 ~10-50ms）。当前调用点已用 `useMemo` 收敛（`Etf/index.tsx:451`、`DataCenter/index.tsx:102,160`），暂未抖动 | 统一收敛到 `utils/useChart.ts`（init 一次 + `setOption(option, true)` + ResizeObserver），消除未来的实例抖动风险 | P2 |
| D-15 | ECharts 实例释放 | **全仓 11 处 `echarts.init` 均有配对 `dispose()`**（`useChart.ts`、`KLineChart`、`MarketHeatmap`、`Backtest/resultParts`、`DataCenter`、`Etf`、`Etf/PerformanceChart`、`EtfDetail`、`MarketOverview/AiPicksPanel`） | ✅ 无需处理 | — |
| D-16 | SWR | `api/swr.ts` 已配 `dedupingInterval: 5000`（同 key 并发合并）+ `retry:false`（避免与 client 重试叠加）✅；但**仅 `MarketOverview` 2 个端点使用**，其余 16+ 页面仍是手写请求 | 按 `swr.ts` 文件头既有路线图继续迁移（实时 30s / 快照 300s / 静态 0），迁移后删除手写 setInterval | P2 |
| D-17 | 大列表 | `/screener/stocks`（`screener.py:710` page_size 1~100，默认 20）与 `/etf/list`（`etf.py:579` page_size 1~100，默认 20）**均有服务端分页** ✅，单页 ≤100 行 | 无需虚拟滚动；若后续放开 page_size 上限，再引入虚拟列表 | — |
| D-18 | 打包 | `App.tsx:15-32` 17 个页面已 `React.lazy` 分包、`lib/echarts.ts` 已按需注册 ✅ | 无需处理；可进一步把 `Backtest`/`Research` 内的 ECharts 图表再拆子 chunk | P3 |

---

## E. 优先级汇总

### P0（功能不可用 / 必崩）
**无。** 契约层 0 缺失、0 死按钮、0 死链；错误码表双向对账 **32 个码完全一致**（差集为空，
取证：`frontend/src/types/api.ts` 的 `ERR` vs `backend/app/core/errors.py` 的 `ERR_*` 集合异或为空集）。

### P1（明显缺陷，建议本迭代修）

| 编号 | 一句话 | 位置 |
|---|---|---|
| C-1 | `/datacenter/datasets` 冷算 >240s 触发 504，前端 90s 必然先超时 | `api/v1/datacenter.py:641` + 日志 :235/:606 |
| C-2 | `/datacenter/quality` 同族全扫（31.6s） | `api/v1/datacenter.py` |
| C-3 | 1.5s 轮询 `/datacenter/sync/status` 无 abort，请求堆积 | `pages/DataCenter/index.tsx:534` |
| C-4 | `POST /datacenter/sync` 15s 超时，前后端任务状态可能不一致 | `api/datacenter.ts:51` |
| C-5 | `/stock/{sym}/predict` 冷路径 15s，超时后重算浪费 | `api/v1/stock.py:201` |
| C-7 | `/desk/capacity` 15s，`rglob` 全目录遍历 | `api/v1/desk.py:279` |
| C-8 | `/screener/watchlist` 15s 且每次全量读预测分区、无缓存 | `api/v1/screener.py:508` |
| D-1 | 后台预热 240s/轮 × 25s/轮，绕过 compute_slot | `main.py:104` |
| D-2 | `rglob("*.parquet")` 全扫后只用最后一个文件 | `api/v1/desk.py:279,343` |
| D-3 | `/datasets` 未用已存在的 manifest 快路径 | `api/v1/datacenter.py:641` vs `:389` |
| D-10 | 49 个调用点裸用 15s 默认，缺 timeout 分级表 | `api/*.ts` |
| D-11 | 5 处轮询无 AbortSignal | `DataCenter:534`、`FactorStudio:119/129`、`useWatchlistQuotes:74`、`OrderDesk:90`、`Alerts:402` |
| D-12 | `resumeTask` 未 clearInterval 即覆盖，定时器泄漏 | `pages/FactorStudio/index.tsx:129` |

### P2（优化项）
A-2（`/market/overview` 未暴露）、B-3（`MarketHeatmap` 死组件）、C-6、C-9、
C 节 P2 表 5 条、D-4、D-5、D-6、D-8、D-9、D-13、D-14、D-16。

---

## F. 需要 QA（运行时）确认的项

静态审查无法覆盖，建议交给运行时验证：

1. `/api/v1/datacenter/datasets`、`/quality` 在**真实数据量**下的冷算耗时曲线（是否稳定 >90s）。
2. `/stock/{symbol}/predict` 冷路径实测耗时（决定 C-5 是否需要放宽到 30s/60s）。
3. `/screener/watchlist` 在选股数 10/50/100 时的耗时（决定是否需要加缓存）。
4. 前端在 15s 超时后是否有"后端任务仍在跑"的可见提示（C-4 的状态一致性）。
5. `MarketHeatmap` 删除或接入，需产品确认。
