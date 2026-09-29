# B1 · 基础设施层审核报告（2026-09-18）

审核员：B1 批次（`core/*` 除 config/errors/trace、`db/*`、`cache/*`）
方法：逐文件通读 + rg 全仓交叉核对 + 定向单文件 pytest + 5 个隔离探针脚本（合成数据，不碰生产库/生产数据）
纪律：本报告每条「确定」均有实际运行输出；未跑通的一律标注「疑似，需验证」。

环境：Python 3.11.15 / `backend/.venv`；所有命令在 `backend/` 下执行，并带本沙箱必需的临时目录补丁：

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH="$base;$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
```

探针脚本放在 `backend/.tmp_testrun/_b1_probe_*.py`（该目录本来就是测试脚手架用的未跟踪目录，不会被 pytest 收集：文件名不以 `test_` 开头；PYTHONPATH 已包含它）。探针全文见 §5。

---

## 1. 逐文件结论

| 文件 | 结论 |
|---|---|
| `core/auth.py` | P1/P2 无。P3 两条：非 ASCII Bearer token 抛 `TypeError`（B1-7）；`JWT_EXPIRE_SECONDS` 只读 `os.environ`（见 §4-d）。SSE ticket 的 `r.`/`m.` 存储域隔离与 GETDEL 一次性语义实现正确，Redis 不可用时不回落 LRU（`auth.py:97-105`），**不是**缺陷。 |
| `core/compute_guard.py` | **P2（B1-1）：等待 slot 的请求被取消时永久泄漏 semaphore 名额**（机制 100% 复现；但本部署下取消路径罕见 ⇒ 严重度按 P2 报，触发条件一旦出现即升级 P1）。除此之外逻辑正确（超时有上限，默认 10s，`config.py:116-123`）。 |
| `core/events.py` | 未发现 P0–P2。`publish()`（非线程安全版）零调用方 = 死代码（B1-8）。 |
| `core/excel.py` | 未发现 P0–P2。`_sheet` 的 dict 分支永不执行（B1-8）。三个 workbook 函数均有调用方（`api/v1/export.py:31/62/88`）。 |
| `core/llm.py` | 未发现 P0–P2。超时 `LLM_TIMEOUT_SECONDS=60` 有默认值；异常统一转 `ERR_LLM_UNAVAILABLE` 且不泄漏 URL/密钥。 |
| `core/logging.py` | 未发现 P0–P2。三个 sink 均 `enqueue=True`（这正是 panic 堆栈必须先 format 成字符串的原因，`resilience.py`/`panic_guard.py` 已正确处理）。 |
| `core/metrics.py` | P3（B1-5）：13 个指标中 **7 个从未被写入**。 |
| `core/panic_guard.py` | **未发现 P0–P2**。逐条见 §3。 |
| `core/pipeline_lock.py` | 未发现 P0–P2。进程级互斥 + 单实例前提已在模块 docstring 明确写出，且 `main.py:83-87` 启动时调用 `warn_if_multi_worker()` 告警（§3）。`current_owner()` 零调用方 = 死代码（B1-8）。 |
| `core/resilience.py` | 未发现 P0–P2。**注意：本模块不是重试工具**（§3 第 5 条）。 |
| `db/session.py` | 未发现 P0–P2。`timeout=30` 已设（`session.py:63-66`）；`get_db` 的所有分支都会 close（§3 第 6 条）。 |
| `db/models.py` | P2（B1-3 的后果面）+ P2（B1-4：`Watchlist` 表无任何生产写入方）。其余表与索引未见问题。 |
| `db/models_auth.py` | `Role`/`User` 被 `api/v1/auth.py` 正常使用；`UserSession`/`ApiToken`（`models_auth.py:45-68`）零引用 = 死表 + 「JWT 撤销」未实现（B1-8）。 |
| `db/init_db.py` | **P2（B1-3）：声称「DB 级持久化 PRAGMA、所有连接自动继承」不成立**，`foreign_keys` 实际恒为 OFF。 |
| `db/kv.py` | 未发现 P0–P2。与主库同路径属有意设计；`mode=ro` 读 WAL 库实测可用（§3 第 7 条）；与 `task_store` 的超时口径一致。P3：与 ORM 侧时间口径混用（B1-9）。 |
| `db/migrations.py` | 未发现 P0–P2。表名/列名均为代码内字面量，无注入面。 |
| `cache/redis_client.py` | P2（B1-2：恢复后回退到更旧的 Redis 值且标为新鲜）。P3：无独立半开态（§3 第 4 条）、防抖锁双域不互斥（B1-10）。key 与序列化格式两种后端**兼容**（§3 第 3 条）。 |
| `cache/swr.py` | 未发现 P0–P2。stale 一定被标注 `stale=True`；`unavailable` 不落地、`degraded` 用 ≤15s TTL 且不留影子键（`swr.py:38-59,81-87`）。B1-2 的回退发生在它下游的 `get()`，不是本文件逻辑错。 |
| `cache/keys.py` | **未发现死常量**：14 个 `k_*` 全部有 app 侧调用方（§3 第 8 条）。 |
| `cache/memory.py` | 未发现 P0–P2。 |

---

## 2. 问题详述

### B1-1（P2 / Bug / 确定·机制；触发条件低频 ⇒ 触发出现即升级 P1）`compute_guard` 在请求被取消时永久泄漏一个并发名额

**位置**：`backend/app/core/compute_guard.py:26-35`

```python
timeout = get_settings().COMPUTE_ACQUIRE_TIMEOUT_SECONDS
acquired = await asyncio.to_thread(_slots.acquire, True, timeout)   # L29
if not acquired:
    raise AQPException(ERR_RATE_LIMITED, ...)
try:
    yield
finally:
    _slots.release()                                                # L35
