# AQP 上线前全检 · 修复补丁规格（Turnkey Patch Spec）

**日期**：2026-09-30
**上游**：[`pre-launch-check-full-2026-09-30.md`](./pre-launch-check-full-2026-09-30.md)
**用途**：把行动清单 A1–A13 落成**可直接套用**的 diff，使实施成为机械动作。
**状态**：**尚未应用到任何生产代码** —— 本文件是规格，不是变更。执行前请确认下方「⚠️ 需先裁决」三项。

> **实施顺序**：严格按 P0-0 → P0-1 → P0-2 → P0-3 → P0-4 → P0-5。每项独立可回滚、独立可验收。

---

## ⚠️ 需先裁决（否则不要开始）

| # | 事项 | 影响哪一项 |
|---|------|-----------|
| 1 | **服务是否只在本机/内网可达？** | 决定 F-12/F-13 是 P1 还是 P0；也决定 `client_max_body_size` 是否必须 |
| 2 | `RBAC_ENFORCE=False` 是否仍为目标姿态？ | 决定 P0-0 的鉴权是否真能挡住匿名 DoS（**若维持 False，仅加 `require_role` 仍可被自助注册绕过**，需同时关 `ALLOW_REGISTRATION`） |
| 3 | **三个 overview 端点是否保持匿名？** | 决定 P0-0 是"补鉴权"还是"直接下线" |

---

## P0-0 · 三个 overview 端点补鉴权（一行级）

**文件**：`backend/app/api/v1/market.py`
**依据**：`require_role` 已在 `:29` 导入、`:648`/`:679` 已用于同类端点 ⇒ 属遗漏。

```diff
@@ :766  /overview/rt
 @router.get("/overview/rt", response_model=APIResponse[dict])
 async def market_overview_rt(
     refresh: int = Query(0, ge=0, le=1, description="1=跳过缓存读强制重算（5s 防抖）"),
+    _user: dict = Depends(require_role("viewer")),
 ) -> APIResponse[dict]:

@@ :830  /overview/daily   （同一改法）
+    _user: dict = Depends(require_role("viewer")),

@@ :895  /overview（DEPRECATED）   （同一改法）
 @router.get("/overview", response_model=APIResponse[dict])
 async def market_overview(
     recommend_k: int = Query(50, ge=1, le=200, description="推荐榜条数"),
     date: str | None = Query(None, pattern=r"^\d{8}$", description="..."),
     refresh: int = Query(0, ge=0, le=1, description="..."),
+    _user: dict = Depends(require_role("viewer")),
 ) -> APIResponse[dict]:
```

**替代方案（推荐给 `/overview`）**：该端点 **前端零调用**（`api/market.ts` 只有 `overviewRt`/`overviewDaily`）。但 `market.py:905-912` 说明它被 `warm_overview_cache` 与 `tests/test_overview_heal_chain.py` 依赖 ⇒ **不要删路由**，**只补鉴权**即可（预热走内部函数调用，不经过 HTTP）。

**验收**：无 `Authorization` 头请求三端点 ⇒ 业务码 `40100`（与 `/stock/search` 同口径）。

---

## P0-1 · 重计算改专用线程池 + daemon 线程（治 B1 + B7）

**问题**：默认池 `min(32, cpu+4)=22` 槽且全站共用（含 `/health/ready`）；且 `ThreadPoolExecutor` worker **非 daemon**，阻塞时阻止进程退出。

**新增文件**：`backend/app/core/compute_pool.py`

