# B7b — API 路由·第二部分（运维 / 任务 / SSE / 导出）审核报告

> 审核日期：2026-09-21（会话实测日）· 审核员：B7b 子审核员
> 纪律来源：`docs/audit-2026-09-18/AUDIT-BRIEF.md`、`docs/chatgpt-full-review-prompt.md` §1 契约 / §3 B7b
> 证据等级：本文每条「确定」均附实际执行的命令与真实输出片段。未跑通的一律标注「疑似，需验证」。

## 0. 运行环境与验证手段（可复现）

| 手段 | 命令 | 说明 |
|---|---|---|
| 路由表内省 | `app.main` 装载后遍历 `APIRoute.dependant.dependencies`，读 `checker` 闭包里的角色串 | 与 `tests/test_read_endpoints_rbac.py::_role_of` 同一算法 |
| 端到端行为 | 手工构造 ASGI scope 直接 `await app(scope, receive, send)` | **绕过 lifespan**，从而绕开本沙箱的 `loguru enqueue=True` → `multiprocessing.SimpleQueue` → `WinError 5`（具名管道被禁） |
| 只读数据度量 | `Get-ChildItem data/parquet/...` 计数 | 证明截断比例 |
| 静态 | `python -m ruff check ... --select F`、`openpyxl` 行为探针、`sqlite3` 语义探针 | — |

> ⚠️ 未运行全量 pytest；未运行任何会外发网络的端点正常路径（`/market/overview*` 仅在第一次尝试中被触发过一次外网调用，随后从用例中剔除）。

执行的隔离环境设置（不触碰生产库）：

```powershell
$base='D:\Python_Project\Alpha Quant Platform\backend'
$t="$base\.tmp_b7b"
$env:DATA_ROOT="$t\parquet"; $env:MODEL_ROOT="$t\models"; $env:LOG_DIR="$t\logs"
$env:SQLITE_URL="sqlite+aiosqlite:///$($t.Replace('\','/'))/sqlite/aqp.db"
$env:REDIS_ENABLED="false"; $env:WARM_OVERVIEW_ON_STARTUP="0"; $env:PYTHONPATH="$base"
```

---

## 1. 逐文件结论行

| 文件 | 结论 |
|---|---|
| `backend/app/api/v1/backtest.py` | **发现 P2**：F841 `source`（L313/316）是**漏用的真 bug** —— 复权口径回退不披露（F6）。3 个端点角色声明与前端一致。 |
| `backend/app/api/v1/portfolio.py` | 未发现 P0–P2 问题。LIKE 通配符已转义（L65）、`limit` 有 `ge/le`（L138）、异常已分 `ValueError`/`Exception` 兜底。 |
| `backend/app/api/v1/datacenter.py` | **发现 P0**（`/train/readiness` 无鉴权，F1）+ P2（`/datasets` 部分扫描失败不披露，F9）+ P3（3 处 `limit` 无界/负值语义错误，F10）。 |
| `backend/app/api/v1/ops.py` | **发现 P2×2**：质量扫描 200/2499 硬截断不披露（F4）；`/dag/rerun` 失败仍返回 `code=0`（F7）。P3：年份默认硬编码 2026（F12）。 |
| `backend/app/api/v1/monitor.py` | 未发现 P0–P2 问题。43 行，两个端点角色正确，无数据时返回 `state=unknown` 而非伪造。 |
| `backend/app/api/v1/alerts.py` | P3：读写角色倒挂（`/events` researcher 读 vs `/events/read` viewer 写，F11）。其余（`truncated`/`recent_truncated` 披露、params 白名单、panic 收口）**是全仓最规范的一处，可作为其他端点披露口径的样板**。 |
| `backend/app/api/v1/notify.py` | SSE 一次性语义 **实现正确**（见 §4 专项）；**发现 P2**：ticket 走 query string 且落进 uvicorn/Nginx access log（F2）。 |
| `backend/app/api/v1/desk.py` | P3 并入 F10（`/orders?limit=-1` → SQLite `LIMIT -1` = 全表 + N+1）；其余未发现 P0–P2 问题。 |
| `backend/app/api/v1/studio.py` | **发现 P2**：`/mining/start` 无并发/幂等/`compute_guard`/`pipeline_lock` 保护，且 `_TASKS` 淘汰会丢弃 **RUNNING** 任务的簿记（F8）。 |
| `backend/app/api/v1/export.py` | **发现 P2×2**：Excel **公式注入**（F3）；非法 `date` 参数被兜底成 `50000`（F5）。文件名字段无路径穿越/无 CRLF 注入（见 §4）。 |
| `backend/app/api/v1/watchlist.py` | 未发现 P0–P2 问题。`symbols` 与 `limit` 均双向有界（100/30 只），超时走 `degraded` 且不伪造行情（L342-364）。 |
| `backend/app/api/v1/app_settings.py` | P3：`/apikeys/rotate` 是永不成功的死端点（F14）。**所有管理类写端点（engine / cache clear / db backup / apikeys）均正确要求 admin** —— 本批越权重点项**通过**。 |
| `backend/app/core/auth.py` | 角色层级单一事实源、SSE ticket 存储域分离（`r.`/`m.`）设计正确；`consume_sse_ticket` 失败原因统一（不泄露 ticket 是否存在/过期/已消费）。 |
| `backend/app/api/v1/auth.py` | 未发现 P0–P2 问题。注册强制降级为非 admin（L45/L203）、登录限速、用户名白名单、并发注册 IntegrityError 兜底。 |
| `backend/app/core/errors.py` | P3：`_AUTH_DETAIL_CODES` 中 `RATE_LIMITED`/`PIPELINE_BUSY` 两个条目**不可达**（F13）。兜底中间件本身工作正常（HTTP 恒 200）。 |

---

## 2. 问题详述

### F1｜P0｜越权：`GET /api/v1/datacenter/train/readiness` 完全没有鉴权依赖

**位置**：`backend/app/api/v1/datacenter.py:1124-1129`

```python
@router.get("/train/readiness")
async def train_readiness_status() -> APIResponse[dict]:   # ← 没有任何 Depends(...)
    """训练前置就绪度（torch/features/样本量/关系边）。"""
    from ...ml.train_service import train_readiness
    return ok(await asyncio.to_thread(train_readiness))
```

**现象/触发条件**：不带任何 `Authorization` 头请求即返回 `code=0`，泄露 PyTorch 是否安装与设备名、`features` 目录绝对路径、预估样本量、GNN 关系边数、训练门禁阈值。

**运行时证据（路由内省）**：

```
=== /api/v1 endpoints with NO require_role dependency ===
  GET     /api/v1/datacenter/train/readiness      ← 本批唯一真·裸奔端点
  GET     /api/v1/market/overview                 ← 有意公开（App.tsx:86「市场概览是唯一公开业务页」）
  GET     /api/v1/market/overview/daily|rt|index/kline
  GET     /api/v1/auth/me, /api/v1/auth/register/status
  POST    /api/v1/auth/login, /api/v1/auth/register
```

