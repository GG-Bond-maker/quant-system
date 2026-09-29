# AQP 全栈审核 · 基线与可复核证据（父审核员自测）

> 本文件记录**父审核员亲自执行**的基线测量与全局不变式检查，是最终报告 1/6 节的可复核依据。
> 所有结论都附「实际执行的命令」与「真实输出摘要」；未执行的一律标注「未验证」。
> 审核日期：2026-09-21 · HEAD 见文末。

---

## 1. 运行环境与沙箱边界（重要：先读）

本审核在**受限沙箱**中执行，有两条**环境限制**会产生测试噪音，均已在测试期间用**注入补丁**绕过，**不属于项目缺陷**：

| 限制 | 表现 | 根因（已定位到源码） | 绕过方式 |
|---|---|---|---|
| 临时目录 ACL | `tempfile.mkdtemp` → `os.mkdir(path, 0o700)` 被拒绝，`pytest` INTERNALERROR `PermissionError: [WinError 5] ...\parquet` | `tempfile` 在 Windows 上用 0o700 创建目录，受限令牌下 DACL 写入被拒 | 注入插件 `audit_mkdtemp_fix.py`：强制 `os.mkdir(..., mode=0o777)` |
| 命名管道禁用 | 日志 `enqueue=True` → `multiprocessing.SimpleQueue` → `_winapi.CreateFile` → `PermissionError`，`lifespan` 启动失败（413 errors） | `backend/app/core/logging.py:42/53/65` 三处 `logger.add(..., enqueue=True)` | 注入插件强制 `enqueue=False` |

补丁文件（**仅测试用，非项目源码**）：`backend/.tmp_testrun/audit_mkdtemp_fix.py`

```
$base='D:\Python_Project\Alpha Quant Platform\backend'
$env:PYTHONPATH="$base\.tmp_testrun"; $env:TEMP="$base\.tmp_testrun"; $env:TMP=$env:TEMP
& "$base\.venv\Scripts\python.exe" -m pytest tests -q -p audit_mkdtemp_fix -p no:cacheprovider
```

> **值得记录的稳健性观察（P3，非环境问题）**：`app/core/logging.py` 的 `setup_logging` 是 `main.py` lifespan 中**唯一没有 try/except 兜底**的启动步骤；一旦日志 sink 不可写（管道/权限/磁盘满），整个进程启动失败而不是降级为「仅 stderr」。同文件中 `enqueue=True` 使**日志可用性成为启动硬依赖**。

---

## 2. 全量离线套件基线

命令（同上，带两处补丁）：

```
1065 passed, 6 failed, 8 skipped, 3 deselected in ~10min
```

项目文档自述基线为 `1071 passed / 8 skipped / 3 deselected / 0 failed`（1071 = 1065 + 6），**总数一致，仅本次 6 例失败**。逐例根因分解（全部为环境产物，**非项目回归**）：

| 失败用例 | 数量 | 直接原因 | 性质 |
|---|---|---|---|
| `tests/test_read_endpoints_rbac.py::test_read_endpoint_allows_authorized[/api/v1/etf/{flow,hot,list}-viewer]` | 3 | `E RuntimeError: 外部数据源访问失败: RemoteProtocolError`（源头 `app/data/realtime.py:139`）→ 逃逸为 `code=50000` | **环境触发（无外网）**，但**逃逸本身是产品缺陷**（见下） |
| `tests/test_pipeline.py::{test_pipe_order_and_success,test_pipe_retry_after_failure,test_pipe_idempotent}` | 3 | `step_build_features` 抛 `PermissionError(13, ..., None, 5, None)`，traceback 帧为 `multiprocessing\connection.py:575, in Pipe` → `_winapi.CreateFile` | **环境触发（禁命名管道）** |

### 2.1 ETF 三例：外部源失败未降级（**真实缺陷，环境只是触发器**）

契约要求：外部数据源失败应**独立降级**（`data.status=degraded`）或至少用 `ERR_DATA_SOURCE=51000`，而不是裸 `50000`。实测该路径把 `RuntimeError` 直接抛到统一异常处理 → 客户端拿到 `code=50000`「系统暂不可用」，无法区分「源头故障」与「平台故障」。
→ 这与 B7b 报告的 `export.py:38`（客户端参数错误被报成 50000）属**同一类错误码错配**。