```python
"""重计算专用线程池：与全站默认池隔离，且 worker 为 daemon。

为什么必须隔离（2026-09-30 全检，头号 P0）：
``asyncio.wait_for`` 到期**只取消外层 await**，``to_thread`` 里阻塞在 socket read
的 worker **不可取消**、继续占槽。默认池仅 min(32, cpu+4) 槽且全站共用
（含 /health/ready 探针）⇒ 2~11 个并发冷算即可打满，探针假死、容器 unhealthy。

为什么必须 daemon（B7）：``asyncio.run`` 收尾会 join 池内 worker；
``ThreadPoolExecutor`` 的 worker 自 Python 3.9 起为**非 daemon**，
阻塞 worker 会让进程**无法退出**（实测 rc=124）⇒ docker stop 挂到 SIGKILL。
"""
from __future__ import annotations

import concurrent.futures
import threading

COMPUTE_CONCURRENCY = 6


class _DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """把 worker 线程置为 daemon，使阻塞线程不阻止解释器退出。"""

    def _adjust_thread_count(self) -> None:  # noqa: D102
        super()._adjust_thread_count()
        for t in list(self._threads):  # type: ignore[attr-defined]
            t.daemon = True


COMPUTE_POOL = _DaemonThreadPoolExecutor(
    max_workers=COMPUTE_CONCURRENCY, thread_name_prefix="aqp-compute")


def shutdown_compute_pool() -> None:
    COMPUTE_POOL.shutdown(wait=False, cancel_futures=True)
```

**改调用点**（把 `asyncio.to_thread(fn, ...)` 换成 `loop.run_in_executor(COMPUTE_POOL, fn, ...)`）：

| 文件:行 | 现状 | 改为 |
|---|---|---|
| `market.py:782` | `await asyncio.wait_for(asyncio.to_thread(_build_rt, td), timeout=RT_BUILD_TIMEOUT_SECONDS)` | `await asyncio.wait_for(loop.run_in_executor(COMPUTE_POOL, _build_rt, td), timeout=RT_BUILD_TIMEOUT_SECONDS)` |
| `market.py:943` | 同上（`_build_overview`） | 同上 |
| `etf.py:207` | 同上 | 同上 |

（`loop = asyncio.get_running_loop()` 在函数体内取一次。）

**`main.py` lifespan（`:44-45`）**：在 `yield` 之后加 `shutdown_compute_pool()`。

**验收**：复跑 QA 的"22 路 `?refresh=1` → 立即打 `/health/ready`"实验，要求**探针全程 200 且耗时回落基线**；再跑 `verify_exit_hang.py` 模式的最小脚本，要求**进程能自然退出**（`rc=0`）。

> ⚠️ **`_adjust_thread_count` 是 CPython 私有方法**。若不愿依赖私有 API，替代方案：用 `threading.Thread(daemon=True)` 手写一个极简池，或在 `atexit` 里 `os._exit()` 兜底。**两条路都必须实测"进程能退出"**。

---

## P0-2 · manifest footer 扫描并行化（34×）

**文件**：`backend/app/data/parquet_store.py::_scan_files_entry`（`:314-335`）
**现状**：`for f in files:` 串行 `pq.ParquetFile(f)` ⇒ 36500 文件 **283.8s** > 240s 全局兜底 ⇒ `/datacenter/datasets` 必然 504。

```diff
 def _scan_files_entry(files: Iterable[Path], date_col: str | None) -> dict:
     """汇总一批 parquet 的 rows 与日期区间（单文件损坏按 0 计并留 WARNING）。"""
+    from concurrent.futures import ThreadPoolExecutor
+
     rows = 0
     lo: str | None = None
     hi: str | None = None
-    for f in files:
+
+    def _one(f: Path):
         try:
-            n, f_lo, f_hi = _parquet_rows_and_range(f, date_col)
+            return f, _parquet_rows_and_range(f, date_col)
         except Exception as e:  # noqa: BLE001 坏文件按 0 计，不阻塞扫描
             logger.warning(f"[manifest] 文件不可读已跳过: {f}（{e!r}）")
-            continue
+            return f, None
+
+    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="manifest-scan") as pool:
+        results = pool.map(_one, files)          # 保持输入顺序，结果确定性
+
+    for f, parsed in results:
+        if parsed is None:
+            continue
+        n, f_lo, f_hi = parsed
         rows += n
         if f_lo and (lo is None or f_lo < lo):
             lo = f_lo
         if f_hi and (hi is None or f_hi > hi):
             hi = f_hi
     entry: dict = {"rows": rows}
```

**验收**：实测 `_scan_files_entry` 全量耗时，要求 **< 30s**（实测并行 w=8 为 8.3s）。断言 `rows/first/last` 与串行版**逐字段相等**。

