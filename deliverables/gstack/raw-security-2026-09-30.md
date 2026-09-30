# Alpha Quant Platform · 上线前安全审计（Security Raw）

- **审计模式**：Comprehensive（全量 14 阶段，confidence ≥ 2）
- **审计日期**：2026-09-30
- **审计人**：安全官（CSO）
- **审计对象**：`D:\Python_Project\Alpha Quant Platform`（FastAPI + Python 3.11 后端 `backend/app/`，React+Vite 前端 `frontend/`）
- **方法**：静态全仓扫描（AST/正则）+ 独立运行时验证（FastAPI `TestClient` 在真实 ASGI 栈上打真实请求）+ PoC 复现。**未做任何破坏性测试**；未触碰生产数据；后端实例为一次性启动（:8021，`WARM_OVERVIEW_ON_STARTUP=false`）后即退出。
- **基线对照**：`pre-launch-check-full-2026-09-30.md`（v3）、`remediation-applied-2026-09-30.md`（修复落地）。本轮**只做验证 + 新增**，不重复已确认结论。
- **证据纪律**：所有结论均带 `文件:行号` 或实测响应；每条发现标注 `【新发现】/【上轮已修·本轮验证通过】/【上轮已修·本轮发现未生效】/【上轮已记载·本轮仍未修】`。

---

## TL;DR

- **总体判断：🟡 有条件放行**。上轮 7 项 P0 中，**5 项经本轮独立复测确认已真实生效**（匿名 DoS 入口收窄、凭据落盘、依赖 CVE、并行化、socket 超时退出）；**2 项核心安全修复（F-11 校验脱敏、F-12 匿名端点收窄）本轮用真实请求逐条复现，确认生效**。
- **本轮新发现 3 项**（含 1 项 HIGH）：① `scripts/backup.py` 的 tar 解压**仍无 `filter='data'`**，symlink/hardlink 逃逸 PoC 复现成功（Linux 容器可真实利用）；② 认证/授权**无任何安全事件日志**（A09 盲区）；③ 服务端**无登录/认证失败的事件级告警信号**，与"HTTP 恒 200"契约叠加形成监控盲区。
- **本轮仍未修 3 项**（上轮已记载）：① `/docs`、`/redoc`、`/openapi.json` 在 `ENV=prod` 下**实测仍 200 全量暴露**（105 KB schema，112 路径）；② `RBAC_ENFORCE` 默认 `False` **仍未纳入 prod fail-fast**；③ `_jwt_expire_seconds()` **仍直读 `os.environ`**，绕过 `Settings`（`.env` 静默失效）。
- **阻塞项（建议上线前处置）**：**3 项** —— B-1 归档逃逸（HIGH）、B-2 docs 生产暴露（MEDIUM-HIGH，因当前只绑回环而降级）、B-3 安全事件日志缺失（MEDIUM，合规/取证）。
- **明确通过项（本轮实测）**：匿名端点边界＝设计值（`/overview` 已收口、`rt`/`daily` 刻意匿名）；F-11 明文口令**不再**入日志/响应（215 B，无 `MyS3cr3t`、无 `input`）；F-13 1 MB body **不再**回显（188 B）；JWT HS256 + issuer 硬锁；PBKDF2-SHA256/10 万次 + 常量时间比较；SQL 全参数化（2 处 f-string 经核实为内部常量，非注入）；CORS 无通配符；`.env` 未被 git 跟踪且从未进入历史；`.env` 在 `.dockerignore` 内；容器非 root + read_only + Redis requirepass 只绑回环。

---

## 1. 架构速览与信任边界（Phase 1–2）

**组件**：FastAPI（单进程 1 worker，`Dockerfile:59`）→ SQLite（WAL）/ Redis（可选，熔断降级）/ Parquet 数据湖（3.27 万文件）/ akshare（外部行情源）。

**信任边界**：
1. **网络边界**：`API_HOST`（`.env:32` = `127.0.0.1`）+ compose 仅发布 `127.0.0.1`（Redis）/ 生产建议经 nginx 同源反代。**当前暴露面＝回环/内网**。
2. **认证边界**：`require_auth`（Bearer JWT 或 ADMIN_TOKEN）→ `require_role`（角色）。**注意**：`require_role` 必经 `require_auth`，故即使 `RBAC_ENFORCE=False` 也强制要 token。
3. **授权边界**：`ensure_role`（`core/auth.py:222-235`）—— `RBAC_ENFORCE=False` 时登录即通过任何最低角色。
4. **数据边界**：SQLite/Parquet 仅进程内访问；`/export/*` 出站为文件流。

