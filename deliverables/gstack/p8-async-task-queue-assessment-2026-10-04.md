# P-8 评估：同步重活改任务队列（backtest / ops / datacenter / research / export）

> **性质：只读评估，未改动任何生产代码、未新增依赖、未变更 API 契约。**
> 日期：2026-10-04 · 范围：`POST /backtest/run`、`POST /backtest/strategy-run`、`POST /research/optimize`、`POST /research/stress-test`（及同族 `ops` / `datacenter` / `export`）

---

## 0. 结论摘要（TL;DR）

| 问题 | 结论 |
|---|---|
| **是否可执行？** | **可执行，但"一次性把 5 个模块全改"不可执行。** 基建已齐（`task_store` + `useTaskPolling` + 3 处可抄范式），缺的是**取消语义**与**闸门三处同改**两笔隐性成本。 |
| **当前该不该全做？** | **不该。** 建议先做零契约变更的 **P8-a**，再用 **1 个端点**试点（P8-b）量化真实成本，`/backtest/strategy-run` 与 `/export/*` 留到最后。 |
| **最高性价比动作** | 4 个端点把 `asyncio.to_thread` 换成 `run_in_executor(get_compute_pool())` —— **零契约、零前端改动**，直接消除已发生的"重活占默认池打挂就绪探针"事故路径。 |
| **最大隐性成本** | ① **真取消**需要给回测/寻优内核加协作式检查点（散落在 `backtest/` 与 `orchestrator`）；② 异步化会让 `_PATH_BUDGETS` 两条预算变孤儿 ⇒ 前端 `LONG_PATHS`、后端 `_PATH_BUDGETS`、闸门脚本**必须同改**。 |
| **一句话** | P-8 是**可行但当前不该全做**：它治的是"算力泄漏 / 不可观察 / 写者互斥"三条结构性问题，而不是"用户等得久"；且**收益不包含"寻优本身变快"**。 |

---

## 1. 现状实测

### 1.1 部署形态

- `backend/Dockerfile:59` → `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]` —— **单进程单 worker**。
- `docker-compose.yml` 服务：`redis` / `aqp-api` / `aqp-web` / `aqp-redis-data` —— **无独立 worker 容器**。
- ⇒ 进程级锁（`core/pipeline_lock.py`）与进程内单例（`datacenter._sync`）在**当前部署下成立**；两个模块的注释都明写「跨 worker 部署需配合 Redis 分布式锁，**尚未实现**」。

### 1.2 已有异步基建（比预期完整 —— 这是评估结论的关键）

| 能力 | 现状 | 位置 |
|---|---|---|
| **任务状态持久化** | ✅ SQLite 表 `background_tasks`（`task_id/task_type/status/progress_json/result_json/error_message/owner/lease_until/created_at/started_at/finished_at`） | `app/services/task_store.py` |
| **状态机** | ✅ `queued → running → (succeeded\|failed\|cancelled)`，含 `cancel_requested` 中间态 | 同上 `update_task()` |
| **原子领取 + 租约** | ✅ `claim_task(task_id, owner, lease_seconds=300)`（含租约过期抢占） | 同上 |
| **僵尸任务回收** | ✅ `reap_stale_running_tasks()`（**仅启动时**调用，docstring 论证了为何不能做运行期 reaper） | 同上 + `main.py:109` |
| **start/status/cancel 范式** | ✅ `POST /datacenter/sync` → `create_task`+`claim_task`+`Thread(daemon)` 立刻返回 `{started, task_id, total, resume, skipped}`；`GET /sync/status`（1.5s 轮询）、`GET /sync/tasks/{id}`（刷新后恢复）、`POST /sync/cancel` | `app/api/v1/datacenter.py:1225+` |
| 同上（第二个范式） | ✅ `POST /datacenter/train/start` + `/train/status` + `/train/cancel` | 同上 |
| 同上（第三个范式） | ✅ `POST /studio/mining/start` → `{task_id}` + `GET /studio/mining/status/{task_id}` + `POST /mining/cancel/{task_id}` | `app/api/v1/studio.py:67+`（`gp_miner` 自带注册表） |
| **前端通用轮询** | ✅ `useTaskPolling(url, intervalMs=2000)` —— 按 `status ∈ {queued, running, cancel_requested}` 或 `running===true` **自动启停**，空闲归零、失焦由 SWR 暂停 | `frontend/src/hooks/useTaskPolling.ts`（**通用件，非页面专用**） |
| 前端消费点 | ✅ 3 处：`DataCenter/index.tsx`（1.5s + 指数退避 1.5→3→6→12s + unmount abort）、`DataCenter/TrainPanel.tsx`（2s + 优雅取消）、`FactorStudio/index.tsx`（2s + localStorage 任务历史恢复轮询 + "任务已失效"文案） | — |
| **进程内调度器** | ✅ `main.py` lifespan 已在跑 `evening_routine_scheduler` / `startup_catchup` / `alert_scheduler` / `auto_sync_scheduler` + 多个预热任务 | `app/main.py:124+` |
| **写者互斥** | ✅ `TASKS = ("sync","fetch","pipeline","mirror","training")`，非阻塞申请 + 直接拒绝（`PipelineBusy`） | `app/core/pipeline_lock.py` |
| **并发闸门** | ✅ `COMPUTE_CONCURRENCY=6` 信号量 + `COMPUTE_ACQUIRE_TIMEOUT_SECONDS=10s` 有界等待；专用 6 槽线程池（隔离默认池 22 槽） | `app/core/compute_guard.py` / `compute_pool.py` |