---

## P0-3 · Nginx 超时链 + 监控可达（一行级 × 4）

**文件**：`frontend/nginx.conf`

```diff
     location /api/ {
         proxy_pass         http://aqp-api:8000/api/;
         ...
-        proxy_read_timeout                 180s;
+        # 后端预算最大 660s（strategy-run）⇒ 必须 ≥ 660s + 余量，否则必然误杀长任务
+        proxy_read_timeout                 700s;
         proxy_connect_timeout              5s;
         proxy_buffering on;
     }
+
+    # 请求体上限：默认 1m 会截断大请求；但校验失败路径会把 body 写日志（见 B6），
+    # 故此处**收紧**而非放宽（1m → 256k 足够业务，且压缩日志洪泛面）
+    client_max_body_size 256k;
+
+    # 监控/探针必须真正反代到后端，否则落 location / 回 index.html ⇒ 假绿
+    location = /metrics { proxy_pass http://aqp-api:8000/metrics; access_log off; }
+    location = /health  { proxy_pass http://aqp-api:8000/health;  access_log off; }
+
+    # SSE 长连接：必须关 buffering 且给足读超时
+    location = /api/v1/notify/stream {
+        proxy_pass         http://aqp-api:8000/api/v1/notify/stream;
+        proxy_http_version 1.1;
+        proxy_buffering    off;
+        proxy_read_timeout 3600s;
+    }
```

**注意 `location = /healthz`（`:36-40`）**：当前是 nginx **硬编码 `return 200 "ok"`**，**从不碰后端** ⇒ **恒真假绿**。建议**删除该块**（改用上面的 `= /health`）。

**验收**：`curl -i http://127.0.0.1:8080/metrics` 返回 Prometheus 文本而非 HTML；`curl` 一个 >180s 的长任务不返回 Nginx HTML。

---

## P0-4 · `read_parquet_columns` 改 `scan_parquet`（6×）

**文件**：`backend/app/data/parquet_store.py::read_parquet_columns`（`:652-682`）
**现状**：逐文件 `pl.read_parquet_schema` + `pl.read_parquet` ⇒ 10924 文件 **19.8s**；`scan_parquet` **3.3s**。

**改法**：保留"跳过失配分区"的语义，但用**惰性扫描 + 一次 collect**：

```python
def read_parquet_columns(files: list[Path], columns: list[str],
                         *, date_cast: bool = True) -> pl.DataFrame:
    ok: list[Path] = []
    for f in files:                                   # 只读 footer，成本极低
        try:
            if all(c in pl.read_parquet_schema(f) for c in columns):
                ok.append(f)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[parquet_store] skip unreadable {f.name}: {e!r}")
    if not ok:
        return pl.DataFrame()
    lf = pl.scan_parquet(ok, missing_columns="insert")  # 允许列集演进
    df = lf.select(columns).collect()
    if date_cast and "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    return df
```

> ⚠️ `missing_columns="insert"` 需 **polars ≥ 1.0**；本项目版本请先确认（`pl.__version__`）。若版本不支持，退化为 `pl.concat([pl.read_parquet(f, columns=...) for f in ok])` 仍是 6× 收益的一部分。
> ⚠️ **必须先做行数一致性回归**：新旧实现输出行数/列集必须完全相等（历史上 polars 大版本有静默截断）。

**验收**：10924 文件读耗时从 19.8s 降到 ~3.3s，且结果 `equals()` 为 True。

---

## P0-5 · akshare socket 超时 + 退出兜底（同时治 B7）

**文件**：`backend/app/main.py` lifespan（`:44`），与 `data/ingest/akshare_adapter.py::_safe_call`

```diff
 # main.py lifespan 开头（日志初始化之后）
+    # akshare 内部用 requests（默认无 timeout）⇒ 对端挂死即永久占线程，
+    # 且不可被 asyncio.wait_for 取消（见 2026-09-30 全检头号 P0）。
+    import socket
+    socket.setdefaulttimeout(10)
```