```

**机制**：`asyncio.to_thread` **无法取消已经在线程池里跑的工作项**。当协程在 L29 等待期间被取消时：线程继续等锁 → 稍后真的拿到名额 → 但协程已被取消，永远不会执行 L32-35，`release()` 永不发生。名额就此永久丢失。

**为什么后果是致命的**：`COMPUTE_CONCURRENCY` 被硬性限制为 1–2（`config.py:112-115`，`ge=1, le=2`）。**两次**这样的取消就能把闸门清零，此后 14 个挂了 `Depends(compute_slot)` 的端点（`export.py:60,81`、`backtest.py:260,638,693`、`desk.py:300`、`portfolio.py:108`、`research.py:168,195,216,288,398,477,529`）全部恒返回 `ERR_RATE_LIMITED(40103)`，直到进程重启。

**触发条件（我原本的第一个假设是"客户端断开"，实测**不成立**，如实记录）**：
- ❌ **客户端断开不会取消处理任务**（本栈实测）：uvicorn 0.30.6 在 `connection_lost` 里只把 `cycle.disconnected` 置真、由应用自己轮询 `http.disconnect`（`uvicorn/protocols/http/h11_impl.py:110-111`、`httptools_impl.py:113`），**不 cancel 任务**；Starlette 0.38.6 的 `BaseHTTPMiddleware` 也只在下游读 `receive` 时把 `http.disconnect` 递下去（`.venv/.../starlette/middleware/base.py:114-131`），没有对运行 app 的 `coro` 做取消。⇒ **在 `main.py:172` 的 timing 中间件下，前端切页/刷新/网关断连都不会触发本缺陷。**
- ⚠️ 真正会命中的取消来源：① `uvicorn --timeout-graceful-shutdown` 超时后 uvicorn 会 `t.cancel()` 所有在飞任务（`uvicorn/server.py:273-286`）——**当前 Dockerfile/run.md 都没设该参数（默认 None ⇒ 永不取消），所以现在打不到，但运维一旦补上就是活雷**；② 换 ASGI 服务器（hypercorn/granian 等会取消断连任务）；③ 代码里将来给含此依赖的请求加 `asyncio.wait_for` 外层超时；④ 测试/脚本里显式 `task.cancel()`。
- **升级判据**：一旦出现任一上述取消来源，本条立刻是 **P1**——因为 `COMPUTE_CONCURRENCY` 上限只有 2、14 个端点受同一闸门约束、且泄漏**不可自愈**（只能重启进程）。

**验证（实跑，脚本 `_b1_probe_e_compute_leak.py`，全文见 §5）**：

```powershell
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\_b1_probe_e_compute_leak.py"
```

```
[1] the waiting task was cancelled (trigger-agnostic probe)
[2] semaphore counter after the slot was freed -> 0
[3] a fresh compute_slot can still get in? -> False
[4] reproduced: new request rejected with AQPException 计算资源繁忙，请稍后重试
```

**最小验证方法**：把 `config.COMPUTE_CONCURRENCY` 设为 1，用 TestClient 起两个并发 compute 请求，让第一个占住 slot、第二个在等待中 `await asyncio.sleep` 超时被取消（或直接 `task.cancel()`），再发第三个请求——修复前必得 40103，修复后应正常返回。

**修复方向（不建议用 shield 了事）**：`asyncio.shield` 只能保住 `await`，保不住资源的归还。正确做法是把「拿到名额」这件事的**所有权**留下：

```python
fut = asyncio.ensure_future(asyncio.to_thread(_slots.acquire, True, timeout))
try:
    acquired = await asyncio.shield(fut)
except asyncio.CancelledError:
    def _give_back(f):                      # 线程后来拿到了名额 -> 立刻归还
        if not f.cancelled() and f.exception() is None and f.result():
            _slots.release()
    fut.add_done_callback(_give_back)
    raise