**B7a 批次已定位到精确缺陷行（本节结论已由 B7a 独立复核并升级为 P1-32）**：
- API 层漏包裹：`app/api/v1/etf.py:288`（flow）、**`:299`（list 的 options，第二处易漏点）**、`:319`（hot）、`:477`；同文件 `overview/performance/scale/detail` 均已正确包裹 ⇒ **属漏改而非设计**。
- 源头：`app/data/etf.py:110,247` → `app/data/realtime.py:139`；根因在 `app/data/etf.py:384`（`build_catalog` 把中国目录调用放在 `try` **之外**，而同函数为美股专门写了保护）。
- 修复价值：这一条同时**修掉全量套件的 3 例红灯**（无需改测试），并使 `ERR_DATA_SOURCE=51000` 首次获得真实抛出点（此前全仓含前端常量零抛点）。

### 2.2 pipeline 三例：顺序敏感的最小复现已定位

二分结果（每步都实跑）：

| 组合 | 结果 |
|---|---|
| `tests/test_pipeline.py` 单独跑 | **通过** |
| `test_api` / `test_expand_universe_2500` / `test_alerts_pipeline_stale_running` / `test_data_prep` + `test_pipeline.py` | 全部通过 |
| `tests/test_feature_incremental.py + tests/test_pipeline.py` | **3 failed, 6 passed（稳定复现）** |

机制（已读码确证）：`test_feature_incremental.py` 用 `monkeypatch.setenv("FEATURE_INCREMENTAL", "1")` 并把特征落到 **session 级共享 `DATA_ROOT`**；随后 `test_pipeline.py` 的 `step_build_features` 因「已有特征」走**增量分支**（`app/orchestrator.py:139-218`），该分支才触发受限环境下的命名管道创建。
→ 该 3 例**不是产品回归**（增量/全量分支本身逻辑正确，回退守卫行为符合注释）；但它暴露一个测试隔离事实：**同一 session 内 `DATA_ROOT` 全共享，跨文件存在隐式状态依赖**。

> 补充（诚实标注无法确证的细节）：`Pipe` 的**具体调用方**未能捕获——模块级属性打补丁（`connection.Pipe`/`multiprocessing.Pipe`/`queues.Pipe`）与 `sys.setprofile`/`threading.setprofile` 钩子均未命中，说明调用来自**扩展模块/已绑定的 C 层引用**。因该调用只在禁管道环境失败、在正常主机不存在，未继续追查；不影响结论。

---

## 3. 静态检查基线（含 CI 门禁实测）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 lint（CI 口径） | `ruff check app/ --select F401,F811,E9` | **通过**（0 命中） |
| 后端 lint（全 F 规则） | `ruff check app --select F*` | **5 条 F841**（已逐条人工研判，见 §5） |
| 前端类型（CI 口径） | `npm run build`（= `tsc -b && vite build`；`tsconfig.json` 开启 `strict`+`noUnusedLocals`+`noUnusedParameters`） | 等价于 `npx tsc --noEmit --noUnusedLocals --noUnusedParameters` → **0 错误 / 0 未用变量** |
| **后端类型（CI 口径）** | `mypy app/ --ignore-missing-imports`（与 `.github/workflows/ci.yml:37-39` 完全一致，仓库内**无 mypy 配置文件**，故本地结果=CI 结果） | **10 errors in 2 files → CI 的 mypy 步骤恒红** |

`mypy` 10 条逐条研判（**均为良性误报，非运行时缺陷**）：
- `app/data/ingest/etf_instruments.py:28-33`（6 条）：`{"col": pl.String}` 写入 `dict[str, pl.DataType]`，mypy 视 `pl.String` 为 `type[String]`，运行时是 `DataType` 实例 → 无误。
- `app/api/v1/screener.py:286-291`（4 条）：`stats_block` 的三元表达式未被 isinstance 收窄 → 运行时可确证为 `{}` 或 `dict`（`data.get("stats")` 已显式 isinstance 判断）→ 无误。

