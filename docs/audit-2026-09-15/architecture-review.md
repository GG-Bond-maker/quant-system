# AQP 增量修改审核 · 架构评审报告

**审核日期**：2026-09-15
**审核人**：高见远（架构师）
**审核方式**：全程只读。仅使用 `git diff` / `Grep` / `Read` / 只读 SQL 查询 / `tsc --noEmit`；未修改任何业务源码
**基线**：`docs/audit-2026-09-14/AUDIT-SUMMARY.md`（P0 × 10 / P1 × 26 / P2 × 47）
**工作区状态**：60 个已修改（M）+ 32 个未跟踪（??），全部未提交

---

## 〇、一句话结论

> **这一轮改动的水位是"真改、改对了大半"，但收尾没做完：10 条 P0 中 4 条已修复、5 条部分修复、1 条未动；
> 更关键的是有 3 处"改了 A 忘了 B"的单侧改动，把 2 个原本还能用的功能直接变成了永久不可用。**

| 判定 | 条数 | P0 编号 |
|---|---:|---|
| ✅ 已修复 | 4 | #4（401 跳转）、#5（SSE）、#7（抓取互斥）、#8（预警路径） |
| 🟡 部分修复 | 5 | #1（特征版本）、#2（首屏阻塞）、#3（研究页并发）、#6（ETF）、#9（测试基线） |
| ⚪ 未动 | 1 | #10（产品误导与合规倒挂，仅改了前端文案，后端字段与默认值原样） |

**另外发现 18 条"半成品"与 8 条本次改动新引入的问题，其中最严重的 3 条是：**

1. **个股「近期事件」与「北向持股」两个块被改成永久不可用**（`panels.py:165` 改读本地空表 → 回退 `fetch_events` → `realtime.py:652` 已被改成恒抛异常；`realtime.py:499` 北向持股同理）。本地 `news_announcement` 表实测 **0 行**，全项目**无任何写入方**。
2. **`/portfolio/search` 搜不到任何 ETF**（改为只查本地 `instrument` 表，而该表实测只有 `stock` 类型 5552 条，**0 条 etf**）。
3. **一条既有测试必然失败**：`tests/test_write_endpoints_smoke.py:538` 仍断言 `/settings/apikeys/rotate` 返回 `aqpx_` 明文密钥，而后端已改为固定拒绝（`app_settings.py:254`）。

---

## 一、P0 验收矩阵（逐条·带源码证据）

### #1 特征双版本污染 —— 🟡 **部分修复**

| 读 features 的入口 | 前 | 后 | 判定 |
|---|---|---|---|
| `api/v1/research.py` | `rglob("*.parquet")` 全版本 | `data/features.py:read_feature_frame` 单版本 | ✅ |
| `api/v1/studio.py` | 同上 | `feature_files` + `assert_unique_feature_rows` | ✅ |
| `api/v1/alerts.py` | `features/year=*.parquet`（错路径） | `read_feature_frame()` | ✅ |
| **`ml/monitor.py`** | `root.rglob("*.parquet")` | **未改，仍混读全版本** | ❌ |

**证据（已修的部分）** —— `backend/app/data/features.py`（新增，105 行）：

```python
# backend/app/data/features.py:97-104
def read_feature_frame(version: str | None = None) -> tuple[str, pl.DataFrame]:
    selected, files = feature_files(version)          # 只 glob version=<x>/year=*.parquet
    frame = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    ...
    assert_unique_feature_rows(frame, selected)        # 单版本内仍重复即 fail-fast
    return selected, frame
```

```python
# backend/app/api/v1/research.py:85-99
selected_version = resolve_feature_version(version)
...
    _version, df = read_feature_frame(selected_version)
...
return _cached(f"features:{selected_version}:{days}", _load)   # 缓存键带版本，防串味
```

```python
# backend/app/api/v1/studio.py:29-52
selected_version = resolve_feature_version(version)
_version, files = feature_files(selected_version)
...
assert_unique_feature_rows(df, selected_version)
_SNAP_CACHE: dict[tuple[str, int], ...] = {}          # 缓存键从 days 改为 (version, days)
```

**证据（未修的部分）** —— `backend/app/ml/monitor.py`：

```python
# backend/app/ml/monitor.py:107-116
def _signature(root_name: str) -> tuple[tuple, list[Path]] | None:
    root = s.DATA_ROOT / root_name
    files = sorted(root.rglob("*.parquet"))            # ← features/ 下所有 version=* 全吃
# backend/app/ml/monitor.py:119-124
def _features_frame(days: int = 400) -> pl.DataFrame:
    sig = _signature("features")                       # ← 多版本混合
# backend/app/ml/monitor.py:446
    feat = _features_frame()                           # run_monitor() 主入口 → PSI/漂移/健康度
```

**影响**：模型监控（PSI 漂移、IC 衰减、健康度、自动重训触发）仍然建立在被污染的面板上，且 `run_monitor` 有自动重训副作用的分支（`config.py:151 AUTO_RETRAIN_ON_DRIFT=True`），污染数据可能触发错误的重训。

**⚠️ 附带风险**：`config.py:79-82` 新增 `FEATURE_VERSION`，默认 `""`（空）。空值时 `features.py:39-51` 按"parquet mtime 最新"自动挑版本并只打一条 `logger.info`。这是**隐式选择**——如果某天 v1 目录被 touch 了一下，全站研究结论会静默切换口径。建议要么在 `.env` 显式钉死，要么把自动选择升级为 warning 并写进响应体。
**另**：`FEATURE_VERSION` 未写入 `.env.example`（见第三节·配置漂移）。

---

### #2 首屏 `/market/overview` 冷缓存阻塞 —— 🟡 **部分修复（从"慢"改成"空"）**

**已做的**（`backend/app/api/v1/market.py`）：