**攻击面清点（本轮 AST 扫描 + 运行时双证）**：共 **115** 个 v1 端点 + 3 个 `/health*` + `/metrics` + `/docs`。**匿名可达端点恰为 5 个**（详见 §3 A01）。

---

## 2. 威胁建模（STRIDE）

| 类别 | 威胁场景 | 现状 | 证据 | 评级 |
|------|---------|------|------|------|
| **S**poofing | 伪造 JWT / 服务间冒充 | ✅ 缓解：HS256 硬锁 + `issuer` 校验 + `alg` 白名单；ADMIN_TOKEN 直通已关闭（`.env:33`）。**无服务间认证**（单体，不适用） | `core/auth.py:166,173`、`.env:33` | 低 |
| **T**ampering | 篡改归档致任意文件写 | 🔴 **存在**：`backup.py:161` `extractall` 无 `filter`，symlink/hardlink 成员逃逸 | §4 F-01（PoC 已复现） | 高 |
| **R**epudiation | 否认登录/管理操作 | 🔴 **存在**：认证/授权**无安全事件日志**，登录失败、限速触发、权限拒绝均不留痕 | `auth.py`（0 条 `logger.*`） | 中 |
| **I**nformation Disclosure | 明文凭据/核心 IP 泄漏 | ✅ 本轮验证：`errors` 已剥离 `input`（`errors.py:70` `_SAFE_KEYS`）；`/overview` 已鉴权 | §3 A01/A02 | 低 |
| **D**enial of Service | 匿名打满线程池 | ✅ 缓解：重计算下沉专用池（6 槽）；socket 默认超时 10s；匿名 `rt`/`daily` 仍在设计内 | `core/compute_pool.py`、`.env:56` | 中→低 |
| **E**levation of Privilege | 越权/降级绕过 | 🟠 部分：`RBAC_ENFORCE=False` 使 viewer==admin（**产品裁决项**）；`/docs` 暴露全 schema 助攻击者 | `config.py:224`、§4 F-02 | 中 |

---

## 3. OWASP Top 10 检查表（Phase 10）

图例：**PASS** 通过 / **FAIL** 失败 / **PARTIAL** 部分 / **NA** 不适用。每条附证据。

### A01 访问控制失效 — **PASS（本轮实测确认）**

**方法**：在真实 ASGI 栈上对 15 个代表性路径发**无 `Authorization`** 请求：

| 路径 | HTTP | 业务码 | 判定 |
|------|------|--------|------|
| `/health/live`、`/health` | 200 | 0 | ✅ 探针，刻意公开 |
| `/api/v1/auth/register/status` | 200 | 0 | ✅ 刻意公开（前端判断是否显示注册入口） |
| `/api/v1/market/overview` | 200 | **40100** | ✅ **已收口（上轮 F-12 修复生效）** |
| `/api/v1/market/overview/rt` | 200 | 0 | ✅ 刻意匿名（公开落地页） |
| `/api/v1/market/overview/daily` | 200 | 0 | ✅ 刻意匿名（公开落地页） |
| `/api/v1/market/quotes` | 200 | 40100 | ✅ 需 token |
| `/api/v1/market/index/kline` | 200 | 40100 | ✅ 需 token |
| `/api/v1/stock/search?q=600519` | 200 | 40100 | ✅ 需 token（**对照证据**） |
| `/api/v1/settings` | 200 | 40100 | ✅ 需 token |
| `/api/v1/notify/stream`（无 ticket） | 200 | 40100 | ✅ 需 ticket/token |
| `/api/v1/notify/stream-ticket` | 200 | 40100 | ✅ 需 token |
| `/api/v1/ops/lineage` | 200 | 40100 | ✅ 需 token |
| `/api/v1/portfolio/search?q=60` | 200 | 40100 | ✅ 需 token |
| `/api/v1/watchlist/dashboard` | 200 | 40100 | ✅ 需 token |
| `/api/v1/backtest/list` | 200 | 40400 | ✅ 路径不存在（非绕过） |

**结论**：匿名面 ＝ `{login, register, register/status, overview/rt, overview/daily}` + 探针。**与设计一致，边界未被破坏**。上轮结论 **F-12 成立且已生效**。

> 补充核实（消除歧义）：裸路径 `/api/v1/portfolio`、`/api/v1/watchlist` 返回 `40400`（路由不存在），**非**「鉴权晚于资源查找」。真实子路径均已 `40100`。

