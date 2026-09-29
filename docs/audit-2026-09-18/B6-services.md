# B6 批次审核报告 · 编排 / 服务 / 定时任务

> 审核员：B6（编排·服务·定时任务）  日期：2026-09-18（会话时钟 2026-09-21）
> 纪律来源：`docs/audit-2026-09-18/AUDIT-BRIEF.md`；契约来源：`docs/chatgpt-full-review-prompt.md` §1/§3-B6
> 所有「确定」条目均已用可复现命令实测；「疑似」条目已注明所需信息。
> 未修改任何源码；所有探针脚本落在 `backend/.tmp_testrun/`（合成数据，未触碰 `data/`）。

## 0. 运行基线（本次实测）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_resume_failed_semantics.py -q -p audit_mkdtemp_fix -p no:cacheprovider`（workdir=`backend/`） | **10 passed in 1.52s** |
| 探针 S1 `b6_probe_s1.py`（data_jobs 状态机 + task_store 租约/回收） | 见 §2 |
| 探针 S2 `b6_probe_s2.py`（续传状态 / stats_cache / market_service） | 见 §3-§5 |
| 探针 S3 `b6_probe_s3.py`（晚间例行语义） | 见 §6 |
| 探针 S4 `b6_probe_s4.py`（`run_pipeline` 端到端 + 事件循环 lifecycle） | 见 §7 |
| 探针 S5 `b6_probe_s5.py`（死代码 / 写入点接线） | 见 §9-§10 |
| 探针 S6 `b6_probe_s6.py`（`/sync/fetch` 互斥 + `step_validate` 盲区） | 见 §5.2 / §12-C2 |
| 探针 S7/S8 `b6_probe_s7.py` / `b6_probe_s8.py`（`/sync/fetch` TOCTOU 确定性复现） | 见 §5.2 |

复现方式（统一环境）：

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH="$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun\b6probe\tmp"; $env:TMP=$env:TEMP
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\b6_probe_s2.py"
```

---

## 1. 逐文件结论行

| 文件 | 结论 |
|---|---|
| `app/orchestrator.py` | **发现 P1×3**：失败任务重跑时 `finished_at`/`duration_ms` 残留（B6-07）；`data_jobs` RUNNING 行无回收（B6-06）；线程事件循环被 `set_event_loop(None)` + 步骤内 `asyncio.run` 清零（B6-05）。状态机本身合法：无 PENDING→SUCCESS 跃迁、SUCCESS 幂等跳过已实测为真。 |
| `app/services/task_store.py` | 状态值/迁移合法（`queued→running→succeeded/failed/cancelled`，`cancel_requested` 为中间态且不可被 re-claim）。但租约机制（`lease_until` / `lease_seconds` / `claim_task` 过期分支）**是死代码**（B6-09），且与 `reap_stale_running_tasks` 的无条件回收策略互相矛盾。 |
| `app/services/sync_service.py` | **发现 P1×1**：`_start_sync_bg` 无条件清空 `restore_sync_state` 刚恢复的断点续传集合（B6-01），P2-14 落盘的续传数据实际永不生效。另：`_sync.failed` 只增不清（B6-11）；`_persist_sync_state` 每 30s 全量重写集合（B6-13）。 |
| `app/services/stats_cache.py` | `cached()` 文档声称「并发下只算一次」，实测 6 并发 = 6 次计算（B6-08，确定）。TTL 与失效路径本身正确（`invalidate_stats_cache` 保留 `logs` 键是设计）。 |
| `app/services/market_service.py` | **发现 P1×1**：仅返回 `ok`/`unavailable`，三个子块只成功一个时仍报 `status="ok"`（B6-03），消费方 `market.py:550-553/579-583` 的 `degraded` 判据对它永久失效。 |
| `app/jobs/evening_routine.py` | **发现 P1×1**：整条流水线 FAILED 时仍落 `status="done"`，当日不再重试（B6-02）。`EVENING_ROUTINE_ENABLED=0` 无残留任务；非交易日 `skipped` 正确；panic 兜底与上报（SSE）已接好。 |
| `app/core/pipeline_lock.py` | 锁本身正确，但 `current_owner()` 零调用方（B6-10）。多 worker 风险按要求只记录不报（见 §11）。 |
| `app/core/compute_guard.py` | 未发现 P0–P2 问题。`asyncio.to_thread` 包装阻塞 `acquire` 正确，超时抛 `ERR_RATE_LIMITED` 有界。 |
| `app/api/v1/ops.py`（`/dag/rerun`） | `_run` 吞异常返回 `ok({"ok":False})` 为 B7b 已实测条目，不重复报；此处仅补充**新证据**：PipelineBusy 时 `run_pipeline` 在毫秒级抛错，用户看到的是「重跑成功」+ 一个字符串 summary（§5.1）。 |
| `app/api/v1/datacenter.py`（`/sync*`、`/train*` 调用面） | **发现 P1×1**：`/sync/fetch` 预检（取锁后立即释放）与 worker 二次取锁之间存在 TOCTOU，窗口内锁被抢即整任务落空，而接口已回 `started:true`、不写 `data_jobs`、`_sync.error` 又在下次请求被清零（B6-04，S8 已确定性复现）。`/train/readiness` 无鉴权为父审核员已确认条目，不重复。 |

---

## 2. 状态机实测（探针 S1）

`data_jobs` 合法状态集 = `PENDING`(列默认) / `RUNNING` / `SUCCESS` / `FAILED`。实测的合法流转：

```
(insert)  -> PENDING        # _create_or_get_job_async L58 建行，同事务内立即改 RUNNING
PENDING   -> RUNNING        # L61-63（实测：status='RUNNING', started_at 已写, error_message=None）
RUNNING   -> SUCCESS|FAILED # _finish_job_async L75-78，后者写 finished_at
SUCCESS   -> (拒绝)          # L55-56 直接返回 should_run=False   ← 实测
FAILED    -> RUNNING        # 允许原地重试（文档声明的语义）
```

未发现「PENDING→SUCCESS 跳过 RUNNING」：`PENDING` 只在同一事务里存活一瞬，且**没有任何代码在 PENDING 态写终态**。
未发现「失败被覆盖成成功」：`_finish_job_async` 只由 `_run_pipeline_impl` 在真实结果后调用一次。
`_create_or_get_job_async` 是 async 且被 `loop.run_until_complete` 驱动，`pipeline_slot` 在整个 `_run_pipeline_impl` 期间持有 ⇒ 同进程内 check-then-act 不存在竞态。

真实探针输出（S1）：

```
=== T1: first claim ===            -> id=1 should_run=True status='RUNNING'
=== T2: finish FAILED, re-claim ===-> should_run=True status='RUNNING' error_message=None
   data_jobs: {'status': 'RUNNING', 'started_at': '...43.98', 'finished_at': '...43.95', 'duration_ms': 1234}