**验收**：起服务后 `curl` 一个冷算端点，观察 worker 最终能返回（不再永久占用）；配合 P0-1 后进程可正常退出。

---

## B6 · 校验错误脱敏（合规必做）

**① `core/errors.py:43-64` — 剥离 `input`**

```diff
     out: list[dict[str, Any]] = []
+    # 只保留可安全外发的键：pydantic v2 会把**违规值本体**放进 input
+    # （超长 password、整个 body），实测 1MB body ⇒ errors 序列化 1,000,215 字符，
+    # 会写进 30 天日志并回显响应体（2026-09-30 全检 F-11/F-13）。
+    _SAFE_KEYS = ("type", "loc", "msg", "ctx")
     for err in errors:
-        item = dict(err)
+        item = {k: v for k, v in err.items() if k in _SAFE_KEYS}
         ctx = item.get("ctx")
```

**② `core/errors.py:230` — 响应体不回显明细**

```diff
-            content=jsonable_encoder(fail(ERR_PARAMS, "请求参数错误", errors)),
+            # 明细只进日志（已脱敏），响应体不回显 —— 避免把用户输入回吐给客户端
+            content=jsonable_encoder(fail(ERR_PARAMS, "请求参数错误")),
```

**③ `core/logging.py:60-79` — 两个文件 sink 显式 `diagnose=False` + 压缩**

```diff
     _add_file_sink(
         settings.LOG_DIR / "app.log",
         ...
         backtrace=True,
+        diagnose=False,          # 未显式传时 loguru 默认 True ⇒ 转储局部变量
+        compression="zip",       # rotation 只限单文件，30 天累积总量无上界
     )
     _add_file_sink(
         settings.LOG_DIR / "app.json.log",
         ...
         enqueue=True,
+        diagnose=False,
+        compression="zip",
     )
```

**④ 环境级兜底**：`backend/.env` 加 `LOGURU_DIAGNOSE=0`
（⚠️ 不是 `LOGURU_DISABLE`，该变量不存在 —— 见报告 R 系列勘误）

**⑤ `api/v1/auth.py` — 限速上移到路由级**

```diff
-@router.post("/login", response_model=APIResponse[dict])
+@router.post("/login", response_model=APIResponse[dict],
+             dependencies=[Depends(login_rate_limit_dep)])
 async def auth_login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
-    _check_rate_limit(req.username)
```

> ⚠️ 路由级 `dependencies=` **仍在校验之后**（FastAPI 先解析 body 再跑依赖）。要覆盖"校验失败"路径，**必须放到中间件**（读 `Content-Length` / 限流 key）。若接受"校验失败路径不占限速额度"，则路由级即可；**但那样 F-13 的日志洪泛面仍在** ⇒ 至少必须同时加 `client_max_body_size`（P0-3）。

**验收**：`POST /api/v1/auth/login` 传 `"A"*1048576` ⇒ 响应体**不含明文**、`app.log` 增量 **< 5KB**（原为 1,049,915 字节）。

---

## 验收清单（落地后逐项打勾）

| # | 验收项 | 判据 |
|---|---|---|
| 1 | 三端点鉴权 | 无 JWT ⇒ `40100` |
| 2 | 池隔离 | 22 路冷算期间 `/health/ready` 全程 200 且 <50ms |
| 3 | 进程可退出 | 压力后进程自然退出（`rc=0`，非 124） |
| 4 | manifest 并行 | 全量扫描 < 30s，且 rows/first/last 与串行一致 |
| 5 | Nginx | `/metrics` 返回指标文本；长任务不被 180s 砍 |
| 6 | parquet 读 | 10924 文件 < 5s，结果 `equals()` |
| 7 | 日志脱敏 | 1MB body ⇒ 响应无明文、日志增量 < 5KB |
| 8 | 回归 | 后端 1903 passed / 0 failed；前端 tsc 0 error |

---

> 本规格由软件工坊主理人基于 4 位专家实测结论汇编；**所有 diff 均为待评审草案，未应用于生产代码**。
> 关键决策（裁决三项）请由工程负责人确认后再执行。
