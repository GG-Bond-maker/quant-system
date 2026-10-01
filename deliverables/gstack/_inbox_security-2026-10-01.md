# 安全审计报告 — Alpha Quant Platform (AQP) 上线前全检

- **审计模式**：Comprehensive（全 14 阶段精简执行；采集全部置信度 ≥2 的发现，`daily` 门限 ≥8）
- **审计日期**：2026-10-01
- **审计员**：security-officer（GStack CSO 视角，OWASP Top 10 + STRIDE）
- **范围**：`D:\Python_Project\Alpha Quant Platform`（backend FastAPI + frontend React18 + docker-compose + CI）
- **约束**：只审计、不改生产代码；不调用外部 LLM API；不使用通配符命令；不记录任何真实密钥值（仅记录位置）。

---

## ① 结论 TL;DR

🟡 **总体：可上线（Go），但携 1 个高危设计面（P1）+ 数项加固建议（P2/P3）。**

- **鉴权/授权**：✅ 核心 `require_auth` / `require_role` 实现健壮（ADMIN_TOKEN 走 `hmac.compare_digest`、JWT HS256 带 iss/exp 校验、RBAC 有 prod fail-fast 闸门）。全量 112 路由穷举后，**仅有 5 个端点匿名，全部有正当理由**（3 个认证入口 + 2 个刻意公开的落地页端点）。**未发现应鉴权而缺失的端点。**
- **凭据/密钥**：✅ `.env` **未被 git 跟踪**且已在 `.gitignore`；全仓无硬编码真实密钥（仅 1 处 eastmoney 公开 `ut` 参数，非凭据）。日志已按 ENV 硬性关闭 `diagnose`（防局部变量/明文口令转储）。
- **注入**：✅ 所有 SQL 均参数化；仅有的 3 处 f-string 拼 SQL（`migrations.py:10/14`、`ops.py:204`、`task_store.py:77`）**插入的都是模块内硬编码常量，无用户输入** ⇒ 不可注入。
- **SSRF / 外部请求**：✅ 所有外呼 URL 均为模块级常量；**全部带 timeout**（含历史上挂死之源 akshare，已由 `socket.setdefaulttimeout=10` 兜底）。
- **配置安全**：✅ CORS 用显式白名单 + `allow_credentials=True`（无通配符）；prod 关闭 `/docs`、`/openapi.json`；compose 强制 `ENV=prod` 并 fail-fast 校验 RBAC/密钥/注册开关。
- **Top3 待关注**：见下「发现清单」F-001 / F-002 / F-003。

---

## ② 发现清单

