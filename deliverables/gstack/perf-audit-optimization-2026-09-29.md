# Alpha Quant Platform · 性能优化评估报告

**日期**：2026-09-29
**场景**：性能审计（4 域并行）→ 主理人实测复核 → 优化建议
**参与成员**：后端接口/并发/中间件/存储（general-purpose-3）、前端渲染/包体（general-purpose-4）、外部数据源/ML/流水线/启动（general-purpose-5）、后端数据层/IO（general-purpose-2）
**工作区**：`D:\Python_Project\Alpha Quant Platform`（Windows，18 核）

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟡 **有条件通过 —— 系统能跑、无 P0 崩溃，但仍有明确可观的性能优化空间**。
- **可优化空间显著**：四域合计 40+ 条可执行项；其中 **1 条为正确性缺陷**（必须修）、**6 条 P0/P1 收益最大**。
- **最大单点收益**：数据中心 `/datasets`、`/quality` 冷扫 **36,500 个 parquet 文件**（26.5s / 33s），而项目**已存在** manifest 快路径（`/overview` 已在用，<1s）——但因 manifest 本身不可信（见 P0-C1），目前不能直接复用。
- **最该先做**：① 修复 manifest 正确性 → ② 打通 manifest 快路径 → ③ 清掉 async 路径里的同步阻塞 → ④ 前端 3 个 P0 请求扇出修复。
- **阻塞项数量**：0（不影响启动）；**强烈建议修复项**：7 条。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go（性能视角） | 🟢 **Go**（可上线）；🟡 **优化后更佳** |
| 严重度分布 | 🔴 1（正确性） / 🟠 6 / 🟡 9 / 🟢 若干 |
| 关键行动项 | 7 条（含 1 条正确性必修） |
| 预计整体收益 | 数据中心冷载 **62s → <5s**；首屏请求数 **-40%**；包体 **-25%**；同步吞吐 **×3~5** |
| 建议负责人 | 后端：数据层 + 接口层；前端：数据中心页 owner |

---

## 1. 各成员核心结论

### 🔧 后端接口 / 并发 / 中间件 / 存储（general-purpose-3）
- **核心判断**：async 路由里混着多处**同步阻塞**，会占用事件循环；重计算并发闸门（`compute_slot`）覆盖不全；`orjson` 未接线；SQLite 连接多数未设 `timeout`。
- **关键建议**：`health_ready` / `stock_profile` / alerts 评估链改 `asyncio.to_thread`；把 `compute_slot` 扩到 datacenter/market/ops；`default_response_class=ORJSONResponse`；统一 `sqlite3.connect(..., timeout=30)`；补索引。

### 🔧 后端数据层 / IO / 存储（general-purpose-2）
- **核心判断**：**`.manifest.json` 不可信**——只覆盖 4/8 个数据集、`daily_bar` 仅 40% 标的有日期区间、`first` 被逐年写覆盖而前移 → `/overview` 与 `/datasets` 数据不一致（**这是正确性缺陷，不只是性能**）。
- **关键建议**：先修 manifest 语义（累计 first/last、补齐缺失数据集、与磁盘对账），再让 `/datasets`、`/quality` 走 manifest 快路径；否则「用 manifest 换性能」会直接把错数据固化。

### 🔧 外部数据源 / ML / 流水线 / 启动（general-purpose-5）
- **核心判断**：AKShare 限速锁**在 sleep 期间持锁**导致全局串行；`akshare_adapter` 文档声称有 `ThreadPoolExecutor` 但**代码里根本没有**；指数抓取 5 个串行；同步是逐标串行 + **每年分区全量重写**。
- **关键建议**：限速改为「锁内算、锁外睡」；指数抓取并发化；`write_partition` 改增量追加（或分片）；文档与实现对齐。

### 🔧 前端渲染 / 包体 / 轮询（general-purpose-4）
- **核心判断**：数据中心页存在**请求扇出放大**（切换「展开全部」重发全部 7 个请求；`autoStatus` 挂载时发两次；1.5s 轮询无退避）；echarts 全量注册拖大包体；`React.memo` 使用为 0。
- **关键建议**：拆分 `loadAll` 依赖、去重 `autoStatus`、轮询加可见性+退避+abort；echarts 按需注册；关键列表组件加 `React.memo`；配置 `manualChunks`。

---

## 2. 综合审查发现（去重合并，按严重度排序）