> **结论（工程纪律，P3）**：CI 的 mypy 门禁**当前是红的且长期未修**（10 个良性误报）——门禁形同虚设：真正的新类型错误会淹没在固定噪音里。建议二选一：修掉这 2 处（`cast`/显式注解，10 分钟内）或把该步降级为 `continue-on-error` 并同时开 `mypy --strict` 的白名单增量门禁。

---

## 4. 全局不变式检查：所有 `/api/v1` 端点的鉴权依赖

父审核员用**运行时路由内省**（非静态 grep）枚举 114 个 `/api/v1` 端点，判定每个端点是否挂了 `require_role` 的 `checker` 闭包或 `require_auth`：

```python
# 关键片段：遍历 app.routes，取 APIRoute，递归 route.dependant 收集依赖函数名
role = 'NONE' if ('checker' not in names and 'require_auth' not in names) else ...
```

**输出（8 个完全无鉴权端点）**：

```
total /api/v1 endpoints: 114
--- NO auth dependency at all (8) ---
  POST /api/v1/auth/login
  POST /api/v1/auth/register
  GET  /api/v1/auth/register/status
  GET  /api/v1/datacenter/train/readiness      ← ⚠️ 未在设计内的公开端点
  GET  /api/v1/market/index/kline
  GET  /api/v1/market/overview
  GET  /api/v1/market/overview/daily
  GET  /api/v1/market/overview/rt
```

裁决：
- 3 个 `/auth/*`：**有意公开**（登录/注册开关）。
- 4 个 `/market/overview*`：`frontend/src/App.tsx:86` 有注释「市场概览是唯一公开业务页」→ **有意公开**（B7b 与 B9a 若报为缺陷应驳回）。
- ⚠️ **`GET /market/index/kline`：本条裁决在 P5 批次被推翻** —— 它虽然出现在上面的「无鉴权」清单里，但**不属于 `App.tsx:86` 的公开声明范围**（该注释只讲"市场概览"）。P5 用 `fastapi.testclient` 直连 ASGI 实测**不带 Bearer 即返回 `code=0` 与完整 `bars`** ⇒ **它是第 2 个"无出处"的未鉴权端点**（见主报告 P1-50）。**父审核员自我更正**：计数「8 个」本身是对的（我的复跑得 9 是因为过滤器把有独立 ticket checker 的 `/notify/stream` 也算进来了，属我方定义放宽）；**真正的错误是下面的裁决把它与 `overview*` 归为一类。**
- **`GET /datacenter/train/readiness`：确认缺陷（P0-3）** — 匿名返回 `code=0`，泄露 torch 设备名、features 绝对路径、样本量、GNN 边数；兄弟端点 `/train/status` 匿名正确返回 40100。独立确认（ASGI 直调，B7b 已完成；本轮红队再次交叉复核维持）。
- **⇒ 无出处端点合计 2 个**（`/datacenter/train/readiness`、`/market/index/kline`），不是 1 个。

> **机制漏洞（比单条缺陷更重要，P5 批次已定位到精确断点）**：`test_write_endpoints_smoke.py:171-179` 的运行时扫描**只取 POST/PUT/DELETE/PATCH**（GET 天生不在守护范围）；而 `test_read_endpoints_rbac.py:184-199` 虽然**遍历了全部 GET 路由**（`_live_get_roles`），但断言**只迭代手工白名单 `EXPECTED_ROLES.items()`** ⇒ **任何不在白名单里的路由都是"未被分类"而非"通过"**。这就是 `/datacenter/train/readiness`（三次登记）与 `/market/index/kline` 能长期存活的原因。
> 建议加一条全局不变式（可直接复用 §4 的 30 行脚本）：**所有 `/api/v1` GET 必须出现在角色白名单或显式公开白名单中，否则失败** —— 即
> ```python
> unclassified = set(_live_get_roles()) - set(EXPECTED_ROLES) - DECLARED_PUBLIC
> assert not unclassified, f"未分类的 GET 路由: {unclassified}"
> ```
> 这**两行**会让 P0-3 与 P1-50 当场变红。详见主报告 §8.3。

---

## 5. 死代码静态扫描（自有工具）

`vulture` 无法安装（无外网）→ 自写 AST+正则扫描器 `docs/audit-2026-09-18/tools/deadcode_scan.py`，输出 `docs/audit-2026-09-18/deadcode-candidates.json`：