```python
# market.py:528-560  _build_rt：三路 IO 受限并发（ThreadPoolExecutor(max_workers=3)）
with ThreadPoolExecutor(max_workers=3, thread_name_prefix="overview-rt") as pool:
    indices_future   = pool.submit(_build_indices)
    flow_future      = pool.submit(_build_money_flow)
    anomalies_future = pool.submit(lambda: _build_anomalies(_build_heat()))

# market.py:590-600  _build_overview：实时/日频两块并发
with ThreadPoolExecutor(max_workers=2, thread_name_prefix="overview") as pool: ...

# market.py:707 / 763 / 817  三个端点统一 asyncio.wait_for(..., timeout=5.0 / 5.0 / 6.0)
# market.py:709-726 / 764-782 / 818-852  超时返回结构化 unavailable，不悬挂
```

```python
# backend/app/api/v1/market.py:275-300 / 326-360 / 362-405  _build_recommend 重写
files = [chosen] if chosen.exists() else []        # 不再回退到「任一历史分区」
...
if close is None and pct is None and amount is None:
    continue                                        # 无真实行情的候选不当推荐
...新增 as_of / reason / message / coverage / freshness 统一契约
```

**未做的 / 新引入的**：

| 残留问题 | 证据 | 影响 |
|---|---|---|
| **AKShare 调用仍无单次超时** | `backend/app/data/ingest/akshare_adapter.py:85-92` `_retry_decorator()` = `stop_after_attempt(3)` + `wait_exponential(1,1,8)`，**没有任何 timeout**；`_throttle()` 还会主动 `time.sleep` | 5s 预算内只要有一次 akshare 重试，`/overview/rt` 必超时 → 冷路径基本**恒为 unavailable** |
| **降级结果被写进缓存** | `market.py:859-861` `cached_or_build(key, _build, ttl=300, ...)`，而 `_build` 内部已把 TimeoutError 转成 degraded 数据 | 一次超时 = **连续 5 分钟**对所有人返回"不可用"，`refresh=1` 是唯一出路，但 refresh 锁只有 5s 防抖 |
| **TTL=-2 / 重建慢的根因未解** | 冷重建的真实耗时没被压缩，只是被预算切断；`warm_overview_cache`（`market.py:865-899`）仍直接 `asyncio.to_thread(_build_overview, ...)`，**无 budget** | 启动预热仍可能长时间卡住；主键 TTL 300s 一旦小于重建耗时，"主键缺失 + 只吃 stale"的现象会复现 |
| **"串行抓 500 项"的源头未定位/未确认消除** | 未在任何修复报告中看到针对该具体循环的改动与实测 | 无法判定是否彻底消失 |

**判定**：阻塞（45s 无响应）**已消除**；但"首屏有数据"**未达成**——冷路径返回的是结构化空态。从用户视角，45s 白屏变成了 6s 后一屏"数据源暂不可用"。**属于止血，不属于修复。**

---

### #3 策略研究页并发超限 —— 🟡 **部分修复（只改了前端）**

**前端已改完**（`frontend/src/pages/Research/index.tsx`）：

```ts
// Research/index.tsx:75-113  ComputeQueue：整个页面共享，最多 2 个在飞
class ComputeQueue {
  private active: number = 0;
  ...
  } else if (this.active < 2) { start(); } else { this.pending.push(start); }

// Research/index.tsx:57-64  40103 / 40900 映射为局部可重试提示（不再整页失败）
function formatSectionError(error: unknown): string {
  if (error instanceof ApiError &&
      (error.code === ERR.RATE_LIMITED || error.code === ERR.PIPELINE_BUSY)) {
    return '计算资源繁忙，请稍后重试';
  }

// Research/index.tsx:194-...  loadSection：4 个区块各自独立加载、独立失败、各自「重试」按钮
// Research/index.tsx:236-...  useEffect cleanup：controller.abort() + 取消全部交互请求
// client.ts:139-176         get/post/put 新增 RequestOptions.signal
// api/research.ts           全部 11 个端点透传 signal
```

**后端一行没动**：

```python
# backend/app/core/compute_guard.py:10-20（未修改）
_slots = threading.BoundedSemaphore(get_settings().COMPUTE_CONCURRENCY)   # = 2
...
if not _slots.acquire(blocking=False):
    raise AQPException(ERR_RATE_LIMITED, "计算资源繁忙，请稍后重试", {"retryable": True})
# ← 仍然「直接抛错不排队」，与 09-14 审计时完全一致
```

**影响**：
- 单页面已不会自伤；但**开两个标签页 / 两个用户**仍会互抢 2 个槽，`40103` 依旧会命中（作者自己在 `research-auth-settings-fix-report.md` 的"残余风险"里也承认了这点）。
- 前端 `ComputeQueue` 是**页面级**的，不是应用级的；换页即重建，跨页无协调。
- `research-auth-settings-fix-report.md` 里承诺的"按需触发/点计算按钮"没有落地——`loadSection` 仍在 `useEffect` 里首屏一次性触发 4 个区块（只是被队列限流了）。

---

### #4 401 拦截器无条件跳登录 —— ✅ **已修复**

```ts
// frontend/src/api/client.ts:19-33
const AUTH_ERROR_CODES: ReadonlySet<number> = new Set([
  ERR.UNAUTHORIZED, ERR.TOKEN_EXPIRED, ERR.INVALID_TOKEN,
  // ERR.FORBIDDEN：已登录但角色不足，由页面展示 ApiError，不清会话。
  // ERR.PIPELINE_BUSY：资源状态冲突，同样不得清除有效登录会话。
]);
function handleAuthFailure(): void {
  useAuthStore.getState().clear();     // ← 只清会话，不再 window.location.href = /login
}
```

```tsx
// frontend/src/components/Topbar.tsx:79-105
useEffect(() => {
  if (!authed) { closeNotify(); return undefined; }   // 未登录不建 SSE 连接
  openNotify();
  return () => closeNotify();
}, [authed, closeNotify, openNotify]);
```

```ts
// frontend/src/stores/useNotifyStore.ts:63-66
const refresh = async (): Promise<void> => {
  if (!useAuthStore.getState().isAuthenticated()) return;   // /notify/recent 也不再匿名调用
```

跳转职责已移交路由守卫：`App.tsx:86-110` 全部受保护路由包 `RequireRole`，`RequireAuth.tsx:57-68` 展示"当前会话仍保持登录"。`tsc --noEmit` 实测 0 error。

