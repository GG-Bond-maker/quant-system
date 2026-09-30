# AQP 上线前全检 —— 产品/代码审查报告（Raw Product Review）

**日期**：2026-09-30
**评审员**：产品评审员（GStack 工程团队）
**审计对象**：`D:\Python_Project\Alpha Quant Platform`（`backend/app/` 134 源文件 + `frontend/`）
**审查方式**：`review` skill 七专家视角（SQL 安全 / LLM 信任边界 / 条件副作用 / 边界条件 / 错误处理 / 并发安全 / 工程卫生）+ 对上一轮修复的**逐项源码验证**
**上游基线**：`pre-launch-check-full-2026-09-30.md`（v3）+ `remediation-applied-2026-09-30.md`
**Git 基线**：`master` 已恢复至 `f7c9410`，含 9 个恢复期提交（`18a3987` 为 HEAD）

> **本文定位**：不重复上一轮已确认的结论；重点是**验证上一轮 16 处修复是否真的落地**，以及**发现上一轮遗漏或新引入的问题**。

---

## 📌 TL;DR

- **结论**：🟠 **条件 Go —— 无 P0；3 项 P1 需处理**。（**订正**：本轮曾误判为 P0 的"venv 损坏"已撤回——实测确认是"并发 pip 安装中间态"，非损坏，见 E-1。）
- **阻塞项数**：**0 项 P0** / **3 项 P1**（P1-a 上轮未落地、P1-b 修复不完整、P1-c 修复不完整）。
- **上一轮修复落地率**：抽查 12 项核心修复，**10 项确认落地**（含 compute_pool / errors 脱敏 / 文件 sink diagnose / nginx / socket 超时 / CI 门禁 / parquet 并行 / CVE 升级），**2 项未落地 / 不完整**（详见 P1-a、P1-b）；另有 1 项**修复不完整**（P1-c 生产容器仍泄漏，因为只修了文件 sink、控制台 sink 仍开）。
- **代码层本身质量高**：SQL 全参数化（3 处 f-string 均为内部常量，**无注入风险**）、LLM 信任边界有 AST 白名单、`ruff --select F,E9` **All checks passed**（实测复现）、前端 `tsc --noEmit` **0 error**（实测复现）。
- **产品层最大风险**：**"HTTP 恒 200"契约让基于状态码的监控系统性失效**——这是设计级的可观测性盲区，不是 bug，需在产品/运维层面显式接受并配套替代监控。

---

## 1. 代码审查发现表