=== T3: finish SUCCESS, re-claim ===-> should_run=False status='SUCCESS' current_step='build_cs_mirror'
=== T4: ORM-insert without status ===-> status='PENDING'（列默认，唯一产生 PENDING 的路径）
=== T5: crash while RUNNING ===    -> data_jobs rows still RUNNING after reap: 1
```

T2 那一行就是 B6-07 的直接证据：重试时 `finished_at` 仍是**上一次失败**的时间戳、`duration_ms` 仍是旧值，而状态已是 `RUNNING`。

---

## 3. B6-01（P1）断点续传恢复被 autoSync 抹掉

**位置**：`app/services/sync_service.py:642-658`（`_start_sync_bg` 内 `_sync.completed.clear()`，L657）、`app/services/sync_service.py:143-160`（`restore_sync_state`）、`app/main.py:57`、`app/services/sync_service.py:614-639`（调度器）。

**机制**：

1. `main.lifespan` 启动时 `restore_sync_state()` 把 SQLite `app_state.sync_state` 里未完成的 `completed` 列表恢复到 `_sync.completed`（日志会打印 "restored sync resume state: N completed"）。
2. 当日首次 autoSync tick（默认 15:45 之后，`resume=False`）调用 `_start_sync_bg("incremental", resume=False)`，该函数**无条件** `_sync.completed.clear()`，与 `resume` 形参无关。
3. 此后用户点「断点续传」（`POST /sync {resume:true}`）时，`datacenter.trigger_sync:698-700` 看到 `_sync.completed` 已空 ⇒ 全量重抓。

只有「重启后、autoSync 首次触发前」这一个小窗口内点 resume 才有效，因此 P2-14 落盘的续传数据在正常运行时**永不生效**。

**实测（S2）**：

```
persisted sync_state  : {'mode': 'incremental', 'completed': ['000001.SZ', '600519.SH', '300750.SZ'], 'finished': False}
after restore_sync_state -> _sync.completed = ['000001.SZ', '300750.SZ', '600519.SH']   mode = incremental
trigger_sync(resume=True) keeps completed -> ['000001.SZ', '300750.SZ', '600519.SH']   ← API 路径正确
before _start_sync_bg  -> _sync.completed = ['000001.SZ', '300750.SZ', '600519.SH']
after  _start_sync_bg  -> _sync.completed = []                                        ← autoSync 路径抹掉
```

**最小验证**：`backend/.tmp_testrun/b6_probe_s2.py` §F1（27 行内即可复现，无需网络：`kv_set("sync_state", ...)` → `restore_sync_state()` → `sync_service._start_sync_bg("incremental", resume=False)`，断言 `_sync.completed == set()`）。
**建议修法**：`_start_sync_bg` 内改为 `if not resume: _sync.completed.clear()`；或让 autoSync 走 `resume=True` 语义（它本就该续传，而不是重头再来）。注意 `_scan` 无副作用，改这一行即可。

**影响量级**：一次中断的增量同步（全市场 2499 只 × 2 口径，`AKSHARE_RATE_LIMIT` 1.2s 全局串行）剩余部分被重抓，量级为**分钟到小时级**的网络重跑；续传特性的对外承诺（`SyncRequest.resume` 文档串「跳过 `_sync.completed` 中上次已完成的 symbol」）不成立。

---

## 4. B6-02（P1）晚间例行把整条流水线 FAILED 记成 done 且当日不再重试

**位置**：`app/jobs/evening_routine.py:75-87`（pipeline try/except）、`L110`（无条件 `_mark("done", detail)`）、`L37-39`（`_already_done_today` 只认 `done`/`skipped`）。

**机制**：`run_pipeline` 返回 `(job, executed)`，`job.status == "FAILED"` 时只做两件事——写 `detail["pipeline"]="FAILED/<step>"`、`events.publish_threadsafe`。随后函数**必然**执行 `_mark("done", detail)`，`_already_done_today()` 从此返回 True，调度器在当日剩余时间里不再尝试。`_run_routine` 全文只写 `"done"`/`"skipped"`，**不存在 `"failed"` 终态**，因此任何一个消费 `app_state.evening_routine_last` 的地方都无法区分「今晚成功」与「今晚完全没跑成」。

**实测（S3）**：

```
app_state.evening_routine_last = {'day': '2026-09-21', 'status': 'done', 'at': '...',
  'detail': {'pipeline': 'FAILED/validate', 'monitor': 'state=degraded', 'report': 'date=2026-09-18'}}