**运行时证据（行为，ASGI 直调）**：

```
=== A. /datacenter/train/readiness : anonymous vs viewer ===
GET /datacenter/train/readiness  role=NONE                 status=200 code=0 msg=ok
GET /datacenter/train/readiness  role=viewer               status=200 code=0 msg=ok
=== A2. control: sibling endpoints must reject anonymous ===
GET /api/v1/datacenter/train/status  role=NONE             status=200 code=40100 msg=UNAUTHORIZED
GET /api/v1/datacenter/quality       role=NONE             status=200 code=40100 msg=UNAUTHORIZED
GET /api/v1/settings                 role=NONE             status=200 code=40100 msg=UNAUTHORIZED
```

**为什么没被发现（覆盖缺口，重要）**：

1. `tests/test_write_endpoints_smoke.py` 的运行时扫描 `_live_write_registry()`（L171-179）只覆盖
   `set(route.methods) & {"POST","PUT","DELETE","PATCH"}` —— **GET 天然不在其守护范围内**，所以那套
   「不得遗漏任何写端点」的守门人用例对本案无效。
2. `tests/test_read_endpoints_rbac.py` 用**手工维护**的 `EXPECTED_ROLES` 字典（L45-95）；它包含
   `/api/v1/datacenter/train/status`（researcher），**却没有 `/datacenter/train/readiness`**。
   全仓**没有**「每个 /api/v1 GET 必须声明非 None 角色，除非在显式公开白名单里」这一全局不变式用例。

**建议改法与要改成什么角色**：`TrainPanel.tsx` 由 `DataCenter/index.tsx:890` **无条件渲染**，而 `/data`
路由是 `RequireRole minimum="viewer"`（`App.tsx:97`）；`TrainPanel.tsx:53` 调用 `datacenterApi.trainReadiness()`。
故**前端要求的实际最低角色是 viewer**，与契约「读端点最低 `require_role("viewer")`」一致：

```python
async def train_readiness_status(
    _user: dict = Depends(require_role("viewer")),
) -> APIResponse[dict]:
```

**回归用例**（建议新增，且必须能在改回缺陷时变红）：遍历 `app.routes` 中所有 `GET` 且 `path.startswith("/api/v1")`
的 `APIRoute`，断言 `_role_of(route) is not None or route.path in PUBLIC_GET_ALLOWLIST`。
把 `PUBLIC_GET_ALLOWLIST` 显式钉为 `/api/v1/auth/register/status` + 4 个 `/api/v1/market/overview*` + `/index/kline`。

**最小验证命令**：`python -m pytest tests/test_read_endpoints_rbac.py -q`（不会变红 —— 正因如此才需要上面的新用例）。

---

### F2｜P2｜SSE 一次性 ticket 泄漏进 uvicorn / Nginx access log（违反代码自述的不变式）

**位置**：`backend/app/api/v1/notify.py:83-91`（`ticket` 是 query 参数）、`frontend/src/api/notify.ts:31`
（`/api/v1/notify/stream?ticket=${encodeURIComponent(ticket)}`）、`backend/app/core/auth.py:50-52`。

`core/auth.py:51` 明确自述：

```python
def _sse_ticket_key(ticket: str) -> str:
    """将不透明 ticket 映射为缓存键；ticket 本身绝不写入日志。"""
```

**但没有任何启动路径关掉 access log**：

```
Dockerfile:59   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
（全仓 grep 'uvicorn|access-log|access_log' 于 *.yml/*.ps1/*.sh/*.md/*.toml/Dockerfile*：
  0 处 --no-access-log）
docs/项目开发文档.md:5809 / 5837   access_log off;   ← 只对 Nginx 的 /assets/ 与 /healthz
docs/项目开发文档.md:5814          location /api/ { proxy_pass http://aqp-api:8000/api/; ... }  ← 未关
```

uvicorn 的 access log 格式**含 query string**（`.venv/Lib/site-packages/uvicorn/protocols/http/h11_impl.py:476-482`）：

```python
self.access_logger.info(
    '%s - "%s %s HTTP/%s" %d',
    get_client_addr(self.scope),
    self.scope["method"],
    get_path_with_query_string(self.scope),   # ← 含 ?ticket=…
    self.scope["http_version"], status)
```

**影响**：ticket 是 60s TTL 的一次性凭据。它同时落进 ①容器 stdout / `app.log`；②Nginx `/api/` access log。
`errors.py:27-40` 的 `_sanitize_query` 确实会把 `ticket` 打码（键名命中 `_SENSITIVE_QUERY_KEYS`），**但它只作用于
`@app.exception_handler(Exception)` 这一条未捕获异常路径**，ASGI 服务器自身的 access log 完全绕过它。

**严重度定为 P2 而非 P1 的理由**（诚实收窄）：ticket 一次性且 60s 过期；日志读取者通常已有主机权限。

**具体改法**：
1. 启动命令统一加 `--no-access-log`（Dockerfile / README / `run.md` / `.github/workflows/nightly.yml`），
   由应用内 loguru 中间件记录**已脱敏**的访问日志；
2. 或在 `main.py` 中把 `uvicorn.access` logger 的 handler 换成一个只打印 `scope["path"]`（不含 query）的过滤器 ——
   这条更稳，因为它不依赖运维是否记得加参数；
3. 文档 Nginx 片段给 `location /api/` 也加 `access_log off;`（或 `map` 掉 query）。

**最小验证**：`uvicorn app.main:app --port 8010` 后
`curl -N "http://127.0.0.1:8010/api/v1/notify/stream?ticket=m.probe"`，观察 stdout 是否出现 `?ticket=m.probe`。
（本沙箱禁具名管道，未实跑；上表为 uvicorn 源码 + 启动命令的静态可证链条。）

---

### F3｜P2｜Excel 公式注入：用户可控字符串直接成为 xlsx 公式单元格

**位置**：`backend/app/api/v1/export.py:22`（`board: str = Query("all")`，**无 pattern**）、`export.py:46-49`
（`board` 原样进 `meta`）、`export.py:93`（`strategy_name` 进 `meta`）、`backend/app/core/excel.py:106-107`/`44-45`。

```python
# core/excel.py:106-107
_sheet(wb, "meta", ["key", "value"], [[k, str(v)] for k, v in meta.items()])
```

**openpyxl 行为已实测**（`str(v)` 不改变前导 `=`）：

```python
>>> ws.append(["=cmd|'/c calc'!A1"]); ws.append(["=1+1"]); ws.append(["normal"])
value="=cmd|'/c calc'!A1"          data_type='f'     ← 被写成公式
value='=1+1'                       data_type='f'
value='normal'                     data_type='s'
```

**触发条件**（两条真实链，均无需绕过任何校验）：

* `GET /api/v1/export/screener?board=%3Dcmd%7C%27%2Fc%20calc%27%21A1` —— `board` 无 pattern 校验，原样进 meta sheet；
* `POST /api/v1/export/strategy-backtest` 带 `"strategy_name": "=cmd|'/c calc'!A1"` ——
  `StrategyBacktestRequest.strategy_name` 只限 `max_length=64`（见 `frontend/src/pages/Backtest/parts.tsx:110` 原样提交），
  `export.py:93` → `meta` → `core/excel.py:107`。