| # | 严重度 | 类别 | 文件:行 | 问题 | 建议 |
|---|--------|------|---------|------|------|
| **E-1** | 🟢 **环境时序说明**（**原判 P0「venv 损坏」已订正 / 撤回**） | 验证环境 / 复现前提 | `backend/.venv/Lib/site-packages/`（全局） | **订正说明**：本轮审查曾观察到 `typing_extensions`/`py`/`mypy_extensions`/`pycparser` 缺失、`import app.main` 失败、`pytest`/`mypy` 无法启动，一度判为"venv 损坏（P0）"。**经主理人复核 + 本轮复验，确认为「一个 pip 安装正在并发进行」的中间态，非损坏、非删除**。<br>**证据**：① 相关 `.dist-info` 的 mtime = **14:26:34 / 14:27:32**（正在被写入），且当时系统有 **7 个 python 进程**在跑；② **复验（安装结束后）**：`typing_extensions.py` ✅、`mypy_extensions.py` ✅、`py/__init__.py` ✅ 均已恢复存在；③ `pytest 8.3.3` 与 `import app.main` 已恢复正常（见 §4 验证环境）。<br>⇒ **结论：不构成阻塞项，不作为 P0。** 仅记录为"验收时点需避开并发安装窗口"的复现纪律。 | ① 复现验收前先确认无并发 pip 进程；② 可保留 CI 的 `python -c "import app.main"` 冒烟断言（有独立价值，非因本次误判而设） |
| **P1-a** | 🟠 P1 | 存档安全（**上轮已提，未落地**） | `backend/scripts/backup.py:161` | `tar.extractall(str(data_dir))` **未传 `filter='data'`**。上一轮安全官已确认：含 symlink 的恶意归档可**绕过** `_check_member_paths`（该函数只校验成员**名**，不校验 symlink/hardlink 类型）。Windows 因无建链权限掩盖，**Linux 容器真实可利用**。本轮验证：**该行代码原样未改** | 一行：`tar.extractall(str(data_dir), filter="data")` |
| **P1-b** | 🟠 P1 | 并发安全（**新发现，修复不完整；经主理人独立侦察交叉验证**） | `market.py:670,837-838,873-874,955,1037,1080`、`etf.py:599,610,749,846`（+ 其余 `to_thread` 点） | **专用池迁移只做了"请求路径上的 5 处"，其余大量 `asyncio.to_thread` 仍走默认池（22 槽，含 `/health/ready`）**。计数存在口径差异（主理人独立侦察：全仓 `to_thread` **133 处** vs 用池 **4 处**；本轮 grep：`await asyncio.to_thread` **117 处**）——**无论取哪个口径，"用池"都是个位数，"用默认池"是三位数**，结论一致。<br>其中**同一端点的不同代码路径不一致**：`/overview/daily` 缓存在场时走计算池（`:889`），**历史快照分支（`:873-874`）却走默认池**；`/overview/rt` 请求路径走计算池（`:802`）但 **SWR 后台重建（`:838`）走默认池**。<br>⇒ 上一轮声称"重计算一律走专用池"的口径**未兑现**，且**同一端点的两条路径爆炸半径不同**，与设计意图矛盾。**主理人建议本条升为 P1 甚至 P0-候选**（它是 B1 隔离方案能否成立的关键） | ① 明确口径：把**所有重计算**（无论请求路径 / 后台 / 历史分支）统一切到 `get_compute_pool()`；② 或至少在 `compute_pool.py` docstring 里如实声明"仅请求路径受保护"，消除"已全隔离"的误导 |
| **P1-c** | 🟠 P1 | **敏感信息泄漏（修复不完整，新发现）** | `docker-compose.yml:49-69`、`backend/app/core/config.py:80`、`backend/app/core/logging.py:47-48` | **上一轮只修了文件 sink 的 `diagnose`，控制台 sink 仍开着，且生产 `DEBUG` 仍是 `True`。** 证据链：① `config.py:80 DEBUG: bool = Field(default=True)`；② `logging.py:47-48` **控制台 sink** 用 `backtrace=settings.DEBUG` / `diagnose=settings.DEBUG`（= **True**）；③ `docker-compose.yml` 的 `environment:` 列表**只传了 10 个键**（`ENV/REDIS_*/TZ/ADMIN_TOKEN/JWT_SECRET/ALLOW_*/CORS_ORIGINS`），**既没有 `DEBUG=false`，也没有 `LOGURU_DIAGNOSE`、`SOCKET_DEFAULT_TIMEOUT_SECONDS`**；④ compose **也不挂载根 `.env`** 进容器（volumes 仅 `./data`、`./backend/logs`）⇒ 根 `.env` 里精心写好的 `DEBUG=false`/`LOGURU_DIAGNOSE=0` **在生产容器里根本不生效**。<br>**后果**：生产容器中 `DEBUG=True` ⇒ 控制台 sink `diagnose=True` ⇒ 登录路径抛异常时**把局部变量（含 password/token 明文）转储到 stdout / 容器日志**（Docker logging driver、日志采集系统）。这正是 F-11 想消除的泄漏，只是换了输出通道。<br>（注：`SOCKET_DEFAULT_TIMEOUT_SECONDS` 代码默认 `10.0`，恰好与 `.env` 一致 ⇒ **B7 修复侥幸生效**；`LOG_LEVEL` 默认 `INFO` ⇒ 无碍。） | ① **首选**：compose 的 `environment:` 显式补 `DEBUG=false`、`LOGURU_DIAGNOSE=0`、`SOCKET_DEFAULT_TIMEOUT_SECONDS=10`（或改挂载 `.env`）；② 纵深防御：`logging.py` 控制台 sink 的 `diagnose` 改为 `settings.DEBUG and settings.ENV != "prod"`（与文档描述一致，不靠 env 传参） |
| **N-2** | 🟡 P2 | 监控盲区（**设计级，需产品裁决**） | `backend/app/core/errors.py` 全文件 | **"HTTP 恒 200"契约**：所有业务错误、鉴权失败（40100）、参数错误（40000）、甚至未捕获异常（50000）都返回 `HTTP 200 + code!=0`。**零 5xx ≠ 健康**。实测 `qa-lead-anon-dos2.log` 佐证：22 路匿名 DoS 期间 **HTTP 分布 `{200: 22}`、业务码 `{0: 22}`，零 5xx**，而 `/health/ready` 已挂死 12003ms。⇒ 任何基于 HTTP 状态码的告警/Prometheus 指标**对本平台系统性失效** | ① **必须**把告警口径从"5xx 率"改为**业务码分布**（`code!=0` 比率）与**端点 P99 延迟**；② 补 `/health/ready` 探针告警（它是唯一能反映"全站被打满"的信号）；③ 在 README/运维手册显式写明这一契约，避免新运维按常规 5xx 告警配置 |
| **N-3** | 🟡 P2 | 产品/文档一致性 | `frontend/src/App.tsx:87-88`、`market.py:770-780` | **匿名端点口径现已自洽**：`/` 与 `/market` 未包 `RequireRole`，对应 `/overview/rt`、`/overview/daily` 保持匿名；`/overview`（DEPRECATED、前端零调用）已加 `require_role("viewer")`（`:926` 实测确认）。**结论：这是**有据可依的刻意决策**，且 `market.py:770-780` 已写长注释锁定。** 但 README **未声明"这两个 API 可匿名取 AI 推荐榜/情绪/资金流数据"**（只声明"市场概览**页面**可公开访问"） | README 补一句 API 层匿名声明，使"页面公开"与"API 公开"口径一致，避免安全评审时被误判为遗漏 |
| **N-4** | 🟡 P2 | 错误语义 | `backend/app/core/errors.py:221-231` | `RequestValidationError` 处理器**将 `errors` 明细回显进响应体**（`fail(ERR_PARAMS, "请求参数错误", errors)`）。虽然 `_jsonable_validation_errors` 已剥离 `input`（**脱敏有效**），但 `errors` 数组**无长度上限** —— 校验失败字段极多时响应体可被放大（上一轮 F-13 已记录 body 类型不匹配时可达 1MB，**脱敏修复了明文泄漏，但未限制体积**） | 给 `errors` 数组设上限（如 `errors[:10]`），并给单个 `msg` 设长度上限 |
| **N-5** | 🟢 P3 | 工程卫生 | `backend/scripts/backup.py` | 上一轮 QA 已记录：`python scripts/backup.py --help` **会直接执行真实备份**。本轮验证：**未修**（无 argparse `--help` 早退） | 加 `if "--help" in sys.argv: print_usage(); return` 或引入 argparse |
| **N-6** | 🟢 正向 | 工程质量 | `backend/app/` 全量 | ✅ **SQL 安全**：全仓 3 处 f-string SQL（`ops.py:204`、`migrations.py:10,14`、`task_store.py:77`）**均为内部常量**（表名/列名来自硬编码常量，值走 `?` 参数化）⇒ **无注入风险**（`ops.py:195` 甚至有 docstring 明确说明）。<br>✅ **LLM 信任边界**：`studio.py` 的 NL→因子 走 `alpha_expr.parse_expr` **AST 白名单**（拒绝属性访问/下标/未知算子），LLM 输出**不被执行**，且 `LLM_PROVIDER` 默认 `none`（未配置时返回 53000）。<br>✅ **JWT**：算法硬锁 HS256 + issuer 校验 | 保持 |