run_pipeline called: 1 x
_already_done_today() -> True
[F6] statuses written by _run_routine: ['done', 'skipped']
```

触发条件现实性：晚间 17:30 与 autoSync（默认 15:45，但若用户改到 17:45+ 或同步跑很久）撞车 ⇒ `run_pipeline` 抛 `PipelineBusy` ⇒ 流水线**毫秒级**失败 ⇒ 当晚 features/infer/榜单全部停更，而每日报告照常产出（`report` 在 pipeline 之后独立执行，`L100-108`），看板上只有一条转瞬即逝的 SSE。

**最小验证**：`backend/.tmp_testrun/b6_probe_s3.py`（patch `orch.run_pipeline` 返回 `status="FAILED"` 的对象 → `_run_routine()` → 断言 `kv_get("evening_routine_last")["status"] == "done"`）。
**建议修法**：`job.status == "FAILED"` 时 `_mark("failed", detail)`，并让 `_already_done_today()` 对 `failed` 返回 False（保留同一交易日内有限次重试）；`_run_routine` 增加 `failed` 终态。

---

## 5. 互斥覆盖与调用面

### 5.1 锁持有范围（现状）

`pipeline_slot` 持有者：`run_pipeline`(`orchestrator.py:464`，覆盖整条流水线)、`_sync_worker`(`sync_service.py:399`)、`/sync/fetch` worker(`datacenter.py:887`)、`/mirror/rebuild`(`datacenter.py:1103`)、训练 worker(`ml/train_service.py:383`)。

**覆盖是完整的**：`orchestrator.step_*` 均不自行取锁（`grep` 确认全部 8 个 step 函数体内无 `pipeline_slot`），但它们只被 `_run_pipeline_impl`（已持锁）调用；S5 列出的所有 `write_partition` / `atomic_write_parquet` 写入点也都在上述某个锁内（`app/data/ingest/tasks.py:127` ← `step_update_daily` / `_sync_worker` / `/sync/fetch`；`app/data/universe.py:87,257,464` ← `step_build_universe` / 回测构建；`app/data/cross_section.py:131` ← `step_build_cs_mirror` / `/mirror/rebuild`；`app/ml/infer.py:107` ← `step_infer`）。

**未覆盖的两个缺口**（都不构成当前数据污染，记录备查）：
- `/text/import`、`/text/build-factor`（`datacenter.py:1038-1061`）直接 `to_thread` 调 `import_documents`/`score_and_build_factor`，不取任何锁。它们写的是 text/sentiment 数据集，与行情/特征数据集不重叠 ⇒ 目前无害；若将来 `build_features` 读情绪因子，会产生「后写者覆盖先写者」。
- `run_pipeline` 的 `pipeline_slot("pipeline")` 被 `/dag/rerun` 经 `asyncio.to_thread` 调用，整条流水线期间独占；由于是**非阻塞拒绝**，此时任何 autoSync/晚间例行都会直接失败（见 B6-02 的触发链）。

### 5.2 B6-04（P1）`/sync/fetch` 的 TOCTOU：接口回 `started:true`，实际一步没跑且不留案底

**位置**：`app/api/v1/datacenter.py:936-942`（预检 `with pipeline_slot("fetch"): pass`）、`L878-887`（`_run_fetch` 内**重新**取锁）、`L974-987`（`_worker`）、`L993-994`（返回 `started:true`）、`L955`（`_sync.error = None` 复位点）。

**机制**：预检在**请求协程**里取锁并**立即释放**（`L939-940` 的 `with` 只包 `pass`），worker 线程随后才**重新**取锁（`L887`）。两者之间存在真实窗口：另一任务（mirror / pipeline / 另一个 sync）在此刻拿到锁，`_run_fetch` 的 `with pipeline_slot("fetch")` 立即抛 `PipelineBusy` ⇒ `_worker`（`L977-980`）把它记进 `_sync.error` 并记一条日志，`finally`（`L981-987`）复位 `running`。但**接口早已返回 `started:true`**，且：

- `/sync/fetch` 路径**从不**写 `data_jobs`（`_record_sync_job` 的调用点只有 `sync_service.py:467/473/478/485`，`grep` 确认；`datacenter.py:62` 只是 import 重导出）；
- 那条 `_sync.error` 会在**下一次** `trigger_fetch` 开头被 `_sync.error = None`（L955）清掉 ⇒ 事后审计窗口极短；
- `_sync.done/total = 0/1`，`/sync/status` 上表现为「已结束、0 进度」，与 `_sync_worker` 的「空跑 ⇒ 记 FAILED」真实性断言（`sync_service.py:448-483`）形成双标：**同一个状态机，一条入口会如实记 FAILED，另一条入口什么都不记**。

**实测（S8，确定性复现）**：把 `datacenter.threading.Thread` 换成拦截子类，让预检通过后**先**让竞争任务拿到 pipe，再放行 worker：

```
[S8-a] busy at pre-check   -> code=40900 message=管道任务 [pipeline] 正在执行，已拒绝并发进入
[S8-b] free at pre-check   -> code=0 data={'started': True, 'asset_type': 'stock', 'total': 1, ...}
       competing lock holder acquired the pipe inside the window
       API told the caller: started=True, total=1
       final _sync.running = False   error = 'PipelineBusy: 管道任务 [pipeline] 正在执行，已拒绝并发进入'
       final _sync.done/total = 0/1
       data_jobs rows written = 0
       logs = ["抓取任务终止: PipelineBusy(...)"]