任何人用 Excel 打开该工作簿即触发 DDE/公式求值。

**具体改法**（`core/excel.py` 单点收口，覆盖全部三个 workbook 函数）：

```python
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

def _cell(v: Any) -> Any:
    if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES):
        return "'" + v          # 前导单引号：Excel 视为文本
    return v
```

在 `_sheet()` 的 `ws.append(...)` 前对每个值过一遍 `_cell`，或在各 `*_workbook` 构造 `rows`/`meta` 时统一处理。

**预期收益**：消除「导出文件即代码执行载体」这一整类风险。**引入风险**：负数金额/以 `-` 开头的合法数值字符串会被
加引号变成文本（本仓导出列均为数值型 `float`，不受影响；`meta` 里 `start`/`end` 为 `YYYY-MM-DD`，安全）。
**验证方式**：新增用例 —— 请求 `?board==1+1` 导出，用 `openpyxl.load_workbook` 读回，断言
`cell.data_type != 'f'` 且 `cell.value.startswith("'")`。

---

### F4｜P2｜数据质量扫描硬截断 200 只标的（占实测 8.0%），且不向调用方披露

**位置**：`backend/app/api/v1/ops.py:52`（`for sym_dir in sym_dirs[:200]:`）、响应构造 `ops.py:71-78`。

**实测数据规模**（只读度量）：

```
=== symbol dirs with year=2026* (glob as used by ops.py) ===
2499
=== daily_bar symbol dirs ===
2499
```

即 **2499 个标的目录全部有 `year=2026*.parquet`**，而扫描只取 `sorted()` 后的前 **200** 个
（`A` 开头的一段标的），覆盖率 **200/2499 = 8.0%**。

响应体只有 `symbols_scanned`，**没有** `total_symbols` / `truncated`：

```python
return {"dataset": req.dataset, "year": year,
        "rows_scanned": rows_scanned, "symbols_scanned": n_syms,
        "n_issues": len(issues), ...}          # ops.py:71-78
```

**现象**：运维/前端看到 `n_issues: 0` 会把「92% 的标的从未被读过」误读成「全库干净」。这正是本仓自己在别处
明确反对的形态 —— 对照 `alerts.py:63-67` 的注释与 `truncated`/`recent_truncated` 字段：

> 但它会让 failed_jobs **饱和**（实测 25 条残留也只报 20），故须配合 payload 里的 ``truncated``
> 字段如实标注"已被截断"，否则运维分不清 20 条和 500 条。

**具体改法**：把上限提为常量并披露，例如

```python
_SCAN_SYMBOL_LIMIT = 200
candidates = sym_dirs
scanned = candidates[:_SCAN_SYMBOL_LIMIT]
...
return {..., "symbols_available": len(candidates),
        "symbols_scanned": n_syms,
        "truncated": len(candidates) > len(scanned),
        "scan_limit": _SCAN_SYMBOL_LIMIT}
```

并把 `truncated=True` 在 `/dataquality` 页面显式渲染（同类披露口径与 `alerts.py` 对齐）。

**预期收益**：消除「质量报告假阴性」；若产品要真扫全库，`200 → 2499` 的耗时需评估（当前每标的读 1 个 parquet）。
**验证方式**：`python -c` 造 250 个 `symbol=X/year=2026.snappy.parquet`，断言响应含 `truncated=True` 且
`symbols_scanned == 200 < symbols_available == 250`。

---

### F5｜P2｜`GET /export/screener?date=<非法>` 被兜底成 `50000` 系统错误（应为 `40000`），并额外抛出 ERROR 级堆栈

**位置**：`backend/app/api/v1/export.py:20`（`day: str | None = Query(None, alias="date")`，**无 pattern**）+ `export.py:38`：

```python
data, _feature_version = await asyncio.to_thread(
    _screen, date_cls.fromisoformat(day) if day else None, ...)   # ValueError 直接穿出
```

**实测（ASGI 直调，客户端所见）**：

```
GET /export/screener?date=not-a-date      http=200 code=50000  msg=系统暂不可用，请稍后重试   reraised=ValueError
GET /export/screener (no date, empty data) http=200 code=51001  msg=暂无可用选股结果…          reraised=None
```

**现象**：客户端的**参数错误**被报成「系统暂不可用」（`ERR_SYSTEM=50000`），前端无法提示用户改日期；
同时 `ValueError` 在 `ServerErrorMiddleware` 发送响应后被 `raise exc from None` **重新抛出**，
uvicorn 会额外打印一整条 `ERROR ... Exception in ASGI application` + 完整堆栈（污染日志、触发告警误报）。

**具体改法**：

```python
from typing import Annotated
day: Annotated[str | None, Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
```

pattern 不匹配 → `RequestValidationError` → 现有处理器给出 `40000`（已验证 `/ops/dag/rerun` 的
`{"trade_date":"bad"}` 正是走这条路径得到 `40000`），且不再重抛。若需保留 `fromisoformat` 的语义校验，
用 `date_cls | None` 作为参数类型并自行 try/except → `AQPException(ERR_PARAMS, ...)`。

**验证方式**：`pytest tests/test_write_endpoints_smoke.py -q` 后手工 `curl "…/export/screener?date=bad"`，
断言 `code == 40000` 且 `app.log` 无 `Exception in ASGI application`。

---

### F6｜P2｜`backtest.py:313/316` 的 F841 `source` —— 是**漏用的真 bug**（复权口径披露缺失），非无害残留

> **✅ 已修（2026-09-21，第 10 轮）**：`_load_strategy_bars` 改为返回
> `(bars, raw_fallback_symbols)`（回退事实外显），响应新增**恒存在**的
> `price_basis{kind,basis(qfq|raw|mixed),raw_fallback_symbols,note}`；同时补
> `date`/`close` 必需列守卫（缺列原会抛 `ColumnNotFoundError` ⇒ 裸 50000，现为 `ERR_DATA_EMPTY` 并点名列）。
> **前端反向担保一并修掉**：`Backtest/index.tsx` 不再硬编码 "QFQ"（缺字段显示"复权口径未知"），
> `parts.tsx` 规则说明改为"优先 QFQ…实际口径以结果页右上角标注为准"，`strategyBacktest.ts`
> 把 `price_basis` 声明为可选（兼容旧 Redis payload）。
> 另按 §P0-4 下半条补 `benchmark_basis`（构造基准显式标注）。
> 验证：`backend/tests/test_backtest_price_basis_disclosure.py` 10 条（含 `raw`/`mixed` 两个真实回退场景、
> `mixed` 只点名真正回退的标的、缺列 51001 且消息含列名、前端静态断言）；
> 全量套件 `1399 → 1402 / 0 failed`。

**位置**：`backend/app/api/v1/backtest.py:310-317`