### 1.1 上一轮修复的落地验证（抽样 12 项）

| 上一轮修复项 | 声称 | 本轮实测证据 | 结论 |
|---|---|---|---|
| `core/compute_pool.py` 专用池（6 槽） | ✅ | 文件存在（7013B），`get_compute_pool()` 懒加载 + `shutdown` 置空；`market.py`×3 + `etf.py`×2 已改 `run_in_executor` | ✅ **落地** |
| `market.py` `/overview` 加鉴权 | ✅ | `:926 _user: dict = Depends(require_role("viewer"))` | ✅ **落地** |
| `/overview/rt`、`/daily` 保持匿名 | ✅（刻意） | `:781`/`:850` 无鉴权依赖；`:770-780` 有锁定注释 | ✅ **落地** |
| `errors.py` 剥离 `input` | ✅ | `:71 _SAFE_KEYS = ("type","loc","msg","ctx")`，`:74` 白名单过滤 | ✅ **落地** |
| `logging.py` sink `diagnose=False` | ✅ | `app.log` sink `diagnose=False`+`compression="zip"`；`app.json.log` 同 | ⚠️ **文件 sink 落地；控制台 sink 未改**（见 P1-c） |
| `socket.setdefaulttimeout` | ✅ | `main.py:18 import socket`、`:59-61` 设置；`config.py:122` 字段 | ✅ **落地** |
| `nginx.conf` 700s + SSE + 探针反代 | ✅ | `proxy_read_timeout 700s`；`location = /metrics`、`= /health{,/ready,/live}`、`= /healthz` 均真实反代 | ✅ **落地** |
| `parquet_store.py` 并行化 | ✅ | `_SCAN_MAX_WORKERS=8`；`:347`/`:724` `pool.map` | ✅ **落地** |
| CVE 升级（pyarrow/lightgbm） | ✅ | `requirements.txt:48 pyarrow==25.0.1`、`:62 lightgbm==4.7.0`、`:63 narwhals==2.26.0` | ✅ **落地**（且核实版本存在于 PyPI） |
| CI 门禁修复（requirements-dev） | ✅ | `ci.yml` 用 `pip install -r backend/requirements-dev.txt`；`requirements-dev.txt` 含 `mypy==2.3.1`/`ruff==0.16.6`（PyPI 核实存在） | ✅ **落地** |
| `backup.py` `extractall(filter='data')` | 上轮 P1 | `:161 tar.extractall(str(data_dir))` —— **无 filter 参数** | ❌ **未落地**（见 P1-a） |
| 重计算全量迁专用池 | ✅ | 仅请求路径 5 处迁走，后台/历史路径 **14 处仍走默认池** | ⚠️ **不完整**（见 P1-b） |