---

### #5 SSE 实时推送从未工作 —— ✅ **已修复（且设计质量高）**

后端：

```python
# backend/app/api/v1/notify.py:36-58
async def require_stream_viewer(ticket=Query(None), credentials=Depends(_stream_bearer_scheme)):
    if ticket is not None:
        user = await consume_sse_ticket(ticket)      # 原子消费
        if user is None:
            raise HTTPException(status_code=200, detail="UNAUTHORIZED")   # 不区分原因
        return ensure_role(user, "viewer")
    return ensure_role(require_auth(credentials), "viewer")   # 旧 Bearer 兼容

# notify.py:60-63  新增 POST /notify/stream-ticket
# notify.py:106-107 gen() 首帧立即输出 ": connected\n\n"
```

```python
# backend/app/core/auth.py:52-120  ticket 实现
SSE_TICKET_TTL_SECONDS = 60
_SSE_TICKET_REDIS_PREFIX = "r."      # Redis 域：GETDEL 原子消费，多 worker 安全
_SSE_TICKET_MEMORY_PREFIX = "m."     # 降级域：lru_take 原子取删，仅本进程
# ticket 用 secrets.token_urlsafe(32)，负载只存 {username, role, iat, exp}
```

```python
# backend/app/cache/redis_client.py:143-177  set_if_available / getdel_if_available
#   —— 明确「不降级到本地 LRU」，避免 Redis ticket 被本地副本重复消费
# backend/app/cache/memory.py:45-55          lru_take 原子读+删
# backend/app/core/errors.py:27              ticket 加入 query 脱敏名单
```

前端：

```ts
// frontend/src/stores/useNotifyStore.ts（整体重写）
// 建连前先换票 → 只把 ticket 放 URL → onerror 主动 close 阻断浏览器自动重连
// → 指数退避「重新换票 + 建连」；connectionGeneration 防幽灵连接
// frontend/src/api/notify.ts:19-28  streamUrl 只拼 ticket，禁止放 JWT/ADMIN_TOKEN
```

**唯一瑕疵（不是 P0 阻断）**：`useNotifyStore.ts:12` `MAX_RETRIES = 3`，重试耗尽后 `status='unavailable'` 且 `retryCount` 不重置；Topbar 的「刷新」按钮（`:270-276`）只调 `refresh()` 拉最近事件，**不会重新建连**。要恢复实时推送必须 `close()+open()`（即切换登录态或重挂载组件）。属于"降级后无法自愈"，建议补一个"重连"入口。

---

### #6 ETF 详情不可用 + 四端点超时 —— 🟡 **部分修复**

**已做的**：

```python
# backend/app/data/etf.py:37-41
_ETF_HTTP_TIMEOUT = 4.0
_ETF_HTTP_RETRIES = 1
# etf.py:76-92  东财全量目录：while page <= 40 / pz=100  →  while page <= 2 / pz=2000
#                （14 次串行请求 → 最多 2 次）
# etf.py:133-136 / 178-181 / 226-229  kline / 腾讯美股批量 / 资金流 统一 4s × 1 次
```

```python
# backend/app/api/v1/etf.py:53-100
_ETF_ENDPOINT_BUDGET_SECONDS = 4.5
_ETF_DETAIL_BLOCK_BUDGET_SECONDS = 4.0
async def _cached_etf_payload(key, build, fallback, *, ttl=300):
    async def _build():
        try:
            data = await asyncio.wait_for(build(), timeout=_ETF_ENDPOINT_BUDGET_SECONDS)
            ...
        except TimeoutError:  return fallback("数据源响应超时，已快速降级")
        except Exception:     return fallback(f"数据源暂不可用（{type(exc).__name__}）")
    return await cached_or_build(key, _build, ttl=ttl, stale_window=1800, ...)

# etf.py:53-65   新增 k_etf_overview / k_etf_performance / k_etf_scale / k_etf_detail 四个
#                 按「数据日 + 完整请求参数」隔离的 SWR 缓存键（cache/keys.py:29-47）
# etf.py:355-365 / 427-437  performance 与 scale 的 K 线读取改为 ThreadPoolExecutor(max_workers=4)
# etf.py:713-775  detail 改为 asyncio.gather 7 块 + 每块 _run_detail_block 4s 预算
```

**判定依据**：`performance-fix-report.md` 实测四端点冷路径 4.49–4.53s（原 59.7–82.8s），但**测评环境外部 ETF 行情源没有在预算内返回，冷调用如实返回了 unavailable**。也就是说：

- **"打不开"→"4.5 秒后给空态"是成立的**；
- **"1383 只 ETF 详情页能用了"尚未被验证**——在真实网络下到底能不能在 4.5s 内拿到数据，报告里没有证据。
- 同 #2 的问题：**降级结果同样被 `cached_or_build` 缓存 300s**（`etf.py:96-99`），一次超时锁死 5 分钟。
- `frontend/src/pages/Etf*` **本次未做任何修改**，新增的 `data_freshness` / `coverage` / `message` 字段前端一律不消费 → 用户看到的是"空"，而不是"为什么空"。这是典型的**单侧改动**。

---

### #7 抓取未纳入 pipeline_slot 互斥锁 —— ✅ **已修复**

```python
# backend/app/api/v1/datacenter.py:835-866
from ...core.pipeline_lock import pipeline_slot
# 与 sync/pipeline/mirror/training 使用同一把非阻塞锁，完整覆盖所有分区写入。
with pipeline_slot("fetch"):
    ...
    for i, sym in enumerate(symbols, 1):
        ...
        n, failed_adj = fetch_and_write_daily_bars(code, start, end, adjusts=("", "hfq"), fetcher=fetcher)
```

```python
# backend/app/api/v1/datacenter.py:885-893  启动前预检，繁忙即时返回而非"伪启动"
try:
    with pipeline_slot("fetch"):
        pass
except PipelineBusy as exc:
    return fail(ERR_PIPELINE_BUSY, str(exc))
```