```python
    df = read_symbol_dataset("daily_bar_qfq", sym)
    source = "qfq"                                    # L313
    if df.is_empty():
        df = read_symbol_dataset("daily_bar", sym)    # 未复权！
        source = "raw"                                # L316  ← 赋值后从未被读取
```

**ruff 复核**：

```
$ .venv\Scripts\python.exe -m ruff check app/api/v1/backtest.py --select F
F841 Local variable `source` is assigned to but never used
   --> app\api\v1\backtest.py:316:13
```

**判为「真 bug」的三条依据**：

1. **契约第 6 条**（`chatgpt-full-review-prompt.md:59`）：「复权口径（none/qfq/hfq）必须在响应或文档中可辨」。
   代码**故意**区分并逐标的跟踪了实际口径（两次赋值），随后把这份信息丢弃；`_run_strategy` 的返回体里
   `liquidity.source` 是常量 `"real_daily_bar"`（`backtest.py:627`），**没有任何按标的或全局的复权口径披露字段**。
2. **全仓唯一**：`grep 'source = "raw"' app/api/v1/*.py app/data/*.py` → 只命中 `backtest.py:316`，
   没有任何其他路径把 qfq→raw 回退披露出去。即这个变量原本就是为披露而设，接线时漏了。
3. **静默口径混用有真实后果**：未复权价在除息日出现跳空，趋势引擎（均线/回撤止损）会把除息跳空读成真实下跌，
   产生假卖出信号与假亏损；而调用方看到的仍是一份「qfq 回测」。

**诚实收窄（已实测）**：

```
=== daily_bar_qfq present? ===
qfq symbol dirs = 2499 ; daily_bar symbol dirs = 2499 ; delta = 0
symbols present in daily_bar but MISSING in daily_bar_qfq : （空）
```

当前生产数据下 qfq 与 raw 覆盖**完全一致（2499/2499，差集为空）**，故该回退分支**当前不可达**，
未产生线上错误结果。它会在「增量同步先落 `daily_bar` 新标的、qfq 待重建」或「qfq 分区损坏被 QC 隔离」时激活。
因此**定级 P2（潜在口径错误 + 确定的披露缺陷）**，而非 P1。

**具体改法**：把 `source` 接进返回值，并在 `_run_strategy` 输出中如实披露：

```python
# _load_strategy_bars 内部
sources[sym] = source
...
return frames, sources
# _run_strategy 响应中
"adjust_basis": {"by_symbol": sources,
                 "mixed": len(set(sources.values())) > 1,
                 "note": "qfq=前复权；raw=未复权（该标的缺 qfq 分区，除权日存在价格跳空，回测结果偏保守）"}
```

并让前端（`frontend/src/api/strategyBacktest.ts`，目前**无任何 adjust/basis 字段**）在
`adjust_basis.mixed == true` 时给出显式 ⓘ 提示。

**验证方式**：单测 —— 把 `read_symbol_dataset` monkeypatch 成「某标的 qfq 返回空 DataFrame、raw 返回 2 行」，
断言响应含该标的的 `raw` 口径标注。

---

### F7｜P2｜`POST /ops/dag/rerun` 失败仍返回成功信封（`code=0`）

**位置**：`backend/app/api/v1/ops.py:434-455`

```python
    def _run() -> dict:
        try:
            summary = run_pipeline(date.fromisoformat(req.trade_date), codes=req.codes, steps=list(FULL_STEPS))
            return {"ok": True, "summary": str(summary)[:500]}
        except Exception as e:                      # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return ok(await asyncio.to_thread(_run))         # ← 失败也被包成 code=0 / message="ok"
```

**实测（ASGI 直调）**：

```
POST /ops/dag/rerun {trade_date:bad}        http=200 code=40000  msg=请求参数错误      reraised=None
POST /ops/dag/rerun {trade_date:9999-99-99} http=200 code=0      msg=ok               reraised=None
```

第二条是 `date.fromisoformat("9999-99-99")` 抛 `ValueError` → 被 `except Exception` 吞成 `data.ok=false`
→ 外层 `ok()` 给出干净的 `code=0, message="ok"`。

**同类**：`run_pipeline` 内部 `with pipeline_slot("pipeline")`（`orchestrator.py:464`）冲突时抛 `PipelineBusy`，
同样被这条 `except Exception` 吞成 `code=0` —— **管道繁忙也以成功信封返回**，与
`core/errors.py:84 ERR_PIPELINE_BUSY=40900`（专为此设的码）以及 `train_service.start_training:420-423`
正确抛 `ERR_PIPELINE_BUSY` 的做法不一致。

**影响收窄说明**：`frontend/src/pages/Pipeline/index.tsx:53` 确实读了 `r.ok`
（`setMsg(r.ok ? … : 执行失败：${r.error}`），故 UI 不会误导用户。但信封级消费者（测试、
`test_envelope_contract_holds_for_all_write_endpoints` 之类的通用校验、第三方脚本）只看 `code`
就会把失败的重跑当成成功。

**具体改法**：

```python
    except PipelineBusy as e:
        raise AQPException(ERR_PIPELINE_BUSY, f"管道任务执行中：{e}") from e
    except Exception as e:  # noqa: BLE001
        raise AQPException(ERR_SYSTEM, f"流水线重跑失败：{type(e).__name__}") from e
```

**验证方式**：`curl -X POST …/ops/dag/rerun -d '{"trade_date":"9999-99-99"}'`，断言 `code != 0`。

---

### F8｜P2｜`POST /studio/mining/start` 无并发/幂等/`compute_guard` 保护；≥21 次启动会让**运行中**任务失去簿记

**位置**：`backend/app/api/v1/studio.py:67-88`、`backend/app/ml/gp_miner.py:457-482`

```python
# studio.py:67-71 —— 只有 require_role，没有 compute_slot / pipeline_lock / 「已有任务在跑」门禁
@router.post("/mining/start", response_model=APIResponse[dict])
async def start_mining(req: MiningRequest,
                       _user: dict = Depends(require_role("researcher")), ...
```

```python
# gp_miner.py:462-467 —— 唯一的"上限"是对簿记字典的淘汰，不区分任务状态
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        if len(_TASKS) > 20:
            for old in sorted(_TASKS, key=lambda k: _TASKS[k].started_at)[:-20]:
                _TASKS.pop(old, None)      # ← RUNNING 的任务同样会被 pop
    threading.Thread(target=_worker, daemon=True, name=f"gp-{task_id}").start()
```

**与同仓同类任务的对比（这就是本仓自己的标准）**：

| 重活 | 并发门禁 | 位置 |
|---|---|---|
| 训练 | `current_pipeline_owner()` + `_job.running` + `pipeline_slot("training")` | `train_service.py:419-443` |
| 同步 | `_sync.running` + `pipeline_slot("sync")` + PipelineBusy 已兜底 | `sync_service.py:395-429` |
| 流水线 | `pipeline_slot("pipeline")` | `orchestrator.py:464` |
| 回测/导出/组合/归因 | `Depends(compute_slot)` | `export.py:60,81`、`portfolio.py:108`、`backtest.py:693` |
| **GP 挖掘** | **无** | **`studio.py:67`** |