```

或把闸门改成「线程内 acquire + 计时器自动过期」的令牌模型。

---

### B1-2（P2 / Bug·一致性 / 确定）Redis 恢复后回退到比 LRU 更旧的 Redis 值，且被当作新鲜数据返回

**位置**：`backend/app/cache/redis_client.py:104-118`（`get` 在 Redis 可用时**只**读 Redis）、`162-164`（降级期间 `set` 只写 LRU 后提前 return）、`215-227`（`get_stale`）；消费方 `cache/swr.py:178-191`。

**机制**：
1. 故障前 Redis 里有关键 `k` 的旧值 `OLD`，TTL 尚未到期；
2. 故障期间 `set(k, NEW)` 只写进进程内 LRU（L162-164 `r is None → return`）；
3. Redis 恢复后 `_ensure()` 复用**同一个** client（`_client` 非空即不重建），`get(k)` 命中 Redis 的 `OLD`，**不回看 LRU**；
4. `get_stale()` 返回 `(OLD, is_stale=False)` → `cached_or_build` 标 `from_cache=True` 且**不带 `stale`**（`swr.py:182-191`），前端看到的就是「新鲜」的旧数据。

回退窗口 = 故障前那个键的剩余 TTL。按 `swr.py:31-32` 自己的实测记录：`stock.north=21600s`、`stock.events=3600s`，即最长可达 **6 小时**的「旧值冒充新鲜」。

**验证（实跑，`_b1_probe_c_redis.py`）**：

```powershell
$env:REDIS_ENABLED='true'  # 探针默认就走 Redis 分支
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\_b1_probe_c_redis.py"
```

```
[1] before outage, get() -> b'OLD'
[2] during outage: LRU holds -> b'NEW' | get() -> b'NEW'
[3] after recovery: get() -> b'OLD'          <-- 比 LRU 更旧
[4] after recovery: get_stale() -> (b'OLD', False)   <-- 且标为新鲜
```

**最小验证方法**：`docker stop aqp-redis` → 请求一次 `/stock/north`（写 LRU）→ `docker start aqp-redis` → 立刻再请求，响应体应出现更新后的字段却带 `from_cache=true, stale` 缺失。或用上面的探针。

**修复方向**：①`get()` 在 Redis 命中时同时比较 LRU 条目的写入时间戳，LRU 更新则回 LRU（需要在 `lru_set` 旁存一个单调时间）；②更简单：降级期间 `set` 同时把键记入一个「本地脏键集」，恢复后 `get()` 对脏键强制读 LRU（或先 `delete` 掉 Redis 副本导致一次 miss 重建）。②的代价是一次重建，语义最安全。

**顺带结论（本批重点直接问答）**：两种后端的 **key 与序列化格式是兼容的**——`RedisClient.set` 对两个存储写的是同一份 `orjson.dumps(data)` 字节、同一批 key（含 `:swr` 影子键，`redis_client.py:159-161`），`get`/`get_stale`/`delete`/`ttl` 的 key 口径也完全一致。因此**不存在「降级后 key/格式错配读到脏数据」的问题**；唯一真实的跨域风险就是上面这条「源的优先级」问题。

---

### B1-3（P2 / Bug·一致性 / 确定）`init_db` 的 `foreign_keys`/`synchronous` 只作用于初始化连接，外键约束实际全程关闭

**位置**：`backend/app/db/init_db.py:40-44`，docstring `init_db.py:5-12`（"DB 级持久化 PRAGMA（WAL 等只需执行一次，之后所有连接自动继承）"）；连接创建点 `backend/app/db/session.py:57-75`。

SQLite 的 PRAGMA 里只有 `journal_mode=WAL` 是**写进库头、持久化**的；`synchronous`、`busy_timeout`、`foreign_keys` 都是**逐连接**的。`engine.begin()` 结束后这条连接被归还/关闭，后续每条连接都回到默认值。

**验证（实跑，`_b1_probe_d_pragma.py`，用临时 SQLITE_URL 走真实 `init_database()`）**：

```powershell
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\_b1_probe_d_pragma.py"
# init_database() 内部确实执行了 PRAGMA journal_mode=WAL / synchronous=NORMAL /
# busy_timeout=30000 / foreign_keys=ON（SQLAlchemy echo 可见），随后：
```

```
[fresh connection] PRAGMA journal_mode = wal        <- 持久化 OK
[fresh connection] PRAGMA synchronous = 2           <- FULL，不是 NORMAL
[fresh connection] PRAGMA busy_timeout = 30000      <- 来自 session.py 的 connect_args timeout=30
[fresh connection] PRAGMA foreign_keys = 0          <- ON 没生效
```

**影响**：
- `foreign_keys` 恒为 OFF ⇒ `models.py:85` 声明的 `watchlist.symbol → instrument.symbol` 外键**在任何应用/worker 连接上都不被强制**，孤儿行可静默写入（`db/kv.py:4-5`、`task_store` 等「WAL + busy_timeout 由 DB 级 PRAGMA 保证」的注释同理不准确，只是被 `connect_args["timeout"]=30` 掩盖了）。
- `synchronous` 实际是 FULL（更安全但更慢），与注释不符——属性能口径问题。

**最小验证方法**：上面这条命令；或 `sqlite3 data/sqlite/aqp.db "PRAGMA foreign_keys;"` 于任意新连接 → 0。

**修复方向**：在 `session.py` 的 `create_async_engine` 上挂 `event.listens_for(engine.sync_engine, "connect")` 执行 `PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL;`，或给 aiosqlite 的 `connect_args` 加这些 pragma；同时修正两处 docstring 的「DB 级持久化」表述（这一条不是"加注释"，是因为错误注释已经让实现走偏）。

---

### B1-4（P2 / 死代码·未接线 / 确定）`watchlist` 表没有任何生产写入方 ⇒ `scope="watchlist"` 的预警规则是永久静默空转

**位置**：`db/models.py:77-92`（表定义）、`api/v1/alerts.py:295-304`（唯一读方）、`api/v1/watchlist.py:253,373`（对外只有 `GET /dashboard`、`GET /correlation`，**没有写入口**）。

**证据**：全仓 rg `Watchlist` 的写方只有 `tests/test_alerts.py:265`（测试自己 `sqlite_insert`）。前端自选股列表由前端本地维护、以 `?symbols=...` 传给 `/watchlist/dashboard`，从不落库。

**后果**：`_rule_symbols()`（scope=watchlist）永远返回 `[]`；`alerts.py:517-611` 的 `for sym in await _rule_symbols(rule)` 全部空转 ⇒ 规则"既不触发、也不报错、也不告警"，页面上却是一个可创建、可启用的规则类型。这正是「功能声称存在但实际不可用」，也是本批要求重点识别的"看似正常实则为空"。

**最小验证方法**：

```powershell
$base\.venv\Scripts\python.exe -c "import sqlite3,sys;sys.path.insert(0,'.');from app.core.config import get_settings as g;print(sqlite3.connect(g().SQLITE_PATH).execute('select count(*) from watchlist').fetchone())"
rg -n "Watchlist\(" backend/app backend/tests   # 只有 tests 命中
```

**修复方向**：要么补自选股的写端点（真正落库），要么在 `_rule_symbols` 返回空集时落一条 WARNING/健康位（与 `_TOPK_HEALTH` 同款做法），让"规则在空转"可观测。

---

### B1-5（P3 / 死代码 / 确定）`core/metrics.py` 13 个指标中 7 个从未被写入

**位置**：`core/metrics.py:19, 36, 37, 44, 45, 50, 52`

| 指标 | 行 | 全仓引用 |
|---|---|---|
| `HTTP_ERRORS_TOTAL` | 19 | 仅定义处 |
| `REDIS_STATUS` | 36 | 仅定义处 |
| `REDIS_CIRCUIT_OPEN` | 37 | 仅定义处 |
| `PIPELINE_TOTAL` | 44 | 仅定义处 |
| `PIPELINE_DURATION` | 45 | 仅定义处 |
| `MODEL_PREDICTION_TOTAL` | 50 | 仅定义处 |
| `MODEL_PREDICTION_ERROR` | 52 | 仅定义处 |

存活的是 `HTTP_REQUESTS_TOTAL`/`HTTP_REQUEST_DURATION`（`main.py:192-197`）、`PANIC_CONTAINED_TOTAL`（`panic_guard.py:132`）、`LOOP_PANIC_CONTAINED_TOTAL`（`resilience.py:109`）、`OVERVIEW_CACHE_TOTAL`（`market.py:699,896`）。

**影响**：`/metrics` 里 `aqp_redis_status`、`aqp_redis_circuit_open` 永远为初始 0 ⇒ 抓 Redis 熔断状态不能靠 Prometheus（得走 `/settings` 的 `breaker` 或 `scripts/alerter.py:74`）；`aqp_pipeline_total`/`aqp_model_prediction_total` 同理永远为空。

**验证**：`rg -n "HTTP_ERRORS_TOTAL|REDIS_STATUS|REDIS_CIRCUIT_OPEN|PIPELINE_TOTAL|PIPELINE_DURATION|MODEL_PREDICTION_TOTAL|MODEL_PREDICTION_ERROR" backend/app backend/scripts` → 只有 `metrics.py`。

---

### B1-6（P3 / 死配置 / 确定）`core/config.py` 死配置完整清单 → 见 §4（父审核员专用）

---

### B1-7（P3 / Bug / 确定）非 ASCII Bearer token 把鉴权失败变成 `50000` 系统错误

**位置**：`backend/app/core/auth.py:196-201`

```python
if (settings.ALLOW_ADMIN_TOKEN_LOGIN and token
        and hmac.compare_digest(token, settings.ADMIN_TOKEN)):   # L198-199