> `task_store.py` 的模块 docstring 已明写：*「lease 字段为后续**多 worker 抢占**提供原子更新基础。当前同步 worker 仍是单机线程，**迁移到独立 worker 时只需复用本模块的状态更新接口**。」* ⇒ **P-8 要的"任务队列"，其状态层已经按队列设计好了。**

### 1.3 待改端点清单（含 P-8 未列出的同族）

| 端点 | 前端超时 | 后端预算 | 派发方式 | 已登记 |
|---|---|---|---|---|
| `POST /backtest/run` | 120s (`heavy`) | **240s（默认）** | `asyncio.to_thread`（**默认池**） | ❌ |
| `POST /backtest/strategy-run` | **600s** (`optimize`) | **660s** | `asyncio.to_thread`（**默认池**） | ✅ `_PATH_BUDGETS` |
| `POST /research/optimize` | 180s (`export`) | **240s（默认）** | `asyncio.to_thread`（**默认池**） | ❌ |
| `POST /research/stress-test` | 180s (`export`) | **240s（默认）** | `asyncio.to_thread`（**默认池**） | ❌ |
| `POST /ops/quality-scan` | 180s (`export`) | 240s（默认） | `to_thread` | ❌ |
| `POST /ops/dag/rerun` | 300s (`rerun`) | 330s | `run_in_executor(compute_pool)` ✅ **已改** | ✅ |
| `POST /export/backtest` / `strategy-backtest` | 180s | 240s | `to_thread` | ❌ |
| `GET /export/screener` | 180s | 240s | `to_thread` | ❌ |
| `POST /report/daily/generate` | — | 240s | `to_thread` | ❌ |
| `POST /desk/attribution` | 180s | 240s | `to_thread` | ❌ |
| `POST /monitor/run` | — | 240s | `to_thread` | ❌ |

**🔴 顺带发现（可直接归入 P8-a，低风险）**：`/backtest/run`、`/research/optimize`、`/research/stress-test` 三条**未登记 `_PATH_MAX_BUDGETS`** ⇒ 落回 240s 默认预算，而前端只等 120~180s ⇒ **用户早已断开，服务端仍白烧 60~120s 的 worker**。这与 `_PATH_MAX_BUDGETS` 已修复的 4 条路径（`/settings`、`/ops/lineage`、`/datacenter/datasets`、`/datacenter/mirror/status`）**是同一缺陷形态**，属于"漏登记"而非新问题。

### 1.4 风险证据（真实日志 / 实测，非推测）

| 证据 | 内容 |
|---|---|
| **默认池被打挂** | `compute_pool.py` 文件头：实测 **22 路** `GET /market/overview?refresh=1` 并发后，`/health/ready` **挂死 12008ms**（对照 `/health/live` 仍 200/10.4ms），容器被判 unhealthy；且因契约「HTTP 恒 200」，**22/22 全 200、零 5xx ⇒ 监控完全看不见**。 |
| **不可取消** | `asyncio.wait_for(coro, budget)` 到期**只取消外层 await**；`to_thread` 内 worker 阻塞在不可中断系统调用时**不会被取消**，继续占槽直到自己返回。 |
| **白烧算力（已 6 次）** | `_PATH_MAX_BUDGETS` 注释：实测 `[timeout-guard] …超时（>240s）：已取消处理并回写 HTTP 504` **6 次**，而前端只等 60~120s ⇒ 白烧 120~180s。 |
| **最坏 702.6s** | `datacenter.py:1956`：单轮预热实测最坏 **702.6s**（`backend-run-1003-r6.log`）⇒ 若占默认池会压住探针与轻量端点**数分钟**（`/api/v1/settings` 前端只等 60s）。 |
| **静默覆盖** | `pipeline_lock.py` 设计动机：`autoSync(15:45)` / 晚间 pipeline(17:30) / 手动 mirror rebuild / training **都写** `daily_bar_qfq` / `cs/` 镜像 / `features`，此前各用各的锁 ⇒ 撞车时后写者**静默覆盖**先写者，产出"看起来正常但错误"的数据。 |
| **永不自愈的僵尸任务** | `reap_stale_running_tasks` docstring：生产库实测一行 `status=running`、`lease_until` 过期 **5 天**、`finished_at` NULL，**经 API 暴露给前端且永不自愈**。 |