**现象**：
1. N 次快速 `POST /studio/mining/start` 会创建 N 个守护线程并发进化（CPU-only 单 worker 机器上互相抢核）。
   无幂等键，重试即重复计算（每次还各自走一遍 `_load_snapshot` 的缓存命中路径）。
2. 启动 ≥21 次后，最老的 `_TASKS` 条目被淘汰 —— 若它仍在 `RUNNING`：
   - `GET /studio/mining/status/{task_id}` → 「任务不存在」（`studio.py:92-99`，`gp_miner.get_task` 返回 `None`，`gp_miner.py:159-161`）；
   - `POST /studio/mining/cancel/{task_id}` → 「任务不存在」（`studio.py:117-131`，`gp_miner.cancel_task` 返回 `False`，`gp_miner.py:187-194`）；
   而**线程仍在跑**。产生「不可查、不可取消、仍在烧 CPU」的孤儿任务。

**具体改法与预期收益**：在 `studio.py:67` 加 `_compute: None = Depends(compute_slot)`，并在 `start_task` 里
加一条「同进程已有 RUNNING 挖掘任务则拒绝」的门禁（返回 `AQPException(ERR_PIPELINE_BUSY, ...)`）；
淘汰时跳过非终态任务（`if _TASKS[old].status == "RUNNING": continue`）。
**引入风险**：多任务并行挖掘会被禁止（产品上应确认是否需要）；若确需并行，则把上限做成显式配置项而非隐式 20。
**验证方式**：连续 21 次 `start_task` 后断言第 1 个 task_id 仍能在 `_TASKS` 中查到（或已被拒绝启动）。

---

### F9｜P2｜`/datacenter/datasets` 分区读取失败被静默吞掉，且**不向调用方披露**（行数与标的数自相矛盾）

**位置**：`backend/app/api/v1/datacenter.py:132-197`（`_scan_dataset`）。失败只进日志：

```python
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    logger.warning(f"[datacenter] scan failed {f.name} in {sym_dir.name}: {e!r}")
```

**实测（构造 1 个健康分区 + 1 个损坏分区，直接调 `_scan_dataset`）**：

```
returned keys : ['bytes', 'end', 'rows', 'start', 'symbols']
rows          : 2  symbols: 2
has 'failed'  : False
has 'partial' : False
=> caller cannot tell that 1 of 2 partitions failed to read
```

**现象**：损坏标的的 `rows` 记 0 但 `symbols` 记 2 —— **同一响应内部就自相矛盾**，
`/datasets` 页面于是显示「2 个标的 / 2 行」，与「1 个标的 / 2 行」不可区分；调用方无法知道哪一半是缺失的。
这与本文件头部 L96-101 记录的 2026-09-19 真 bug（announcements 因 `ColumnNotFoundError` 被吞、
整行从面板消失）**是同一根因的未修完部分**：那次修的是「不静默丢整行」，但「部分文件读失败」仍然静默。

**具体改法**：`_scan_dataset` 返回 `partial: bool` 与 `failed_files: int`（或 `unreadable: [symbol...]`），
`/datasets` 原样透出，前端在 `partial` 时打 ⓘ 标注「N 个分区不可读，统计仅含可读部分」。

**验证方式**：即上表那段脚本 —— 断言 `out["partial"] is True and out["failed_files"] == 1`。

---

### F10｜P3｜4 处分页参数无界，且负值语义与直觉相反（`limit=-1` 会「少一条」或「等于无限」）

| 位置 | 代码 | 实测/语义后果 |
|---|---|---|
| `datacenter.py:553` `/quality` | `limit: int = 50` → `data["items"][:limit]`（L559） | `limit=-1` → **丢掉最后一条**（实测 `code=0` 被接受；Python 切片 `[:-1]` 实测 `[0,1,2,3,4][:-1] == [0,1,2,3]`） |
| `datacenter.py:593` `/logs` | `limit: int = 60` → `out[-limit:]`（L617） | `limit=0` → `out[-0:]` == `out[0:]` = **全部日志**（实测切片 `[0,1,2,3,4][-0:] == [0,1,2,3,4]`，且 `code=0` 被接受） |
| `datacenter.py:999` `/instruments` | `limit: int = 50` → SQL `LIMIT ?`（L1008/L1012） | `limit=-1` ⇒ SQLite `LIMIT -1` = **无限制**（实测：`select id from t limit -1` → 全 5 行；`limit 2` → 2 行）⇒ viewer 可一次拉走整张 `instrument` 表（实测 2499+） |
| `desk.py:212` `/orders` | `limit: int = 50` → `sa.select(...).limit(-1)` | 同上 SQLite 语义 + 每单 N+1 次 fills 查询 |

**`limit=10**9` 的答案**（简报明确提问）：`/quality`、`/logs` 会**接受** `10**9`（实测 `code=0`），
服务端不做上限校验，并把全量列表序列化后返回；`/instruments` 同样接受 `999999999`。
截断时**是否披露**：`/quality` 返回 `affected_symbols`（截断前总数）与 `checked`，调用方**可以**据此推断
（弱披露，无显式 `truncated`）；`/logs`、`/instruments`、`/orders` **完全不披露**。
对照同批内正确样板：`alerts.py:247 Query(50, ge=1, le=200)`、`export.py:21 Query(50, ge=1, le=200)`。

**具体改法**：给 4 处加边界并写成显式常量，例如
`limit: int = Query(50, ge=1, le=500, description="返回条数上限")`；
`/logs?limit=0` 的「返回全部」是未定义意图，应改为 `ge=1`。
若产品确实要「显示全部」，则新增 `all: bool = Query(False)` 参数，避免用 `-1`/`0`/`10000` 三种隐式魔法值表达同一语义
（前端 `DataCenter/index.tsx:403` 目前正是用 `limit=10000` 表达「显示全部」）。

**验证方式**：`curl "…/datacenter/instruments?limit=-1"`（对照 `limit=1`）比较返回条数；加边界后应返回 `40000`。

---

### F11｜P3｜`alerts` 读写角色倒挂：viewer 不能读事件，却能把全部事件标为已读

**位置**：`backend/app/api/v1/alerts.py:248`（`GET /events` → `require_role("researcher")`）
vs `alerts.py:271`（`POST /events/read` → `require_role("viewer")`，支持 `{"all": true}`）。

**现象**：`AlertEvent.is_read` 是**全局列**（无 `user_id` 维度），因此一个 viewer 令牌
（不能读取事件内容）可以直接 `POST /api/v1/alerts/events/read {"all": true}`，
把 researcher/其他账号的未读预警全部清成已读 —— 写权限低于读权限，且是跨账号的状态污染。
前端 `/alerts` 页面是 `<RequireRole>`（`App.tsx:104`，`RequireRole.tsx:28` 默认 `researcher`），
所以 UI 路径不可达；但直连接口可达。