---

## 2. 产品/上线就绪评估

### 2.1 Go / No-Go：🟠 **条件 Go**

**理由**：产品**功能与代码质量**达到可上线水准（ruff/tsc 全绿、SQL 安全、LLM 边界清晰、上一轮 7 项 P0 确有 5 项落地），**无 P0**。剩余风险集中在 3 项 P1：一处上轮未落地的存档安全修复、一处专用池迁移不完整、以及生产容器仍开着的 `diagnose`（明文凭据落 stdout）。这三项均为**小改**，处置后可放行。

**放行条件（建议）**：
1. **P1-a**：`backup.py` 加 `filter='data'`（一行，消除 Linux 容器可真实利用的存档穿越）。
2. **P1-b**：统一专用池口径（或如实声明范围），消除"同一端点两条路径爆炸半径不同"（**这是 B1 隔离方案能否成立的关键**，主理人建议升 P1/P0-候选）。
4. **P1-c**：compose 补 `DEBUG=false`（+#46 `logging.py` 控制台 sink `diagnose` 收口），消除生产容器明文口令落 stdout。
5. **N-2**：把监控口径改为业务码 + 延迟（否则上线后"服务被打满却零告警"）。

### 2.2 监控盲区（恒 200 契约）—— 上线风险评级 🟠 P1（产品级）

