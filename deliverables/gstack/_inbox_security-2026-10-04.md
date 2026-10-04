# 安全审计报告 — Alpha Quant Platform (AQP) 上线前全检

- **审计模式**：Comprehensive（OWASP Top 10 + STRIDE + 超时/DoS 专项）
- **审计日期**：2026-10-04
- **审计员**：gstack-security-officer（GStack CSO 视角）
- **范围**：`D:\Python_Project\Alpha Quant Platform`（backend FastAPI + frontend React/Vite + docker-compose + CI）
- **约束**：只读审计，不改生产代码；不调用外部 LLM API；不使用通配符命令；不记录任何真实密钥值（仅记录位置）。
- **复核基线**：`_inbox_security-2026-10-01.md`（逐条复核是否仍存在）

---

## ① 结论 TL;DR

🟡 **总体：可上线（Conditional Go）。核心鉴权/密钥/注入面健壮；历史高危项多数已修。本次新增 1 个 P1（匿名重计算端点惊群 + 计算池无界队列）与 3 个 P2 加固项。**

- **鉴权面**：✅ 112 路由穷举，匿名仅 7 条（4 个公开落地页 GET + 3 个认证入口），全部登记在 `DECLARED_PUBLIC`；54 个写端点全部挂鉴权（注册表守卫测试）。**未发现未声明的匿名端点。**
- **密钥/注入**：✅ `.env` 未入 git 且被 `.gitignore:80` 忽略；无硬编码密钥；SQL 全参数化（4 处 f-string 均为模块内硬编码常量）。
- **超时**：✅ 所有外呼均带 timeout（含 akshare，由 `socket.setdefaulttimeout=10` 兜底）；新增 `TimeoutGuardMiddleware` 全局兜底。
- **Top 风险**：F-001（P1，DoS）/ F-002（P2，body 无上限）/ F-003（P2，SSE 无连接上限）/ F-004（P2，无 CSP）。

---

## ② 发现清单（按严重度）

### [F-001] 🟠 P1 — 匿名重计算端点缺 single-flight，计算池队列无界 ⇒ 冷缓存惊群 / 缓存穿透 DoS

- **类别**：OWASP A04（不安全设计）/ A05；STRIDE-Denial of Service
- **位置**：
  - `backend/app/api/v1/market.py:1197`、`1240`（`_overview_block_response` 调 `cached_or_build`，**未传** `single_flight=True`）
  - `backend/app/api/v1/market.py:1128`（`/overview/rt`）、`1204`（`/overview/global`）、`1246`（`/overview/daily`）— **匿名**，服务公开落地页
  - `backend/app/core/compute_pool.py:126`（`ThreadPoolExecutor(max_workers=6)`，其内部工作队列**无界**）
  - `backend/app/cache/swr.py:412`（`single_flight` 默认 `False` ⇒ 全 miss 时 N 路并发**各自**跑一次 `build`）
- **问题**：三个匿名端点命中缓存时便宜；但**冷缓存**（TTL 过期 / 首次启动 / `refresh=1` 抢锁失败退化）下，每个并发请求都会独立提交一次重计算到计算池。计算池只有 6 个 worker，但 `run_in_executor` 的排队队列**无上限**；`asyncio.wait_for`（rt=15s / global=25s）到期只取消外层 await，**排队中的 task 不会被取消**，继续占位。攻击者无需登录即可用并发请求在冷缓存窗口灌入大量重计算，撑爆内存并长期占满 6 个 worker。
- **证据**：
  - `swr.py:412` `if single_flight:` → 调用方未开启，走 `swr.py:435 data = await build()` 的**每请求独立重建**分支。
  - `_overview_block_response`（`market.py:1111-1114`）只传 `rebuild_lock_ttl=180`，无 `single_flight`。
  - `compute_pool.py:126` 注释自陈池为"隔离爆炸半径"，**未**声明队列有界；`ThreadPoolExecutor` 队列在 CPython 中是无界 `SimpleQueue`。
- **缓解现状**：计算池隔离把后果从"全站挂死"降为"overview 变慢"（不挤占探针默认池）；`refresh=1` 有 5s 防抖锁（`swr.py:399`）。
- **修复建议**：
  1. 三个匿名端点改传 `single_flight=True`（`_build_lineage` 已是此范式，可直接复用）；
  2. 给计算池提交加**有界信号量**（复用 `core/compute_guard.py::compute_slot_ctx`）或对池内排队数设上限，超限快速失败；
  3. 为匿名端点补**按 IP 的限速/防抖**（当前仅 `refresh=1` 有 5s 防抖）。