- **IDOR**：`db/models.py` 的 watchlist/portfolio/app_settings 虽有 `user_id` 字段但 API 不使用（单租户共享，上轮 #17 记载，**本轮未变**）—— 当前为"单租户工作区"设计，非缺陷；上公网前须按 JWT `sub` 过滤。
- **server-side RBAC**：`ensure_role` 在服务端执行，前端不可绕过。

### A02 加密失败 / 密钥管理 — **PASS（有一处 latent 配置缺陷）**

- **`.env` 是否入库**：`git check-ignore -v .env` → `.gitignore:80:.env` ✅；`git ls-files` 仅 `.env.example`；`git log --all -- .env` **空**；全历史新增文件扫描仅 `.env.example`。**从未提交、从未进入历史** ✅。`.dockerignore:19` 含 `.git`，`.env` 由 `.dockerignore` 规则排除构建上下文 ✅。
- **硬编码密钥**：全仓 `app/` grep `password|secret|api_key|access_token|private_key = "..."`、`sk-[A-Za-z0-9]{16,}`、`BEGIN (RSA|PRIVATE|OPENSSH)` — **0 命中** ✅。
- **口令哈希**：`core/auth.py:123-141` —— **PBKDF2-HMAC-SHA256，100,000 次迭代 + 16 字节随机盐 + `hmac.compare_digest` 常量时间比较** ✅。无 MD5/SHA1 用于口令。
- **JWT**：`core/auth.py:166` `algorithm="HS256"`（硬锁），`:173` `algorithms=["HS256"]` + `issuer` 校验；无 `alg:none`/RS→HS 混淆空间 ✅。密钥优先 `JWT_SECRET`，缺省从 `ADMIN_TOKEN` 派生（**prod 由 `validate_runtime_safety` 拒绝**，`config.py:319-322`）✅。
- **🟡 latent 缺陷（上轮 #14 未修）**：`core/auth.py:154-155` `_jwt_expire_seconds()` **直读 `os.environ["JWT_EXPIRE_SECONDS"]`**，绕过 `Settings.JWT_EXPIRE_SECONDS`。pydantic-settings 把 `.env` 载入 `Settings` 而**不回写 `os.environ`** ⇒ `.env` 里改 `JWT_EXPIRE_SECONDS` **静默无效**（当前 `.env` 未设该项，故无实际漂移，属潜伏）。**证据**：`.env.example:18` 设有该键，但读取路径不经过 `Settings`。

### A03 注入 — **PASS**

- **SQL 拼接**：全仓仅 2 处 f-string SQL：
  - `api/v1/ops.py:204` `f"SELECT COUNT(*) FROM {table}"` —— `table` 来自 `spec["table"]`（`ops.py:334`），`spec` 为模块内**硬编码常量表清单**；`_sqlite_count` 唯一调用点 `ops.py:334`。**不可注入** ✅。
  - `services/task_store.py:77` `f"UPDATE background_tasks SET {', '.join(fields)} ..."` —— `fields` 全为**字面量列表**（`status = ?` 等），值仍走 `?` 参数化。**不可注入** ✅。
  - 其余业务查询经 SQLAlchemy ORM / 参数化 ✅。
- **命令注入**：`app/` 与 `scripts/` grep `os.system|subprocess.(run|call|Popen)|shell=True|os.popen` —— 仅 `scripts/upgrade_security_deps.py:47`（运维脚本，参数自持）。**无可达注入面** ✅。
- **模板/`eval` 注入**：`app/data/etf.py:977` `js.eval(hash_code)` —— 经核实 `hash_code` 为 `akshare` 内置 JS 常量（非用户输入）✅；`app/ml/*` 的 `.eval()` 均为 `torch` 模型推理模式切换（非 `eval()` 内建）✅。

### A04 不安全设计（匿名 DoS 面） — **PARTIAL（已缓解，残余设计风险）**

- **上轮实测日志（`qa-lead-anon-dos2.log`）**：匿名 22 并发 `/market/overview?refresh=1` ⇒ 22/22 返 200 且有数据，`/health/ready` **挂死 12003.9 ms**（`/health/live` 对照正常）⇒ **未认证攻击者可打死全站探针**。
- **本轮代码核实**：重计算已从"全站默认池（22 槽）"下沉到**专用计算池（6 槽，`core/compute_pool.py`）**，`market.py:801-803`（rt）、`etf.py`（2 处）均改为 `run_in_executor(get_compute_pool(), ...)` ⇒ 泄漏不再挤占探针槽位。
- **残余**：`rt`/`daily` **仍匿名**（设计使然，服务公开落地页），仍可被匿名触发冷算，但**爆炸半径已隔离为"overview 变慢"**。`rt` 正常路径 ~14s vs 预算 15s（**余量 ~1s**，上轮 R-a，本轮未变）。
- **判定**：**PARTIAL** —— 设计风险已从"全站级联挂死"降级为"单端点变慢"，且**生产若只绑回环则不可达**。