这是**本平台最需要产品/运维显式接受的设计**：

- **事实**：`errors.py` 的 4 个异常处理器（`HTTPException`/`AQPException`/`RequestValidationError`/`Exception`）**全部 `status_code=200`**。鉴权失败=40100、参数错=40000、系统错=50000，**HTTP 层全是 200**。
- **后果**：`qa-lead-anon-dos2.log` 实测——22 路匿名 DoS 期间 **HTTP `{200:22}`、业务码 `{0:22}`、零 5xx**，而 `/health/ready` 已挂死 12s。**若运维按常规配"5xx 率>1% 告警"，本次全站降级 100% 静默。**
- **缓解已部分到位**：上一轮把 `/health/ready` 从假绿改成真实反代（nginx），这是**唯一**能反映"全站被打满"的信号。但**探针告警本身没有被纳入验收**。
- **建议**：告警三件套 = ① `code!=0` 业务码比率；② 端点 P99 延迟；③ `/health/ready` 连续失败。并在 README 明文警告"不要按 5xx 配置告警"。

### 2.3 匿名端点风险 —— 🟡 P2（有据可依，但需文档补齐）

- `/overview/rt`、`/overview/daily` **刻意匿名**，服务公开落地页（`App.tsx:87-88`）。上一轮的 DoS 风险已通过"专用池隔离爆炸半径"降级为"overview 变慢"。
- **残余风险**：匿名端点返回 `recommend`（AI 推荐榜，`recommend_k` 上限 200）、`ai_stats`、`sentiment`、`money_flow`，且 `date=YYYYMMDD` 可拉**任意历史交易日**的推荐快照 ⇒ **核心 IP 匿名可取、可批量爬取**。
- **判断**：作为"公开落地页"的产品决策**可以接受**，但必须①在 README 显式声明 API 匿名；②考虑给 `date` 历史快照加访问频率限制（当前 `refresh=1` 有 5s 防抖，但**批量拉历史日期不受防抖约束**）。

### 2.4 错误处理对用户是否友好 —— 🟢 良好

- 统一信封 `{code,message,data,trace_id,ts}`，`trace_id` 进日志可 grep 定位。
- 错误码语义分层清晰（40100 未授权 / 40000 参数 / 40400 不存在 / 50000 系统），且 `errors.py:221-231` 明确把 405/406/415 归入参数错误而非系统错误（避免"假 500"）。
- 内部异常**不泄露 Python 类名/堆栈**（`errors.py:255-263`），响应体只留"系统暂不可用，请稍后重试"。
- **唯一不足**：见 N-4（`errors` 明细无体积上限）。

### 2.5 README / CHANGELOG 与实际能力一致性 —— 🟡 P2

- **CHANGELOG.md**：三段式历史（`0.1.0` 对象完整 / `0.2.0` 仅存信息 / `Unreleased` 全检修复）**结构诚实**，且明示"勿 `reflog expire` / `gc --prune=now`"——**这是加分项**。
- **偏差**：`Unreleased` 段声称"16 个文件、7 项 P0 全解除"，但实测 **P1-a（backup filter）未落地**、**P1-b（专用池）不完整**。CHANGELOG 的"全解除"口径**略乐观**。
- **README**：未声明匿名 API（见 2.3）。
- **建议**：CHANGELOG 把 P1 批次的"未落地/不完整"如实标注，避免后续评审者误以为已全修。

---

## 3. 行动清单