| # | 严重度 | 域 | 位置 | 问题 | 建议 | 来源 |
|---|--------|----|------|------|------|------|
| 1 | 🔴 | 数据正确性 | `app/data/parquet_store.py:237,221-228`；`data/parquet/.manifest.json` | manifest 只覆盖 4/8 数据集；`daily_bar` 仅 994/2499 标的有 first/last；`_manifest_record` 用**单年** min/max 覆盖 `first` ⇒ 起始日期前移；`/overview` 信 manifest ⇒ 行数/标的/区间**与 `/datasets` 不一致** | 修 manifest 语义：first/last 取累计 min/max；`_manifest_scan_dataset` 补日期区间；启动时与磁盘对账；补 qfq/features/predictions | gp-2 |
| 2 | 🟠 | 后端 IO | `app/api/v1/datacenter.py:374-381` → `_scan_dataset`→`_scan_file`（`:234`） | `/datasets`、`/quality` 冷扫**全部 36,500 个 parquet footer**（实测 26.5s / 33s），而 `/overview` 走 manifest <1s —— 两套实现并存 | 修好 manifest 后，让 `/datasets`、`/quality` 复用 manifest（带「manifest 过期则回退扫描」闸门） | gp-2 / 主理人实测 |
| 3 | 🟠 | async 阻塞 | `app/main.py:351`（`:367` `sqlite3.connect(timeout=2)` + `:370` `BEGIN IMMEDIATE`；`:396` `rglob`） | `/health/ready` 在事件循环里做同步 SQLite 写锁探测 + 全树 `rglob`，健康探针会卡住整个 loop | 整体包进 `asyncio.to_thread`；`rglob` 换 `os.scandir` 或缓存计数 | gp-3（主理人复核） |
| 4 | 🟠 | async 阻塞 | `app/api/v1/alerts.py:353`（`pl.read_parquet`）、`:425`（`sqlite3.connect`）、`:587/641`；调度 `:709` | 预警评估链**全程同步**，每 30s（盘中）在 loop 里跑；多规则时叠加 | 评估链整体 `to_thread`；或移入独立 worker/线程池 | gp-3（主理人复核） |
| 5 | 🟠 | 前端扇出 | `frontend/src/pages/DataCenter/index.tsx:525,527` | `loadAll` 的 `useCallback` 依赖含 `showAllQuality` ⇒ 切换质量表「展开全部」**重发全部 7 个请求**（含 90s 的 datasets/quality），可自我触发二次全量加载 | 把 `showAllQuality` 从依赖移除，改为「单独只刷新 quality」；或拆成独立 effect | gp-4（主理人复核） |
| 6 | 🟠 | 前端重复请求 | `frontend/src/pages/DataCenter/index.tsx:458-470` vs `:488` | `autoStatus()` 挂载时被**触发两次**（独立 effect + `loadAll` 内），多一次无谓往返 | 保留 `loadAll` 内一处，删掉独立 effect；或反之 | gp-4（主理人复核） |
| 7 | 🟠 | 前端轮询 | `frontend/src/pages/DataCenter/index.tsx:534` | `setInterval(…, 1500)` 硬编码：无可见性判断（后台标签页照轮）、无退避、无 abort | 加 `document.visibilityState` 守卫 + 指数退避（1.5s→3s→6s）+ 复用 abort signal | gp-4（主理人复核） |
| 8 | 🟡 | 并发闸门 | `app/core/compute_guard.py:11`；`config.py:181` `COMPUTE_CONCURRENCY=2 (le=2)` | `compute_slot` 只被 backtest/desk/export/portfolio/research 使用；**datacenter/market/ops 的重扫未纳入** ⇒ 多页并发重扫无统一上限 | 给 parquet 重扫/聚合统一加闸门；把上限从「写死 2」改为可配 | gp-3（主理人复核） |
| 9 | 🟡 | 线程配置 | `app/__init__.py:34` `min(cpu-2, 12)` = 12；ThreadPoolExecutor 默认 `min(32, cpu+4)` = 22 | 22 个池线程 × 每个 polars 12 线程 ⇒ 峰值 264 线程，嵌套并行过载 | 下调 `POLARS_MAX_THREADS`（如 4~6）并显式限定池大小；或给重扫任务用专用小池 | gp-3 |
| 10 | 🟡 | 序列化 | `app/main.py:210` `app = FastAPI(...)` 无 `default_response_class` | `orjson` 已装但**未接线**，所有 JSON 走 stdlib 慢序列化 | 加 `default_response_class=ORJSONResponse` | gp-3（主理人复核） |
| 11 | 🟡 | SQLite | 全仓 `sqlite3.connect` 共 30+ 处，仅 **7 处**带 `timeout=` | 其余走默认 5s；WAL 下并发写易 `database is locked` | 统一封装 `_sqlite_ro/_sqlite_rw`，统一 `timeout=30` + `PRAGMA busy_timeout` | gp-3（主理人实测） |
| 12 | 🟡 | SQLite 索引 | `app/db/models.py` | 多张表缺少高频查询列索引（如 paper_fill.order_id 已有、但部分时间序列列无） | 按慢查询补索引；上线前跑一次 `ANALYZE` | gp-3 |
| 13 | 🟡 | 中间件 | `app/main.py:268` `@app.middleware("http")` | 仍用 Starlette `BaseHTTPMiddleware`（项目已声明禁用），每请求多一层 anyio 任务 | 改纯 ASGI 中间件 | gp-3（主理人复核） |
| 14 | 🟡 | 外部源串行 | `app/data/ingest/akshare_adapter.py:73` | `_throttle()` **在 `with _throttle_lock:` 内 `time.sleep()`** ⇒ 全局串行（`market.py:563` 的 3 路并发被吃掉） | 改为「锁内计算等待时长 → 锁外 sleep → 锁内更新 `_last_call`」 | gp-5（主理人复核） |
| 15 | 🟡 | 文档失真 | `app/data/ingest/akshare_adapter.py:10` | docstring 声称「批量并发：ThreadPoolExecutor（max_workers 默认 4）」，但 `grep ThreadPoolExecutor app/data/ingest/` **零实现** | 要么补并发实现，要么改文档如实描述（当前是同步） | gp-5（主理人复核） |
| 16 | 🟡 | 外部源串行 | `app/api/v1/market.py:46-48`（`_build_indices` 循环 `CORE_INDICES`） | 5 个指数**逐个串行**抓取，每个受 1.2~1.5s 限速 ⇒ 仅指数就 6~7.5s | 指数级并发（受限速约束下最多 2~3 路），或预取缓存 | gp-5（主理人复核） |
| 17 | 🟡 | 同步吞吐 | `app/services/sync_service.py:280-322` | 增量同步**逐标的串行** `fetch_and_write_daily_bars` | 小并发（2~3）+ 现有熔断/限速；注意 `_sync` 状态需加锁 | gp-5（主理人复核） |
| 18 | 🟡 | 写放大 | `app/data/parquet_store.py:584` `write_partition` | 每次增量写**读整年分区 → concat → 去重 → 原子重写整年文件**（在 `_rw_lock` 内） | 改「按月/季分片」或「追加 + 定期 compaction」，减少重写量 | gp-5（主理人复核） |
| 19 | 🟡 | 缓存去重 | `app/data/etf.py:60-74` `_cached` | 无 single-flight：TTL 过期后并发请求会**同时重算** | 复用 `stats_cache` 的单飞闸门 | gp-5 |
| 20 | 🟡 | ML | `app/ml/features.py:212-233` | groupby 特征计算单线程 | 视数据量评估并行化（收益视规模而定，谨慎） | gp-5 |
| 21 | 🟢 | 启动 | `app/main.py:177-191` | 4 个预热任务并发启动（overview/etf/lineage/datacenter），冷启动互相抢 IO | 改为**错峰/串行**预热，或统一进一个受闸门的预热队列 | gp-5（主理人复核） |
| 22 | 🟢 | 包体 | `frontend/src/lib/echarts.ts:18,29` 全量注册；`vite.config.ts:31` `chunkSizeWarningLimit:1500` | echarts 全量组件进包（实测 `echarts-*.js` = **695KB** raw）；无 `manualChunks`；警告阈值被抬高掩盖问题 | echarts 按需注册（只留用到的组件）；配 `manualChunks` 拆 vendor；阈值调回 500 | gp-4（主理人实测） |
| 23 | 🟢 | 渲染 | 全仓 `React.memo` = **0 处**；`AbortController` 仅 5 个文件 | 长列表/图表组件未 memo，父级刷新即全量重渲；多数页面请求不可中断 | 高频列表/图表组件加 `React.memo`；推广 `useAbortableTask` | gp-4（主理人实测） |
| 24 | 🟢 | 依赖清理 | `frontend/package.json` | `lightweight-charts` 声明但**源码零引用**（`grep` 为空）——审计中「~40KB」的说法经复核**不成立**，实为死依赖 | 移除未使用依赖 | 主理人复核（修正 gp-4） |