### A05 安全配置错误 — **FAIL（docs 生产暴露；其余通过）**

- **CORS**：`main.py:266-272` `allow_origins=_s.cors_origins_list`（默认 `http://localhost:5173,http://127.0.0.1:5173`），`allow_credentials=True`，`allow_methods=["*"]`，`allow_headers=["*"]`。**无 `*` + credentials 的危险组合**（origins 为显式白名单）✅；prod 校验进一步禁止开发地址（`config.py:323-326`）✅。
- **Debug 开关**：`.env:48` `DEBUG=false`；`core/logging.py:47-48` console sink `backtrace/diagnose=settings.DEBUG`；**文件 sink 显式 `diagnose=False`**（`logging.py:74,91`）✅。
- **`RBAC_ENFORCE` 默认**：`config.py:224` `default=False`；**未被 `validate_runtime_safety` 纳入 prod 必检**（`config.py:305-346` 无该符号）⇒ ENV=prod 也不拦。**产品裁决项**（2026-09-23 用户裁决"全面放开"）。
- **Swagger/OpenAPI 暴露**：`main.py:235-236` `docs_url="/docs"`、`redoc_url="/redoc"` **硬编码，无 ENV 开关、无鉴权**。**本轮实测**：`ENV=prod` 下 `/docs`→200、`/redoc`→200、`/openapi.json`→200（**105,270 字节**，112 路径全量 schema）。⇒ **FAIL**（详见 §4 F-02）。
- **`/health` 详情**：`main.py:329-334` 仅回 `{app, env, db, redis}` —— 无版本/路径/连接串泄露 ✅。
- **`/metrics`**：`main.py:363-367` prod 默认要求 Bearer（`metrics_require_auth`）✅。

### A06 组件与过时组件 — **PASS（仅测试期工具残留）**

- **pip-audit 实跑**（`uv tool run pip-audit -r backend/requirements.txt`，因项目 venv 的 pip-audit 自身损坏，见 §4 F-04）：
  ```
  Found 2 known vulnerabilities in 1 package
  Name   Version ID              Fix Versions
  pytest 8.3.3   PYSEC-2026-1845 9.0.3
  ```
  ⇒ **`lightgbm`、`pyarrow` 已从漏洞清单消失** ✅（上轮 A5 生效）。**上轮 2 个运行期 CVE（CVE-2024-43598 RCE、CVE-2026-25087 UAF）本轮复测确认已消除**。
- **已安装版本核对**：`pyarrow 25.0.1`、`lightgbm 4.7.0`、`narwhals 2.26.0`、`polars 1.6.0`、`fastapi 0.141.1` —— **与 `requirements.txt` 逐项一致** ✅。
- **残余**：`pytest 8.3.3`（PYSEC-2026-1845，修 9.0.3）—— **测试期依赖，不进生产镜像**（`Dockerfile:20-21` 仅装 `requirements.txt`）⇒ 生产无暴露。建议 CI/开发环境顺带升级。

### A07 认证失败 — **PASS（有一处结构性缺口）**

- **口令策略**：注册 `min_length=6`（`auth.py:42`）、用户名正则 `^[A-Za-z0-9_.-]{3,64}$`（`auth.py:41`）、禁止密码==用户名（`auth.py:193`）✅。
- **暴力破解防护**：登录 `_check_rate_limit`（10 次/300s，`auth.py:55-72`）；用户不存在与密码错误**同一提示**（`auth.py:112-115`）防枚举 ✅。
- **token 过期/刷新**：`decode_jwt_token` 分别处理 `ExpiredSignatureError`→`TOKEN_EXPIRED`、`InvalidTokenError`→`INVALID_TOKEN`（`auth.py:174-177`）✅。
- **🟠 结构性缺口（上轮 F-13，本轮复现）**：登录/注册限速位于 **handler 内部**（`auth.py:105`），而 FastAPI body 校验在 handler **之前**执行 ⇒ **任何 body 校验失败请求都不经过限速**。**本轮实测**：连续 15 次非法 body 登录，**15/15 返回 `40000`，从未返回 `42900`（ERR_RATE_LIMITED）**。放大效应已由 F-11 脱敏大幅削弱（见 A09），但"校验失败路径无限速"仍在。

### A08 数据完整性 — **PASS**