另：本批 declare-vs-frontend 对比中，`/alerts` 页 researcher ↔ `/alerts/*` 全部 researcher 是一致的，
**只有 `events/read` 这一个写端点例外**。

**具体改法**：二选一 ——
(a) `events/read` 提到 `require_role("researcher")`（与本页其余端点一致）；
(b) 若确实允许 viewer 处置自己看到的预警，则先给 `AlertEvent` 加 owner 维度并在查询里按 `user` 过滤。
**验证方式**：`viewer` 令牌调 `POST /api/v1/alerts/events/read {"all": true}`，期望 `40300`。

---

### F12｜P3｜`ops.py:47` 年份默认硬编码 `2026`，与字段声明「默认最新一年」不符

**位置**：`backend/app/api/v1/ops.py:25` vs `ops.py:47`

```python
year: int | None = Field(None, ge=2020, le=2100, description="扫描年份（默认最新一年）")   # L25
...
year = req.year or 2026                                                                   # L47
```

**实测**：当前磁盘上可用年份的**最大值恰好是 2026**

```
=== all distinct year partitions (前 400 个标的) ===
2018 2019 2020 2021 2022 2023 2024 2025 2026
```

所以**今天**两者巧合一致，无即时错误。但 2027 年起不传 `year` 会去扫 2026（陈旧年份）却仍自称「最新一年」；
而在 2026 分区尚不存在的环境（新装/回滚）里，所有标的 `continue`，返回
`n_issues=0, symbols_scanned=0` 的 `code=0` —— 一份**假清白**报告（`symbols_scanned=0` 是弱线索但不显眼）。

**具体改法**：`year = req.year or max(available_years)`（复用 `/datacenter/datasets` 已有的年份扫描），
并把 `description` 与实现对齐；若一年分区都没有则抛 `AQPException(ERR_DATA_EMPTY, ...)`（与 L42-46 已有做法一致）。
**验证方式**：monkeypatch 磁盘只有 `year=2024` 分区，断言不传 `year` 时扫的是 2024 而非空转。

---

### F13｜P3｜死代码：`errors.py` 的两个认证错误码映射不可达

**位置**：`backend/app/core/errors.py:88-95`

```python
_AUTH_DETAIL_CODES: dict[str, int] = {
    "UNAUTHORIZED": ERR_UNAUTHORIZED,
    "TOKEN_EXPIRED": ERR_TOKEN_EXPIRED,
    "INVALID_TOKEN": ERR_INVALID_TOKEN,
    "RATE_LIMITED": ERR_RATE_LIMITED,      # ← 全仓无生产者
    "PIPELINE_BUSY": ERR_PIPELINE_BUSY,    # ← 全仓无生产者
    "FORBIDDEN": ERR_FORBIDDEN,
}
```

**证据**：全仓 `grep 'HTTPException(status_code=200, detail='` 只产出 4 个字面量 ——
`UNAUTHORIZED`(×2)、`FORBIDDEN`、`INVALID_TOKEN`、`TOKEN_EXPIRED`；
而 `grep 'detail="RATE_LIMITED"|detail="PIPELINE_BUSY"'` **0 命中**。
限速与管道繁忙实际走的是 `AQPException(ERR_RATE_LIMITED)` / `AQPException(ERR_PIPELINE_BUSY)`
（`auth.py:71`、`train_service.py:422`、`orchestrator.py` → `PipelineBusy`），根本不经过这张表。

**风险**：两者语义不同（前者是 HTTPException detail 常量映射，后者是 AQPException 直传），
留着会误导后来者以为「抛 `HTTPException(detail="PIPELINE_BUSY")` 也能得到 40900」。
**改法**：删除两条，或反过来让 `PipelineBusy` 统一转成 `HTTPException(detail="PIPELINE_BUSY")` 收敛到单一路径。

---

### F14｜P3｜死代码：`POST /settings/apikeys/rotate` 无任何前端引用且永不成功

**位置**：`backend/app/api/v1/app_settings.py:254-263`

```python
@router.post("/apikeys/rotate")
async def rotate_api_key(_user: dict = Depends(require_role("admin"))) -> APIResponse[dict]:
    return fail(ERR_PARAMS, "API Key 功能未启用；如需第三方集成请配置 .env 中的服务端凭据")
```

**证据**（前端全量递归扫描，非 PowerShell `**` 单层通配）：

* 本批 43 个写端点 + 34 个读端点中，**只有 2 个**的路径字面量在前端完全不存在：
  `POST /api/v1/settings/apikeys/rotate` 与 `GET /api/v1/alerts/health`；
* `grep 'apikey|apiKey|api_key' frontend/src` → **0 命中**（仅有 `rotate` 命中的无关 CSS/图表代码）。

**定性**：`/alerts/health` 是**有意的运维端点**（`alerts.py` 头部说明「运维可据此配 data_health 告警」），
属于「无 UI 但有 HTTP 入口」，不算死代码；`/apikeys/rotate` 则**既无 UI 入口、又永远返回 `fail`** ——
一个需 admin 权限、不产生任何行为、且不可能成功的兼容残桩。

**改法**：删除该路由（前端零引用，删除零风险）；若需保留对外承诺，改为返回 `ERR_NOT_FOUND(40400)` 语义
「功能未实现」而不是 `ERR_PARAMS(40000)`「参数错误」—— 当前把「功能不存在」报成「参数错误」也是误导。

---

### F15｜P3｜一致性：viewer 级页面内嵌 researcher 级操作，`TrainPanel` 对被拒用户不设防

**位置**：`frontend/src/App.tsx:97`（`/data` → `RequireRole minimum="viewer"`）、
`frontend/src/pages/DataCenter/index.tsx:890`（无条件 `<TrainPanel />`）、
`frontend/src/pages/DataCenter/TrainPanel.tsx:53/76/86`。

| 该面板调用的端点 | 后端要求 | viewer 实际结果 |
|---|---|---|
| `GET /datacenter/train/readiness` | **无（F1）** | `code=0`（能读） |
| `GET /datacenter/train/status`（`useTaskPolling`，`TrainPanel.tsx:44`） | researcher | `40300` → 面板显示「训练状态加载失败」 |
| `POST /datacenter/train/start` / `cancel` | researcher | 点「开始训练」→ `40300` |

**现象**：一个 viewer 打开 `/data` 会看到完整的「开始训练 / 取消训练」按钮（`TrainPanel` 内**没有任何角色判断**，
只有 `disabled={busy || running || gate !== true}`），点击才失败。同样形态也存在于同页的 `TextDataPanel`
（`/datacenter/text/status` viewer vs `/text/import`、`/text/build-factor` researcher）。

**改法**：用 `hasMinimumRole(user?.role, 'researcher')`（`RequireAuth.tsx:20` 已导出）
把整块面板换成「需 researcher」提示，或在 `RequireRole` 外包一层；同时把 `/data` 里的训练入口
与后端角色对齐（若产品意图是「viewer 可看不可训」，则按钮应禁用并说明原因 —— 与
`RequireAuth.tsx:49-56` 已有的「权限不足」文案风格一致）。

---