```python
# backend/tests/test_pipeline_lock.py:53-61  新增契约测试
def test_custom_fetch_rejected_when_pipeline_slot_is_busy():
    with pipeline_slot("sync"):
        with pytest.raises(PipelineBusy):
            datacenter_mod._run_fetch(["000001.SZ"], "stock", "2026-01-02", "2026-01-02")
```

**残留小瑕疵**：预检（885-893）与后台线程真正持锁（835）之间存在 **TOCTOU 窗口**——两个并发请求可以同时通过预检，第二个会在后台线程里抛 `PipelineBusy` 并被 `_worker` 的 `except` 吞成 `_sync.error`，对外仍是 `{"started": true}`。严格来说又变成了一次"伪启动"。建议把预检改成"在预检锁内启动线程"或直接让 `_worker` 的失败可观测。

---

### #8 因子分位预警路径错误 —— ✅ **已修复**

```python
# backend/app/api/v1/alerts.py:31 / 539-548
from ...data.features import read_feature_frame
...
root = get_settings().DATA_ROOT / "features"
try:
    version, df = read_feature_frame()          # ← 替代了原来的 glob("year=*.parquet")
except AQPException as exc:
    logger.warning(f"[alerts] rule {rule.id} factor_quantile 无 features 文件；root={root} detail={exc.message}")
    return []
```

三条跳过路径（因子列不存在 / 样本 <30 / 当前值缺失）现在都有 `logger.warning` 留痕（`alerts.py:549-570`），不再是"静默返回空"。`docs/audit-2026-09-14/alert-feature-probe-result.json` 也留了实测证据。

---

### #9 全量 pytest 跑不完 —— 🟡 **部分修复**

```ini
# backend/pytest.ini:8-11
addopts = -v --tb=short -p faulthandler
faulthandler_timeout = 600
markers =
    network: test performs a real external network request and is excluded from offline baselines
```

```python
# backend/tests/test_universe.py:136        @pytest.mark.network  (test_real_sampling_multi_board)
# backend/tests/test_multi_source.py:93     @pytest.mark.network  (test_real_akshare_primary)
# backend/tests/test_p1_data.py:81          @pytest.mark.network  (原 real_network，已重命名并统一)
```
```sh
# backend/scripts/run_offline_tests.sh:13（新增，另有 .ps1 版本）
exec "$PYTHON_BIN" -m pytest -q -m "not network"
```

**未完成的**：
1. **`faulthandler_timeout = 600` 太长**。09-14 的失败现象是 `test_universe` 挂 13min 无输出；600s 超时意味着单个用例仍要等 10 分钟才 dump 栈。审计建议是 `pytest-timeout`（可按用例/按 marker 分级），现在是"全局 10 分钟兜底"，只能防死等，不能防慢。
2. **离线基线没有跑完的证据**。仓库里最新的全量离线日志 `backend/pytest_task12_offline_final.txt`（09-15 16:02）**542 行但没有 summary 行**（无 `passed/failed` 统计），只跑到 `test_api.py` 就被截断；最近一次有完整结论的是 `pytest_offline_result.txt`（09-15 01:16）：`2 failed, 773 passed, 8 skipped, 2 deselected, 23 warnings in 1823.79s (0:30:23)`——那是**本轮多处改动之前**的快照。
3. **8 个新增测试文件都没有空壳**（实测 assert 数 3–23，0 个 skip），这点很好：

| 新增测试 | 行数 | assert | 用例数 | skip |
|---|---:|---:|---:|---:|
| test_data_freshness_degradation.py | 145 | 23 | 6 | 0 |
| test_feature_version_guard.py | 58 | 3 | 2 | 0 |
| test_hotpath_portfolio_datacenter.py | 178 | 21 | 6 | 0 |
| test_hotpath_watchlist_panels.py | 166 | 21 | 5 | 0 |
| test_market_etf_performance.py | 114 | 10 | 3 | 0 |
| test_notify_sse_ticket.py | 104 | 14 | 5 | 0 |
| test_ops_model_governance.py | 102 | 10 | 5 | 0 |
| test_route_permission_contract.py | 182 | 16 | 7 | 0 |

4. **`backend/` 根目录堆了 13 个临时产物文件**（`pytest_*.txt`、`hotpath_benchmark_step*.txt`）未跟踪、未清理。

---

### #10 产品误导与合规倒挂 —— ⚪ **未动（仅前端文案改名，后端与默认值原样）**

| 子项 | 判定 | 证据 |
|---|---|---|
| ① 风险等级 = 预测收益 | ❌ **未动** | `backend/app/api/v1/screener.py:127` 原样：`risk = ("low" if score >= 0.3 else "mid" if score >= 0.1 else "high") if score is not None else None`。<br>前端 `Screener/FilterPanel.tsx:18-22` 把标签从「低/中/高」改成「弱信号/中性/强信号」，`index.tsx:82` `RISK_MAP` 同步改名，`StatsCards.tsx:197` 改文案，**但字段名仍是 `risk`、取值仍是 `low/mid/high`、后端映射逻辑一行没改**。 |
| ② 选股榜无模型声明 | ✅ **已修** | 新增 `frontend/src/components/ResearchDisclaimer.tsx`，已在 4 处落地：`Backtest/index.tsx:240`、`MarketOverview/AiPicksPanel.tsx:197`、`Portfolio/index.tsx:273`、`Screener/index.tsx:359`；`AiPicksPanel` 表头也从「预测涨幅」改为「未来 5 日预测」并加 title 说明。 |
| ③ ADMIN_TOKEN / ALLOW_REGISTRATION 默认值 | ❌ **未动** | `backend/app/core/config.py:40-43` 仍 `default="aqp-dev-token-change-me"`；`config.py:123-126` 仍 `ALLOW_REGISTRATION: default=True`；`.env.example:11,18` 也仍是这两个值。<br>本轮只把 prod 校验抽成 `validate_runtime_safety()`（`config.py:188-214`）并新增 dev 环境 warning + 一条 `test_prod_config_guard.py` 用例 → **生产会 fail-fast，但开发环境的实际默认值没有变**。 |