> 说明：#1 是**正确性**缺陷（不只是性能），优先级最高；#2 依赖 #1 先修好才能安全落地。

---

## 3. 优化路线图（按收益/成本排序）

### 阶段一：正确性 + 最大收益（建议本周）
1. **修 manifest**（#1）——累计 first/last、补齐 4 个缺失数据集、启动对账。**这是 #2 的前置条件。**
2. **打通 manifest 快路径**（#2）——`/datasets`、`/quality` 走 manifest（带过期回退）。**预期：数据中心冷载 62s → <5s。**
3. **清 async 阻塞**（#3/#4）——`health_ready`、alerts 评估链 `to_thread`。**预期：健康探针不再拖 loop。**
4. **前端扇出三连修**（#5/#6/#7）——拆依赖、去重、轮询加守卫。**预期：首屏请求数 -40%，切「展开全部」不再重发全量。**

### 阶段二：并发与 IO（下一迭代）
5. **统一重扫闸门 + 下调 polars 线程**（#8/#9）——预期峰值线程数降一个量级。
6. **限速锁改「锁外睡」+ 指数并发**（#14/#16）——预期指数块 6~7.5s → 2~3s。
7. **`write_partition` 改分片/追加**（#18）+ **同步小并发**（#17）——预期同步吞吐 ×3~5。