## 3. 写端点 × 要求角色 清单（本批 43 个，**实测自路由依赖内省**）

| # | 方法 | 路径 | 要求角色 | 备注 |
|---|---|---|---|---|
| 1 | POST | `/api/v1/backtest/run` | researcher | `compute_slot` |
| 2 | POST | `/api/v1/backtest/strategy-run` | researcher | `compute_slot` |
| 3 | POST | `/api/v1/backtest/signal-analysis` | researcher | `compute_slot` |
| 4 | POST | `/api/v1/portfolio/backtest` | researcher | `compute_slot` |
| 5 | POST | `/api/v1/datacenter/sync` | researcher | `_sync.running` 门禁 |
| 6 | POST | `/api/v1/datacenter/sync/auto` | researcher | 写 `.auto_sync.json` |
| 7 | POST | `/api/v1/datacenter/sync/cancel` | researcher | |
| 8 | POST | `/api/v1/datacenter/sync/fetch` | researcher | 外部拉取 |
| 9 | POST | `/api/v1/datacenter/text/import` | researcher | 写库 |
| 10 | POST | `/api/v1/datacenter/text/build-factor` | researcher | 写 parquet |
| 11 | POST | `/api/v1/datacenter/mirror/rebuild` | researcher | 重建镜像 |
| 12 | POST | `/api/v1/datacenter/train/start` | researcher | `pipeline_lock` + running 门禁 |
| 13 | POST | `/api/v1/datacenter/train/cancel` | researcher | |
| 14 | POST | `/api/v1/ops/quality-scan` | researcher | 只读扫描；200/2499 截断（F4） |
| 15 | POST | `/api/v1/ops/dag/rerun` | researcher | 失败返回 `code=0`（F7） |
| 16 | POST | `/api/v1/monitor/run` | researcher | 触发自动重训 |
| 17 | POST | `/api/v1/alerts/rules` | researcher | |
| 18 | PUT | `/api/v1/alerts/rules/{rule_id}` | researcher | |
| 19 | DELETE | `/api/v1/alerts/rules/{rule_id}` | researcher | |
| 20 | **POST** | **`/api/v1/alerts/events/read`** | **viewer** | ⚠️ 低于同资源读端点（F11） |
| 21 | **POST** | **`/api/v1/notify/stream-ticket`** | **viewer** | 一次性 ticket 签发（F2） |
| 22 | POST | `/api/v1/desk/kill-switch` | researcher | 熔断 + 撤单 |
| 23 | POST | `/api/v1/desk/exclusion` | researcher | |
| 24 | POST | `/api/v1/desk/exclusion/toggle` | researcher | |
| 25 | POST | `/api/v1/desk/orders` | researcher | 下单 |
| 26 | POST | `/api/v1/desk/fills/run` | researcher | 撮合 |
| 27 | POST | `/api/v1/desk/attribution` | researcher | `compute_slot` |
| 28 | POST | `/api/v1/studio/mining/start` | researcher | ⚠️ 无 `compute_guard`/幂等（F8） |
| 29 | POST | `/api/v1/studio/mining/cancel/{task_id}` | researcher | |
| 30 | POST | `/api/v1/studio/nl-to-factor` | researcher | LLM 调用 |
| 31 | POST | `/api/v1/studio/alpha-eval` | researcher | |
| 32 | POST | `/api/v1/studio/factors` | researcher | |
| 33 | DELETE | `/api/v1/studio/factors/{factor_id}` | researcher | |
| 34 | POST | `/api/v1/studio/factor-report` | researcher | |
| 35 | POST | `/api/v1/export/backtest` | researcher | `compute_slot` |
| 36 | POST | `/api/v1/export/strategy-backtest` | researcher | `compute_slot`；xlsx 公式注入（F3） |
| 37 | **PUT** | **`/api/v1/settings/preferences`** | **viewer** | 见下方说明 |
| 38 | PUT | `/api/v1/settings/engine` | **admin** | ✅ 实测 viewer→40300 |
| 39 | POST | `/api/v1/settings/connectors/test` | researcher | 外网探测 |
| 40 | POST | `/api/v1/settings/apikeys/rotate` | **admin** | 死端点（F14） |
| 41 | POST | `/api/v1/settings/data/sync` | researcher | 复用 datacenter sync |
| 42 | POST | `/api/v1/settings/data/cache/clear` | **admin** | ✅ 实测 viewer→40300 |
| 43 | POST | `/api/v1/settings/db/backup` | **admin** | ✅ 实测 viewer→40300 |

**角色分布**：researcher 36 · admin 4 · viewer 3。
**越权重点项结论**：`datacenter.py` / `ops.py` / `studio.py` / `app_settings.py` 的**管理类端点全部正确**
（`/settings/engine`、`/settings/data/cache/clear`、`/settings/db/backup`、`/settings/apikeys/rotate` 全 admin；
主线同步/训练/镜像/重跑全 researcher）。**没有「只校验登录不校验角色的写操作」**（唯一的 viewer 级写是
`notify/stream-ticket` 与 `settings/preferences`，以及 F11 的 `alerts/events/read`）。

**两个 viewer 级写的定性**（都被判定为设计意图而非越权）：
`notify/stream-ticket` 只换一张 60s 一次性票，权限不超出 viewer 自身；
`settings/preferences`（theme/nickname/email/refresh_freq）是个人偏好 —— **但注意**
`app_settings._save_settings` 写的是 `user_settings` 表的**单行** `user_id='default'`（`app_settings.py:91-94`），
所以「个人偏好」实际上是**全局共享的一行**，任何 viewer 保存都会覆盖其他账号的偏好。
单租户教学工具下影响很小，故仅记录，不计为独立缺陷；若将来引入多账号，这是必须先修的共享状态。

**对照全仓口径**：本批 43 个 + B7a（auth 2 + research 8）= **53**，与
`tests/test_write_endpoints_smoke.py:322` 的 `assert len(WRITE_ENDPOINTS) == 53` 完全吻合，
说明该注册表当前无遗漏、无腐化（但它对 GET 无效 —— 见 F1 覆盖缺口）。

---

## 4. 读端点角色表 + 三个专项核查（含「未发现问题」的明确结论）

### 4.1 读端点（GET，本批 34 个）

| 角色 | 数量 | 端点 |
|---|---|---|
| viewer | 14 | `datacenter/overview`、`datacenter/datasets`、`datacenter/quality`、`datacenter/task-stats`、`datacenter/instruments`、`datacenter/text/status`、`datacenter/mirror/status`、`notify/stream`(自定义 checker)、`notify/recent`、`monitor/health`、`portfolio/search`、`studio/mining/status/{id}`、`watchlist/dashboard`、`watchlist/correlation`、`settings` |
| researcher | 18 | `datacenter/logs`、`datacenter/sync/status`、`datacenter/sync/tasks/{id}`、`datacenter/sync/auto`、`datacenter/train/status`、`ops/lineage`、`ops/dag`、`alerts/health`、`alerts/rules`、`alerts/events`、`desk/kill-switch`、`desk/exclusion`、`desk/exclusion/screen`、`desk/orders`、`desk/account`、`desk/capacity`、`studio/factors`、`export/screener` |
| **无** | **1** | **`datacenter/train/readiness`（F1，P0）** |