> 说明：① 属于"用前端文案绕过了后端语义"。字段名 `risk` 与取值 `low/mid/high` 会被任何直接调 API 的消费者按"风险"理解，且 `Screener/index.tsx:731` 的说明文字已改成"映射 weak / neutral / strong"，与后端实际返回的 `low/mid/high` **字面不一致**。这是本轮最典型的"单侧改动"。

---

## 二、半成品清单

> 共 **18 条**。判定方法：Grep 全项目引用数 = 0（孤儿）/ 单侧 grep（只在一侧出现）/ 只读 SQL 实测。

### A. 孤儿代码（定义了但全项目无调用方）

| # | 位置 | 内容 | 建议 |
|---|---|---|---|
| A1 | `backend/app/api/v1/watchlist.py:152` | `def _etf_names() -> dict[str, str]` —— 改写 `dashboard` 时把唯一调用点删了，函数本体留着 | 删除；或改回用于 ETF 自选名称兜底（见 B1） |
| A2 | `backend/app/api/v1/datacenter.py:286-289` | `def _dir_size(root)` —— 注释写着"兼容既有调用方"，但 grep 全项目已无调用方 | 连同 `_storage_stats` 的兼容层一起删除 |
| A3 | `backend/app/api/v1/datacenter.py:307-318` | `_refresh_overview()` + `_OVERVIEW_REFRESHING` + `_OVERVIEW_REFRESH_LOCK` —— `refresh=1` 的后台刷新分支被 `cached_or_build` 取代后，整套状态机成为死代码（`overview()` 里 `global ..., _OVERVIEW_REFRESHING` 也留着，见 :335） | 删除死状态机与残留 `global` 声明 |
| A4 | `frontend/src/components/RequireAuth.tsx:24` | `export default function RequireAuth` —— `App.tsx:6` 改为只 import `RequireRole` 后无人使用 | 删除（保留 `hasMinimumRole` 与 `RequireRole`） |
| A5 | `frontend/src/types/api.ts:37-38` | `ERR.LLM_UNAVAILABLE / ERR.EXPR_INVALID` 新增但全前端零引用 | 使用或删除 |
| A6 | `frontend/src/api/client.ts:41` | `sanitizeApiMessage` 被 `export` 但只在同文件 `ApiError` 构造器内使用 | 去掉 `export`（或补单测） |
| A7 | `backend/app/api/v1/app_settings.py:54` | 设置默认数据仍含 `"api_keys": []`；前端 `settings.ts` 已删除对应类型与调用 | 后端同步移除该字段 |

### B. 单侧改动（改了后端没改前端，或反之）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| **B1** | `backend/app/api/v1/portfolio.py:57-104` | `_search_assets` 改为**只查本地 `instrument` 表**。实测 `instrument` 只有 `('stock', 5552)`，**没有任何 `instrument_type='etf'` 的行** → `/portfolio/search` 从此**搜不到 ETF**，而响应里还保留着 `"tracking_index": None` 的 ETF 分支。前端 `Portfolio` 页未同步（仍在按"股票+ETF"提示） | 要么把 ETF 灌进 `instrument` 表（同步流水线里补），要么保留原 ETF 远端目录作为兜底，并在两者皆空时返回明确 `unavailable` |
| **B2** | `backend/app/data/panels.py:165-190` + `backend/app/data/realtime.py:652-658` | `build_events` 改为"先查本地 `news_announcement` 表，空则 `fetch_events`"，而 `fetch_events` 已被改成**恒抛 `RuntimeError`**。实测 `news_announcement` 表 **0 行**且**全项目无写入方** → 个股详情「近期事件」块**永久 unavailable** | 二选一：① 在同步流水线里补写 `news_announcement`（`db/models.py:210` 表已存在）；② 恢复一个有界超时的远端公告源。当前状态是"功能被下线但没告诉任何人" |
| **B3** | `backend/app/data/realtime.py:499-511` | `fetch_north_holding` 改为**恒抛 `RuntimeError("北向持股暂无有界数据源")`** → 个股详情「北向持股」块永久 unavailable。注释承诺"由同步管线写入后读取"，但没有任何落地 | 同 B2：要么补数据源写入，要么在前端明确标注该块已下线（现在会显示"数据源暂时不可用"，用户无法区分"暂时"和"永远"） |
| B4 | `backend/app/api/v1/watchlist.py:320,343` | 新增 `summary.flow_status`（`ok/degraded/unavailable`）与 `items[].status`，前端 `types/p1.ts` 的 `WatchlistQuote` / 看板 summary 未扩展 | 补类型；`status='unavailable'` 时前端应显示明确空态而非 0 |
| B5 | `backend/app/api/v1/etf.py`（4 个端点） | 新增 `data_freshness` / `coverage` / `message` / `status`，`frontend/src/pages/Etf*` **本次 0 改动**，全部不消费 | ETF 中心 + 详情页补「数据不可用原因 + 更新时间」展示，否则 4.5s 后直接白屏 |
| B6 | `backend/app/api/v1/market.py:370-405` | `/market/overview` 的 `recommend` 新增 `as_of / reason / message / coverage / freshness`，`types/stock.ts:378-395` 只补了 `close/pct/amount` 与 `BlockBase` 的部分字段，`RecommendBlock` 本身未补 `status/message/coverage` | 补 `RecommendBlock`（`AiPicksPanel.tsx:99-101` 已在读 `recommend.message` / `as_of`，靠的是 `any` 之外的隐式结构，类型层面是缺的） |
| B7 | `backend/app/api/v1/datacenter.py:411-428` | `refresh=1` 语义从"立即回旧值 + 后台重扫"改为"同步强制重算"，响应不再返回 `refreshing` 字段；`frontend/src/types/datacenter.ts:21-22` 仍声明 `refreshing?: boolean` | 删除前端残留类型；如后端确要保留异步语义需重设计 |
| B8 | `backend/app/api/v1/screener.py:127` vs `frontend/src/pages/Screener/*` | 见 P0 #10①：字段语义改了但字段名/取值/后端逻辑未动，前端说明文字写的是 `weak/neutral/strong`，实际是 `low/mid/high` | 后端把 `risk` 字段改名为 `signal_strength` 并把取值改为 `weak/neutral/strong`，前后端一次改干净 |