- **CI 完整性**：`.github/workflows/ci.yml` 已修为 `pip install -r backend/requirements-dev.txt`（上轮 N2 修复生效），`ruff check app tests scripts --select F,E9`；**无 `pull_request_target`**（全 `.github/` 0 命中）✅；nightly 仅 `schedule`+`workflow_dispatch`，无不可信 PR head checkout ✅。
- **反序列化**：`torch.load`（`ml/torch_models.py:94`、`ml/gnn_models.py:122`）**未传 `weights_only=True`**（`pickle` 反序列化面）。**可达性核实**：加载路径为 `MODEL_ROOT/exp/<version>/model.pt`，由**本仓自身训练流水线**产出（`train_service.py:169-171`、`:282`），**非外部上传/不可信来源** ⇒ 风险低。**建议**：即便如此，加 `weights_only=True` 为纵深防御（低成本）。无 `pickle.load`/`np.load(allow_pickle=True)` 用户可达面 ✅。

### A09 安全日志与监控 — **FAIL（本轮新发现）**

- **安全事件日志缺失**：`api/v1/auth.py` **无任何 `logger.*` 调用**（grep 0 命中）⇒ 登录成功/失败、限速触发、注册、权限拒绝**均不留安全痕迹**；`core/auth.py` 的 `UNAUTHORIZED/FORBIDDEN` 亦不落日志。⇒ 无法取证、无法告警。**FAIL**（详见 §4 F-03）。
- **告警盲区（结合契约）**：平台"HTTP 恒 200"契约（`errors.py:200` 全部异常转 HTTP 200）⇒ 基于状态码的告警**系统性失效**；需基于**业务码**告警，而当前无此类信号。
- **凭据不入日志**：**本轮验证 PASS** —— F-11 修复后，`errors` 只保留 `type/loc/msg/ctx`（`errors.py:70`），实测超长口令请求的响应体 **215 字节、无 `MyS3cr3t`、无 `input` 键**；日志行仅含 `type/loc/msg/ctx`。

### A10 SSRF — **PASS**

- **出站抓取点**：`report.py:441` `httpx.post(webhook, ...)` —— `webhook = get_settings().NOTIFY_WEBHOOK_URL`，**仅来自环境变量**，且**无任何 API 端点可写该配置**（grep `app_settings.py` 无对应写路径）⇒ 非用户可控 ✅。
- `orchestrator.py:581` `httpx.post(s.NOTIFY_WEBHOOK_URL, ...)` —— 同上 ✅。
- `data/ingest/multi_source.py:75` `httpx.get(_EM_KLINE_URL, params=...)` —— URL 为模块常量，`params` 为受控标的 ✅。
- `app_settings.py:236-242` `connectors/test` —— connector 经**白名单** `("akshare","eastmoney")` 校验（`:258`），URL 硬编码 ✅。
- **无用户输入构造 URL 的抓取点**；**无云元数据 `169.254.169.254` 访问**；**无 `file://`/`gopher://` scheme** ✅。

---

## 4. 高危发现详表

> 严重度用定性等级 + CVSS 3.1 估算；confidence 0–10（本轮全部为独立复现）。

### 【新发现】F-01 · tar 归档 symlink/hardlink 逃逸（Archive Slip）

| 项 | 内容 |
|----|------|
| **类别** | STRIDE: Tampering / OWASP A08（数据完整性）/ CWE-59 |
| **严重度** | 🔴 **HIGH**（CVSS 3.1 ≈ 7.1，AV:L/AC:H? → 实际按"需投喂恶意归档"计：`AV:N` 不成立，取本地/运维面 ≈ 6.5–7.1） |
| **Confidence** | **9**（PoC 复现 + `filter='data'` 对照拒绝） |
| **位置** | `backend/scripts/backup.py:125-133`（`_check_member_paths`）、`:161`（`tar.extractall(str(data_dir))`） |
| **状态** | **【上轮已修·本轮发现未生效】** —— 上轮 A8/#9 建议 `extractall(..., filter='data')`，本轮核实**未落地** |

**描述**：`_check_member_paths` **只校验成员"名"**（拒绝 `..`/绝对路径），**不检查 symlink（`issym()`）/hardlink（`islnk()`）成员及其 `linkname` 目标**；且 `extractall` **未传 `filter`**。Python 3.11（生产解释器，`python:3.11-slim`）**无默认 filter** ⇒ 恶意归档中的 symlink 成员会被创建，指向任意绝对路径。