```

`hmac.compare_digest` 对含非 ASCII 的 `str` 直接抛 `TypeError`（CPython 官方行为），而 HTTP header 由 Starlette 以 **latin-1** 解码，任何客户端都能塞进 ≥0x80 的字节。

**验证（实跑）**：

```powershell
& "$base\.venv\Scripts\python.exe" -c @"
from fastapi.security import HTTPAuthorizationCredentials
from app.core.auth import require_auth
for cred in ['ascii-token', '\u00e9', '\u4e2d\u6587token']:
    try: print(repr(cred), '->', require_auth(HTTPAuthorizationCredentials(scheme='Bearer', credentials=cred)))
    except Exception as e: print(repr(cred), '-> RAISES', type(e).__name__, ':', e)
"@
```

```
'ascii-token' -> RAISES HTTPException : 200: INVALID_TOKEN
'é' -> RAISES TypeError : comparing strings with non-ASCII characters is not supported
'中文token' -> RAISES TypeError : comparing strings with non-ASCII characters is not supported
```

**后果**：`errors.py:160-173` 把未捕获的 `Exception` 映射为 `ERR_SYSTEM(50000)` 并打全量堆栈日志。即：本该是 `40101/40102`（前端据此跳登录页）的鉴权失败，被报成系统错误 + 每次一坨 ERROR 日志（未认证者即可刷）。不崩溃、不越权，故 P3。

**修复**：先 `token.encode("utf-8")` + `settings.ADMIN_TOKEN.encode("utf-8")` 再比较（bytes 无此限制），或 `try/except TypeError: pass`；最小验证方法：对 `Authorization: Bearer é` 发一条请求，断言 `code == 40102` 而不是 50000。

---

### B1-8（P3 / 死代码 / 确定）其它小而确定的死代码

| 位置 | 证据 | 建议 |
|---|---|---|
| `core/pipeline_lock.py:54-56` `current_owner()` | 别名，全仓零调用方（app/tests 全用 `current_pipeline_owner`） | 删除 |
| `core/events.py:48-50` `publish()` | 全仓零调用方（`from .core.events import publish` 无命中；`events.publish(` 无命中；全部用 `publish_threadsafe`/`recent`） | 删除或在文档里保留为**公开 API**并说明为何无内部调用方 |
| `db/models_auth.py:45-68` `UserSession`、`ApiToken` | 仅定义处；无任何读写 | 删除或明确接线；`UserSession` 的缺失意味着**JWT 无法撤销**（登出后旧 token 在 `JWT_EXPIRE_SECONDS`=7 天内仍有效）——若这是已知取舍，应在 `auth.py` docstring 写明 |
| `core/excel.py:20-21` `_sheet` 的 dict 分支 | 三个调用方全部传 `list[list]`（`screener_workbook`/`backtest_workbook`/`strategy_backtest_workbook`） | 简化为单个列表推导（注意：若将来真传 dict，`r[h]` 缺键会 KeyError，属隐藏地雷） |
| `cache/keys.py` | **无死常量**（14 个 `k_*` 全有调用方：`market.py`/`stock.py`/`etf.py`/`screener.py`/`portfolio.py`/`watchlist.py`/`datacenter.py`/`backtest.py`） | 无需动作 |

---

### B1-9（P3 / 一致性 / 疑似）同一张 `app_state` 表两种时间口径

`db/kv.py:41-45` 写 `datetime('now','localtime')`（本地时间），而 `models.AppState.updated_at` 的 `server_default/onupdate = func.now()`（`models.py:396-398`）在 SQLite 上是 `CURRENT_TIMESTAMP` = **UTC**，`trading/paper.py:78` 走 ORM 写 `kill_switch`。同表同列相差一个时区（本机 UTC+8）。

**为什么只标疑似**：目前 `kv_get` 只 `SELECT value`，我没有找到读取 `app_state.updated_at` 的消费方；一旦有页面/接口展示"状态更新时间"，就会凭空差 8 小时。
**最小验证**：`rg -n "AppState" backend/app` 看是否有 `updated_at` 读取；或对比 `select key, updated_at from app_state` 与 `select datetime('now','localtime')`。

---

### B1-10（P3 / Bug / 确定·低影响）防抖锁在两个存储域之间不互斥

`cache/redis_client.py:243-273`：Redis 可用时 `try_lock` **只**写 Redis（不写 `_local_locks`）；Redis 故障时只写本地；而 `unlock` **无条件双清**。于是 Redis 恢复的一瞬间，A 持本地锁、B 可拿到 Redis 同名锁（两个域互不相识），且 A 结束时会把 B 的 Redis 锁删掉。影响面仅限 SWR 后台重建（`swr.py:99-127`）可能重复构建（浪费算力），不产生错误数据。**最小验证**：按 `_b1_probe_c_redis.py` 的双域桩，让 Redis 分支与本地分支先后拿同一个 `rebuild:` key，断言两次都返回 True。

---

## 3. 本批重点问题 · 逐条作答（含「未发现问题」的正面结论）

1. **`compute_guard`/`pipeline_lock` 的作用域与单进程前提**
   - 两者都是**进程内**：`pipeline_lock._LOCK` 是 `threading.Lock`（`pipeline_lock.py:30`），`compute_guard._slots` 是 `threading.BoundedSemaphore`（`compute_guard.py:11`）。
   - 有代码依赖它们做数据一致性保护：`pipeline_lock` 明确以"防两个写者互相覆盖 daily_bar_qfq / cs 镜像 / features"为目的（docstring `pipeline_lock.py:1-18`），五个调用点锁粒度都覆盖**完整任务体**：`sync_service._sync_worker`（`sync_service.py:396-399`）、`orchestrator.run_pipeline`（`orchestrator.py:462-465`）、`datacenter._run_fetch`（`datacenter.py:883-913`）、`datacenter.cs_mirror_rebuild._rebuild`（`datacenter.py:1098-1104`，在 worker 线程内持锁）、`train_service` 的 `_worker`（`train_service.py:379-383`）。→ 未发现"拿了锁却提前释放"的假保护。
   - 单进程前提**有明文**：模块 docstring `pipeline_lock.py:11-12`（"进程级互斥；当前部署为单 worker，见 Dockerfile --workers 1；跨 worker 需 Redis 分布式锁，尚未实现"）+ `main.py:80-87` 启动调用 `warn_if_multi_worker()`（只告警不阻断，符合裁决）。`detect_worker_count` 的 env/argv 双路推断（`pipeline_lock.py:83-208`）实现与 docstring 一致。
   - **唯一的表述缺口**：`compute_guard` 的 docstring 只写"进程内资源闸门"，没有像 `pipeline_lock` 那样写明"多 worker 下会各有一份名额、总额度翻倍"这一前提。属文档口径，不单列问题。
   - 结论：**不算当前 bug**（多 worker 才失效，前提已注明）。

2. **`panic_guard.py` 能否真正捕获 `BaseException` / 是否破坏优雅退出 / 是否中间件+50001**
   - **能**。`except Exception: raise`（L100-102）先放行普通异常给 `errors.py`；`except BaseException as exc`（L103）接住其余；`BaseExceptionGroup` 递归取**首个叶子**再判定（`_leaf_exception` L57-63，Python 3.11 有内建，3.10 走 `NameError` 兜底 —— 本项目 3.11.15，走内建分支）。
   - **不破坏优雅退出**：`KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError` / `GeneratorExit` 在 `_PASSTHROUGH`（L49-54）中一律 `raise`；`response_started` 后同样 `raise`（L107-109）；非 HTTP（lifespan/websocket）原样透传（L86-88）。`resilience.is_fatal_base_exception` 是同一份四元组，口径一致。
   - **是中间件 + 兜底码**：`app.add_middleware(PanicGuardMiddleware)`（`main.py:147`），`ERR_PANIC_CONTAINED = 50001`（`errors.py:72`），返回 `JSONResponse(status_code=200, ...)`。注册顺序正确：`user_middleware=[timing, CORS, guard]` ⇒ 最外到内 `ServerError → timing → CORS → guard → ExceptionMiddleware → router`，guard 在 CORS 内侧 ⇒ 兜底响应仍带 CORS 头，且 `X-Trace-Id` 仍由 timing 中间件设置（`main.py:181-190`）。我核对了 Starlette `add_middleware` 的 `insert(0)` + `build_middleware_stack` 反转顺序，与 `main.py:140-147` 的注释完全一致。
   - `_contain` 用 `traceback.format_exception` 字符串化后再 `logger.error`，规避 `enqueue=True` 对不可 pickle 的 `PanicException` 丢整条日志的问题（L118-139 注释所述行为与代码一致）。
   - 实测：`tests/test_panic_guard_middleware.py` + `tests/test_resilient_loop.py` 共 **28 passed**（§6）。**未发现 P0–P2**。

3. **`resilience.py` 的重试/退避/上限**
   - **本模块不含任何重试逻辑**（只有 `is_fatal_base_exception` / `format_exception_stack` / `log_contained`）。全仓唯一的通用重试实现在 `data/ingest/akshare_adapter.py:85-97`：`stop_after_attempt(max(1, AKSHARE_RETRY))` + `wait_exponential(multiplier=1, min=1, max=8)` + `reraise=True` ⇒ **有上限、有指数退避，不会无限重试**（AKSHARE_RETRY 默认 3）。
   - 「超时是否存在默认无上限」：`resilience.py` 不涉及超时；`akshare`/`recent.requests` 调用本身未见显式 timeout（B3b 范围，呈**疑似**，此处仅作交叉提示，不计入 B1 问题）。
   - Redis/LLM/DB 侧的超时都有默认值：`REDIS_TIMEOUT=1.0`（`config.py:73`）、`LLM_TIMEOUT_SECONDS=60`、SQLite `timeout=30`。`compute_slot` 的等待有 `COMPUTE_ACQUIRE_TIMEOUT_SECONDS=10.0`——**除了 B1-1 的泄漏**，不存在无上限等待。

4. **`cache/redis_client.py` 熔断是否有半开探测**
   - **没有独立的半开态**，但也**不是"永不开门"**：`_ensure()` 只在 `open_until > now` 时拒绝（L67-68）；窗口（`OPEN_SECONDS=60`）到期后直接恢复真实请求，靠 `_mark_fail` 重新累计到 `FAIL_THRESHOLD=5` 才再次打开（L89-95），即隐式半开。
   - 代价：每次恢复窗口要付出**最多 5 次失败**（每次 ≤ `REDIS_TIMEOUT=1.0s`）才重新熔断；`ping()` 的失败也计入（L298-300），所以 `/health` 或 `/settings` 的轮询会加速重新熔断。
   - 结论：属设计取舍，**未发现 P0–P2**；若要改成标准半开（窗口到期只放 1 个探测请求，成功即清零并关闭、失败立即重开），改 `_ensure`/`_mark_ok` 一处即可，收益是 Redis 抖动时少 4 次 1s 阻塞。

5. **降级到 LRU 后两种后端的 key/序列化是否兼容 / SWR 的 stale 是否被标新鲜**
   - **兼容**（见 B1-2 的顺带结论）：同一份 bytes、同一批 key（含 `:swr`）、`delete`/`ttl` 两域口径一致。
   - **stale 会被正确标注**：`get_stale` 命中影子键返回 `is_stale=True`，`swr.py:184-189` 一定写 `data["stale"]=True`。**唯一漏标的是 B1-2 的场景**：Redis 恢复后从 Redis 读到旧值，`is_stale=False`，于是旧数据被当作新鲜数据。
   - 另：`unavailable` 不落地、`degraded` 只落地 ≤15s 且不留影子键（`swr.py:38-59`），与"空态不自愈"的历史缺陷修复一致；`_bg_tasks` 持引用防 `create_task` 被 GC（`swr.py:27,125-127`）✓。

6. **`db/session.py`：连接 timeout / session 关闭 / WAL 长事务**
   - `timeout=30` 已设（`session.py:63-66`，仅对 `sqlite+aiosqlite://` 前缀生效；若有人把 `SQLITE_URL` 换成别的方言，`connect_args={}` 也正确）。
   - session 关闭：`async with factory() as session:`（L96）保证 `__aexit__` 必关闭；`except Exception: rollback; raise`（L100-102）+ `finally: await session.close()`（L103-104）。**BaseException（如 polars panic）**不会走 rollback 分支，但 `finally` 仍执行、`AsyncSession.close()` 本身会回滚未提交事务 ⇒ **所有分支都关闭**。
   - WAL 长事务：`get_db` 在整个请求期间持有 session；SQLAlchemy **惰性**占用连接（首个查询才 checkout），且引擎是 aiosqlite 文件库默认池、`busy_timeout=30s`。我**没有**找到"先查库 → 再长 await（>30s）"的确证端点，故此项为**疑似（需 profile）**：可用 `sqlite3 ... "pragma wal_checkpoint"` 观察 `-wal` 增长，或在 `get_db` 的 `before/after` 插桩记录持有时长。不列为问题条目。

7. **`db/kv.py`：路径是否分离 / 是否互相锁死**
   - **不分离，同一个 `SQLITE_PATH`**（`kv.py:19-22,38` 与 `config.py:189-194`），这是有意的（worker 线程需要读主库状态）。`kv_get` 用 `file:{path}?mode=ro` URI；`kv_set` 用可写连接；`task_store`/`sync_service` 的同步连接同样指向主库。
   - **不会互相锁死**：SQLite 单写者，两处都是 `timeout=30`（`kv.py:22,38` 与 `session.py:65` 的 `busy_timeout=30000` 口径一致），冲突表现为等待而非嵌套死锁。
   - 我特别验证了本批重点里最可疑的一点——**`mode=ro` 读 WAL 库是否可用**（含 Windows 反斜杠 + 仓库路径带空格）：**可用**，不是缺陷：

```powershell
& "$base\.venv\Scripts\python.exe" "$base\.tmp_testrun\_b1_probe_a_kv_wal.py"
```

```
[1] files on disk after clean close: ['_b1_probe_a.db']      # -wal/-shm 已删
[2] journal_mode (persisted, read via mode=ro): ('wal',)
[3] mode=ro select -> ('"v"',)
[4] kv.kv_get('k') (real function, value IS in the table) -> v
[5] kv.kv_set then kv.kv_get('k2') -> {'a': 1}
[6] kv.kv_get('k') while a RW connection is open -> v
```

8. **`cache/keys.py` 死常量** —— **无**。14 个 `k_*` 全部有 app 调用方（`rg` 见 §4 扫描命令的输出）：`k_market_overview(_rt/_daily)`→`market.py`、`k_etf_*`→`etf.py`、`k_stock_*`→`stock.py`、`k_portfolio_search`→`portfolio.py`、`k_watchlist_dashboard`→`watchlist.py`、`k_datacenter_overview`→`datacenter.py`、`k_screener`/`k_screener_stocks`→`screener.py`、`k_backtest`→`backtest.py`。

9. **`db/migrations.py` / `db/models.py` 其它**：`ensure_columns` 幂等、只接受代码内字面量表名（无注入）；`ScreenerSnapshot.risk` 物理列名与对外 `signal_strength` 的映射有明确注释（`models.py:462-466`），属已知历史债，不重复报。

---

## 4. 死配置完整清单（父审核员专用）

扫描方法（可复现）：把 `config.py` 的每个字段名当作词，在 `backend/app/**`、`backend/scripts/**`、`backend/tests/**`、`Dockerfile*`、`docker-compose*`、`.env*`、`*.ps1`、根目录文档中逐个精确匹配（词边界，排除 `config.py` 自身），再人工核对命中处是否为真正的读取。

```powershell
# 复现命令（输出见本报告 §7 附录）
& "$base\.venv\Scripts\python.exe" -c "<§5 的 scan 脚本>"
```

### A. 完全死配置（全仓 0 个读取方）

| # | 配置项 | 声明处 | 实测引用情况 | 类型 / 建议 |
|---|---|---|---|---|
| 1 | `NOTIFY_EMAIL_TO` | `config.py:147` | 全仓**仅声明处**（1 次命中）。`backend/app` 内**根本不存在任何 SMTP/邮件发送实现**（`rg -i "smtp" backend/app` 零命中；唯一的 `email` 命中是 `app_settings.py:46,174` 的用户资料占位字段，与通知无关）；通知渠道白名单只有 `{"sse","webhook"}`（`alerts.py:42`），webhook 走 `NOTIFY_WEBHOOK_URL`（`orchestrator.py:434`、`alerts.py:688`、`report.py:425`） | 死配置。删字段，或在文档标注「邮件通道未实现」 |
| 2 | `API_HOST` | `config.py:50` | 唯一非声明命中是 `config.py:246` 的**告警字符串**；没有任何代码用它绑定监听地址（服务由 uvicorn CLI / `Dockerfile` 决定） | 死配置（且会误导运维以为改了 .env 就能换监听地址） |
| 3 | `API_PORT` | `config.py:51` | 全仓仅 `.env.example:24` + 文档，**0 个代码引用** | 死配置（同上） |

### B. 半死：字段存在但从不被读取，`.env` 里设置无效（`os.environ` 才是真源）

| # | 配置项 | 声明处 | 真实读取点 | 实测 | 备注 |
|---|---|---|---|---|---|
| 4 | `JWT_EXPIRE_SECONDS` | `config.py:132` | `auth.py:154` `os.environ.get("JWT_EXPIRE_SECONDS","604800")` | 探针 B：`.env=1234` → `Settings.JWT_EXPIRE_SECONDS=1234`，`os.environ=None`，**签发时实际用 604800** | **旧报告已登记**（`docs/audit/AQP_架构审查_20260912.md` A-06）。本次补充：`.env.example:16` 至今仍在文档化该键 ⇒ 运维改它 100% 无效 |
| 5 | `AQP_PANEL_BLOCK_TIMEOUT` | **不是 Settings 字段**（`.env.example:59` 文档化） | `stock.py:69` `os.getenv(...)` | 探针 B [5][6]：`'AQP_PANEL_BLOCK_TIMEOUT' in Settings.model_fields → False`；`.env=9.5` → `os.getenv → None` | **旧报告已登记**（A-06、C-04）。补充：`.env.example` 已收录该键，但收录方式（写进 .env）依然无效——必须补成 `Settings` 字段才会生效 |
| 6 | `FEATURE_INCREMENTAL` | 未纳入 Settings | `orchestrator.py:110` `os.environ.get` | 同 A-06 已登记 | 只在真实环境变量下有效；`.env` 无效、`.env.example` 未收录 |

### C. 文档/描述里记载但实际不存在的开关名（同根因：`Settings` 无 `env_prefix`）

| # | 记载的名字 | 记载处 | 实际字段 | 实测 |
|---|---|---|---|---|
| 7 | `AQP_ALLOW_REGISTRATION` | `config.py:137` 描述、`api/v1/auth.py:4` docstring、`docs/auth-register.md:38` | `ALLOW_REGISTRATION`（`config.py:135`） | 探针 B [4]：`AQP_ALLOW_REGISTRATION=false` 下 `Settings.ALLOW_REGISTRATION` **仍为 True**（`extra="ignore"` 静默丢弃） |
| 8 | `AQP_REGISTER_DEFAULT_ROLE` | `api/v1/auth.py:5` docstring、`docs/auth-register.md:39` | `REGISTER_DEFAULT_ROLE`（`config.py:139`） | 同上（同一机制，未单独跑探针） |
| 9 | `AQP_TALIB_BACKEND`、`AQP_REDIS_URL`、`AQP_SMTP_HOST/PORT/USER/PASSWORD` | `docs/项目文档.md:2664 / 3147 / 3160` | **不存在**（`rg -i "smtp\|TALIB\|AQP_REDIS_URL" backend/app` 零命中） | 文档幻觉 / 规划稿残留 |

**为什么 7/8 仍值得报**：pydantic-settings 没有 `env_prefix`，所以按文档写 `AQP_ALLOW_REGISTRATION=false` 会被 `extra="ignore"` 吃掉。生产环境侥幸不是静默漏洞——`validate_runtime_safety`（`config.py:207-208,222-223`）会因 `ALLOW_REGISTRATION=True` 直接 `ValueError` 拒绝启动——但**报错信息不会提示"你用错了变量名"**，运维会卡在"我明明设了 false"。dev 下则完全静默（只打一条开发告警）。属 P3 可诊断性缺陷。

**注意（与"死配置"相对）**：`CORS_ORIGINS`、`METRICS_REQUIRE_AUTH` 的字段名本身没有直接命中，但通过属性 `cors_origins_list`（`main.py:153`）/`metrics_require_auth`（`main.py:247`）被消费，**不是死配置**；`DEBUG`/`ENV`/`LOG_DIR`/`USD_CNY_RATE`/`TUSHARE_TOKEN`/`AQP_CPU_THREADS`/`LLM_*`/`ML_*`/`EVENING_ROUTINE_*`/`AUTO_RETRAIN_ON_DRIFT`/`RETRAIN_MIN_INTERVAL_HOURS`/`WARM_OVERVIEW_ON_STARTUP`/`QUOTES_TTL`/`COMPUTE_*`/`AKSHARE_*` 均确认有读取方。

---

## 5. 探针脚本与扫描脚本全文（可复现）

所有脚本位于 `backend/.tmp_testrun/_b1_probe_*.py`（未跟踪的脚手架目录）。

**扫描脚本（死配置矩阵）**：

```python
import re, pathlib
root = pathlib.Path(r'D:\Python_Project\Alpha Quant Platform')
fields = '''ENV DEBUG APP_NAME ADMIN_TOKEN ALLOW_ADMIN_TOKEN_LOGIN API_HOST API_PORT
METRICS_REQUIRE_AUTH CORS_ORIGINS SQLITE_URL REDIS_HOST REDIS_PORT REDIS_DB
REDIS_PASSWORD REDIS_TIMEOUT REDIS_ENABLED DATA_ROOT MODEL_ROOT FEATURE_VERSION
USD_CNY_RATE LOG_LEVEL LOG_DIR AKSHARE_RATE_LIMIT AKSHARE_RETRY
WARM_OVERVIEW_ON_STARTUP QUOTES_TTL COMPUTE_CONCURRENCY
COMPUTE_ACQUIRE_TIMEOUT_SECONDS ML_LABEL_HORIZON ML_HOLDOUT_DAYS ML_TEST_DAYS
JWT_SECRET JWT_EXPIRE_SECONDS ALLOW_REGISTRATION REGISTER_DEFAULT_ROLE
NOTIFY_ENABLED NOTIFY_WEBHOOK_URL NOTIFY_EMAIL_TO TUSHARE_TOKEN AQP_CPU_THREADS
EVENING_ROUTINE_ENABLED EVENING_ROUTINE_TIME AUTO_RETRAIN_ON_DRIFT
RETRAIN_MIN_INTERVAL_HOURS LLM_PROVIDER LLM_BASE_URL LLM_API_KEY LLM_MODEL
LLM_TIMEOUT_SECONDS'''.split()
code_suffix = {'.py','.ps1','.sh','.ts','.tsx','.js','.yml','.yaml','.toml','.cfg','.ini','.bat','.cmd','.env'}
paths = []
for pat in ('backend/app/**/*','backend/tests/**/*','backend/scripts/**/*','backend/*','*.ps1','*.md','*.yml','*.yaml','*.toml','Dockerfile*','docker-compose*','.env','.env.example','frontend/src/**/*'):
    paths += [p for p in root.glob(pat) if p.is_file()]
res = {f: [] for f in fields}
for p in sorted(set(paths)):
    if p.suffix not in code_suffix and not p.name.startswith(('Dockerfile','docker-compose')):
        continue
    if p.name == 'config.py' and p.parent.name == 'core':
        continue
    txt = p.read_text(encoding='utf-8', errors='ignore')
    for f in fields:
        for m in re.finditer(r'(?<![A-Za-z0-9_])' + re.escape(f) + r'(?![A-Za-z0-9_])', txt):
            res[f].append(f'{p.relative_to(root)}:{txt[:m.start()].count(chr(10)) + 1}')
for f in fields:
    print(f, len(res[f]), ';'.join(res[f][:8]))
```

**探针 A（`kv_get` 在 WAL 库上的 `mode=ro` 读取）**、**B（配置有效性）**、**C（Redis 降级/恢复回退）**、**D（PRAGMA 继承）**、**E（compute_guard 泄漏）**：见同名文件，关键输出已内嵌在 §2/§3 各处。

---

## 6. 定向 pytest 记录（未跑全量）

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH="$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
& "$base\.venv\Scripts\python.exe" -m pytest tests/test_compute_guard_queueing.py tests/test_pipeline_lock.py tests/test_db_connect_timeout.py -q -p audit_mkdtemp_fix
& "$base\.venv\Scripts\python.exe" -m pytest tests/test_panic_guard_middleware.py tests/test_resilient_loop.py -q -p audit_mkdtemp_fix
```

```
tests\test_compute_guard_queueing.py ...                                 [ 25%]
tests\test_pipeline_lock.py ......                                       [ 75%]
tests\test_db_connect_timeout.py ...                                     [100%]
======================= 12 passed, 2 warnings in 2.16s ========================

====================== 28 passed, 16 warnings in 28.03s =======================
```

（两组均全绿 ⇒ 上述问题都是**现有用例覆盖不到**的新增面：`test_compute_guard_queueing.py` 只测"排队—释放"正常路径，不测取消；`test_pipeline_lock.py` 只测单进程语义。）

---

## 7. 统计

- P0：0
- P1：0（**本批没有 P1**；B1-1 的机制达 P1 破坏力，但当前部署无取消入口，按 P2 报并给出升级判据）
- P2：4（B1-1 compute_guard 名额泄漏、B1-2 Redis 恢复回退、B1-3 PRAGMA 不继承、B1-4 watchlist 空转）
- P3：6（B1-5 死指标、B1-6 死配置、B1-7 非 ASCII token、B1-8 其它死代码、B1-9 时间口径、B1-10 防抖锁双域）
- 明确「未发现 P0–P2」的文件/模块：`panic_guard.py`、`pipeline_lock.py`、`resilience.py`、`events.py`、`llm.py`、`logging.py`、`excel.py`、`migrations.py`、`session.py`、`kv.py`、`memory.py`、`keys.py`、`swr.py`、`auth.py`
- 已核实**不是**缺陷的可疑点：`mode=ro` 读 WAL 库（实测可用）、LRU/Redis 的 key 与序列化格式（实测兼容）、panic guard 的中间件注册顺序与 CORS/trace 关系（源码级核对一致）、多 worker 下的进程级锁（前提已明文注明 + 启动告警）、**客户端断开是否会取消请求任务（uvicorn 0.30.6 + starlette 0.38.6 实测不会，故不能当作 B1-1 的触发条件）**

---

## 8. 跨批提示（**不属于 B1 范围**，请父审核员转交 B0 负责人）

> 这两条都落在 `backend/Dockerfile` + `docker-compose.yml` + `config.py`（父审核员文件）交界处。我在这里**只报线索与验证方法**，不做结论——本沙箱**没有 Docker daemon**（`docker version` 连不上 pipe），无法实跑容器。

**提示 1（疑似，需 Docker 复现）：容器内 `PROJECT_ROOT` 会退化成 `/`，与 compose 的挂载点、`.env` 全部错位。**
- 推理（可离线复算）：`config.py:24` 是 `Path(__file__).resolve().parents[3]`，磁盘布局 `.../backend/app/core/config.py` ⇒ `parents[3]` = 仓库根 ✓；而镜像里 `COPY backend/app $APP_HOME/app` + `WORKDIR /app`（`Dockerfile:42-43`）把 `backend/` 当成 `/app`，于是 `__file__=/app/app/core/config.py`、`parents[3] = "/"` ⇒ **PROJECT_ROOT=/，应为 /app**（`parents[2]`）。实测复算已确认该索引关系（`PurePosixPath('/app/app/core/config.py').parents[3]` = `/`）。
- 连带后果：`LOG_DIR=/backend/logs`、`DATA_ROOT=/data/parquet`、`MODEL_ROOT=/data/models`、`SQLITE_PATH=/data/sqlite/aqp.db`；而 compose 挂的是 `./data:/app/data`、`./backend/logs:/app/logs`（`docker-compose.yml:56-58`）⇒ **挂载点完全不被使用**；镜像里 `RUN mkdir -p $APP_HOME/data/parquet ... $APP_HOME/logs`（`Dockerfile:48`）建的正是 `/app/data`、`/app/logs`，即"该建的地方"与"代码要去的地方"恰好错开一级。再加 `read_only: true`（`docker-compose.yml:43`）+ `USER aqp`（uid 10001，`Dockerfile:49-52`）：`get_settings()` 的 `mkdir(parents=True, exist_ok=True)`（`config.py:234-237`）要在 `/` 下创建 `backend`、`data` —— 只读根 + 非 root 双重不可能 ⇒ 抛 `OSError`；而 `app/core/compute_guard.py:11` 在**模块导入期**就调用 `get_settings()` ⇒ **容器起不来**（这是推理链，故标疑似）。
- **最小验证**（任一）：`docker compose build && docker compose up`，看是否 `Read-only file system` 起不来；或 `docker run --rm backend-aqp python -c "from app.core.config import PROJECT_ROOT,get_settings;print(PROJECT_ROOT);print(get_settings().DATA_ROOT)"`。

**提示 2（疑似，安全相关）：docker 路径下 `.env` 根本进不到进程，`ADMIN_TOKEN`/`JWT_SECRET` 会落到默认值。**
- 证据：`Dockerfile` 只 COPY `app/scripts/pytest.ini/tests`（**不 COPY `.env`**），`docker-compose.yml` 的 api 服务**没有 `env_file:`**，`environment:` 只列了 `REDIS_ENABLED/REDIS_HOST/REDIS_PORT/REDIS_PASSWORD/TZ`（`docker-compose.yml:49-55`）。
- 于是容器内：`ADMIN_TOKEN` = `aqp-dev-token-change-me`（公开默认值）、`ENV` = `dev` ⇒ `validate_runtime_safety` 只在 `ENV=prod` 时 fail-fast（`config.py:210`），所以**只打一条 warning**；`JWT_SECRET=None` ⇒ `auth.py:150` 用 `"aqp-derive:"+ADMIN_TOKEN` 派生签名密钥。
- **最小验证**：`docker compose run --rm aqp-api python -c "from app.core.config import get_settings as g;s=g();print(s.ENV, s.ADMIN_TOKEN[:6], bool(s.JWT_SECRET), s.ALLOW_ADMIN_TOKEN_LOGIN)"` → 期望看到 `dev aqp-de False True`。