| 维度 | 结果 |
|---|---|
| Python 文件 | 255 |
| 未被引用的顶层名 | 224 |
| orphan 模块（无任何 import） | 23 |
| 疑似未被读取的配置项 | 2 |
| 前端文件 | 93 |
| 未引用导出 | 11 |
| orphan 文件 | 1（`components/charts/MarketHeatmap.tsx`） |

`ruff F841`（唯一被认真对待的死变量类）5 条，逐条人工定性：

| 位置 | 变量 | 定性 |
|---|---|---|
| `app/api/v1/backtest.py:316` | `source`（qfq→raw 回退标记） | **信息丢失型真缺陷**：跟踪了复权回退却从不读取 ⇒ 复权口径从不披露（违契约第 6 条）；实测当前 `daily_bar_qfq`(2499) 与 `daily_bar`(2499) 差集为空，回退分支暂不可达 → 定 P2 |
| `app/api/v1/etf.py:436` | `last`（`bars[-1]`） | **意图偏离型**：份额计算用 `e["price"]` 而非最新收盘价；行为是否错误取决于上游是否已含最新价（B7a 复核） |
| `app/backtest/ma_cross.py:104` | `day_idx` | 无害残留，可安全删 |
| `app/ml/gp_miner.py:64` | `head` | 无害残留 |
| `app/ml/monitor.py:718` | `label` | 无害残留（`kind_msg` 已完整表达四种状态） |

---

## 6. 序列化边界实测（nil float：`NaN`/`Inf` 会怎样）

背景：B2 实测 `app/domain/portfolio.py:365-366` 在「基准首值为 0」时产出 `benchmark: nan/inf`；B2 报告推测「前端 `JSON.parse` 会抛」。父审核员实测了**真实序列化链**：

```
orjson      -> b'{"nan":null,"inf":null,"ninf":null}'      # orjson 静默转 null
orjson+np   -> b'{"x":null}'
starlette   -> RAISES ValueError Out of range float values are not JSON compliant
versions: fastapi 0.115.0 starlette 0.38.6
```

裁决（**修正 B2 的表述，随后又被红队二次修正**）：FastAPI 默认响应类是 **Starlette `JSONResponse`**（仓库内无 `default_response_class=ORJSONResponse`，`orjson` 仅用于 Redis 缓存读写与 cache key），其 `json.dumps(..., allow_nan=False)` 在**直接构造**时确实抛 `ValueError`。**但这不是端点的真实渲染路径** —— 见下方红队修正。

### 6.1 ⚠️ 红队二次修正（本条初稿的推论被推翻，**以本节为准**）

初稿由「`JSONResponse` 直测抛 ValueError」推出「该接口对该输入恒返回 `code=50000`」——**该推论不成立**，红队用**真 app + 自造 `data`** 走真实端点路径实测：

| 路径 | 实测结果 |
|---|---|
| 带 `response_model=APIResponse[dict]` 的端点（**`/portfolio/backtest` 正是此类**） | **http=200 / code=0 / body 内 `"benchmark": null`、无 ValueError** —— 走 **Pydantic v2 序列化**（`ser_json_inf_nan='null'`），NaN/Inf 被渲染为 `null` |
| **无 `response_model`** 的路由 | 才会 `code=50000` |
| `JSONResponse({'x':float('inf')})` 直测（= 初稿所测） | 确实 `ValueError` —— **但它不是端点路径** |

补充证据：全站 **114 个 `/api/v1` 路由中仅 4 个无 `response_model`**（`notify/stream` + 3 个 export），且**都不产出 NaN 浮点** ⇒「恒 50000」**不可达**。

**触发条件也被否证**：初稿假设「基准窗口首日停牌/缺失 ⇒ `benchmark.iloc[0]` 为 0/NaN」。实测 `app/domain/portfolio.py:201` 的 `benchmark.reindex(union_idx).ffill().dropna()` 会**丢掉首部 NaN** ⇒ `benchmark.iloc[0]` 恒为首个**有效**收盘；实测 `first=NaN → code=0`（正常返回）。**只有字面 0 才触发**该分支。