---

## 2. 可执行性分析

### 2.1 P-8 的**增量**不是"引入任务队列"，而是四件事

1. **响应契约**：`结果` → `受理回执 {task_id, status} + 配套 status/cancel 端点`（4 个端点 × 3 个路由 = 12 条新路由）。
2. **执行体迁移**：从"请求线程的 `to_thread`"改成"后台线程 / worker 从队列取"。
3. **前端改造**：6 个页面（`Backtest/index`、`SignalAnalysisPanel`、`TopKPanel`、`DataQuality`、`Pipeline`、`Research`）从"await 结果"改成"提交 → 轮询 → 渲染"。
4. **取消真正生效**（见 2.2 第 1 条，最贵）。

### 2.2 真正难点（按代价排序）

| # | 难点 | 说明 | 代价 |
|---|---|---|---|
| 1 | **取消语义** | 现在**没有任何机制**能中断 `k_backtest` / 寻优网格 / `run_pipeline` 的中途。真取消必须给计算内核加**协作式检查点**，散落在 `backtest/engine.py`、`backtest/strategy_base.py`、`orchestrator.run_pipeline`、research 的协方差/情景循环。`datacenter` sync 之所以能取消，是因为它是**逐标的循环**（天然检查点）；**回测/寻优不是**。<br>⚠️ **不做真取消就只能"标记取消 + 结果丢弃"，worker 照烧** —— 这会造出"看起来能取消但实际不停"的 UI，是**判据 10（假交互）的同族**，**比现在更糟**。 | 🔴 高 |
| 2 | **闸门三处同改** | 异步化后 POST 只花 <100ms ⇒ `_PATH_BUDGETS` 里 `strategy-run(660s)` / `dag/rerun(330s)` 变**孤儿预算** ⇒ `scripts/check-timeout-consistency.mjs` 双向校验**直接 FAIL**。必须同改：前端 `LONG_PATHS`/`CAPPED_PATHS`、后端 `_PATH_BUDGETS`/`_PATH_MAX_BUDGETS`、闸门脚本期望集。 | 🟠 中（固定成本，漏一侧即红） |
| 3 | **幂等 / 去重** | 现在靠 `Redis 10min 缓存 + 相同 req 同 key`（`_run_cache_key`）。异步化后"同参数重复提交"要么**复用进行中的 task**（需按参数指纹查在飞任务），要么产生 N 个并行同质任务**吃光 6 个 slot** ⇒ **新引入的失效模式**。 | 🟠 中 |
| 4 | **单 worker 前提被显式化** | `--workers 1` + 进程锁 + 进程内单例。队列化会把这个**隐含前提变成显式**（好事），但**"改队列" ≠ "能横向扩展"** —— 分布式锁仍缺。 | 🟡 低（但需明确声明） |
| 5 | **失败/取消后的中间产物一致性** | `cs/` 镜像、`features`、`daily_bar_qfq` 的半成品状态**没有守卫**；`reap_stale_running_tasks` 只在启动时无条件回收（不能做运行期 reaper，docstring 有论证）。 | 🟡 低 |

### 2.3 影响面（已量化）

- **后端测试：12 个文件**直接打这些端点 —— `test_api.py`、`test_backtest_price_basis_disclosure.py`、`test_error_code_governance.py`、`test_frontend_b9_fixes.py`、`test_ops_quality_scan_year.py`、`test_ops_silent_success_fixes.py`、`test_portfolio_constraints.py`、`test_read_endpoints_rbac.py`、`test_rejected_orders_observable.py`、`test_route_permission_contract.py`、`test_timeout_guard.py`、`test_write_endpoints_smoke.py`。
  ⇒ 异步化后这些用例要从「直接 `await` 拿结果」改成「提交 → 轮询 → 断言」。**本仓测试基线 2137 passed**，改完必须重定基线。