### C. 配置漂移（代码读了但文档/模板没写，或反之）

| # | 位置 | 问题 |
|---|---|---|
| C1 | `backend/app/core/config.py:79-82` | 新增 `FEATURE_VERSION`，**`.env.example` 未收录**。留空=隐式按 mtime 自动选版本（见 P0 #1 附带风险） |
| C2 | `backend/app/core/config.py:52-55` | 新增 `METRICS_REQUIRE_AUTH`（+ `metrics_require_auth` property，`main.py:190` 读取），**`.env.example` 未收录**。部署方无从得知这个开关 |
| C3 | `backend/app/api/v1/stock.py:68` | `_PANEL_BLOCK_TIMEOUT` 默认值 **20 → 4.5**（环境变量 `AQP_PANEL_BLOCK_TIMEOUT`），`.env.example` 无此项，README/文档未提。默认值腰斩属行为变更 |
| C4 | `config.py:100-103` `WARM_OVERVIEW_ON_STARTUP` | 已有开关，`.env.example` 未收录；`tests/conftest.py:171` 从 `setdefault` 改为强制 `os.environ[...]="0"`，说明这个开关是测试隔离的关键，但对外无文档 |

### D. 重复实现 / 双口径

| # | 位置 | 问题 |
|---|---|---|
| D1 | `frontend/src/types/datacenter.ts:21-22` vs 后端 | `refreshing` 字段：后端已不再产出，前端类型仍声明 → 类型与实现双口径 |
| D2 | `backend/app/services/market_service.py:66` | `_build_money_flow = build_money_flow` —— 为兼容旧 import 名留的别名；`market.py:37` 用 `# noqa: F401` 导入。建议直接用新名，去掉别名层 |
| D3 | ETF 缓存键 | `cache/keys.py:29-47` 新增 4 个 `k_etf_*`，与 `etf.py` 内部原有的 `NS` 手工拼键（`etf.py:29` 仍 `from ...cache.keys import NS`）并存 → 两套键生成方式 |
| D4 | SSE 重试上限 | `useNotifyStore.ts:12` `MAX_RETRIES=3` 与后端 ticket 60s TTL 是两套独立的"过期"概念，前端耗尽后无自愈入口（见 P0 #5 瑕疵） |

### E. 遗留标记 / 文档陈旧

| # | 位置 | 内容 |
|---|---|---|
| E1 | `frontend/src/stores/useWatchlistStore.ts:3` | 注释"后端 watchlist 接口尚未实现，一期先本地持久化"—— 后端 `/watchlist/*` 端点早已存在（本次还大幅重构），注释与现实不符 |
| E2 | `backend/app/core/pipeline_lock.py:12` | "跨 worker 部署需配合 Redis 分布式锁，**尚未实现**" —— 已知未实现，属技术债登记，非本轮新增 |
| E3 | `README.md:110-118` | 本次已更新角色说明与接口表 ✅，但 `frontend/src/api/market.ts:8` 仍写着"实测 overview?refresh=1 冷算约 49.5s"的旧注释，与新的 6s 预算矛盾 |

---

## 三、本次改动引入的新问题

### 🔴 N1（确定性失败）：一条既有测试必然挂

```python
# backend/tests/test_write_endpoints_smoke.py:538-552
def test_settings_apikeys_rotate_writes_isolated(client: TestClient, tokens) -> None:
    body = client.post("/api/v1/settings/apikeys/rotate", headers=tokens["admin"]).json()
    _ok(body)                                                    # ← assert code == 0
    raw = data.get("key")
    assert isinstance(raw, str) and raw.startswith("aqpx_"), body # ← 必然失败
```

而后端已改为：

```python
# backend/app/api/v1/app_settings.py:254-261
return fail(ERR_PARAMS, "API Key 功能未启用：平台当前不验证此类密钥，不能生成可用凭证")
```

`tests/test_write_endpoints_smoke.py` **不在本轮 60 个修改文件里** → 改了后端没改测试。这是本轮唯一一处**可以确定的、必然的**回归。

### 🔴 N2：降级结果被缓存，一次超时锁死 5 分钟

`market.py:859-861`、`etf.py:96-99`、`watchlist.py:381-384`、`datacenter.py:420-427` 都把"超时/异常 → 结构化空态"包在 `build()` 里交给 `cached_or_build`：

```python
# backend/app/cache/swr.py:129-132（未修改）
data = await build()                      # ← build 内部已把 TimeoutError 吞成 degraded
await write_cache(key, data, ttl, stale_window)
```

后果：任何一次冷启动 / 外部源抖动，**全站用户连续 5 分钟（watchlist 60s、rt 块 45s）看到"不可用"**，且不会自动重试（SWR 的后台重建只在主键过期后触发，而刚写进去的主键是"新鲜的空态"）。
建议：`cached_or_build` 增加 `valid: Callable[[dict], bool]` 谓词，或在各 `fallback` 里附加短 TTL（如 15s）后重建。

### 🔴 N3：两处功能被静默下线（B2 / B3）

`panels.py:165` + `realtime.py:652`（公告）与 `realtime.py:499`（北向持股）。作者本意是"外部源无界 → 明确声明不可用"，这是正确的**原则**，但执行时**没有补上被依赖的写入方**，导致：

- 本地 `news_announcement` 表 0 行（只读 SQL 实测），且全项目无写入方 → 公告块 100% 空
- 北向持股 100% 空
- 前端仍按"数据源暂时不可用"展示，用户和运维都无法区分"暂时"与"永久下线"

### 🟠 N4：`/datacenter/sync/fetch` 预检存在 TOCTOU 窗口

`datacenter.py:885-893` 预检持锁后立即释放，`_worker` 线程随后才去申请锁。两个并发请求可同时通过预检，第二个在后台抛 `PipelineBusy` 被 `_worker` 的 `except` 吞成 `_sync.error`，**对外仍返回 `{"started": true}`** —— 又变成了一次"伪启动"，与本次修复的初衷相悖。