```

（S8-a 同时证明**预检本身是对的**：真正在忙时会如实返回 `40900`。缺陷只在「预检通过后锁被抢走」的窗口。另注：`_run_fetch` 单测会直接抛 `PipelineBusy`——见 `b6_probe_s6.py` §F11，因为它是 `_worker` 的 `except Exception` 兜住的，不是自己吞的。）

**最小验证**：`b6_probe_s8.py`（无需网络，直接调 `trigger_fetch` 协程 + 拦截线程）。
**建议修法**：让 `trigger_fetch` 在**同一次持锁**内把锁交给 worker（`threading.Event` 交棒 / 在 worker 内 `acquire` 成功后再回执），或至少让 `_worker` 的 `PipelineBusy` 分支写 `data_jobs` FAILED + 推 SSE，别让「什么都没跑」长成成功态。

### 5.3 `/dag/rerun` 的失败语义（已知条目的新证据）

`ops.py:448-455` 的 `ok({"ok": False})` 父审核员已实测。这里补充一点：`summary` 是 `str((job, executed))`，即一段 Python tuple 的 `repr`，客户端拿不到 `job.status`；而 PipelineBusy 会让 `run_pipeline` 在**取锁瞬间**（毫秒）抛错，于是「重跑」返回 code=0、`ok:false`、`error="PipelineBusy: ..."`——三次点击得到三个不同的真相（第一次成功、第二三次全部瞬时失败），用户界面上没有任何区分。同批的 `RerunRequest.codes`（`max_length=20`）意味着手动重跑最多 20 只代码，而 `data_jobs` 只有一行 `current_step`，**无法分辨这次 SUCCESS 是覆盖了全市场还是只跑了 3 只**（见 §8 与 §12）。

---

## 6. B6-03（P1）`build_money_flow` 部分成功仍报 `ok`，`degraded` 判据永久失效

**位置**：`app/services/market_service.py:59-62`（只有 `unavailable` / `ok` 两个出口）；消费方 `app/api/v1/market.py:550-553`（`_build_rt` 的 `degraded` 判定）、`market.py:579-583`、`market.py:597-598`；前端 `frontend/src/components/charts/MarketHeatmap.tsx:59`、`frontend/src/pages/MarketOverview/KpiCards.tsx:45-73`。

**机制**：三个子块（北向 / 大盘主力 / 行业结构）各自 try/except。只要**任意一个**成功（`north is not None or main is not None or sector_flows`），就返回 `status:"ok"`，且**不带 `reason`**。`_build_rt` 的 `degraded = any(block.status in ("degraded","unavailable"))` 因此永远看不到降级；`data_freshness` 写 `fresh`，界面按契约把不完整的资金数据当「新鲜/正常」呈现。契约（P0 背景块 §一.3）明确三态 `ok/degraded/unavailable`，本函数只实现两态。

**实测（S2）**：

```
north ok / main+sector down -> {'status': 'ok', 'north_net_today': 3.0, 'main_net_today': None, 'sector_flows': []}
north down / main ok        -> {'status': 'ok', 'north_net_today': None, 'main_net_today': 3.0}
reason field present? False
```

**最小验证**：`b6_probe_s2.py` §F3（桩掉 `app.services.market_service.get_akshare` / `_safe_call`，让 1~2 个子块抛错，断言 `out["status"]`）。
**建议修法**：`errs` 非空但仍有数据时返回 `{"status": "degraded", "reason": "; ".join(errs), ...}`；`sector` 块的异常当前连 `errs` 都没进（`market_service.py:56-57` 只 `logger.debug`），需一并补记。

---

## 7. B6-05（P2）事件循环被线程级清零 + 步骤内 `asyncio.run()`，引擎被反复重建

**位置**：`app/orchestrator.py:509-510`（`asyncio.new_event_loop()` + `asyncio.set_event_loop`）、`L562-564`（`finally: asyncio.set_event_loop(None); loop.close()`）；触发方：`app/data/universe.py:161`（`build_universe_history` 内 `asyncio.run(_load_instruments())`）、`app/data/universe.py:48/381`、`app/data/cross_section.py`（`build_mirror` 路径）。

**机制**：`_run_pipeline_impl` 把事件循环**设进当前线程**（`set_event_loop(loop)`），而 `build_universe_history` / `build_mirror` 在这个线程里调 `asyncio.run(...)`。实测（Python 3.11.15）：

```
set loop -> True
asyncio.run 后 loop.is_closed() = False          ← 本机 3.11 不关它
asyncio.run 后 asyncio.get_event_loop() -> RuntimeError: There is no current event loop in thread 'MainThread'
```

即 `asyncio.run()` 的 `Runner.close()` 把线程的 event loop 引用清成 `None`（并重置 event loop policy），`_run_pipeline_impl` 的 `finally` 又再清一次。`db/session.py:42-55` 的 loop 守卫（`_engine_loop is not loop` ⇒ 重建引擎）把所有后续异步 DB 操作救回来了，代价是**每次异步操作都重建一个引擎**：

```
[F8] engine constructions before run: 0
     run_pipeline -> status='SUCCESS' step='build_cs_mirror' executed=True
     engine constructions after run: 4
     current loop after run      : RuntimeError: There is no current event loop in thread 'MainThread'
     session._engine_loop after run: <ProactorEventLoop running=False closed=True debug=False>