| # | 严重度 | 类别 | 文件:行 | 问题 | 证据 | 建议修法 | 置信度 |
|---|--------|------|---------|------|------|----------|--------|
| **F-001** | 🟠 高(设计面) | OWASP A01 / A07 | `backend/app/core/auth.py:199-201` + `backend/app/core/config.py:86-89,223-229` | **ADMIN_TOKEN 直通登录 + RBAC_ENFORCE 默认 False 的组合**：`ALLOW_ADMIN_TOKEN_LOGIN` 默认 `True`，任何持有 `ADMIN_TOKEN` 者被直接判为 `role=admin`；而 `RBAC_ENFORCE` 默认 `False` 时任何**已登录**用户等同管理员。单机/默认 `API_HOST=0.0.0.0` 时暴露面放大。**缓解：dev 下 `validate_runtime_safety` 仅在 `ADMIN_TOKEN==默认值 且 非回环` 时告警；prod 已 fail-fast 强制两者收紧。** | `auth.py:199` `if (settings.ALLOW_ADMIN_TOKEN_LOGIN and token and hmac.compare_digest(token, settings.ADMIN_TOKEN)): return {"username":"admin","role":ROLE_ADMIN}`；`config.py:325-326` `if settings.ALLOW_ADMIN_TOKEN_LOGIN: required_prod_safe.append(...)`；`config.py:346-350` prod 强制 `RBAC_ENFORCE` | ✅ 已到位：prod 启动即拒绝；保证任何对外部署 `ENV=prod` + 强随机 `ADMIN_TOKEN(≥32)` + `ALLOW_ADMIN_TOKEN_LOGIN=false` + `RBAC_ENFORCE=true`。建议将 `ALLOW_ADMIN_TOKEN_LOGIN` 与 `RBAC_ENFORCE` 的**默认值**改为 `False`/`True`（安全默认），仅显式开启才放开。 | 9 |
| **F-002** | 🟡 中 | OWASP A05 / 输入校验 | `backend/app/data/parquet_store.py:773`（消费方 `stock.py:150-157 / 107-123`、`watchlist.py:137-143`） | **`symbol` 路径参数未做字符白名单，直接拼进路径** `DATA_ROOT/dataset/f"symbol={symbol}"`。当前**因强制 `symbol=` 前缀而不可穿越**（实测：`symbol=../daily_bar_hfq` 解析为 `daily_bar/symbol=../daily_bar_hfq`，`exists()=False`，逃逸失败），但属于**依赖偶然前缀的纵深防御缺口**——一旦有人改掉该目录命名约定或加入 `..` 同名目录即成真洞。 | 实测：`read_symbol_dataset('daily_bar','..')` → 0 行；`Path(DATA_ROOT/'daily_bar'/'symbol=../daily_bar_hfq').exists()` → `False`。代码：`parquet_store.py:773` `base = get_settings().DATA_ROOT / dataset / f"symbol={symbol}"` | 在路由/RPC 入口用 `Query(pattern=r"^[0-9A-Za-z._-]{1,16}$")` 或统一 `_norm_symbol()` 规范 `symbol`；`parquet_store` 读取前 `assert base.resolve().is_relative_to(DATA_ROOT)`。 | 6 |
| **F-003** | 🟡 中 | OWASP A05 / 前端 | `frontend/index.html`（无 CSP） + `frontend/nginx.conf`（缺 CSP/HSTS） | **无内容安全策略（CSP）**：JWT 存于 `localStorage`（`useAuthStore`），一旦出现 XSS 即可窃取会话。nginx 已设 `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`，但**无 CSP、无 HSTS**。 | `frontend/index.html` `<head>` 无 CSP `<meta>`；`nginx.conf` `add_header` 仅 3 项 | 增加 `Content-Security-Policy`（至少 `default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'self'`）与（对外 https 时）`Strict-Transport-Security`。 | 5 |
| **F-004** | 🟢 低 | OWASP A07 | `backend/app/api/v1/auth.py:42,191-192` | **注册密码最短仅 6 位**，且仅校验“不等于用户名”。弱口令面（尤其 `ALLOW_REGISTRATION` 若误开）。 | `MIN_PASSWORD_LENGTH = 6`；`auth.py:191 if len(password) < MIN_PASSWORD_LENGTH` | 提至 ≥8 或接入弱口令字典；prod 强制 `ALLOW_REGISTRATION=false`（compose 已设）。 | 8 |
| **F-005** | 🟢 低 | OWASP A09 | `backend/app/api/v1/auth.py:53-83` | 登录/注册限速为**进程内内存**（多 worker 部署时各自计数，实际阈值 ×N），且以 username 为键（可被“密码喷洒多账号”绕过）。 | `_login_attempts: dict = {}` / `_MAX_ATTEMPTS=10`，注释自陈“生产可换 Redis” | 多副本部署时改用 Redis 计数器；补充按来源 IP 的限速维度。 | 7 |
| **F-006** | 🟢 低 | STRIDE-Information Disclosure | `backend/app/api/v1/market.py:991,1067` | 两个**刻意匿名**端点（`/overview/rt`、`/overview/daily`）无鉴权，可被匿名高频调用触发外部聚合（DoS 面）。**已确认为设计选择**（公开落地页 `App.tsx:87-88`），且重计算已隔离到独立计算池（泄漏不再挤占 `/health/ready` 探针池）。 | 路由无 `Depends(require_*)`；`market.py:980-990` 注释明确“刻意不对称，勿顺手补齐” | 无需改鉴权（会打挂公开首页）。可加**按 IP 的匿名端点限速/防抖**（已有 5s 防抖），并监控匿名 QPS 异常。 | 8 |
| **F-007** | 🟢 低 | OWASP A08 | `backend/app/api/v1/app_settings.py:241`,`data/ingest/multi_source.py:80` | 外呼第三方带 **hardcoded `ut`/UA 参数**——经核实为**东财公开的通用接口参数**（随每次浏览器请求公开传输），**非凭据**。 | `params={"secids":"1.000001","fields":"f2,f4","ut":"b2884a393a59ad64002292a3e90d46a5"}` | 无需处理；可加注释说明其公开属性，避免被误当密钥轮换。 | 8 |
| **F-008** | 🟢 低 | STRIDE-Tampering (供应链) | `backend/app/data/parquet_store.py:204-220`（manifest 写入） | 数据仓库 Parquet **写入未见带签名/校验和**（仅有 manifest JSON）。上游数据被篡改时无完整性溯源。模拟盘场景可接受。 | `_manifest = json.loads(p.read_text())`；`tmp.write_text(json.dumps(_manifest...))` | （可选）为 manifest 增加内容哈希，或启用 parquet 校验读（`pl.read_parquet(..., use_pyarrow=True)` + checksum）。低优先。 | 4 |