| # | 优先级 | 行动 | 负责方 | 预期收益 |
|---|--------|------|--------|---------|
| 1 | **P1** | `backup.py:161` 加 `filter="data"`（**一行**） | 后端 | 消除 Linux 容器可真实利用的 tar symlink 穿越 |
| 2 | **P1** | 统一专用池口径：把 `market.py`/`etf.py` **其余重计算**（含历史分支 `:873-874`、SWR 后台重建 `:837-838`）迁到 `get_compute_pool()`；或如实声明范围 | 后端 | 消除"同端点两路径爆炸半径不同"；**B1 隔离方案成立的关键** |
| 3 | **P1** | **生产容器 `DEBUG` 未关**：compose `environment:` 补 `DEBUG=false`+`LOGURU_DIAGNOSE=0`（或挂载 `.env`）；并把 `logging.py` 控制台 sink 的 `diagnose` 收口为 `settings.DEBUG and settings.ENV != "prod"` | 运维 + 后端 | 消除生产容器"登录异常转储明文口令到 stdout/容器日志"（P1-c） |
| 4 | **P2** | `errors.py` 给 `errors` 数组 + `msg` 设长度上限 | 后端 | 限制校验失败响应体放大 |
| 5 | **P2** | README 补"匿名 API"声明（`/overview/rt`、`/daily` 返回 AI 推荐榜等） | 产品 | 文档与实现口径一致 |
| 6 | **P2** | CHANGELOG 如实标注 P1 批次的"未落地/不完整" | 发布 | 避免后续评审误判 |
| 7 | **P3** | `backup.py` 加 `--help` 早退（防误触发真实备份） | 后端 | 消除误操作 |
| 8 | **P3** | 复现纪律：验收前确认无并发 pip 进程（见 E-1） | 发布 | 避免把"安装中间态"误读为环境损坏 |

---

## 4. 诚实边界

- **验证环境（已订正）**：审查期间一度观察到 `import app.main`/`pytest`/`mypy` 失败，曾判为"venv 损坏（P0）"。**经复核确认为「并发 pip 安装中间态」（见 E-1），已撤回该 P0**。复验后 `pytest 8.3.3` 与 `import app.main` 均正常。
  - ⚠️ 但**本轮的 `pytest` 全量回归仍未由本人执行**（留给 qa-lead 的 #8 任务）：因此本报告的代码层结论来自**静态审查 + 实测复现的 ruff/tsc + 源码逐项验证**；"1906 passed"仍是**引用上一轮**，非本轮复现。最终回归结论以 qa-lead 的实测为准。
- **静态审查覆盖**：逐行深读 `errors.py`、`logging.py`、`compute_pool.py`、`main.py`（lifespan）、`market.py`（overview 段）、`etf.py`（详情块）、`backup.py`、`config.py`、`task_store.py`、`studio.py`、`ci.yml`、`nginx.conf`、`requirements*.txt`、`docker-compose.yml`。
- **未逐行深读**：`datacenter.py`（1754 行）、`backtest/engine.py`、`ml/*` 数值内核、前端 `pages/*`（仅模式扫描）。
- **未做**：真实端到端（下单/同步/训练）；Nginx 运行时 curl 验证（未起容器，仅结构核对）。
- **一次误判已订正**：E-1（原 N-1）。教训——**同一时刻的静态快照会把进行中的变更误读为损坏**；凡以文件存在性为判据，须同时看 mtime 与在跑进程。
- **静态审查覆盖**：逐行深读 `errors.py`、`logging.py`、`compute_pool.py`、`main.py`（lifespan）、`market.py`（overview 段）、`etf.py`（详情块）、`backup.py`、`config.py`、`task_store.py`、`studio.py`、`ci.yml`、`nginx.conf`、`requirements*.txt`。
- **未逐行深读**：`datacenter.py`（1754 行）、`backtest/engine.py`、`ml/*` 数值内核、前端 `pages/*`（仅模式扫描）。
- **未做**：真实端到端（下单/同步/训练）；Nginx 运行时 curl 验证（未起容器，仅结构核对）。
- **venv 损坏根因未最终定位**：症状（dist-info 在、模块文件缺失）与上一轮 §6.1 记载的"宿主级 safe-delete 守卫拦截 pip 卸载"高度吻合，但**未排查到具体是哪一步操作、影响了哪些包**。建议重建时留意同类拦截。

---

> 本报告由 GStack 产品评审员生成，关键结论请由工程负责人复核。