[F9] 同日第二次 run_pipeline -> status='SUCCESS' executed=False   ← 幂等跳过，正确
```

**影响**：(a) 一次流水线至少 4 次引擎/连接池重建并留下 `closed=True` 的孤儿 loop；(b) `run_pipeline` 返回后**调用线程没有当前事件循环**——任何「我先 set 好 loop 再调 run_pipeline」的调用方（脚本 / 恢复工具 / 未来的测试）会在返回后被清掉自己的 loop，`asyncio.get_event_loop()` 变 `RuntimeError`；(c) `orchestrator.step_screener_dump:362-367` 正是靠 `get_event_loop()` 这个 API 工作，一旦上游顺序变化（例如某步先调了 `asyncio.run`），它会**新建并 set 一个 loop** 而引擎仍绑定旧 loop ⇒ 进入同一个重建循环。

**最小验证**：`b6_probe_s4.py`（用桩步骤模拟 `asyncio.run` 的调用模式，打印 `len(created)` 与运行后的 `get_event_loop()`）。本机 3.11 **不会崩**（引擎守卫兜住），因此定级 P2 而非 P1；但 `asyncio.Runner.close()` 在 3.12+ 的实现改动是**升级 Python 才可能引爆**的前向风险，本轮**无法验证**（沙箱无外网，且只有 3.11 解释器），标注「疑似，需在 3.12 上复跑 `b6_probe_s4.py`」。
**建议修法**：`run_pipeline` 用 `asyncio.Runner` / 模块级单例 loop，或把 `set_event_loop(None)` 换成「保存原 loop 并在 finally 还原」；步骤内改用 `asyncio.run_coroutine_threadsafe` 或直接复用 `_run_pipeline_impl` 已建的 loop（传入 `loop` 而不是各自 `asyncio.run`）。

---

## 8. B6-06 / B6-07（P2）`data_jobs` 的 RUNNING 残留与重试残留字段

### B6-06 无回收

**位置**：`app/orchestrator.py:61-63`（唯一写 RUNNING 处）；对照 `app/services/task_store.py:109-149`（只回收 `background_tasks`）、`app/main.py:74-78`。

实测（S1 T5）：进程在 RUNNING 时被杀 ⇒ 该行状态永久停在 RUNNING；`reap_stale_running_tasks()` 只清理 `background_tasks`（`data_jobs rows still RUNNING after reap: 1`）。`_create_or_get_job_async` 只在**同 (job_type, trade_date)** 被再次触发时才覆盖它；跨日则永不。消费方 `/ops/dag`（`ops.py:415-419` `recent_jobs`）、`/overview` 健康灯、AI 日报都会把它当「正在跑」。

**建议**：启动时同款回收（`daily_pipeline` 的 RUNNING 行在启动阶段不可能有在飞任务，与 `reap_stale_running_tasks` 同一前提），或在读侧对「RUNNING 且 started_at 超过 N 小时」如实标注为 `STALE`。

### B6-07 重试残留字段

**位置**：`app/orchestrator.py:61-63`。

```python
row.status, row.started_at, row.error_message, row.traceback = (
    "RUNNING", datetime.now(), None, None)
```

只重置 4 列。`finished_at` / `duration_ms` / `current_step` 保留**上一次**的值。实测（S1 T2）：

```
after FAILED + re-claim: {'status': 'RUNNING', 'current_step': 'infer',
                          'started_at': '...43.980', 'finished_at': '...43.956', 'duration_ms': 1234}