### 🟠 N5：`_build_recommend` 的 None 解引用隐患（已加保护但语义变窄）

```python
# backend/app/api/v1/market.py:337-348
close_value = bdf["close"][-1]
close = float(close_value) if close_value is not None else None
if bdf.height >= 2 and bdf["close"][-2] not in (None, 0):
    pct = round((close / float(bdf["close"][-2]) - 1) * 100, 2) if close is not None else None
```

已加保护，但 `bdf["close"][-2] not in (None, 0)` 用的是 `not in` 成员判断，若 `bdf["close"][-2]` 是 `float('nan')`，`nan not in (None, 0)` 为 `True` → `pct` 变成 `nan`，会一路透传到响应体 JSON（`orjson` 会写成 `null`，可接受）。建议显式 `math.isfinite`。

另外：`market.py:279` 把「指定日期无分区」从"回退到最新分区"改为 `files = []` → 返回 `unavailable`。语义更诚实了（赞），但**前端 `AiPicksPanel` 的 `recommend.date` 与新增的 `as_of` 出现分叉**（`date` 来自文件名，`as_of` 来自 parquet 内日期），两者可能不同。

### 🟠 N6：`_fund_flow_total` 新增 12 只硬上限

```python
# backend/app/api/v1/watchlist.py:226-229
if len(stocks) > 12:
    return None, "unavailable"
```

自选超过 12 只股票时，资金流合计**直接声明不可用**。前端未展示这个阈值原因（`flow_status='unavailable'` 未消费，见 B4）→ 用户只会看到"资金流 -"，不知道是自选太多。需要在 UI 上说明或改为抽样估算并标注口径。

### 🟡 N7：`market.py` 内部运行时 import 造成模块耦合倒置

```python
# backend/app/api/v1/market.py:403
from .screener import _freshness
```

`market` 反向依赖 `screener` 的私有函数，且是**函数内运行时 import**（循环依赖风险被延后到运行期）。建议下沉到 `services/` 或 `domain/`。

### 🟡 N8：异常被吞且只留类名

`etf.py:539/583/669/684` 仍把 `type(e).__name__` 拼进对外文案（如 `f"K线数据暂时不可用: {type(e).__name__}"`）。本轮已经在 `market.py`、`errors.py`、`client.ts:41` 做了异常脱敏，但 **ETF 模块没跟上**，形成脱敏口径不统一。

### 🟡 N9：`stats` 在 `_finalize_screener_payload` 中被整体重建

```python
# backend/app/api/v1/screener.py:196-202
data["stats"] = {
    "today": _stats(items, pool_size),
    "prev": stats_block.get("prev"),
    "prev_date": stats_block.get("prev_date"),
}
```

`pool_size` 取自 `today_stats`，但 `items` 已被过滤。若旧缓存里的 `stats.today` 结构不含 `pool_size`（老版本缓存未失效），`pool_size` 会退化成 0 → 胜率/占比分母为 0。建议对 `pool_size` 缺失做显式降级。

---

## 四、收尾清单（按优先级）

### P0-A：立刻做（半天内，都是"改一半"的直接后果）

| # | 文件 | 改法 | 工作量 |
|---|---|---|---|
| 1 | `backend/tests/test_write_endpoints_smoke.py:538-552` | 把 `test_settings_apikeys_rotate_writes_isolated` 改为断言 `code == ERR_PARAMS` 且 `data is None`，并校验"不再写库"；或整条删除并替换为"接口已下线"契约测试 | 小 |
| 2 | `backend/app/data/panels.py:165-190` + `backend/app/data/realtime.py:652` | 二选一：① 在同步流水线里补写 `news_announcement`（`db/models.py:210` 表已存在）——推荐；② 若决定永久下线，把 `build_events` 的兜底改成返回带明确文案的 `unavailable`，并在前端事件卡固定展示"公告数据源已下线" | 中 |
| 3 | `backend/app/data/realtime.py:499-511` | 同 #2：要么补北向持股落库（可复用 `universe_daily` 之外的独立分区），要么前端「北向持股」卡改为持久下线说明 | 中 |
| 4 | `backend/app/api/v1/portfolio.py:57-104` | 让 ETF 可搜：在 instrument 同步流程里写入 `instrument_type='etf'`（`E.build_catalog()` 的 cn 部分即可），或保留远端目录作为本地无结果时的兜底 | 中 |
| 5 | `backend/app/api/v1/screener.py:127` + `frontend/src/pages/Screener/*` + `types/p1.ts` | 一次性改干净：后端字段 `risk` → `signal_strength`，取值 `low/mid/high` → `weak/neutral/strong`，前端 `RISK_OPTIONS` / `RISK_MAP` / 类型同步 | 小 |

### P0-B：本周内（把 P0 从"部分"推到"已修"）

| # | 文件 | 改法 | 工作量 |
|---|---|---|---|
| 6 | `backend/app/ml/monitor.py:107-124` | 把 `_signature("features")` / `_features_frame` 切到 `data/features.py:read_feature_frame`，或至少按 `FEATURE_VERSION` 限定单版本；`_close_wide` 前的重复行断言也要加 | 小 |
| 7 | `backend/app/core/config.py:79-82` | `FEATURE_VERSION` 显式化：`.env.example` 收录 + 若留空则把 `logger.info` 升为 `warning` 并把选中的版本写入响应体 | 小 |
| 8 | `backend/app/data/ingest/akshare_adapter.py:85-102` | 给 `_safe_call` 加**单次调用超时**（`httpx` 层或 `signal`/线程中断），并把 `AKSHARE_RETRY` 的指数退避纳入端点预算计算；否则 5s 预算形同虚设 | 中 |
| 9 | `backend/app/cache/swr.py:69-139`（或各调用方） | 增加"降级结果不缓存 / 短 TTL 缓存"能力（如 `valid=` 谓词或 `degraded_ttl=`），覆盖 `market.py` / `etf.py` / `watchlist.py` / `datacenter.py` 四处 | 中 |
| 10 | `backend/app/core/compute_guard.py:10-20` | 研究相关端点改为**有界排队**（`asyncio.Semaphore` + 等待超时）而非直接抛错；或至少在 40103 响应里带上 `retry_after` 与当前队列长度 | 中 |
| 11 | `backend/app/core/config.py:40-47 / 123-126` | 把 `ADMIN_TOKEN` 默认值改为空串（空则 dev 首次启动自动生成随机串并写 `.env`），`ALLOW_REGISTRATION` 默认 `false`，`.env.example` 同步 | 小 |
| 12 | `backend/pytest.ini:9` | `faulthandler_timeout` 从 600 降到 120（配合 `network` marker 已隔离外网用例，离线用例不应超过 2 分钟）；或直接引入 `pytest-timeout` 并按 marker 分级 | 小 |
| 13 | `frontend/src/pages/Etf*` + `EtfCenter` | 消费新增的 `data_freshness` / `status` / `message` / `coverage`，把"4.5 秒后白屏"变成"明确的不可用原因 + 数据时间" | 中 |