**修正后的定级与缺陷描述**：P1-8 **降为 P2（披露类）**，真实缺陷是「**基准异常时静默置 `null`、且响应无 `basis` 披露**」，而非可用性故障。修复方向不变（上游基准缺失应走 `degraded`/`basis` 披露），但**不再是 P1**。

**方法教训（写入报告 §9.2 的流程改进）**：直测库函数 ≠ 端到端路径。凡涉及框架层的结论，必须**经真实 app 走一遍**，否则会得出"某个库抛异常"就断言"端点必崩"的错误推论。

---

## 7. 数据面抽查（只读）

数据库实际位置（由 `PROJECT_ROOT` 推导）：`<repo>/data/sqlite/aqp_test.db`（**不是** `backend/data/`）。只读连接（`file:...?mode=ro`）结果：

| 表 | 行数 | 与其它批次结论的交叉印证 |
|---|---|---|
| `instrument` | 15 | `delist_date` 非空 **0/15**、`list_date` 为 NULL 8/15 → 印证 B5「退市数据未回填」「list_date 大量 NULL」 |
| `data_update_log` | **0** | 印证「全仓无 INSERT、读端点永远空」 |
| `api_tokens` / `user_sessions` | **0** / **0** | 印证「API Token / 会话撤销未接线（JWT 无状态）」 |
| `news_announcement` | **0** | 印证 B3b「公告**写**路径断链」 |
| `dividend_split` | **0** | 印证「分红/送转未接线」 |
| `watchlist` | **0** | 印证 B1「`watchlist` 表无生产写入方」 |
| `screener_snapshot` / `_stats` | 5 / 4 | 快照有产出（选股链路活着） |
| `model_registry` | 6 | 与 B4a「生产模型自 09-05 冻结」一致 |
| `data_jobs` | 3 | — |

### 7.1 备份目录无保留策略 + 被测试执行污染（P3）

```
count=117  totalMB=35.3  oldest=2026-08-31 03:14:05  newest=2026-09-21 19:05:26
```

`POST /settings/db/backup` 直接落 `backend/backups/`，**无任何清理/保留上限**（`config.py` 与 `app_settings.py` 中无 KEEP/RETAIN 相关配置）。
且 `tests/test_write_endpoints_smoke.py:529` **真的会调用该端点**（不是只验权限）⇒ **每次跑测试都会在仓库里堆真实备份文件**（本目录 117 个文件的来源之一，且时间戳与测试运行时刻吻合）。

---

## 8. CI 门禁全景（`.github/workflows/`）

| 门禁 | 命令 | 现状 |
|---|---|---|
| pytest | `pytest -q --tb=short`（带 Redis service、`DATA_ROOT=./data/test_parquet`） | 文档基线绿；本地受沙箱限制需补丁 |
| mypy | `mypy app/ --ignore-missing-imports` | **红（10 errors）**，见 §3 |
| ruff | `ruff check app/ --select F401,F811,E9` | 绿（选择面窄，F841 不入选） |
| frontend | `npm run build`（`tsc -b` + vite build） | 绿（strict + noUnused*） |
| nightly E2E | `pytest tests/test_e2e.py -v \|\| echo "E2E non-blocking"` | **非阻塞**（`|| echo`）⇒ 前端 E2E 失败不会报警 |

补充：前端 `package.json` **没有任何测试框架**（无 vitest/jest），`devDependencies` 仅构建链 → 前端**零单测**，全部依赖 nightly 的 Playwright（且非阻塞）。

---

## 9. 未验证/无法验证的事项（诚实边界）

- Docker/镜像内行为（`Dockerfile` 的 `PROJECT_ROOT` 推导、compose 挂载点、`.env` 是否进容器）：本机**无 docker daemon**，B1 标注为「疑似」并给出验证命令。
- 外网数据源的真实响应（东财/新浪/akshare 字段口径一致性）：本沙箱**无外网**，只能用恒等式/快照数据反证（B3b 已用恒等式证明 ETF 资金流字段口径错）。
- 长周期性能与内存（全量 universe 训练、多年回测）：只做了量级估计与单点实测（B5 实测 2000×250 网格 1.02s），未做压测。
- 前端浏览器行为（渲染、竞态的实际观感）：静态读码 + 类型检查，未跑浏览器（无 dev server 权限）。