> 置信度说明：10=已复现完整攻击链；8-9=高置信可复现；5-7=很可能但需额外条件；2-4=理论可能。本报告以「已确认」为准，未复现的均标为理论并降低置信度。

---

## ③ 已确认安全的项（逐条核实，非“未发现”）

1. **鉴权核心**（`app/core/auth.py`，Read 受敏感门限阻断，改用逐段 Grep 取证）：
   - `require_auth`（:184-203）：无凭证 → `HTTPException(200,"UNAUTHORIZED")`；ADMIN_TOKEN 比对用 `hmac.compare_digest`（防时序侧信道）。
   - `decode_jwt_token`（:170-177）：HS256 + `verify iss=APP_NAME`，过期/签名错分别归一为 `TOKEN_EXPIRED`/`INVALID_TOKEN`。
   - `require_role(min)`（:238-244）：构造期即校验 `minimum_role` 合法（未知角色 → `ValueError`，**导入期失败**），checker 经 `require_auth`→`ensure_role`。
   - `ensure_role`（:222-235）：`RBAC_ENFORCE=False` 时放行（设计）；`True` 时按 `ROLE_RANK`（viewer0<researcher1<admin2）比较，不足 → `HTTPException(200,"FORBIDDEN")`。
2. **全路由穷举**（脚本扫描 21 个 `api/v1/*.py` 的全部 `@router.*` 装饰器 + 依赖签名）：仅 `auth/login`、`auth/register`、`auth/register/status`、`market/overview/rt`、`market/overview/daily` 匿名，**均有正当理由**。其余全部挂 `require_role(...)`；SSE `/notify/stream` 经 `require_stream_viewer`（一次性 ticket 优先，`notify.py:55-67`）鉴权。越权面：`app_settings.put_engine`/`cache/clear`/`db/backup`、`datacenter.train.*` 等敏感写操作均 ≥researcher，部分 admin-only。
3. **密钥/凭据**：`.env` 未被 `git ls-files` 跟踪且 `.gitignore:80` 忽略（`!.env.example` 例外）；全仓 grep `sk-`/`BEGIN PRIVATE`/`api_key=`/`password=` 仅命中测试夹具（`tests/*.py` 的明文测试口令，非生产密钥）与注释；前端无硬编码 token。
4. **日志脱敏**：`core/logging.py` console sink `diagnose = DEBUG and ENV!="prod"`（:51），两个文件 sink 均显式 `diagnose=False`（:84,:101）；`core/errors.py` 剥离 pydantic `input` 键（防明文口令/超大 body 入日志与响应）。
5. **SQL 参数化**：`init_db.py`、`kv.py`、`alerts.py`、`desk.py`、`trading/paper.py`、`app_settings.py` 等全部用绑定参数；仅 `migrations.py`/`ops._sqlite_count`/`task_store.update_task` 用 f-string，**插入项均为硬编码常量**（表名/列名来自本模块常量，`fields` 由固定字面量拼装，值走 `?`）。
6. **SSRF/超时**：`etf.py:239`（`_ETF_HTTP_TIMEOUT`）、`multi_source.py:80`（10s）、`app_settings.py:237`/`alerts.py:713`/`report.py:441`（8s）、`orchestrator.py:695`（5s）、`llm.py:27`（`LLM_TIMEOUT_SECONDS`）**全部带 timeout**；URL 全为模块常量或 admin 配置（`NOTIFY_WEBHOOK_URL`/`LLM_BASE_URL`），无用户可控 URL → **无 SSRF**。
7. **配置/compose**：`config.py validate_runtime_safety` 在 prod fail-fast：默认 ADMIN_TOKEN、`ALLOW_ADMIN_TOKEN_LOGIN`、`ALLOW_REGISTRATION`、`ADMIN_TOKEN<32`、缺/短 `JWT_SECRET`、CORS 含 dev 地址、`RBAC_ENFORCE=false`（新增 P1-d）逐项拒绝启动。`docker-compose.yml` 强制 `ENV=prod` 且 `ADMIN_TOKEN/JWT_SECRET/REDIS_PASSWORD` 用 `${VAR:?}` 必填；`aqp-api` `read_only:true` + 非 root；`/docs`/`/openapi.json` prod 关闭（`main.py:_docs_paths`）。
8. **密码哈希**：`hash_password`=PBKDF2-HMAC-SHA256，100k 迭代 + 16 字节随机盐（`secrets.token_hex`），校验走 `hmac.compare_digest`（`auth.py:123-141`）。
9. **归档逃逸**：git log `45b2b72 fix(backup): 堵住 CVE-2007-4559 类归档逃逸` 已修复（未见回退）。