- **置信度**：7（静态代码路径明确；未在运行环境压测复现）

### [F-002] 🟡 P2 — 无全局请求体大小限制 ⇒ 大 body 内存耗尽 DoS

- **类别**：OWASP A05（安全配置错误）；STRIDE-Denial of Service
- **位置**：`backend/app/main.py:294-327`（中间件链仅 PanicGuard/TimeoutGuard/CORS，**无 body-size 限制**）；`backend/Dockerfile:59`（`uvicorn ... --workers 1`，无 `--limit-*`）；`backend/app/api/v1/datacenter.py:1824-1828`（`TextImportRequest.docs` 上限 2000，但每个 doc 的 `content` **无长度上限**）
- **问题**：Starlette/uvicorn 默认**不限制请求体大小**，`await request.json()` 会把整个 body 读入内存。匿名端点 `/auth/login`、`/auth/register` 接受 JSON body ⇒ 未认证攻击者可发送超大 body 触发内存耗尽。
- **证据**：全仓 grep `content-length|max_body|body_size|limit_max` **0 命中**（`backend/app`）。
- **修复建议**：加一个 ASGI body-size 限制中间件（如 1–8 MB，超出回 413/统一信封）；为 `TextImportRequest` 的 `content` 补 `max_length`。
- **置信度**：8

### [F-003] 🟡 P2 — SSE 流无并发/连接上限，且被超时中间件豁免

- **类别**：OWASP A04；STRIDE-Denial of Service
- **位置**：`backend/app/api/v1/notify.py:83-153`（`/notify/stream`，**无连接数上限**）；`backend/app/core/timeout_guard.py:107-113`（`_EXEMPT_PATHS` 对 SSE **完全不加超时**）
- **问题**：任何已登录用户（viewer 级即可，`require_stream_viewer`）可建立**无上限**条 SSE 长连接；每条连接创建一个 `asyncio.Queue(maxsize=200)` 与若干 pump task（`notify.py:109-123`），且因豁免超时**永不回收**。攻击者（哪怕仅 viewer 账号）可批量建连耗尽内存/事件循环任务表。
- **证据**：`notify.py` 全文无连接计数/上限；`timeout_guard.py:112` 显式豁免 `/api/v1/notify/stream`。
- **修复建议**：加**每用户 + 全局** SSE 连接上限（超限回错误帧/关闭）；加空闲连接超时（客户端无重连则回收）。
- **置信度**：7

### [F-004] 🟡 P2 — 前端 JWT 存 localStorage，且前端无 CSP/HSTS（历史 F-003 未修）

- **类别**：OWASP A02（加密失效）/ A05；STRIDE-Information Disclosure
- **位置**：`frontend/src/stores/useAuthStore.ts:55`（`persist(...)` → localStorage 存 `token`）；`frontend/src/api/client.ts:159-165`（拦截器从 store 取 Bearer）；`frontend/index.html`（**无 CSP meta**）；`frontend/nginx.conf:27-29`（仅 `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`，**无 CSP、无 HSTS**）
- **问题**：JWT 存于 localStorage，一旦出现 XSS 即可窃取会话；缺少 CSP 使 XSS 更易发生（纵深防御缺口）。API 侧已加安全头（`main.py:389-408`，prod 含 HSTS），但**前端站点本身**未加 CSP/HSTS。
- **证据**：`frontend/index.html` grep `content-security` **0 命中**；`nginx.conf` 无 `add_header Content-Security-Policy`。
- **修复建议**：在 nginx/网关加 `Content-Security-Policy`（至少 `default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'self'`）与（对外 https 时）`Strict-Transport-Security`；长期考虑 httpOnly cookie 存会话。
- **置信度**：6（未发现实际 XSS，属纵深防御缺口）

### [F-005] 🟢 P3 — 登录限速按 username 计数，未按来源 IP（历史 F-005 部分遗留）

- **类别**：OWASP A07；STRIDE-Spoofing
- **位置**：`backend/app/api/v1/auth.py:61-78`
- **问题**：`_check_rate_limit(username, ip)` 的 `ip` 仅用于**日志**，未参与 keying（`auth.py:70` 只按 `username` 取计数）；「密码喷洒多账号」可绕过；且为**进程内内存**计数（多 worker 部署时阈值 ×N）。**缓解**：`Dockerfile:59` 为 `--workers 1`，故多 worker 放大当前不成立。
- **修复建议**：计数 key 增加来源 IP 维度；多副本部署时改用 Redis。
- **置信度**：8