```

于是出现「RUNNING 且 finished_at 早于 started_at 且 duration_ms 非 0」的自相矛盾行；`/ops/dag` 的 `recent_jobs` 会把它当已结束的任务渲染，而 `current_step` 还指向失败的那一步（本次可能根本还没跑到 infer）。同理 `L61` 把 `error_message` 清成 None，`/ops/dag` 重跑一次失败任务后，**上一次的失败原因在库里消失**（traceback 也一起没了）。

**最小验证**：`b6_probe_s1.py` §T2，或定向单测 `_create_or_get_job_async` 两次 + `_finish_job_async("FAILED")`，断言 `finished_at is None and duration_ms == 0 and current_step is None`。

### 附带：非交易日分支同样不落 finished_at

`orchestrator.py:524-530`：`_finish_job_async(job.id, "FAILED", None, "non_trade_day: ...", None, 0)` 内部的 async session 是**另建引擎**的（见 §7），且未参与外层 `loop` 的事务——本机实测该 `_finish_job_async` 的写入**静默未生效**：

```
[F10] non-trade-day -> status='FAILED' error='non_trade_day: ...' duration_ms=0 finished_at=None
```

（内存对象的 `job.finished_at` 从未被赋值，所以这里 `None` 是代码事实；DB 行是否落 `finished_at` 依赖那次异步写是否成功，属「疑似」——需要直接在真库查一行非交易日 FAILED 记录来确认。）

---

## 9. B6-08 / B6-11 / B6-13（P2/P3）缓存与续传状态的实现与文档不符

### B6-08（P2）`cached()` 并发去重是假的

`app/services/stats_cache.py:17-27` docstring：「带 TTL 的进程级缓存；并发下只算一次」。实现是「锁内查、锁外算、锁内写」——没有 single-flight。实测（S2）：

```
6 concurrent cached() calls with cold key -> fn() executed 6x (calls=[5,0,2,3,4,1]); values=[0,1,2,3,4,5]
```

对 `mirror_status`（`datacenter.py:1072-1077` 注释自述实测稳定 **21~23s**、逐文件读 ~3.1 万个 parquet）这类慢计算，N 个并发请求 = N 次全量重扫，且每个请求各自把结果写进同一 key（最后一次胜出，值都相同，故**不是**正确性问题，是资源放大）。**最小验证**：`b6_probe_s2.py` §F2。**建议**：加 per-key in-flight Event，或直接把慢计算改成后台协程预热（`warm_overview_cache` 已有同款模式）。

### B6-11（P3）`_sync.failed` 只增不减

`sync_service.py:62/65`（`completed` 与 `failed` 两个集合）→ `L434-435` 只对**成功结束**清 `completed`，`failed` 永不清；`L242/322/366` 的 `pending` 过滤只跳过 `completed`，`failed` 只用于「写进续传记录」。用户在同步里看到的 `failed_count`（`snapshot()` L118）会跨任务、跨重启单调累积，无法反映「本次还剩多少待重试」。另外 `datacenter.py:959`（`/sync/fetch`）与 `L957-960` 也不清 `failed`。**最小验证**：连续跑两次 `_run_incremental`（一次让某 symbol 抛错），断言失败第二次后 `len(_sync.failed)` 仍为 1。

### B6-13（P3）续传进度每 30s 全量重写

`sync_service.py:83-100`（`_maybe_persist`）把 `list(self.completed)` 全量 JSON 写入 `app_state.sync_state`，`kv_set`（`db/kv.py:33-47`）是 `INSERT ... ON CONFLICT` 整值覆盖。全市场 2499 只时每次约 45KB（符号名 `000001.SZ` 计），每 30s 一次；一次 2 小时的同步 ≈ **240 次 × 45KB ≈ 10MB** 写入并反复持有 SQLite 写锁。**建议**：只存 `{mode, day, completed_count, 最近 N 个 symbol}` 或改存「每 symbol 的最后成功日」表；或把周期拉长到 5 分钟。

---

## 10. B6-09 / B6-10（P2/P3）死代码与未接线

### B6-09（P2）`task_store` 的租约机制整条是死代码，且与回收策略矛盾

- `update_task(..., "running")`（`task_store.py:67-69`）只 `COALESCE(started_at, ?)`，**从不触碰 `lease_until`**。实测（S1 T6）：`lease after claim = 2026-09-21T11:20:44+00:00` → `update after = 同一值` → `renewed? False`。
- `claim_task`（`L80-93`）的第二个条件（`status='running' AND lease_until < now` ⇒ 允许他人抢占）**要求两次调用同一 task_id**。全仓调用点只有 `datacenter.py:687`，且用的是 `create_task()` 刚生成的全新 id ⇒ 该分支恒不可达。实测 `claim_task on succeeded -> False`、`claim_task on cancel_requested -> False`，只有人为把 lease 改到过去再调才会 `True`。
- `reap_stale_running_tasks`（`L109-149`）**无条件**回收所有 `running`（docstring 还专门论证「不许按 lease 过滤」）。于是同一个字段上并存两种互斥策略：一个说「租约过期才能抢」，另一个说「租约完全不看」。
- `task_store.py:3-5` 的「lease 字段为后续多 worker 抢占提供原子更新基础」与 `claim_task` 的 `lease_seconds=300` 默认值，共同暗示了一个**不存在**的能力。这不是「未接线」的将来特性，而是**已接线但恒不生效**（`claim_task` 每次都以新 id 调用、`lease_seconds=300` 硬编码在唯一的调用点）。

**最小验证**：`b6_probe_s1.py` §T6（4 行断言）。**建议**：要么删掉 lease 列与过期分支、把 `background_tasks` 明确成「单进程状态表」；要么补上 worker 侧续租（`update_task(..., "running")` 内刷新 `lease_until`）并把 `reap_stale_running_tasks` 改成按 lease 过滤。二者选一，不要两头都只写一半。

### B6-10（P3）`current_owner()` 零调用方

`app/core/pipeline_lock.py:54-56`。S5 全仓扫描：`current_owner` 在 `app/` 内出现 1 次（就是它自己的定义行），`tests/` 内 0 次；`current_pipeline_owner` 出现 4/7 次。属纯别名死代码（不是将来特性，因为同义的 `current_pipeline_owner` 已被 `train_service.py:417-419` 使用）。**最小验证**：`b6_probe_s5.py` 的 `grep -n current_owner` 段。

---

## 11. 多 worker 风险（按要求：评估，不作为当前 bug 上报）

- `pipeline_slot` 是进程内 `threading.Lock`（`pipeline_lock.py:30-31`），多 worker 下互斥静默失效 ⇒ `atomic_write_parquet` 的「后写者覆盖先写者」（`parquet_store.py:506-541` 只保证单文件原子，不保证两个写者的先后）会真的发生。
- `reap_stale_running_tasks`（启动无条件回收）在 `--workers N` 下会把其它 worker 在飞的合法任务翻成 failed；`restore_sync_state` 的 `_sync.completed` 也会被每个 worker 各自恢复/覆盖。
- `warn_if_multi_worker`（`pipeline_lock.py:211-219`）+ `main.py:83-87` 已接线且只告警，`test_single_instance_guard.py` 有接线可证伪用例。**结论：当前单实例部署下无问题，护栅到位；迁移到多 worker 前必须补 Redis 分布式锁 + 协调式回收**（与源码注释一致，无需额外行动）。

---

## 12. 策略合理性（目标 C）

### C-1 流水线只有「整日」幂等，没有「整步」幂等；失败即全量重来

当前：单个 `data_jobs` 行 + 一个 `current_step`，任一 step 抛错 ⇒ fail-fast 终止（`orchestrator.py:545-555`），重试从**第一步**重新开始。
问题：全市场一次流水线里 `update_daily` 的纯冗余重下载下限约 100min（`orchestrator.py:401-409` 注释自述），而 `data_jobs` 里没有任何「哪些 step 已成功」的记录，因此第 8 步 `build_cs_mirror` 失败也只会得到「重跑整条」这一个选项——而重跑的第一步又会把 4998 次网络调用再做一遍。
具体改法：新增 `data_job_steps(job_type, trade_date, step, status, finished_at)` 表（或在 `data_jobs` 加 `steps_done_json`），每个 step 成功后落一行；`_run_pipeline_impl` 在派发前跳过「本次目标日已 SUCCESS」的 step，并让 `?force=true` 才全量。
预期收益：晚间断点重跑的时间从「整条 ≈ 100min+」降到「失败步及其后继」，量级 10×。
引入风险：若某 step 的成功先于其输入变更（例如 `validate` 成功后有人手工 `/sync/fetch` 改了当日 raw），跳过会用到陈旧产物——因此「跳过」必须同时校验输入数据集的 `mtime`/manifest 版本，或只在同一 `job_type` 的连续重试内生效。
验证方式：定向单测——第二次跑时把 8 个 step 函数换成记录器，断言只被调用「未成功」的子集。

### C-2 `step_validate` 的守卫对「首次运行 / 手工重跑」恒等于空操作

`data/pipeline.py:106-124`：当日无数据时，只有「上一交易日**有**该 code 的数据」才计入 `missing_errs`；否则计 `n_suspended` 并跳过。实测（S6）：

```
data present for 2026-09-18 -> none; 且上一交易日也为空
step_validate -> validated=0/3 suspended=3 missing_vs_prev=0 degraded=0    ← 不抛错
with prev-day rows present -> ValueError: 昨有今无 3/3 只（阈值 5%）        ← 正确致命
```

现实触发面：晚间例行（全市场 2499 codes）有 autoSync 兜底，通常不会命中；但 `/dag/rerun` 的默认 `codes` 是 3 只（`orchestrator.py:499`），**首次部署 / 数据目录被重建 / 上一交易日整体缺失**时，`validate` 会对「一只都没拉到」的当日放行，随后 `infer`/`screener_dump` 在缺数据的前提下继续（`screener_dump` 只在 predictions 分区**不存在**时报错，`orchestrator.py:304-306`；若旧的 `date=<今天>` 分区已存在，则整条流水线 SUCCESS 而榜单其实是陈旧内容）。
具体改法：`n_suspended` 需要一个上界——当日**全市场覆盖率为 0**（`n_ok == 0 and n_expected == 0`）必须致命；另外让 `screener_dump` 校验 predictions 分区的 `date` 列最大值等于目标 `trade_date`（而不是只看文件是否存在）。
预期收益：把「整体静默空洞」从「SUCCESS + 空榜」变成「FAILED + 可定位」。
引入风险：极小范围的正常场景（例如全市场停市的极端日子）会误报——可用「日历显示是交易日」作为前提条件来排除。
验证方式：`b6_probe_s6.py` §F12 的三行断言（空库 ⇒ 期望抛错）。

### C-3 `step_update_daily` 把逐代码失败降级成字符串，与 `validate` 无信息交换

`data/pipeline.py:33-51`：每个 code 的抓取失败只 `logger.warning` + 计入本地 `failed` 列表，最终拼进返回字符串 `"rows=N failed=（...）"`。这个字符串只被 `logger.info`，`validate` 拿不到它，只能独立重查数据库并用 5% 容差（`pipeline.py:61`）判断。小代码集下容差失去分辨力：

```
n_expected=3     tolerance=0.15  -> 需 >0 只缺失才失败（即 1 只就失败，尚可）
n_expected=2499  tolerance=124.9 -> 需 >124 只缺失才失败
```

即全市场跑时最多 124 只标的抓取失败仍会被判为「正常日」（这是有意的停牌容差），但**没有任何地方汇总「本次 update_daily 有多少只失败」**，`detail` 字符串里的 `failed=` 数量级（如 120 只）与 `validate` 的沉默并存。具体改法：让 `step_update_daily` 把失败清单写进 `data_jobs`（例如 `error_message` 或新的 `detail_json` 列）并在 `failed/len(codes) > 2%` 时直接 FAILED；或让 `step_validate` 接收上一步的失败集合做子集校验。
预期收益：把「红着的 hidden failure」变成可查询的一列。
引入风险：停牌/退市标的天然会进 failed（`n==0 and not failed_adj` ⇒ `failed.append`，`data/pipeline.py:43-45`），阈值不能设成 0，需要按「大盘面停牌基线」校准。
验证方式：定向单测——`fetch_and_write_daily_bars` 桩成全抛错，断言 `step_update_daily` 的返回值/落库能反映 100% 失败。

---

## 13. 疑似项（需补充信息才能确认，本轮未验证）

1. **`daily_bar_hfq` 基准一致性在编排链上无强制**（疑似，需真实 akshare 应答才能定级）
   `data/ingest/tasks.py:141-150` 明确写「akshare 的 hfq 是『以最新数据为基准重算整条序列』，因此必须**一次性抓取完整历史**再分区，绝不能逐年抓取后拼接」，但唯一的调用者路径违反它：`orchestrator.step_update_daily` 传 `(ds, ds)`（单日区间，`data/pipeline.py:35`）、`sync_service._run_incremental` 传 `(start, ds)`（缺口区间，`sync_service.py:288`）、`/sync/fetch` 传用户任意区间（`datacenter.py:898-899`），`fetch_and_write_daily_bars` 只把**应答里的行**写进 `write_partition`（`tasks.py:163-179`）。若 `stock_zh_a_hist(adjust="hfq")` 的基准锚在「应答内最后一个 bar」，则同一天被两次不同区间的请求写入会得到**两个不同数值**（破坏幂等），且跨除权日的主链 hfq 会出现基准跳变。可验证的最小方法：对同一 code 分别请求 `(2026-01-01, 2026-06-30)` 与 `(2026-06-01, 2026-06-30)`，比对 2026-06-30 这一天的 `close` 是否相等（本沙箱无外网，无法执行）。**该条属 B5 数据层权责，这里只登记编排链上的触发路径。**
2. **非交易日 FAILED 行的 `finished_at` 是否真的落库**（见 §8 末）——需在真库 `SELECT status, started_at, finished_at FROM data_jobs WHERE status='FAILED'` 对照。
3. **`asyncio.Runner.close()` 在 Python 3.12+ 的 `subprocess`/event-loop 行为**（见 §7）——需 3.12 解释器复跑 `b6_probe_s4.py`。
4. **`step_build_features` 增量守卫的 `equal_nan=True`**（`orchestrator.py:180`）：若既存特征某列**整列为 NaN**、而本次重算该列也是全 NaN（例如某 symbol 全段停牌导致 `skew_ret_*` 恒 NaN），`np.allclose(..., equal_nan=True)` 会判「一致」⇒ 已有的整列 NaN 缺陷被守卫静默放行，进而在增量合并里原样保留。本轮**未构造出**可复现的整列 NaN 既有分区，故列为疑似；最小验证是手工把某年分区的 `skew_ret_20` 整列改 NaN 后跑 `step_build_features`，断言它被守卫拦下而不是判一致。相关正向结论：`except Exception` 回退全量（`orchestrator.py:215-218`）**不会**掩盖真实数据损坏（全量重算 = 从 hfq 真值重导，是安全恢复路径）；缺陷只在可观测性——回退后返回串仍是 `mode=full(cols=...)`，与「本来就走全量」无法区分，操作员看不到守卫触发过（`logger.warning` 在后台进程日志里）。

---

## 14. 明确「未发现问题」的部分

- `task_store` 状态值集合与迁移：`queued → running → succeeded|failed|cancelled`，`cancel_requested` 作为中间态不可被 `claim_task` 抢走（实测），`reap` 幂等（`[]` when no running），`update_task` 的 `finished_at` 只在三个终态写 —— 合法。
- `compute_guard.py`：无界等待已换成有界（`asyncio.to_thread(_slots.acquire, True, timeout)`），超时抛业务码，`finally: release()` 无泄漏 —— 未发现 P0–P2。
- `evening_routine` 的 panic/异常兜底与放行：`is_fatal_base_exception` 分支保留、`log_contained` 留痕、`startup_catchup` 对 `BaseException` 兜住后正常返回 —— 与 `sync_service.auto_sync_scheduler` 同款且一致。`EVENING_ROUTINE_ENABLED=0` 时不产生任何 `data_jobs`/`background_tasks` 残留（只读 `app_state` 的 `evening_routine_last`），无泄漏。
- `sync_service` 的终态落库口径：`cancelled` / `worker_error` / `noop` / 正常四条分支互相排斥且都写 data_jobs + `update_task` 终态 + SSE，`noop` 被如实记成 FAILED（`sync_service.py:448-489`）—— 未发现「空跑冒充成功」。
- `parquet_store.write_partition`：read-merge-dedup-atomic-write，同日重跑幂等（`test_data_jobs_timestamp.py`/`test_pipeline.py` 覆盖）—— 在 `pipeline_slot` 覆盖的调用路径下未发现重复行/半写。