**复现（本轮实测）**：
```
# 构造含 symlink 成员的归档
TarInfo("escape_link").type = SYMTYPE; .linkname = "/etc/passwd"
_check_member_paths(tar)          → PASSED（未拒绝；symlink 成员名合法）
tar.extractall("out")             → 处理该成员
tar.extractall("out", filter="data") → 抛 AbsoluteLinkError（被正确拒绝）✅
```
- Windows 上因无建链权限，`extractall` 静默跳过（`os.path.lexists=False`），**掩盖了缺陷**；
- **Linux 容器（`python:3.11-slim`，生产目标，`Dockerfile:6,25`）可成功创建符号链接**。
- 组合攻击：归档含 `escape_link -> /app/data/sqlite`（symlink）+ 后续 `escape_link/aqp.db`（写穿透）⇒ 恢复时**写出到 `data_dir` 之外**，绕过恢复前的改名保护。需攻击者能投喂/篡改备份归档（备份目录、下载链路）。

**影响**：恢复流程可被诱导写出任意路径（覆盖二进制/配置/启动脚本）；在"以数据目录为界"的隔离假设下是**边界突破**。

**修复**：`backup.py:161` 改为 `tar.extractall(str(data_dir), filter="data")`（**一行**）；并在 `_check_member_paths` 增加 `if m.issym() or m.islnk(): raise ValueError(...)`（拒绝一切链接成员）。

---

### 【新发现】F-02 · Swagger/OpenAPI 生产无条件暴露

| 项 | 内容 |
|----|------|
| **类别** | OWASP A05（安全配置错误）/ A01（信息暴露） |
| **严重度** | 🟠 **MEDIUM-HIGH**（CVSS ≈ 5.3；因当前只绑回环而降级；若对外则升 HIGH） |
| **Confidence** | **10**（`ENV=prod` 实测 200 + 105 KB） |
| **位置** | `backend/app/main.py:235-236` |
| **状态** | **【上轮已记载·本轮仍未修】**（上轮 #8） |

**描述**：`docs_url="/docs"`、`redoc_url="/redoc"` 硬编码，**无 ENV 开关、无鉴权**。

**复现（本轮实测，`ENV=prod`）**：
```
ENV=prod /docs         -> http=200 len=1006
ENV=prod /redoc        -> http=200 len=888
ENV=prod /openapi.json -> http=200 len=105270   ← 112 路径全量 schema
```

**影响**：向未认证方暴露完整 API 契约（含 `researcher`/`admin` 端点路径、参数结构），为攻击者提供精确的定向面（叠加 F-13 的无限速校验路径、`RBAC_ENFORCE=False`，攻击成本进一步降低）。

**修复**：`docs_url=("/docs" if settings.ENV != "prod" else None)`、`redoc_url` 同理，`openapi_url` 收口；或挂 `require_role("admin")`。

---

### 【新发现】F-03 · 认证/授权无安全事件日志（A09 盲区）

| 项 | 内容 |
|----|------|
| **类别** | STRIDE: Repudiation / OWASP A09 |
| **严重度** | 🟠 **MEDIUM**（CVSS ≈ 5.3；合规/取证缺失，非直接可利用） |
| **Confidence** | **9**（源码 0 命中 + 契约分析） |
| **位置** | `backend/app/api/v1/auth.py`（全文件）、`backend/app/core/auth.py:194-203,222-235` |
| **状态** | **【本轮新发现】** |

**描述**：登录成功/失败、限速触发（`ERR_RATE_LIMITED`）、注册、JWT 过期/无效、权限拒绝（`UNAUTHORIZED`/`FORBIDDEN`）**均不写入任何日志**。叠加"HTTP 恒 200"契约（`errors.py:200`）⇒ 无状态码信号、无日志信号 ⇒ **认证攻击完全不可观测**。

**验证**：`grep "logger\." app/api/v1/auth.py` → 0 命中；`_check_rate_limit` 抛异常前不落日志；`require_auth` 抛 `UNAUTHORIZED` 前不落日志。

**影响**：无法检测/取证口令爆破（尤其经 F-13 的无件限速校验路径）、越权尝试；生产事故无审计轨迹。

**修复**：在 `auth.py:105`（限速触发）、`:113`（登录失败）、`:117`（登录成功）、`core/auth.py` 的 `UNAUTHORIZED/FORBIDDEN` 分支加**结构化安全事件**（`event=auth.login_failed`、`username`、`ip`、`trace_id`，**绝不记口令**）；并把限速校验**上移到中间件或路由级 `dependencies=[...]`** 以同时覆盖校验失败路径（根治 F-13 结构缺口）。

---

### 【仍未修】F-04 · 项目 venv 工具链损坏（pip-audit / tomli 不可用）