（`export/screener`、`studio/factors`、`datacenter/logs`、`alerts/*` 等 GET 要求 researcher 严于 viewer，
符合「**最低** viewer」的表述 —— 不构成契约违反，但与前端页面角色需逐一对照，已出具 F15。）

### 4.2 专项：`notify.py` SSE 一次性 ticket —— **实现正确，未发现 P0–P2 缺陷**

逐条回答简报的四个提问：

1. **是否真只能消费一次** —— 是。`core/auth.py:86-113`：Redis 侧用**原子 `GETDEL`**
   （`redis_client.py:196-212`，注释明确「Redis 可用时用 GETDEL 保证多 worker 下 ticket 只能被一个请求消费」）；
   内存侧用 `lru_take`（`cache/memory.py:69-78`，在 `_LOCK` 内 `pop` 后判过期，原子）。
2. **是否可重放** —— 否。已由 `tests/test_notify_sse_ticket.py:93-104`
   (`test_stream_rejects_missing_or_replayed_ticket`) 固化：先消费再拿同一 ticket 建连 → `40100`。
3. **是否会把 JWT 放进 URL** —— 否。URL 里只有**不透明随机 ticket**（`secrets.token_urlsafe(32)`，`auth.py:74`），
   JWT 始终走 `Authorization: Bearer`（`notify.py:58/67`，前端 `notify.ts:28`）。
4. **降级是否破坏一次性语义** —— 否，且处理得比多数实现更严谨：ticket 以 `r.`/`m.` **前缀编码存储域**
   （`auth.py:44-47`），Redis 侧票据在 Redis 不可用时**绝不回落到本地 LRU**
   （`redis_client.py:203-204` 返回 `redis_available=False` → `consume_sse_ticket` 直接 `None`），
   避免「Redis 故障期间同一张票据被本地副本二次消费」。以可用性换严格一次性，方向正确。
5. **失败原因是否泄露** —— 否。过期 / 不存在 / 已消费 / 格式错**统一返回 `None`**
   → `HTTPException(200,"UNAUTHORIZED")`（`notify.py:63-65`），调用方无法区分。

**唯一缺陷是日志侧的 F2（ticket 进 access log）**，与消费逻辑本身无关。

### 4.3 专项：`export.py` 文件名与响应头注入 —— **未发现缺陷**

* `Content-Disposition` 的 `filename` 只由 `export.py:50`（`data['date']`，服务端生成）、
  `export.py:71`（`req.start`/`req.end`）、`export.py:111`（同上）拼接；
  `BacktestRequest.start/end` 与 `StrategyBacktestRequest.start/end` 均带
  `pattern=r"^\d{4}-\d{2}-\d{2}$"`（`backtest.py:31-32` 等），**无法注入 `\r\n` 或引号**，
  也不存在把用户串当路径片段拼接的写法（无路径穿越面）。
* 真正的导出侧缺陷是 **F3 的 Excel 公式注入**（用户串进单元格而非文件名）。

### 4.4 专项：裸 500 路径排查

**结论：本批未发现「裸 HTTP 500」**。兜底链路完整：`core/errors.py:160-174` 的
`@app.exception_handler(Exception)` 能接住端点内异常并返回
`HTTP 200 + code=50000`（实测 `/export/screener?date=not-a-date` 得到 `http=200 code=50000`，
`PanicGuardMiddleware` 另接 `BaseException`）。

但发现**两类次级问题**（已单列）：

* **错误码错配**：客户端参数错误被归为 `50000` 系统错误（F5，`export.py:38`）；
* **兜底后重抛**：Starlette `ServerErrorMiddleware` 在发出响应后 `raise exc from None`，
  故该请求在 uvicorn 侧仍会打印 `ERROR ... Exception in ASGI application` + 完整堆栈。
  即「响应正确但日志里有 ERROR 堆栈」，会让日志告警产生误报。F5 的改法同时消除这两点。
* 另注意到 `datacenter.py:1017` 自行把异常转成 `fail(5001, ...)` ——
  `5001` 是**不在 `core/errors.py` 任何码段内**的临时码；同文件的临时码还有
  `fail(4002, ...)`（L684、L952）、`fail(5000, ...)`（L689）、`fail(4003, ...)`（L969）。
  前端无法按码分类处理这套自制码表。属一致性小项，建议并入统一码段
  （`ERR_DATA_SOURCE=51000` / `ERR_SYSTEM=50000` / `ERR_PIPELINE_BUSY=40900` / `ERR_PARAMS=40000`）。

---

## 5. 覆盖缺口清单（供父审核员纳入后续用例）

| 缺口 | 说明 | 对应发现 |
|---|---|---|
| **GET 端点无全局不变式** | `test_write_endpoints_smoke.py:171-179` 的运行时扫描只取 POST/PUT/DELETE/PATCH；`test_read_endpoints_rbac.py:45-95` 的 `EXPECTED_ROLES` 是手工白名单且**漏掉 `/datacenter/train/readiness`** | **F1（P0）** |
| 无「viewer 页面内的 researcher 级操作」前端用例 | 无 tsc/测试捕捉角色—页面错配 | F15 |
| 无 xlsx 单元格类型断言 | 现有导出用例只验字节/表头，不验 `data_type != 'f'` | F3 |
| 无非法 `date` query 的错误码用例 | `test_write_endpoints_smoke.py` 只测**带 body** 的结构错（`json=[1,2,3]`），不测 query 参数格式错 → 掩盖 `50000` 错配 | F5 |
| 无「失败即非零信封」用例 | `test_envelope_contract_holds_for_all_write_endpoints` 只断言响应结构，不断言失败分支的 `code != 0` | F7 |
| 无并发/幂等用例 | 训练有 `_job.running` 但无对应用例；GP 挖掘无门禁也无用例 | F8 |
| 无 access log 脱敏用例 | 脚本层无校验启动参数是否含 `--no-access-log` | F2 |

---

## 6. 三条最高优先级行动建议

1. **立刻修 F1**：给 `datacenter.py:1124` 加 `Depends(require_role("viewer"))`，并补上
   「每个 `/api/v1` GET 必须声明非 None 角色，除非在显式公开白名单内」的全局不变式用例 ——
   这一类缺陷（漏装饰器）只有这条不变式能长期挡住。
2. **统一「截断/部分失败必披露」口径**：以 `alerts.py` 的 `truncated` / `recent_truncated` 为模板，
   修 `ops.py:52`（F4）、`datacenter.py` 的 `_scan_dataset`（F9）、以及 `/logs`/`/instruments`/`/orders`（F10）。
   本仓已有正确样板，属于口径漂移而非缺方法。
3. **`core/excel.py` 单点加公式转义**（F3）—— 一处 5 行改动覆盖全部三个导出工作簿，
   是本批性价比最高的修复。