### P1：本轮改动的卫生收尾

| # | 文件 | 改法 | 工作量 |
|---|---|---|---|
| 14 | A1–A7 共 7 处孤儿代码 | 直接删除（A1/A2/A3/A4/A6/A7）或使用（A5） | 小 |
| 15 | B4 / B7 / D1 类型同步 | `types/p1.ts` 补 `flow_status` / `status`；`types/datacenter.ts` 删 `refreshing` | 小 |
| 16 | C1–C4 配置漂移 | `.env.example` 补 `FEATURE_VERSION` / `METRICS_REQUIRE_AUTH` / `WARM_OVERVIEW_ON_STARTUP` / `AQP_PANEL_BLOCK_TIMEOUT` | 小 |
| 17 | `backend/app/api/v1/datacenter.py:885-893` | 消除 TOCTOU：把 `pipeline_slot` 的申请与线程启动合并（在锁内 `t.start()`），或让 `_worker` 的 `PipelineBusy` 可被 `/sync/status` 观测 | 小 |
| 18 | `backend/app/api/v1/etf.py:539/583/669/684` | 移除对外文案里的 `type(e).__name__`，与 `market.py` / `errors.py` 的脱敏口径统一 | 小 |
| 19 | `frontend/src/stores/useNotifyStore.ts:12,63-66` | 给"重试耗尽"补一个可见的「重新连接」入口（重置 `retryCount` 后 `open()`） | 小 |
| 20 | `backend/app/api/v1/watchlist.py:226-229` | 12 只上限要么提高（并发已受限 4，30 只也可控），要么在响应里带上 `flow_limit=12` 供前端解释 | 小 |
| 21 | E1 / E3 注释陈旧 | 更新 `useWatchlistStore.ts:3` 与 `api/market.ts:8` | 小 |
| 22 | `backend/` 根目录 13 个 `pytest_*.txt` / `hotpath_benchmark_*.txt` | 清理或加入 `.gitignore` | 小 |

---

## 五、本轮做得好的部分（建议保留）

1. **SSE ticket 设计是本轮最高质量的改动**。威胁模型（URL 泄漏 / 重放 / 上下文混淆 / 跨存储域重放）逐条对应到实现，`r.` / `m.` 前缀区分存储域、Redis 不可用时不回落本地副本、失败语义统一为 40100 不泄露 ticket 状态——这是一份可以作为范例的授权委托实现。
2. **前端 Research 页重构**：`ComputeQueue` + `AbortController` + `mountedRef` + 分块独立失败/重试，把"一个 reject 整页死"彻底解决了。
3. **统一可用性契约**（`status / as_of / reason / message / coverage / items`）在 `market.recommend` 与 `screener` 上落地得很干净，`test_data_freshness_degradation.py` 的 23 条断言覆盖了全部分支。
4. **"不伪造数据"红线守得住**：所有超时/异常路径都返回结构化空态 + 明确文案，没有一处用估算值或旧值冒充实时行情。
5. **错误码撞码修得干净**：`ERR_PIPELINE_BUSY` 40104 → 40900，`ERR_CREDENTIALS` 保持 40104，且有 `test_route_permission_contract.py:114-116` 固化唯一性；`ROLE_PERMISSIONS` 死矩阵已彻底删除（全项目 grep 0 残留）。
6. **Prometheus 标签基数治理**：`main.py:108-118` 用路由模板替代原始 URL，`/unmatched` 兜底，`METRICS_REQUIRE_AUTH` 生产默认收紧。
7. **新增 8 个测试文件无一空壳**，总计 118 条断言、0 skip。
8. **`tsc --noEmit` 实测 0 error**（`Settings/index.tsx` 此前阻断 tsc 的 JSX 语法错误已修复）。

---

## 六、审核方法与局限

**已执行的验证**

- `git status --porcelain` / `git diff --stat`（60 M + 32 ??）
- 逐模块 `git diff -- <path>`：后端 15 个业务模块 + core/cache/data + 8 个测试；前端 api/components/pages/stores/types 全部
- `Grep` 全项目引用计数，判定孤儿代码与单侧改动（17 组关键词）
- 只读 SQL 实测：`instrument` 表类型分布、`news_announcement` 行数
- `ast.parse` 全量扫描 `backend/app/**/*.py` → **0 语法错误**
- `npx tsc --noEmit` → **0 error**
- 新增测试文件断言/skip 统计
- 读取本轮 7 份 `*-fix-report.md` 作为"意图来源"，全部回源码复核

**未执行（按分工交给 QA / 明确禁止）**

- 未运行全量 pytest（历史会挂，已交给 QA）
- 未运行 `vite build`、未启动后端实例做端到端实测
- 未修改任何业务源码

**判定口径说明**

- **已修复**：代码路径已按审计建议改到位，且有测试或实测证据
- **部分修复**：主要症状缓解，但根因未消 / 只改了一侧 / 引入了新的等价问题
- **未动**：源码与 09-14 审计时一致
- **无法验证**：本轮**没有**任何一条 P0 落在这一档——所有 10 条都能在工作区里直接读到代码