- **前端：11 个文件**（`api/backtest.ts`、`api/export.ts`、`api/production.ts`、`api/research.ts`、`api/screener.ts`、`api/strategyBacktest.ts`、`api/timeouts.ts` + `timeouts.test.ts`、`pages/Backtest/TopKPanel.tsx`、`pages/Pipeline/index.tsx`、`pages/Screener/index.tsx`），**6 个消费页面**。

---

## 3. 收益 vs 风险

### 收益（可量化 / 已发生）

| 收益 | 量化依据 |
|---|---|
| 消除"客户端断开后白烧" | 已有 **6 次**实测 504 记录（120~180s 白烧） |
| 6 个 compute slot 不再被单次寻优独占 600s | `COMPUTE_CONCURRENCY=6` |
| 长任务跨重启可恢复 | 本仓后端**频繁被应用重启杀掉**（已知事实，见 `REFERENCE.md` 沙箱守卫节） |
| 写者互斥有强制登记点 | 杜绝 `pipeline_lock` 覆盖不到的静默覆盖 |
| 用户可见进度 | `progress_json` 字段**已存在**，无需改表 |

### 风险

| 风险 | 说明 |
|---|---|
| **闸门耦合** | 3 张表 + 1 个脚本必须同改 ⇒ "改对了也要重配"的固定成本 |
| **假取消** | 只做标记取消会造出欺骗性 UI（判据 10 同族） |
| **新增并发面** | 同质任务去重、队列积压上限、`background_tasks` **无 TTL / 无清理**（会持续增长） |
| **测试面迁移** | 12 个后端测试文件 + 前端 11 个文件；测试基线需重定 |
| **⚠️ 收益边界** | **不覆盖"单次寻优本身要 600s"** —— 只是把它从"阻塞"变成"可观察"，用户**仍然要等 600s** |

---

## 4. 建议：切成 3 段，第一段不做契约变更

### P8-a｜零契约变更（低风险，**建议立即做**）

1. 4 个端点的 `asyncio.to_thread` → `run_in_executor(get_compute_pool())`，与 `/ops/dag/rerun`、`/datacenter/*` **对齐**。
   ⇒ **零契约、零前端改动**，直接消除"重活占默认池打挂 `/health/ready`"这条**已发生**的事故路径。
2. 给 `/backtest/run`、`/research/optimize`、`/research/stress-test` 补 `_PATH_MAX_BUDGETS` + 前端 `CAPPED_PATHS`（见 §1.3 的红字发现）。
3. 闸门登记同步（这是 P8-a 唯一"要动三处"的部分，但**只是加登记**，不改语义）。

> 前置性：**队列化之前必须先修"占错池"**，否则将来队列 worker 也占错池。

### P8-b｜单端点试点（中风险，**需产品决策**）

- **建议试点 `/research/optimize`**：前端超时最短（180s）、无文件流、参数结构简单（`OptimizeRequest` 5 个字段）、失败可重试、前端**已有** `ComputeQueue` 排队语义。
- 把 `useTaskPolling` + `task_store` 接到它上面，跑通「提交 → 轮询 → 取消 → 刷新后恢复」全链路。
- **目的不是收益，是量化**：把"闸门三处同改"的固定成本、测试迁移成本、真取消的实现量**测出来**，再决定 P8-c。

### P8-c｜高风险（最后做）

- `/backtest/strategy-run`（600s + 寻优网格，需要真取消检查点）
- `/export/backtest`、`/export/strategy-backtest`、`/export/screener`（**文件流**，需改成"异步生成 + 按 task_id 下载"，**契约变更最大**）
- `/report/daily/generate`、`/desk/attribution`、`/monitor/run`（同族，收益更低）

### 不建议做

- **`/ops/dag/rerun` 与 `/datacenter/*` 不要重写** —— 它们**已经是**异步 / 专用池范式，是**参照系，不是改造对象**。

---

## 5. 触发条件：什么时候从"可选"变"必须"

只有出现下列**之一**，P8-b/c 才必须做：

1. 后端部署从 `--workers 1` 走向**多 worker / 多容器**（此时进程内单例与进程锁**双双失效**）。
2. 出现「用户提交寻优 → 服务重启 → **任务蒸发**」的**真实投诉**。
3. 单进程 6 个 compute slot 被长任务**长期占满** —— 可用 `compute_guard` 的 `ERR_RATE_LIMITED` 频次做度量（**当前无此度量**，见 §6）。