### 阶段三：收口与清洁（可选）
8. `orjson` 接线（#10）、SQLite 统一 timeout + 索引（#11/#12）、ASGI 中间件（#13）、echarts 按需 + `manualChunks`（#22）、`React.memo`（#23）、删死依赖（#24）、启动错峰（#21）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望 |
|---|------|--------|--------|------|
| 1 | 修复 `.manifest.json` 正确性（累计区间 + 补齐数据集 + 对账） | 数据层 | **P0** | 本迭代 |
| 2 | `/datasets`、`/quality` 复用 manifest 快路径（带过期回退） | 数据层 | **P0** | 本迭代 |
| 3 | `health_ready`、alerts 评估链改 `asyncio.to_thread` | 接口层 | **P1** | 本迭代 |
| 4 | 前端：`loadAll` 拆依赖 + `autoStatus` 去重 + 轮询守卫 | 前端 | **P1** | 本迭代 |
| 5 | 重扫统一闸门 + `POLARS_MAX_THREADS` 下调 + `orjson` 接线 | 接口层 | P1 | 下迭代 |
| 6 | 限速锁「锁外睡」+ 指数并发 + `write_partition` 分片 | 数据层 | P1 | 下迭代 |
| 7 | 前端包体：echarts 按需 + `manualChunks` + 删死依赖 | 前端 | P2 | 有空即做 |

---

## ⚠️ 待完善 / 已知局限

- **未做压测**：本报告为**静态审计 + 单点实测**，未做并发压测（如 10 并发下的端到端 P95）。落地后建议补一轮压测验证收益。
- **`data/` 规模**：实测 **39,944 个 parquet / 2.4GB**（`data/parquet/` 36,500）；另存在 `data/_purged_pre2022/`（3,417）、`data/_backup_*` 等归档目录——若生产部署规模不同，绝对耗时需重新测量。
- **磁盘水位**：磁盘已用 ~93%，`write_partition` 的整年重写在低水位下可能失败；优化前先确认磁盘空间。
- **收益为估算**：表中「预期收益」多为量级估计，非实测；#2 的 62s→<5s 基于 manifest 路径已在 `/overview` 上验证过的 <1s 表现推导。
- **未改动任何代码**：本次仅审计与建议，未提交任何实现改动。

---

## 📚 成员产出索引

- general-purpose-2（后端数据层/IO）：manifest 完整性实测、36,500 文件冷扫分析、`_scan_all_datasets` vs `_manifest_dataset_summary` 双实现对比
- general-purpose-3（接口/并发/中间件/存储）：15 条发现（`health_ready` 阻塞、`compute_slot` 覆盖缺口、orjson 缺失、SQLite timeout、BaseHTTPMiddleware）
- general-purpose-4（前端）：15 条发现（请求扇出、轮询、echarts 全量注册、`React.memo`=0、`manualChunks` 缺失）
- general-purpose-5（外部源/ML/流水线/启动）：15 条发现（限速锁持锁 sleep、文档失真、指数串行、整年重写、启动并发预热）

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
> 落盘路径：`deliverables/gstack/perf-audit-optimization-2026-09-29.md`