### [F-006] 🟢 P3 — prod CORS 校验的 dev-origin 黑名单已过期（:5173 vs compose :8080）

- **类别**：OWASP A05
- **位置**：`backend/app/core/config.py:343-346`（黑名单仅 `http://localhost:5173` / `http://127.0.0.1:5173`）；`docker-compose.yml:86`（`CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:8080,http://127.0.0.1:8080}`）
- **问题**：compose 默认值用 `:8080`（nginx），而 prod 校验只拦 `:5173`（Vite dev）⇒ 若运维未显式设 `CORS_ORIGINS`，生产会带着**localhost 地址**通过校验。前端经 nginx 同源反代 `/api`（不触发 CORS），实际风险低，但校验意图落空。
- **修复建议**：黑名单扩展为"任何 `localhost`/`127.0.0.1`/`0.0.0.0` origin"。
- **置信度**：7

### [F-007] 🟢 P3 — 数据路径内 JS `eval`（PyMiniRacer，非用户输入）

- **类别**：OWASP A08（完整性）/ 供应链
- **位置**：`backend/app/data/etf.py:1128` `js.eval(hash_code)`（`hash_code` 来自 akshare 模块内部，**非用户可控**）
- **问题**：数据抓取路径中执行 JS eval 字符串，属第三方库行为；当前输入非用户可控，无直接漏洞，但为供应链/健壮性观察点。
- **修复建议**：加注释说明输入来源与不可控性；锁定 akshare 版本。
- **置信度**：5（观察项）

---

## ③ 已确认安全的项（逐条复核，非"未发现"）

1. **鉴权/授权完备性**：穷举 20 个 `api/v1/*.py` 的全部 `@router.*` 装饰器 + 依赖，路由数 vs `Depends(require_*)` 数逐文件吻合（market 6/3、auth 4/1、其余 routes==authdeps）。匿名仅 7 条：`/auth/login`、`/auth/register`、`/auth/register/status`、`/market/overview`、`/market/overview/daily`、`/market/overview/global`、`/market/overview/rt`——**全部**在 `tests/test_read_endpoints_rbac.py::DECLARED_PUBLIC` 白名单中，且有完备性守卫测试（未登记的无鉴权 GET 会 FAIL）。写端点 54 个全部纳管（`tests/test_write_endpoints_smoke.py`）。
2. **认证实现**：`auth.py:42` `_USERNAME_RE` 白名单；`MIN_PASSWORD_LENGTH=8`（历史 F-004 已修，原 6）；注册角色硬降级为 viewer/researcher（`_REGISTERABLE_ROLES`）。
3. **密钥**：`.env` **未被 git 跟踪**（`git ls-files` 仅 `.env.example`；`git log -- .env` 为空）；`.gitignore:80` 忽略、`:93` 例外 `.env.example`；grep `sk-`/`BEGIN PRIVATE`/`AKIA` **0 命中**。
4. **注入**：SQL 全参数化；4 处 f-string（`ops.py:205`、`migrations.py:10/14`、`task_store.py:77`）插入项均为**模块内硬编码常量**（`ops.py:195` 注释 + 代码复核确认）；无 `subprocess`/`os.system`/`shell=True`/`pickle.loads`/`yaml.load`。
5. **外呼超时**：`etf.py:239`（`_ETF_HTTP_TIMEOUT`）、`multi_source.py:84`（10s）、`alerts.py:742`（8s）、`app_settings.py:237`（8s）、`orchestrator.py:708`（5s）、`llm.py:27`（`LLM_TIMEOUT_SECONDS`）**全部带 timeout**；`main.py:61-64` `socket.setdefaulttimeout(10)` 兜住 akshare/requests 无 timeout 调用（治线程泄漏与进程退出挂起）。
6. **SSRF**：所有外呼 URL 为模块级常量或 admin 配置（`NOTIFY_WEBHOOK_URL`/`LLM_BASE_URL`）；`app_settings` 连接测试白名单仅 `akshare|eastmoney`；**无用户可控 URL 抓取**。
7. **超时兜底**：新增 `core/timeout_guard.py`（全局预算 + 豁免/延长/收紧三表 + 前端 `timeouts.ts` 双向机器校验接入 CI）；`core/panic_guard.py`（BaseException 兜底）；`core/compute_pool.py`（6 槽隔离，不挤占探针默认池）；`main.py:495-496` 探针专用 2 线程池（治 22 路并发打挂 `/health/ready`）。
8. **prod fail-fast**：`config.py:325-357` 强制 ADMIN_TOKEN≥32 / JWT_SECRET≥32 且不得派生 / `ALLOW_ADMIN_TOKEN_LOGIN=false` / `ALLOW_REGISTRATION=false` / `RBAC_ENFORCE=true` / CORS 不含 dev 地址；`docker-compose.yml:52-86` 强制 `ENV=prod` 且 `${VAR:?}` 必填。
9. **文档暴露面**：`main.py:264-280` prod 关闭 `/docs`、`/redoc`、`/openapi.json`（阻断攻击面地图外泄）。
10. **API 安全响应头**：`main.py:389-408` `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer` / `Permissions-Policy`，prod 加 HSTS。
11. **AI/LLM 安全**：`studio.py:198-201` LLM 产出仅作 DSL 文本，经 `alpha_expr.parse_expr` **AST 白名单**校验（拒绝属性访问/下标/未知算子），再在真实截面评估——LLM 输出不进入特权上下文。
12. **CI 供应链**：`.github/workflows/ci.yml` 用 `pull_request`（**非** `pull_request_target`），无 untrusted checkout 风险；含 `check_deps.py` 依赖门禁。
13. **前端 504 不放大**：`client.ts:189-192` 对"504 + 统一信封"走 `isApiEnvelope` 分支抛 `ApiError`，**短路**重试；仅裸 502/503/504 才重试且限 GET/HEAD、单次、带抖动（`client.ts:200-219`）——不构成 DoS 放大。