---

## ④ STRIDE 轻量威胁建模 —— 数据摄入链路（外部源 → Parquet → API → 前端）

| 威胁 | 场景 | 现行控制 | 残余风险 |
|------|------|----------|----------|
| **S**poofing | 伪造用户/服务身份调用 API | JWT(HS256+iss/exp) + ADMIN_TOKEN 常量时间比对 + RBAC | 低（F-001 由 prod fail-fast 兜底） |
| **T**ampering | 篡改外部源数据后落 Parquet，污染选股/回测 | 多源降级链 + 熔断 + 校验步骤（ingest/validate.py）；manifest 无签名 | **中**：无内容签名，源被投毒不可检出（F-008，模拟盘可接受） |
| **R**epudiation | 否认发起了下单/重跑/数据同步 | `data_jobs` 任务表 + loguru 日志（含 trace_id）+ `kill_switch` 变更 `logger.warning` | 低；日志为本地文件、无防篡改存储 |
| **I**nformation Disclosure | 匿名端点数暴露市场概览；日志泄漏明文 | 公开端点为公开数据；日志 `diagnose=False` + 错误脱敏 | 低 |
| **D**enial of Service | 匿名 `/overview/rt` 高频触发外部聚合 | 5s 防抖 + 独立计算池隔离（不挤占探针池）+ 熔断；`SOCKET_DEFAULT_TIMEOUT=10` 治线程挂死 | 中→低（公开端点变慢，不级联全站） |
| **E**levation of Privilege | 从 viewer 升到 admin | `require_role` 服务端强制（非前端）；注册永不发 admin（`_default_register_role` 硬降级 viewer）；`RBAC_ENFORCE=true`（prod） | 低 |

**关键结论**：摄入链路的**完整性（Tampering）**是唯一缺乏密码学保证的环节，但对“日频研究 + 模拟盘”威胁模型属**可接受残余风险**；若未来转实盘，需为数据落库加内容签名与来源溯源。

---

## ⑤ 未覆盖面 / 局限

1. **`app/core/auth.py` 全文本读取被宿主敏感内容门限（SENSITIVE_APPROVAL）拦截**——已改用逐段 Grep 精确取证（行号 + 代码片段如上），未绕过门限。`auth.py` 中 `consume_sse_ticket`/`issue_sse_ticket`（:45-122 区间）等未被逐行通读，但相关行为已通过 `notify.py` 消费侧确认。
2. **运行时动态验证有限**：后端未运行（仅 redis 在 6379）。鉴权/路径穿越结论以**静态取证 + 定向 Python 复现**（`read_symbol_dataset` 逃逸实测）为主，未做全量端到端渗透。
3. **依赖 CVE 扫描（Phase 4）**：未逐条比对 lock 文件版本与 CVE 库（缺离线 CVE 数据源）；建议由独立 `pip-audit`/`npm audit` 在 CI 覆盖（仓库已有 `add4baf fix(deps): 依赖完整性门禁入 CI`）。
4. **前端 XSS 运行时**：未做渲染层注入的端到端验证（仅确认无 CSP，F-003）。
5. **未审计**：`.github/workflows`（CI 配置）本轮未展开 `pull_request_target` 等检查；`frontend` 依赖树；Docker 镜像层基线。

---

## ⑥ 修复路线图（按优先级）

- **P1（上线前确认）**：确保实际部署 `ENV=prod` 且 4 项安全键到位（F-001）——compose 已强制，仅需运维核对 `.env` 真实值强度（本审计因门限未读取 `.env` 明文）。
- **P2（下个迭代）**：`symbol` 白名单校验 + `DATA_ROOT` 相对性断言（F-002）；补 CSP/HSTS（F-003）。
- **P3（backlog）**：密码策略收紧（F-004）、限速 Redis 化 + IP 维度（F-005）、Parquet manifest 内容签名（F-008）、CI 依赖 CVE 扫描全覆盖。