---

## 6. 本次未验证 / 未量化（诚实披露）

| 项 | 说明 |
|---|---|
| 各端点**真实 P50/P99 耗时** | 只找到注释里的零散实测（`/ops/lineage` 32~38s、`mirror/status` 冷 >120s、`datasets` 74s/`quality` 86s、预热最坏 702.6s）；**`/backtest/run`、`/research/optimize`、`/research/stress-test` 无实测值** ⇒ 是否需要异步化的**紧迫性无法定量**。建议先补一次打点。 |
| `ERR_RATE_LIMITED` 触发频次 | 无度量 ⇒ 无法判断 slot 是否真的长期占满。 |
| `background_tasks` 表当前行数与增长速率 | 未查（`data/sqlite/aqp.db`）。 |
| 真取消的实现量 | 未逐个读 `backtest/engine.py` / `strategy_base.py` / `orchestrator.run_pipeline` 的循环结构 ⇒ **P8-c 的工期无法给数**。 |
| 12 个后端测试的具体断言形态 | 只统计了"命中路径的文件数"，未逐个确认哪些是"直接 await 拿结果"（真正需要改的）。 |

---

## 7. ⚠️ 证据基线声明（必读）

本报告的代码事实**读自工作区（working tree），而非 `HEAD`**。评估期间检测到工作区存在一批**本报告作者未做的未提交改动**：

```
18 files changed, 803 insertions(+), 134 deletions(-)
backend/app/api/v1/{alerts,datacenter,desk,etf,ops,report,research}.py
backend/app/cache/swr.py
backend/app/core/{compute_guard,compute_pool,config,timeout_guard}.py
backend/app/main.py
backend/app/ml/registry.py
backend/tests/test_model_registry.py
frontend/scripts/check-timeout-consistency.mjs
frontend/src/api/{market,timeouts}.ts
新增：backend/tests/{_sandbox_pytest_runner,test_readiness_pool_isolation,
      test_swr_single_flight_budget,test_timeout_budget_ceiling,
      test_warmer_schedule_and_pool}.py
```

该批次主题为 **P-3 / P-7 / T-1 / T-2「上线前全检」**（计算池与信号量同源、超时预算收紧、预热调度与池隔离、SWR 单飞预算）。

**对结论的影响：无。** 已逐项复核，§1.3 的四条关键事实在当前工作区**仍然成立**：

- `backend/app/api/v1/backtest.py:491/939/997` 与 `research.py:185/205/226/277/313/386/468/524/591` **仍为 `asyncio.to_thread`（默认池）**；
- `_PATH_BUDGETS` 仍只有 **2** 条（`strategy-run` 660s、`dag/rerun` 330s）；
- `_PATH_MAX_BUDGETS` 仍只有 **4** 条（`settings` / `ops/lineage` / `datacenter/datasets` / `datacenter/mirror/status`）；
- `Dockerfile` 仍为 `--workers 1`。

**但请注意两点**：

1. 报告中提到的 `CAPPED_PATHS` / `_PATH_MAX_BUDGETS` / `TIMEOUT.quote` 属于**上述未提交改动**，不在 `HEAD` 里 —— 引用时须区分"已提交"与"工作区"。
2. `compute_guard` 与 `compute_pool` 的并发度已在上批改动中**同源为 `COMPUTE_CONCURRENCY`（默认 6）**；而 `frontend/src/pages/Research/index.tsx` 的 `ComputeQueue` 注释仍写「研究接口的后端计算槽只有两个」⇒ **前端串行度 2 与后端槽 6 已不一致**（前端更保守，非缺陷，但注释已过期）。

---

## 附：本报告的证据来源

- 代码：`backend/Dockerfile`、`backend/app/main.py`、`backend/app/core/{timeout_guard,compute_guard,compute_pool,pipeline_lock,config}.py`、`backend/app/services/{task_store,sync_service}.py`、`backend/app/api/v1/{backtest,research,ops,datacenter,export,studio,report,desk,monitor}.py`、`frontend/src/{api/timeouts.ts,hooks/useTaskPolling.ts,pages/Research/index.tsx,pages/DataCenter/index.tsx}`
- 日志引用（在代码注释中）：`backend-run-1003.log`、`backend-run-1003-r6.log`、`backend-run.log`
- 全部为**只读**操作；**未修改任何文件**。