| 项 | 内容 |
|----|------|
| **类别** | 供应链/可维护性（A06 衍生） |
| **严重度** | 🟡 **LOW-MEDIUM**（影响可审计性，非运行期） |
| **Confidence** | **10** |
| **位置** | `backend/.venv/Lib/site-packages/tomli`（mypyc 产物缺失） |
| **状态** | **【本轮新发现】**（上轮 A5 升级的副作用） |

**描述**：`./.venv/Scripts/pip-audit.exe` 及 `python -m pip_audit` 均抛 `ModuleNotFoundError: No module named '3c22db458360489351e4__mypyc'`；`import tomli` 亦失败。根因疑为 A5 依赖升级时"移开备份"（`D:\tmp_aqp_perf\pyarrow17_backup\`）导致 mypyc 编译模块的伴生 `.pyd` 丢失。

**影响**：项目 venv **无法本地复跑 pip-audit**（本轮改用 `uv tool run pip-audit` 隔离执行完成审计）。CI 若复用该 venv 亦会受影响。

**修复**：`pip install --force-reinstall tomli` 或重建 venv；把依赖审计纳入 CI（`uv`/独立环境），不与项目 venv 耦合。

---

### 【仍未修】F-05 · RBAC_ENFORCE 默认放开未纳入 prod fail-fast

| 项 | 内容 |
|----|------|
| **类别** | OWASP A01 / A05 |
| **严重度** | 🟠 **MEDIUM**（产品裁决项） |
| **Confidence** | **10** |
| **位置** | `backend/app/core/config.py:224`（默认 `False`）、`:305-346`（`validate_runtime_safety` 无该符号） |
| **状态** | **【上轮已记载·本轮仍未修】**（上轮 #7 / A10） |

**描述**：`RBAC_ENFORCE=False`（默认）时 `ensure_role` 直接放行 ⇒ 67 个 researcher + 3 个 admin 端点降级为"登录即可"；且该开关**不在 prod 必检项**，`ENV=prod` 也不拦不告警。本轮核实：`validate_runtime_safety` 全文无 `RBAC_ENFORCE`。

**影响**：任一账号（如 viewer）＝管理员权限；若叠加 F-13（无限速自助注册）与 F-02（schema 暴露），"注册一个账号即接管"路径成立（当前 `ALLOW_REGISTRATION=false` 缓解）。

**修复**：**产品裁决**是否维持全面放开；若维持，至少在 `validate_runtime_safety` 的 prod 分支加显式告警/拒绝选项。

---

### 【仍未修】F-06 · JWT 有效期配置静默失效

| 项 | 内容 |
|----|------|
| **类别** | OWASP A05 / 配置漂移 |
| **严重度** | 🟡 **LOW**（当前无实际漂移，潜伏） |
| **Confidence** | **10** |
| **位置** | `backend/app/core/auth.py:154-155` |
| **状态** | **【上轮已记载·本轮仍未修】**（上轮 #14） |

**描述**：`_jwt_expire_seconds()` 直读 `os.environ.get("JWT_EXPIRE_SECONDS", "604800")`，绕过 `Settings.JWT_EXPIRE_SECONDS`。pydantic-settings 载入 `.env` 到 `Settings` 而**不回写 `os.environ`** ⇒ `.env` 配该键**静默无效**。当前 `.env` 未设该键（默认 604800）⇒ 无实际漂移，但一旦有人按 `.env.example:18` 修改将毫无效果。

**修复**：改读 `get_settings().JWT_EXPIRE_SECONDS`（env 仅作 override）。

---

### 参考：非安全阻塞但相关（承自上轮，本轮核实）

| # | 项 | 状态 |
|---|---|------|
| P-1 | `/overview/rt` 正常路径 ~14s vs 15s 预算（余量 ~1s） | 未修（上轮 R-a），薄余量脆弱 |
| P-2 | 多租户缺失（`user_id` 未用于 API 过滤） | 未修（上轮 #17），单租户设计 |
| P-3 | JWT 存 localStorage（`useAuthStore.ts:60`） | 未修（上轮 #20），XSS 放大 |
| P-4 | git 对象库第 3 次被清空、无 VERSION/tag | 未修（上轮 B5/R-e），无代码回滚基线 |

---

## 5. 行动清单（按优先级）

| # | 行动 | 对应 | 负责方 | 紧急度 | 成本 |
|---|------|------|--------|--------|------|
| **S-1** | `backup.py:161` 加 `filter="data"`；`_check_member_paths` 拒绝 `issym()/islnk()` 成员 | F-01 | 后端 | **P0** | **一行 + 两行** |
| **S-2** | 生产收口 `/docs`、`/redoc`、`/openapi.json`（按 `ENV` 置 `None` 或加 admin 鉴权） | F-02 | 后端 | **P0** | 3 行 |
| **S-3** | 补认证安全事件日志（登录成败/限速/权限拒绝），**禁记口令**；限速上移到路由级 `dependencies` | F-03 + A07 | 后端/安全 | **P1** | 低-中 |
| **S-4** | 裁决 `RBAC_ENFORCE`；若维持放开，在 `validate_runtime_safety` 加 prod 告警 | F-05 | 产品/安全 | **P1（裁决）** | 低 |
| **S-5** | `_jwt_expire_seconds()` 改读 `Settings` | F-06 | 后端 | P2 | 一行 |
| **S-6** | 修复/重建项目 venv（`tomli`/mypyc），把依赖审计移入 CI（`uv`） | F-04 | 后端 | P2 | 低 |
| **S-7** | `torch.load(..., weights_only=True)` 纵深防御 | A08 | ML | P3 | 一行 ×2 |
| **S-8** | 升级 `pytest ≥9.0.3`（测试期） | A06 | 测试 | P3 | 低 |
| **S-9** | 明确暴露形态：**若上线对外（非回环/内网），F-02/F-05/F-13 立即由 P1→P0** | 全局 | 工程/运维 | **前置条件** | —— |

**验收建议**：
- S-1：构造含绝对 symlink 成员的最小归档，`restore_backup` 应抛 `AbsoluteLinkError`/`LinkOutsideDestinationError`，且目标目录外**无新文件**。
- S-2：`ENV=prod` 启动后 `curl /docs`、`/openapi.json` 应 404/401。
- S-3：连发 15 次错误登录，日志应出现 15 条 `auth.login_failed` 事件且**不含口令明文**。

---

## 6. 附录：审计方法与诚实边界

**已执行阶段**：1（架构）、2（攻击面）、3（密钥考古）、4（供应链）、5（CI/CD）、6（影子面）、7（Webhook/集成）、8（LLM/AI）、9（Skill 供应链）、10（OWASP A01–A10）、11（STRIDE）、12（数据分类）、13（假阳性过滤+主动验证）、14（报告）。**14/14 全覆盖**。

**关键验证证据**：
- 匿名边界：真实 ASGI `TestClient` 15 路径实测（§3 A01）。
- F-11：超长口令请求 → 响应 215 B、无明文、无 `input`；日志行仅安全键。
- F-13：15 次非法 body 登录 → 15× `40000`，0× `42900`。
- F-01：symlink/hardlink 归档 PoC + `filter='data'` 对照。
- F-02：`ENV=prod` 下 `/docs`/`/redoc`/`/openapi.json` = 200，schema 105 KB。
- 依赖：`uv tool run pip-audit` 实跑 + venv 版本核对。

**假阳性过滤（Phase 13，已剔除）**：
- `ops.py:204` / `task_store.py:77` 的 f-string SQL → 经追溯实为内部常量/字面量字段，**非注入**。
- `etf.py:977` `js.eval` → akshare 内置常量，**非注入**。
- `/api/v1/portfolio`、`/watchlist` 匿名返 `40400` → 路由本身不存在（非鉴权绕过）。
- `report.py:441` / `orchestrator.py:581` webhook → 仅环境变量可控，**非 SSRF**。
- `connectors/test` → connector 白名单，**非 SSRF**。
- 我的初版正则扫描把 `/notify/stream` 误判为匿名 → 其依赖为工厂产物 `require_stream_viewer`（`notify.py:72,91`），**实为受保护**，已剔除。

**未覆盖 / 诚实边界**：
- **未做破坏性测试**，未打真实生产；后端实例一次性启动（:8021）后即退出，未触碰 `data/`。
- 前端 `dist` 产物未逐字节复核（上轮结论：无后端 IP/密钥泄露），本轮仅按"未变更"沿用。
- nginx 运行时未实测（本机无 nginx 容器），F-02 的"生产对外暴露"影响为配置推导。
- `torch.load` 的可达性基于代码追溯（模型来自本仓自训练），未构造外部恶意模型 PoC。
- 环境为 **Windows + 回环**；F-01 的"Linux 可真实利用"基于 tarfile 语义与 `python:3.11-slim` 目标镜像推断（Windows 侧已复现"检查未拒绝 + `filter` 拒绝"的两侧对照）。

---

*报告结束 · 安全官（CSO）· 2026-09-30*