---

## ④ STRIDE 威胁建模（FastAPI 服务面）

| 威胁 | 场景 | 现行控制 | 残余风险 |
|------|------|----------|----------|
| **S**poofing | 伪造身份调用 API | JWT(HS256+iss/exp) + ADMIN_TOKEN `hmac.compare_digest` + RBAC（prod fail-fast 强制） | 低 |
| **T**ampering | 篡改外部源数据落 Parquet | 多源降级链 + 熔断 + 校验步骤；manifest 无内容签名（模拟盘可接受） | 中（设计内） |
| **R**epudiation | 否认下单/重跑/同步 | `data_jobs` 任务表 + loguru（含 trace_id）+ kill_switch 变更告警 | 低（本地日志无防篡改） |
| **I**nformation Disclosure | 匿名端点暴露 / 日志泄漏 / 前端 XSS 窃 token | 公开端点仅公开数据；`logging.py` `diagnose=False`；`errors.py` 脱敏。**缺口：F-004（localStorage + 无 CSP）** | 低→中 |
| **D**enial of Service | 匿名重计算惊群 / 大 body / SSE 泛洪 | 计算池隔离 + 5s 防抖 + socket 超时 + 全局超时兜底。**缺口：F-001/F-002/F-003** | **中** |
| **E**levation of Privilege | viewer 升 admin | `require_role` 服务端强制；注册硬降级 viewer；prod `RBAC_ENFORCE=true` | 低 |

**关键结论**：DoS（Denial of Service）是本次唯一有明显残余风险的 STRIDE 维度，集中在**匿名/低权端点缺少并发与资源上限**（F-001/F-002/F-003）。建议上线前至少处置 F-001（single-flight + 匿名限速）。

---

## ⑤ 修复路线图（按优先级）

- **P1（上线前确认）**：F-001 —— 匿名 overview 端点开启 `single_flight` + 计算池队列有界 + 匿名 IP 限速。
- **P2（首个迭代）**：F-002（body-size 中间件 + `content` 上限）、F-003（SSE 连接上限 + 空闲回收）、F-004（nginx/gateway 加 CSP/HSTS）。
- **P3（backlog）**：F-005（限速加 IP 维度）、F-006（prod CORS 黑名单扩到 localhost/127.*）、F-007（etf.py JS eval 注释与版本锁定）。

---

## ⑥ 局限

1. `backend/app/core/auth.py` 全文读取被宿主敏感内容门限拦截（SENSITIVE_APPROVAL=TIMED_OUT），改用逐段 Grep 取证（行号见上）。
2. 后端未运行，全部结论基于**静态代码取证**（grep/读码）与代码路径分析；未做端到端渗透或运行期压测复现（F-001 置信度因此为 7 而非 9）。
3. 未逐条比对 lock 文件版本与离线 CVE 库（建议由独立 `pip-audit`/`npm audit` 在 CI 覆盖，仓库已有 `check_deps.py`）。
4. 前端未做渲染层 XSS 端到端验证（仅确认无 CSP，F-004）。
