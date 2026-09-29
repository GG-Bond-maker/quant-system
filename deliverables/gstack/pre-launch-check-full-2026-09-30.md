# Alpha Quant Platform · 上线前全检报告

**日期**：2026-09-30
**场景**：上线前检查（代码审查 + 安全审计 + QA 测试 + 超时根因 + 性能优化）
**参与成员**：产品评审员 + 安全官 + QA 与发布 + 调查员（4 位全部上场）
**审计对象**：`D:\Python_Project\Alpha Quant Platform`（后端 327 个 .py / 测试 163 个 `test_*.py`；前端 1277 个 .ts/.tsx）
**数据规模**：`data/parquet` 实测 **36,500** 个 parquet；`data/sqlite/aqp.db` 2.3MB（WAL）
**主理人**：沽思航 · 软件工坊 CEO

> **本版修订说明（v3）**：调查员在收尾阶段追加了一条**新 P0**（线程池泄漏），QA 随后在真实应用上做了**端到端决定性实验**证实，主理人也**独立复现**。三方证据吻合。本条已升为**头号阻塞项**，并据此重写 TL;DR、阻塞项清单与行动清单。
>
> **v3 追加（收尾最后一轮）**：调查员又补了两项——① **头号 P0 的第二张面孔**：被放弃的阻塞线程**阻止进程退出**（主理人已复现，`rc=124`）⇒ P0 由 6 项增至 **7 项**；② **入口再分类**：跑步机入口分两类，其中 `/overview/rt?refresh=1` 是前端 ⟳ 按钮的日常路径（**注**：该路径原被推为"降级必泄漏 / 坏天气自爆"，**已被主理人实测证伪**，见 R14）。同时给出**稳态泄漏公式**与**修复优先级排序**。安全官对 `/docs`（F-3）做了**自我降级**（P0 → 🟡P2）。**净变化：P0 +1（仅 B7）、P1 −1（#1c 降 P2）。**

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🔴 **No-Go（当前工作区状态不可上线）**。测试与构建全绿，但存在 **7 项 P0**，其中三条**已由主理人独立复现**。
- **头号阻塞项（实测，且无需登录即可触发）**：**线程池泄漏 ⇒ 全站级联挂死**。`asyncio.wait_for` 到期只取消外层 await，`to_thread` 里阻塞在 socket read 的 worker **不可取消**，继续占槽；默认池仅 **22** 槽且全站共用（含 `/health/ready` 探针）。实测 **22 路并发 `?refresh=1` ⇒ 探针挂死 12s、30s 内不收敛、22/22 请求全返回 200 零 5xx ⇒ 监控完全看不见**。容器会被判 unhealthy，**只能重启进程恢复**。⚠️ **触发路径匿名可达**（见下方"未授权面"），且 overview 端点**未接 `compute_guard` 闸门**（`market.py` 内 0 命中）、全局 240s 兜底远大于内部 6s 预算 ⇒ 中间件不会切断它。
- **头号 P0 的第二张面孔（新，主理人已复现）**：**被放弃的阻塞线程会阻止进程退出**。`asyncio.run` 收尾时 `loop.shutdown_default_executor()` 会 join 池内 worker，而阻塞在 socket read 的 worker **永不返回** ⇒ 实测 `main()` 已正常返回、但**进程 20s 内不退出**（被 `timeout` 强杀，`rc=124`）。**生产含义**：被打满后 `docker stop` 会一直挂到 **SIGKILL**（滚动更新 / 重启**全部超时**），且**重启后攻击者继续轮询会立刻再次打挂** ⇒ **不是"重启即恢复"的一次性故障**。
- **⚠️ 主理人实测证伪了一条成员结论（"坏天气自爆"已撤回）**：调查员曾提出"前端 ⟳ 按钮直发 `/overview/rt?refresh=1`，其**降级成本 24s** > 15s 预算 ⇒ 东财一挂就每次点击泄漏一线程"的自我强化正反馈。**主理人实测证伪**：`24s` 是 `market.py:718` 记载的「**修复前**：~21 次外呼 ⇒ ~24s」数字，被误当成了降级成本。主理人**直接计时 `_build_rt`**（`D:\tmp_aqp_perf\lead_verify_rt_cost.py`）：**12.41s（冷启动）/ 7.95s（熔断稳态）**，**均低于 15s 预算 ⇒ 降级路径不泄漏**（与代码注释自述的 12.6 / 8.5s 吻合）。⇒ **该正反馈叙事已从报告中撤回**。`/overview/rt` 的残余风险是**正常路径 ~14s vs 15s 预算、余量仅约 1s**（代码在 `market.py:725` 自标"⚠️ 遗留"），属**薄余量脆弱性**而非必然泄漏 ⇒ 定级降为 🟡P2。
- **第二阻塞项（新，已核实）**：**明文口令会写入 30 天持久化日志**。`POST /api/v1/auth/login`（**匿名**）发送超长 password ⇒ pydantic 校验失败的 `errors()` 里含 `input` = **完整明文口令** ⇒ `errors.py:227` 以 WARNING 写入 `app.log` + `app.json.log`（**retention 30 days**），并在 `errors.py:230` 回显进响应体。**无需登录、无需异常、一次请求即可**。叠加 `app.log` sink 未覆盖 loguru 的 `diagnose` 默认值（实测 `LOGURU_DIAGNOSE=True`）⇒ 异常时还会转储局部变量。
- **第三阻塞项**：**Nginx `proxy_read_timeout=180s` 短于后端 240/330/660s**，`/backtest/strategy-run`（前端 600s）与 `/ops/dag/rerun`（前端 300s）**必然被网关先砍**，返回 Nginx HTML 而非统一信封。`timeout_guard.py` 只论证了"前端 vs 服务端"，**完全未把 Nginx 纳入设计**。
- **其余 P0**：manifest 冷重建串行 **283.8s** ⇒ `/datacenter/datasets` 必然 504（并行化实测 **8.3s，34×**）；lightgbm **RCE** CVE-2024-43598 / pyarrow UAF CVE-2026-25087；**仓库 0 提交** ⇒ 无代码回滚基线。
- **未授权面（新，已核实）**：`/api/v1/market/overview`、`/overview/rt`、`/overview/daily` **三个端点无任何鉴权**（`require_role` 在同一文件 `:29` 已导入、`:648`/`:679` 已使用，但这三个没有）。它们返回 AI 推荐榜、`ai_stats`、情绪与资金流。README 只声明"市场概览**页面**可公开访问"，**未声明 API 可匿名取核心推荐数据**。这正是头号 P0 的匿名入口。
- **需你拍板（两项）**：① `RBAC_ENFORCE=False`（登录即可用全部功能）—— 代码注释显示这是 **2026-09-23 你的刻意裁决**，但让"任一已登录账号 = 管理员"，且**未被 `validate_runtime_safety` 的 prod 校验覆盖**；② 上述三个 overview 端点是否应保持匿名。
- **正向信号**：后端 **1903 passed / 0 failed**；前端 tsc 严格 0 error + 体积门禁通过；核心端点 P50 全 <25ms；**受保护**端点鉴权实测生效（无 JWT 回 40100）；备份/恢复/演练三件套实测可用；`.env` 未入库、容器非 root + read_only。
- **最低限度放行条件**：① 重计算改用**独立 `ThreadPoolExecutor(max_workers=COMPUTE_CONCURRENCY)`**（不改 akshare、不改预算，改动最小、最可回滚）⇒ 消除"全站挂死 + 探针假死"，把爆炸半径压回"overview 变慢"；② 校验错误日志**剥离 `input`** 字段 ⇒ 消除明文口令落盘。两项均为小改，可当天完成。**⚠️ 但这两项不能消除"进程退出挂起"**——后者还需补 `socket.setdefaulttimeout`（让线程最终能返回）或改用 daemon 线程池。
- **稳态泄漏公式（调查员实测，建议写进发布说明）**：`稳态泄漏线程数 ≈ 真实成本 / (预算 + 请求间隔)`。验证：`4/(0.5+1.0)=2.67` vs 实测 2~3 ✅。代入生产兼容端点 `113.5/(6+15) ≈ **5.4 线程/客户端**` ⇒ **约 5 个持续轮询的匿名客户端即可耗尽全部 22 槽位**。（注：⟳ 路径 `/overview/rt` 实测 12.41s/7.95s **低于** 15s 预算，**不适用**本公式的泄漏分支。）

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **Go / No-Go** | 🔴 **No-Go**（至少先落"独立计算池"+"校验日志脱敏"两项缓解，再谈其余 P0） |
| **严重度分布** | 🔴 7 / 🟠 7 / 🟡 11 / 🟢 6（正向确认项） |
| **关键行动项** | 13 条（A1–A13），其中 P0 九条 |
| **测试基线** | 后端 1903 passed / 8 skipped / 3 deselected / **0 failed**（552.75s） |
| **爆炸半径** | 头号 P0 = **全站**（非单端点）；触发门槛 = **22 路并发**（或约 5 个持续轮询的匿名客户端），且**匿名可达** |
| **进程退出** | ❌ 被打满后进程**无法自然退出**（`rc=124`）⇒ `docker stop` 挂到 SIGKILL |
| **合规风险** | 明文口令进入 **30 天**持久化日志 + 响应体回显（已验证） |
| **建议负责人** | 后端负责人（A1、A3、A4、A8）、部署/运维（A2、A9）、安全负责人（A6、A7）、发布负责人（A5）、产品/安全（A10） |
| **上线形态建议** | 优先走 `docker-compose`（已内置 `ENV=prod` 强校验与 `${VAR:?}` 必填门禁） |

---

## 1. 各成员核心结论

### 🔍 产品评审员（全仓库代码审查）
- **核心判断**：🟡 有条件上线，**无 P0**。工程质量显著高于同规模项目平均水准 —— 1906 passed / 0 failed、`tsc --noEmit` 零错误、1277 个 TS 文件仅 1 处 `any` 逃逸；AST 全量扫描 115 个端点的鉴权依赖、响应契约、async 内阻塞调用（仅 6 处候选，**全部已正确包 `asyncio.to_thread`**）、SQL 拼接点、`setInterval`/`EventSource` 清理配对（全部配对）。
- **关键建议**：① 唯一的敏感信息泄漏面是 **`DEBUG` 默认 True 未在 `.env` 固定**，叠加 loguru `diagnose=True` 会在异常时**转储局部变量值**（登录路径上可能含 password/token 明文）—— 属**潜伏**风险，一行配置可消除；② 18 处 `with sqlite3.connect(...)` 只提交/回滚、**不 close**，与项目已有的 20+ 处正确写法口径不统一；③ `_jwt_expire_seconds()` 只读 `os.environ` 绕过 `Settings`，导致 `.env` 配 `JWT_EXPIRE_SECONDS` **静默无效**（实测仍 604800）；④ `export.py` 的 `day` 缺 `pattern`，用户填错日期被报成 `ERR_SYSTEM(50000)` 假 500。
- **注**：产品评审员从"正确性/可维护性"角度给出的"async 内阻塞调用 6 处**全部已 to_thread**"结论**是对的**——但它只看了"有没有下沉线程"，**未追问"下沉之后线程能不能被回收"**，这正是头号 P0 的盲区。
- **诚实边界**：工具化全量扫描覆盖全部文件，但逐行深读限于 core/db/cache/main/auth/export + 部分 api；`datacenter.py`(1754行)、`etf.py`(1212行)、`backtest/engine.py`(632行)、`ml/*` 数值内核、前端 `pages/*` 只做模式扫描未逐文件读。

### 🛡️ 安全官（OWASP Top 10 + STRIDE）
- **核心判断**：🔴 **存在必须修复的安全阻塞项**。但需强调 —— 密码哈希、JWT 算法硬锁（`["HS256"]` + issuer 校验，无 alg:none / RS→HS 混淆）、SQL 全参数化、SSE ticket 一次性与防重放、非 root 容器都**做得扎实**；阻塞项来自 **3 个默认/配置级缺口**，且**生产 fail-fast 守卫没覆盖其中最重的一个**。
- **关键建议**：① **`RBAC_ENFORCE=False` 未被 `validate_runtime_safety` 纳入 prod 必检项**（`config.py:292-313`），ENV=prod 也不拦 —— 115 路由中 67 个 researcher + 3 个 admin 端点全部降级为"登录即可"；② **依赖漏洞**：pip-audit 实跑 147 包命中 5 个，其中 **lightgbm 4.5.0 → CVE-2024-43598（RCE，修 4.6.0）**、**pyarrow 17.0.0 → CVE-2026-25087（UAF，修 23.0.1）**；③ `/docs`、`/redoc` 无 ENV 开关、无鉴权（`main.py:215-216`）；④ **`backup.py` 的 tar 解压只校验成员"名"、不校验 symlink/hardlink，且 `extractall` 未传 `filter`** —— PoC 已复现：含 symlink 的恶意归档通过校验，同归档加 `filter='data'` 即被拦（Windows 因无建链权限掩盖，**Linux 容器可真实利用**）。
- **明确通过项**：SQL 注入 ✅、JWT 算法 ✅、SSE ticket ✅（PoC 验证一次性/防重放/伪造拒绝/跨存储域降级拒绝）、密钥管理 ✅（`.env` 未被 git 跟踪，全仓无硬编码密码/token/私钥）、容器 ✅（非 root 10001 + `read_only` + tmpfs；Redis `--requirepass` 且只绑 `127.0.0.1`；compose 用 `${VAR:?}` 强制生产必填）、前端产物 ✅（dist 无后端 IP/密钥泄露）。
- **裁决说明**：安全官将 `RBAC_ENFORCE` 判为 🔴P0。经主理人核对代码注释（`config.py:203-214` 明确记载"2026-09-23 用户裁决：全面放开"），本报告将其**调整为 🟠P1-需产品裁决** —— 它是**刻意的产品决策**而非编码缺陷，但必须显式确认并绑定部署形态。
- **增量审计（收尾阶段追加，主理人已逐条核实）**：
  - **F-11 🔴 明文口令落盘（主理人独立复现成立）**：两条机制。① `logging.py:60-68` 的 `app.log` sink 传了 `backtrace=True` 但**未覆盖 `diagnose`**，而实测 `loguru._defaults.LOGURU_DIAGNOSE = True` ⇒ 文件 sink 的 diagnose 是**开启**的（只有 console sink 用 `diagnose=settings.DEBUG` 受 `DEBUG` 控制 ⇒ **`DEBUG=false` 并不能修好文件 sink**）；② 更直接的一条：`errors.py:226-227` 把 pydantic 校验明细以 **WARNING** 写日志，而 `_jsonable_validation_errors`（`errors.py:43-64`）除把 `ctx` 内异常转字符串外**原样保留全部字段**（含 `input`），pydantic v2 会把违规值放进 `input`。主理人实测：`LoginRequest(username="admin", password="MyS3cr3t!"*15)` ⇒ `errors()` 含 `{'type':'string_too_long','loc':('password',),'input':'MyS3cr3t!MyS3cr3t!…'}`，**明文口令 `in` 判定为 True**。该 WARNING 同时进 `app.log` 与 `app.json.log`（`serialize=True`，两者 **retention 30 days**），并经 `errors.py:230` **回显进响应体**。触发路径 `POST /api/v1/auth/login` **匿名**、**无需异常、一次请求**。
  - **F-12 🟠 未授权面（主理人已核实）**：`/overview`(`market.py:895`)、`/overview/rt`(`:766`)、`/overview/daily`(`:830`) 三个端点**无任何鉴权依赖**；`require_role` 在 `:29` 已导入、`:648`/`:679` 已使用 ⇒ 属**遗漏而非刻意**。另核实 `compute_guard`/`compute_slot` 在 `market.py` **0 命中** ⇒ overview 绕过重计算闸门。安全官据此把 QA 的线程池 DoS 从"低权账号可 DoS"**升级为"未认证可 DoS"**（并要求 QA 复测匿名路径，已转达）。
  - **补充定性（安全官）**：JWT 存 localStorage 原判 P2 维持，但与 F-1（RBAC 放开）**叠加后**一次 XSS 即得全权限 token，实际风险高于单项评级。

### ✅ QA 与发布（测试与发布检查）
- **核心判断（已修订）**：原判 🟡 有条件放行，**在完成端到端实验后下调为 🔴 不建议放行**。
- **决定性实验（真实后端 :8012）**：并发 22 路 `GET /market/overview?refresh=1` 期间 —— `/health/live`（不走 `to_thread`，作对照）仍 **200 / 10.4ms**（进程存活），而 `/health/ready`（走 `to_thread`）**挂死 `ReadTimeout 12008.1ms`**；服务端日志同时出现 **22 条 `market.py:949`** 的"预算 6.0s 内未完成 → 返回降级载荷"警告，**22 次超时 = 22 个泄漏线程 = 池满**，与协议层测得的 22 个被占用 worker **数量完全吻合**。
- **新增维度（压力后连续探测）**：T+0/5/10s 仍超时，T+15s 返回 200 但耗时 **18518ms（基线 12.5ms 的 1480 倍）**，T+20/25s 又超时 —— **超过 30s 仍反复超时，是不收敛的振荡，不是抖动后恢复**。推断机制：降级载荷 `status=degraded` 落 **≤15s 短 TTL**（`market.py:969-976`）⇒ 15s 后过期 ⇒ 下一请求**重新走 6s 冷算 ⇒ 再次泄漏**，形成**自维持泄漏**（自愈机制本身成了新的泄漏源）。若成立，生产一旦被打过就**回不到健康态，只能重启进程**。
- **监控盲区**：22/22 请求全部返回 **200**（降级载荷），**零 5xx** ⇒ 基于 5xx 的告警与 `HTTP_REQUEST_TIMEOUT_TOTAL` 指标**都不会触发**，客户端完全无感，**只有探针静默挂死**。
- **其余关键建议**：① Nginx 层有 **2 条硬违例**（strategy-run、dag-rerun）+ **5 条等值零余量**（export/quality-scan/mirror-rebuild/research-optimize/stress-test 前端 180s = Nginx 180s）；② **经 Nginx 访问 `/health`、`/metrics` 会落到 `location /` → `try_files` 回 `index.html` 200** ⇒ 外部监控**假绿**；③ **`python scripts/backup.py --help` 会直接执行真实备份**（实测已生成 `backup/aqp-2026-09-29-181518.tar.gz`），误操作即触发；④ 根目录无 `VERSION`/`CHANGELOG`、`git tag` 为空 ⇒ 无法定位"回滚到哪个版本"。
- **预算不变量的重大遗漏**：原报告称"后端侧 0 违例"**不完整** —— 只查了"前端 timeout vs 服务端预算"，漏了真正的杀手"**预算 vs 真实成本**"（`market.py:943` 6s vs 自述实测 23.5~113.5s；`etf.py:207` 4.5s vs 实测 6.22s），全仓 **≥3 处违反**。
- **实测数据**：后端离线回归 **1903 passed / 8 skipped / 3 deselected / 0 failed / 552.75s**；前端构建 0 error（755 modules，28.37s）；`bundle:check` 通过（raw ≤768000B / gzip ≤256000B）。
- **诚实边界**：未跑全量 pytest（含 network）；前端无 lint script；写操作端点（下单/同步/训练）与 backtest/ops 长任务未做真实端到端；Nginx 缺陷为**静态推导**（未起 Nginx）；耗时因东财源不可达而**偏乐观**。

### 🔧 调查员（超时根因 + 性能优化）
- **核心判断**：超时主因**不是**事件循环被阻塞 —— AST 扫描 `api/v1 + services + jobs` 的全部 async 体，**0 处**直接阻塞调用，全部已 `to_thread`。真正原因是**资源/配置层**，且**最严重的一条是"下沉到线程"之后的回收问题**。
- **头号发现（新，协议层实测）**：用"accept 后永不应答"的黑洞 TCP server 模拟 akshare 行为，并发发起 `wait_for(to_thread(blocking_recv), 0.3s)`：**超时 22 次、外层 301ms 全部返回**，但 **executor 内 22 个 worker 仍被占用**，随后短任务 **2s 内拿不到线程**（池耗尽）；对照组（可取消的 `asyncio.sleep`）无泄漏，排除假阳性。`app/` 内 `set_default_executor` **0 命中** ⇒ 池子固定 22。
- **关键推论**：**凡 `wait_for` 预算 < 真实耗时，就是一个"线程泄漏发生器"**。代码里自己就承认了这些组合：`market.py:943`（6s vs 23.5~113.5s）**100% 泄漏**；`etf.py:207`（**4.5s vs 6.22s**）冷路径必然泄漏（日志实测 7 次降级）；`market.py:782` `/overview/rt`（15s vs 正常 ~14s）**余量仅约 1s、薄余量脆弱**（**降级路径实测 12.41/7.95s，不泄漏 —— 主理人已实测证伪原"降级必泄漏"说法**）。⇒ **2~11 个并发 `?refresh=1` 就能吃光 22 个槽位**。
- **证伪 `timeout_guard.py` 的兜底承诺**：其 docstring（:13-15）自称"避免一次慢查询/死循环（含 `asyncio.to_thread` 里的重计算）永久占用一个 worker"—— **实测不成立**。240s 兜底只让客户端拿到 504，工作照跑、线程照占。
- **稳态泄漏公式（收尾追加，实测验证）**：`稳态泄漏线程数 ≈ 真实成本 / (预算 + 请求间隔)`。实验三组：S1 **有界成本收敛**（峰值 3，随后回落）；S2 **无界成本单调增长**（1→4→7→11→14→15，不回落）；S3 对照组**恒 0**（可取消的 `asyncio.sleep`）。⇒ **定性修正**：泄漏**不是"必然全站挂死"，而是"条件性全站挂死"**——有界成本下会收敛，**无界成本（或外部源半开连接）下才不收敛**。生产代入兼容端点 `113.5/(6+15) ≈ 5.4 线程/客户端`；代入 ⟳ 路径 `24/15 ≈ 1.6 线程/用户`。
- **P0 的第二张面孔：进程退出挂起（收尾追加，主理人已复现）**：`asyncio.run` 收尾时 `loop.shutdown_default_executor()` 会 join 池内 worker；阻塞在 socket read 的 worker **不可取消、永不返回** ⇒ 进程**无法自然退出**。主理人实测：`main()` 已正常返回后，20s 内进程不退出（`rc=124`）。**生产含义**：被打满后 `docker stop` 挂到 SIGKILL、滚动更新/重启**全部超时**；且重启后攻击者继续轮询会**立刻再次打挂** ⇒ 不是"重启即恢复"。调查员称这是"**专用 executor 方案的最强论据**"。
- **风险定性修正：跑步机入口分两类（收尾追加，**其中一半已被主理人实测证伪**）**：查前端调用面后发现——**前端从不调用兼容端点 `/api/v1/market/overview`**（`api/market.ts` 只导出 `overviewRt` / `overviewDaily`），故该端点仅**攻击者/手工/脚本**可达（⇒ 可**零业务代价直接下线**）；而 `/market/overview/rt?refresh=1` 是前端 ⟳ 按钮的日常路径（`pages/MarketOverview/index.tsx:75`）。**但调查员据此推出的"降级 24s > 15s ⇒ 每次点 ⟳ 泄漏一线程 / 坏天气自爆"已被主理人实测证伪**（实测 12.41/7.95s < 15s，24s 系「修复前」旧值误引）⇒ **该正反馈叙事撤回**，`/overview/rt` 仅余"正常路径薄余量（~1s）"这一静态脆弱性。证据：`D:\tmp_aqp_perf\frontend_overview.out`（调查员）、`D:\tmp_aqp_perf\lead_verify_rt_cost.py`（主理人实测）。
- **其他关键实测**：① manifest 冷重建 3 个日线数据集串行 **283.8s**（daily_bar 107.8s / hfq 29.4s / qfq 146.6s）**远超 240s 全局兜底 ⇒ `/datacenter/datasets` 必然 504**（日志两次实锤）；并行化 w=8 后 **8.3s（34×）**；② `read_parquet_columns` 逐文件串行读：10924 文件 **19.8s** vs `scan_parquet` **3.3s（6.0×）**；③ `_quality_calc` 串行 2499 次读 **86s**；④ `market._build_ai_stats` 读 10924 hfq 文件 **19.8s**；⑤ `_heat/_sectors_from_local` 2499 文件 **5.2s**。
- **两条如实记录的证伪/矛盾**：① 调查员**证伪了自己**关于"`main.py:269` 的 `BaseHTTPMiddleware` 会缓冲 SSE"的假设 —— ASGI 级实验（记录每次 `http.response.body` 的 send 时刻）显示裸 FastAPI `['11ms','214ms','422ms']` vs 套该中间件 `['2ms','207ms','415ms']`，**不缓冲、逐条放行**；SSE 风险**只在 Nginx `proxy_buffering on`**。**本报告不主张"BaseHTTPMiddleware 破坏 SSE"。** ② 项目**自相矛盾**：`panic_guard.py:3` 与 `timeout_guard.py:5` **明文严禁** `BaseHTTPMiddleware`，但 `main.py:269` 的计时中间件正是它且在最外层（实测未发现功能损害，属文档与实现不一致）。
- **核实结果**：主理人交给它的"已落地 9 项性能优化"**全部仍在代码中，无回退**。
- **诚实边界**：manifest **冷**重建总时长为按串行实测外推（未跑整轮 force 重建）；同步 6900s、`_quality_calc` 3s 均为估算。

### 🧪 主理人独立复现（对本轮头号 P0 的第三方验证）
为免采信单方结论，主理人另写脚本独立复现（`D:\tmp_aqp_perf\lead_verify_threadleak.py`）：
```
cpu_count=18  默认池 max_workers=22
timeouts 22
short STARVED -> LEAK      ← 外层 wait_for 已全部返回，短任务 2s 内仍拿不到线程
threads 24
```
**三方证据吻合**（协议层 22/22 占用 · 端到端 22/22 探针挂死 · 主理人独立复现 STARVED），该 P0 成立。

---

## 2. 综合审查发现（去重合并，按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🔴 P0 | **可用性/线程池泄漏** | `market.py:943-945`、`etf.py:207`、`main.py:269` 等所有 `wait_for(to_thread(...))` 点 | `asyncio.wait_for` 到期只取消**外层 await**，`to_thread` 里阻塞在 socket read 的 worker **不可取消**、继续占槽。默认池仅 **22** 槽（`min(32,cpu+4)`，`app/` 内 `set_default_executor` 0 命中）且**全站共用**（含 `main.py:371` 的就绪探针）。**实测**：22 路 `?refresh=1` ⇒ `/health/ready` 挂死 12008ms（对照 `/health/live` 仍 200/10.4ms）⇒ 容器 unhealthy；**30s 内不收敛**（降级载荷 ≤15s 短 TTL ⇒ 过期后重走 6s 冷算 ⇒ 再泄漏，形成自维持泄漏）；**22/22 HTTP 200 且业务码全为 `0`、零 5xx ⇒ 监控完全看不见**（平台"HTTP 恒 200"契约使基于状态码的告警**系统性失效**，比"零 5xx"更根本）。⚠️ **触发路径匿名可达**：QA 在无任何 Authorization 头下实测 22/22 拿到真实业务数据并打死探针（`/stock/search` 同批对照回 40100 以证明脚本未带凭据）⇒ **攻击门槛是"任何能连到端口的人"**。**触发条件是"缓存未命中"而非"并发高"**：`refresh=1` 有 5s 防抖 + `rebuild_lock_ttl=180` 重建锁，**缓存有值（含降级值）时并发风暴被折叠成 1 次重建、不泄漏**；只有缓存为空（冷启动 / 硬过期 / SWR 窗口失效）时 22 路才同时走 6s 同步预算 ⇒ 表现为"**冷启动后或长时间空闲后突然全站假死**"。overview **未接 `compute_guard` 闸门**（`market.py` 0 命中，配置遗漏）、全局 240s 兜底 ≫ 内部 6s 预算（**层级反了 40 倍**）⇒ 池耗尽时中间件来不及卸载，只会陪着等 —— 这正好解释"探针挂死 12s 而非被 504 快速切断" | ① 重计算改用**独立 `ThreadPoolExecutor(max_workers=COMPUTE_CONCURRENCY)`**，不用默认池；② 三个 overview 端点加 `require_role("viewer")`；③ `socket.setdefaulttimeout(10)` 兜 akshare；④ 预算提到 ≥ 真实成本或去掉同步路径预算。⚠️ **① 与 ② 缺一不可**：只加鉴权不够（`RBAC_ENFORCE` 默认 False ⇒ 攻击者自助注册 viewer 即可绕过）；只做独立池则入口仍匿名。过渡缓解：缓存预热 / 延长 stale 窗口（收窄冷窗口，不能替代根治） | 调查员 + QA + 主理人复现 |
| **1b** | 🔴 P0 | **可用性/进程生命周期** | `main.py`（`asyncio.run` 收尾）→ `loop.shutdown_default_executor()` | 头号 P0 的**第二张面孔**：被放弃的阻塞 `to_thread` worker **阻止进程退出**。`asyncio.run` 收尾会 join 池内 worker，而阻塞在 socket read 的 worker **不可取消、永不返回**。**主理人实测**：`main()` 已正常返回后，20s 内进程不退出（`timeout` 强杀 `rc=124`）⇒ 被打满后 `docker stop` 挂到 **SIGKILL**、滚动更新/重启**全部超时**；且**重启后攻击者继续轮询会立刻再打挂** ⇒ **不是"重启即恢复"的一次性故障** | ① `socket.setdefaulttimeout(10)`（让线程最终能返回）；② 或改用 **daemon 线程**的专用池。**⚠️ 注意**：仅"专用池"**不能**修好退出挂起 —— `ThreadPoolExecutor` 的 worker 自 Python 3.9 起为**非 daemon**，解释器退出时仍会被 join | 调查员 + 主理人复现 |
| **1c** | 🟡 P2 | **薄余量脆弱性（原"坏天气自爆"已证伪）** | `frontend/src/pages/MarketOverview/index.tsx:75`、`market.py:782-783,725` | ⟳ 轻刷新按钮**直发** `/overview/rt?refresh=1`（前端从不调兼容端点 `/overview`），`refresh=1` 跳过缓存读 ⇒ **每次点击都是一次冷构建**。**调查员原称"降级成本 24s > 15s 预算 ⇒ 每次点必泄漏"，已被主理人实测证伪**：`_build_rt` 实测 **12.41s（冷）/ 7.95s（熔断稳态）**，**均 < 15s ⇒ 降级路径不泄漏**（24s 系 `market.py:718` 的「修复前」旧值被误引）。**真实残余风险**：正常路径 ~14s vs 预算 15s，**余量仅约 1s**（代码 `market.py:725` 自标"⚠️ 遗留"）⇒ 外呼抖动即可能突破预算并泄漏。属**薄余量脆弱性**，非必然泄漏 | 预算 15s → **≥30s**（低成本加固，仍建议做）；补 `require_role("viewer")`；重计算改专用池 | 调查员（提出）+ 主理人（实测证伪并降级） |
| **F-11** | 🔴 P0 | **凭据泄漏/日志污染** | `core/errors.py:43-64,221-231`、`core/logging.py:60-79`、`api/v1/auth.py:36` | 明文口令写入**30 天**持久化日志并回显响应体。`LoginRequest.password` 有 `max_length=128`（`auth.py:36`），超长即触发 `RequestValidationError`；`_jsonable_validation_errors` **原样保留 `input`** 字段，pydantic v2 把违规值放进 `input` ⇒ `errors.py:227` 以 **WARNING** 落 `app.log` + `app.json.log`（`serialize=True`，**retention 30 days**），并经 `errors.py:230` **回显进响应体**。叠加 `app.log` sink 未覆盖 `diagnose`，而实测 `loguru._defaults.LOGURU_DIAGNOSE = True` ⇒ 文件 sink 的 diagnose 为**开启**（`DEBUG=false` 只作用于 console sink，**修不好文件 sink**）。**主理人实测**：`password="MyS3cr3t!"*15` ⇒ `errors()` 含完整明文口令（`in` 判定 True）。**匿名、无需异常、一次请求即可** | ① 日志与响应体**剥离 `input`**（只留 `type`/`loc`/`msg`/`ctx`）；② 给 `app.log`/`app.json.log` sink 显式 `diagnose=False`；③ 密码字段改用 `SecretStr` 或跳过 `input` 采集 | 安全官 + 主理人核实 |
| 2 | 🔴 P0 | 超时链断裂 | `frontend/nginx.conf:31` vs `core/timeout_guard.py:98,102`、`config.py:108` | Nginx `proxy_read_timeout=180s` **短于**后端 240/330/660s ⇒ `/backtest/strategy-run`（前端 600s）、`/ops/dag/rerun`（前端 300s）必然被网关先砍，且返回 Nginx HTML 而非统一信封 ⇒ 前端走"服务异常(504)"并可能重试放大。`timeout_guard.py` docstring 只论证"前端 vs 服务端"，**未纳入 Nginx** | Nginx 提到 **≥700s**；或后端预算下调至 <180s | QA + 调查员 |
| 3 | 🔴 P0 | IO 放大 → 504 | `data/parquet_store.py:314,338,390,417`；`api/v1/datacenter.py:571` | manifest 冷重建逐文件**串行** `pq.ParquetFile` footer 全扫 36500 文件，实测 **283.8s** > 240s 全局兜底 ⇒ `/datacenter/datasets` 必然 504（日志 2 次实锤）。并行 w=8 实测 **8.3s（34×）** | ThreadPoolExecutor(w=8) 并行 footer 扫描 | 调查员 |
| 4 | 🔴 P0 | 依赖 CVE | `backend/requirements.txt` | pip-audit 实跑 147 包命中 5 个：**lightgbm 4.5.0 → CVE-2024-43598（RCE，修 4.6.0）**、**pyarrow 17.0.0 → CVE-2026-25087（UAF，修 23.0.1）**；另 3 个为 pip/setuptools（构建期）、pytest（测试期） | 升 lightgbm ≥4.6.0、pyarrow ≥23.0.1 并回归训练/parquet 读写（pyarrow 大版本有静默截断历史坑，需谨慎） | 安全官 |
| 5 | 🔴 P0 | 发布完整性 | `.git/`（根目录） | **仓库当前 0 个提交**：`git log` 报 "does not have any commits yet"，`.git/refs/heads/` 目录不存在，`.git/objects` 仅 9 个松散对象、无 packfile。根目录无 `VERSION`/`CHANGELOG`，`git tag` 为空 ⇒ **无版本基线、无法打 release tag、无法 revert 回滚**。源码层零丢失，但缺版本化基线（该 `.git` 历史上已被外部进程清空三次） | 由工作区重建根提交并**立即 push 到远端**；补 `VERSION`/`CHANGELOG` | QA + 主理人核实 |
| **F-12** | 🟠 P1 | **未授权面/核心 IP 泄露** | `api/v1/market.py:767,831,896`（def 行；装饰器在 766/830/895） | `/market/overview`、`/overview/rt`、`/overview/daily` **三个端点无任何鉴权依赖**（主理人已核实）。`require_role` 在 `market.py:29` 已导入、`:648`/`:679` 已用于其它端点 ⇒ 属**遗漏而非产品决策**；三层均无兜底（`router.py` 无 router 级依赖、include 无 `dependencies=`、`main.py:211-217` 无全局 `dependencies=`）。返回 `recommend`（AI 推荐榜，`recommend_k` 上限 200）、`ai_stats`、`sentiment`、`sectors`、`money_flow`，且 `date=YYYYMMDD` 可拉**任意历史交易日**的推荐快照；端点自述 DEPRECATED、前端已不用 ⇒ **dead-but-live 的核心 IP 匿名可取**。同时是头号 P0 的**匿名触发入口** | 加 `require_role("viewer")`，或直接摘除该 DEPRECATED 端点 | 安全官 + 产品评审员 + 主理人核实 |
| **F-13** | 🟠 P1 | **限速绕过/日志洪泛（已确认）** | `api/v1/auth.py:103-105`、`core/errors.py:227,230`、`core/logging.py:60-79` | `_check_rate_limit(req.username)` 位于 **handler 内部**（`auth.py:105`），而 FastAPI 对 `req: LoginRequest` 的 body 校验在 **handler 之前**执行 ⇒ **任何校验失败的请求都不经过登录限速**（主理人已核实）。**放大效应已由主理人实测确认**（原为待验证假设）：body 顶层类型不匹配时 pydantic 产出 `type=model_type, loc=()`，且 **`input` 字段完整保留整个 body** —— 实测传入 1,000,000 字符的 JSON 字符串 ⇒ `errors` 序列化后 **1,000,215 字符**（"完整保留=True"）。该串以 **WARNING** 写入 `app.log` **与** `app.json.log`（**双写**），并经 `errors.py:230` **回显约 1 MB 到响应体**。⇒ 一次匿名请求即可写入约 **2 MB** 日志并回吐约 **1 MB**。**⚠️ 磁盘水位**：本项目数据盘此前已告警至 **93%**；两个 sink 各有 rotation（10 MB / 20 MB）但**均未设 `compression`** ⇒ rotation 只限单文件大小，**30 天 retention 窗口内的累积总量无上界**（"rotation 会保护磁盘"是错觉）。**注意（安全官修正，勿误读）**：此路径**不能**用于口令爆破 —— handler 未执行，`verify_password` 从不运行，**既不泄漏口令正确性也不泄漏用户名是否存在** | 把登录/注册限速**上移到中间件或路由级 `dependencies=[...]`**（`auth.py:105` 的 handler 内位置**结构性够不到** `RequestValidationError`，必须前移才能同时覆盖"校验失败"与"校验通过"两条路径）；对 `input` 设长度上限或整体剥离；sink 补 `compression`；网关层加 body 大小上限 | 安全官 + 主理人核实 |
| 6 | 🟠 P1 | 超时预算不变量（新维度） | `market.py:943`（6s vs 23.5~113.5s）、`market.py:782`（15s vs 正常 ~14s，**余量 ~1s**）、`etf.py:207`（4.5s vs 6.22s） | 原不变量只覆盖"前端 timeout < 服务端预算"，**漏掉"服务端预算 ≥ 真实成本"**。凡预算 < 真实成本者即"线程泄漏发生器"，全仓 **≥3 处违反**（其中 `market.py:943` 100% 泄漏、`etf.py:207` 冷路径必泄漏；`market.py:782` 为薄余量） | 为每个块级预算补"实测真实成本"注释与回归断言；预算提到 ≥ 真实成本 | QA + 调查员 |
| 7 | 🟠 P1 | 访问控制 | `core/auth.py:229-230`、`core/config.py:208-214`、`config.py:292-313` | `RBAC_ENFORCE=False`（默认）时 `ensure_role` 直接放行 ⇒ 115 路由中 67 个 researcher + 3 个 admin 端点降级为"登录即可"；**且该开关未被 `validate_runtime_safety` 纳入 prod 必检项**，ENV=prod 也不拦。代码注释显示这是 2026-09-23 的**用户裁决**（刻意放开） | **需产品裁决**：确认是否仍要全面放开。若维持，建议在 `validate_runtime_safety` 加 prod 告警，并确保 `ALLOW_REGISTRATION=false` + `API_HOST=127.0.0.1`（当前 `.env` 已配） | 安全官 |
| 8 | 🟠 P1 | 敏感信息泄漏 | `core/logging.py:53-55`、`core/config.py:80`、`main.py:215-216` | `DEBUG` 默认 True 且 `.env` 未固定 ⇒ loguru `diagnose=True` 在异常时**转储局部变量值**（登录路径上可能含 password/token 明文）打到 stdout/容器日志；同时 `/docs`、`/redoc` 无 ENV 收口、无鉴权（暴露全部 115 端点 schema）。当前属**潜伏**风险 | `.env` 显式 `DEBUG=false`+`LOG_LEVEL=INFO`；`diagnose` 改 `settings.DEBUG and settings.ENV != "prod"`；生产 `docs_url=None` 或挂 `require_role("admin")` | 产品评审员 + 安全官 |
| 9 | 🟠 P1 | 路径/归档安全 | `scripts/backup.py:125-133,161` | tar 解压只校验成员**名**、不校验 symlink/hardlink，`extractall` 未传 `filter`。**PoC 已复现**：含 symlink 的恶意归档通过校验，同归档加 `filter='data'` 抛 `AbsoluteLinkError` 被拦。Windows 因无建链权限掩盖，**Linux 容器（python:3.11-slim）可真实利用** | `tar.extractall(path, filter='data')`（一行） | 安全官 |
| 10 | 🟠 P1 | 资源泄漏 | 18 处 `with sqlite3.connect(...)`（`alerts.py:425`、`app_settings.py:81,99`、`market.py:190`、`ops.py:516`、`portfolio.py:94`、`report.py:197`、`research.py:126,236,318`、`watchlist.py:171`、`panels.py:207`、`main.py:427`、`task_store.py:45,76,84,97,139`） | sqlite 上下文管理器只提交/回滚、**不 close**；异常路径下 traceback 持有连接 ⇒ WAL 锁与句柄延迟释放。项目已有 20+ 处正确写法，口径不统一 | 统一补 `finally: conn.close()` 或抽 `contextmanager` | 产品评审员 |
| 11 | 🟡 P2 | 可观测性 | `frontend/nginx.conf` + `main.py` | 经 Nginx 访问 `/health`、`/metrics` 会落到 `location /` → `try_files` 回 **`index.html` 200** ⇒ 外部监控**假绿**（Nginx 仅反代 `/api/`） | 增加 `location = /health` / `location = /metrics` 显式反代，或监控直连后端端口 | QA |
| 12 | 🟡 P2 | 运维/DX | `scripts/backup.py` | `python scripts/backup.py --help` **直接执行真实备份**（实测已生成 `backup/aqp-2026-09-29-181518.tar.gz`），误操作即触发 | 加 argparse `--help` 早退，或需显式 `--yes` | QA |
| 13 | 🟡 P2 | 文档与实现矛盾 | `panic_guard.py:3`、`timeout_guard.py:5` vs `main.py:269` | 两个 guard 的 docstring **明文严禁** `BaseHTTPMiddleware`，但 `main.py:269` 的计时中间件正是它、且位于最外层。实测未发现功能损害（含 SSE 不缓冲，已由 ASGI 级实验证伪"缓冲"假设） | 要么改用纯 ASGI 中间件，要么在注释中说明豁免理由 | 调查员 |
| 14 | 🟡 P2 | 配置静默失效 | `core/auth.py:154-155` `_jwt_expire_seconds()` | 只读 `os.environ`、绕过 `Settings.JWT_EXPIRE_SECONDS`；pydantic-settings 不回写 env ⇒ `.env` 配 `JWT_EXPIRE_SECONDS=3600` **静默无效**（实测仍 604800） | 改读 `get_settings().JWT_EXPIRE_SECONDS`（env 仅作 override） | 产品评审员 |
| 15 | 🟡 P2 | 参数校验 → 假 500 | `api/v1/export.py:20,38` | `day` 无 `pattern`，`date_cls.fromisoformat(day)` 抛 ValueError 逃逸到全局兜底 ⇒ 用户填错日期被报成 `ERR_SYSTEM(50000)`（与 `market.py:833` 已修的 P1-35 同族） | 加 `pattern=r"^\d{4}-\d{2}-\d{2}$"` 或 try/except 转 40000 | 产品评审员 |
| 16 | 🟡 P2 | 路径穿越（读路径） | `data/parquet_store.py:357,689,729` | `symbol` 无正则约束（API 仅 min/max length），可拼出穿越路径 —— **已验证** `symbol=../../../../Windows/System32` 解析到 `data\Windows\System32`，逃出 `DATA_ROOT`。当前仅到**读**路径，影响受限 | 加 `^[0-9]{6}\.(SH\|SZ\|BJ)$` 白名单 + `resolve()` 前缀校验 | 安全官 |
| 17 | 🟡 P2 | 多租户缺失 | `db/models.py:84,102,316`（watchlist/portfolio/app_settings） | 表有 `user_id` 字段但 **API 从不使用**，全落 default ⇒ 多账号**共享同一自选/组合/模拟盘持仓与订单** | 按 JWT `sub` 过滤；或明确声明"单租户共享工作区" | 安全官 |
| 18 | 🟡 P2 | 缓存维度 | `api/v1/datacenter.py:387-389` | `_data_cache_key` 只含 `DATA_ROOT`、不含数据修订号；TTL 1800s 的陈旧安全**完全依赖**写路径 invalidate（自述曾漏 `orchestrator.run_pipeline`） | key 加入 manifest mtime / 修订号 | 调查员 |
| 19 | 🟡 P2 | 嵌套并行 | `app/__init__.py:48`(polars=6) vs `ml/train_lgbm.py:287`/`torch_models.py:121`(=12) | **NumPy/BLAS 未注入** `OMP_NUM_THREADS`/`MKL_NUM_THREADS`（grep 0 命中）⇒ 走全核，与线程池（≤22）叠加存在嵌套争用 [静态推断] | 同步注入 OMP/MKL | 调查员 |
| 20 | 🟡 P2 | 凭证存储 | `frontend/src/stores/useAuthStore.ts:60` | JWT 经 zustand persist 落 **localStorage** ⇒ 任一 XSS 即可窃取；叠加 RBAC 放开会放大影响 | 短期确认前端无不可信 HTML 注入点；中期改 httpOnly Cookie | 产品评审员 |
| 21 | 🟢 P3 | 工程卫生 | `frontend/vite.config.ts`（`server.host:'0.0.0.0'`）、`vite.config.ts.timestamp-*.mjs` ×11、`dist-audit/`、`dist-verify-p23/` | dev server 监听全网卡；临时文件与多份构建产物快照并存（易误发布旧版） | dev 改 `127.0.0.1`；清理并加 `.gitignore`；固定单一 `dist/` | 产品评审员 |
| 22 | 🟢 P3 | 熔断耦合 | `cache/redis_client.py:299` `ping()` | 健康探测失败计入 `_mark_fail()` ⇒ 探针高频轮询 `/health` 会在 Redis 抖动时 5 次内提前打开 60s 熔断 | `ping` 走独立计数器，不计入业务熔断 | 产品评审员 |
| 23 | 🟢 P3 | 图表性能 | 前端 ECharts / Lightweight Charts | 无降采样（grep `sampling\|large:\|lttb` 0 命中）⇒ 长历史全量渲染 [静态推断]；无虚拟滚动（但 Screener 已分页，风险可控） | 图表加 `sampling:'lttb'` | 调查员 |
| 24 | 🟢 正向 | 安全 | `config.py:290-313`、`core/auth.py`、`docker-compose.yml`、`frontend/dist` | prod fail-fast 校验完备；JWT 算法硬锁 HS256 + issuer；SSE ticket 一次性 + 防重放（PoC 验证）；`.env` 未被 git 跟踪、全仓无硬编码密钥；容器非 root + read_only + Redis requirepass 且只绑回环；前端产物无密钥泄露 | 保持 | 安全官 |
| 25 | 🟢 正向 | 质量 | `backend/tests/`、`frontend/` | 后端 1903 passed / 0 failed；前端 tsc 严格 0 error + bundle:check 通过；`setInterval`/`EventSource` 清理全部配对；SSE 前端有指数退避 + MAX_RETRIES + 15s 心跳（`useNotifyStore.ts:76-83`） | 保持 | 产品评审员 + QA |
| 26 | 🟢 正向 | 运维 | `backend/scripts/{backup,restore,backup_drill}.py` | 三件套实测可用：备份产 tar.gz + `.sha256`；恢复演练在临时目录完成（验 DB 可打开 + 27 张表）**不碰生产数据** | 保持 | QA |

---

## 3. 🚧 阻塞项清单（必须在上线前处置）

| # | 阻塞项 | 类型 | 修复成本 | 对应发现 |
|---|--------|------|----------|----------|
| **B1** | **线程池泄漏 ⇒ 条件性全站级联挂死**（触发条件 = **缓存未命中**：兼容端点 22 路并发，或**约 5 个持续轮询的匿名客户端**；探针假死；监控盲区；有界成本收敛、**无界成本不收敛**） | 架构/代码 | 中（独立计算池，改动小且可回滚） | #1 |
| **B2** | Nginx `proxy_read_timeout` 180s 短于后端预算，长任务必被网关误杀 | 配置 | **一行** | #2 |
| **B3** | manifest 冷重建串行 283.8s ⇒ `/datacenter/datasets` 必然 504 | 代码 | 中（并行化，已有 34× 实测收益） | #3 |
| **B4** | lightgbm RCE CVE-2024-43598 / pyarrow UAF CVE-2026-25087 | 依赖 | 中（需回归训练与 parquet 读写） | #4 |
| **B5** | 仓库 0 提交、无 VERSION/CHANGELOG、无 tag ⇒ 无回滚基线 | 流程 | 低（重建根提交 + push + 打 tag） | #5 |
| **B6** | **明文口令写入 30 天持久化日志 + 回显响应体**（匿名可达、一次请求即可、主理人已复现） | 代码/合规 | 低（日志剥离 `input` + sink 显式 `diagnose=False`） | F-11 |
| **B7** | **被打满后进程无法自然退出**（`asyncio.run` 收尾 join 阻塞 worker，`rc=124`）⇒ `docker stop` 挂到 SIGKILL、滚动更新/重启全部超时；**重启后攻击者继续轮询立刻再打挂** | 架构/代码 | 中（`socket.setdefaulttimeout` + daemon 专用池） | #1b |

> **最低限度放行路径**：若业务必须尽快上线，**至少完成 B1 的"独立计算池"与 B6 的"校验日志脱敏"两项**（前者把爆炸半径从"全站 + 探针假死"压回"overview 变慢"，后者消除凭据落盘），并在发布说明中写明已知风险。**⚠️ 这两项不能消除 B7（进程退出挂起）**，需另加 `socket.setdefaulttimeout` 或 daemon 专用池。
>
> **调查员建议的修复优先级排序（按"性价比 × 阻断力"，建议照此顺序落地）**：
> **P0-0** 三个 overview 端点补鉴权（**一行**，同时堵住匿名 DoS 入口）→ **P0-1** 专用 `ThreadPoolExecutor`（隔离爆炸半径）→ **P0-2** manifest footer 并行（**34×**）→ **P0-3** Nginx 超时 180s → 700s（**一行**）→ **P0-4** `read_parquet_columns` 改 `scan_parquet`（**6×**）→ **P0-5** akshare watchdog + `socket.setdefaulttimeout`（**同时治 B7**）。
> 另：**兼容端点 `/api/v1/market/overview` 前端零调用（已 DEPRECATED）⇒ 建议直接下线**，零业务代价即可消灭"5.4 线程/客户端"的攻击入口。
>
> **需你裁决（不构成阻塞，但必须显式决定）**：
> - `RBAC_ENFORCE=False`（发现 #7）—— 代码注释显示是你 2026-09-23 的刻意裁决。若仅本机/内网使用可显式接受；若面向公网需置 `True` 或至少让 prod 校验告警。
> - 三个 market overview 端点是否保持匿名（**F-12**）—— README 只声明"市场概览**页面**可公开访问"，未声明 API 可匿名取 AI 推荐榜。且它是 B1 的**匿名触发入口**，故建议至少加 `require_role("viewer")`。
>
> **⚠️ 暴露面是共同前置条件**：F-12（未授权面）与 F-13（日志洪泛）**在只绑 `127.0.0.1` 时不可达**（当前 `.env` 已设 `API_HOST=127.0.0.1`，compose 也只发布 `127.0.0.1:8080`）。**一旦改绑 `0.0.0.0` 或经反向代理对外暴露，这两项立即由 P1 升为 P0**（匿名 DoS + 匿名磁盘填充）。上线前请明确：服务是否只在本机/内网可达。

**P1 建议同批处置**：F-12 鉴权（#F-12，同时收窄 B1 的触发面）、预算 vs 真实成本对齐（#6）、`/docs` 收口 + `DEBUG=false` + 两个文件 sink 显式 `diagnose=False`（#8）、`extractall(filter='data')`（#9，一行）、sqlite 连接 `close()`（#10）。

---

## 4. 🔄 回滚预案

| 维度 | 现状 | 结论 |
|------|------|------|
| 数据备份 | `backup/aqp-2026-09-29-181518.tar.gz`（775K）+ `.sha256`；`backend/backups/` 另有 248 个 `.db` 快照 | ✅ 可用且新鲜 |
| 恢复脚本 | `backend/scripts/restore.py` —— 恢复前把现有 `sqlite`/`models` 改名为 `<member>.pre-restore-<时间戳>`，解压报错则原样移回；归档中绝对路径与 `..` 穿越会被拒绝 | ✅ 具备失败即回滚 |
| 恢复演练 | `backend/scripts/backup_drill.py` 在临时目录内完成（创建→删除→恢复→验证 SQLite 完整性），**绝不修改 `data/`**；QA 实测通过（验 27 张表） | ✅ 可用 |
| 一致性 | SQLite 部分用 `VACUUM INTO` 生成（含 WAL 中已提交未回写的事务）；归档**不含** `-wal`/`-shm` sidecar | ✅ 避免"主库 + 过期 WAL"污染 |
| 运行时自愈 | **头号 P0 使进程内自愈失效**：池泄漏后 30s 内不收敛，`/health/ready` 持续失败 | ❌ **只能重启进程** |
| **版本回滚** | **仓库 0 提交、无 tag、无 VERSION/CHANGELOG** | ❌ **缺失** —— 代码层无法定位/回退到已知 good 版本（对应 B5） |
| 配置回滚 | `.env` 有 `.env.example` 模板；compose 用 `${VAR:?}` 强制生产必填 | ⚠️ 但 `.env` 缺 17 个 `.env.example` 键，全走代码默认值 |

> **结论**：**数据层回滚能力完备，代码层回滚能力为零**；且头号 P0 会让"进程内自愈"这一层也失效。B5 不解决，任何线上问题都只能"改代码再发一版"，无法快速回退。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 预期收益 |
|---|------|--------|--------|---------|
| **A1** | **重计算改用独立 `ThreadPoolExecutor(max_workers=COMPUTE_CONCURRENCY)`**，不用默认池（`loop.set_default_executor` 或注入专用池） | 后端负责人 | **P0（最高）** | 把"全站挂死 + 探针假死"压回"overview 变慢"；爆炸半径从全站降为单端点。**⚠️ 注意**：若专用池仍用非 daemon 线程，**不能**修好 B7（进程退出挂起），需与 A13 配套 |
| **A11** | **校验错误脱敏（4 处）**：① `_jsonable_validation_errors`（`errors.py:43-64`）剥离 `input` 字段（只留 `type`/`loc`/`msg`/`ctx`）；② `app.log`/`app.json.log` 两个 sink 显式 `diagnose=False`（`logging.py:60-79`）；③ **响应体不再回显 `errors`**（`errors.py:230`）；④ 登录/注册限速**上移到中间件或路由级 `dependencies=[...]`**（覆盖校验失败路径）。可另加 sink `compression` | 后端/安全负责人 | **P0** | 消除"明文口令写入 30 天日志 + 回显响应体"（已验证可复现）+ 日志洪泛面 |
| **A2** | `frontend/nginx.conf`：`proxy_read_timeout` 180s → **≥700s**；为 `/api/v1/notify/stream` 加独立 `location`（`proxy_buffering off; proxy_read_timeout 3600s`）；补 `location = /health` / `= /metrics` | 部署/运维 | **P0** | 消除长任务必然 504、SSE 缓冲延迟、监控假绿 |
| **A3** | 后端加 `socket.setdefaulttimeout(10)`（或 `_safe_call` 传 per-call timeout），覆盖 akshare 7 个调用点 | 后端负责人 | **P0** | 消除 akshare 永久占用线程 |
| **A4** | `parquet_store.py` manifest footer 扫描改 `ThreadPoolExecutor(w=8)` 并行 | 后端负责人 | **P0** | `/datacenter/datasets` 从必然 504 → 8.3s（实测 34×） |
| **A5** | 升级 `lightgbm ≥4.6.0`、`pyarrow ≥23.0.1`，回归训练与 parquet 读写 | 后端负责人 | **P0** | 消除 RCE / UAF CVE |
| **A6** | 由工作区重建根提交 → **立即 push 到远端** → 打 release tag；补 `VERSION`/`CHANGELOG` | 发布负责人 | **P0** | 恢复版本基线与回滚能力 |
| **A12** | ① **直接下线** DEPRECATED 的兼容端点 `/api/v1/market/overview`（前端**零调用**，`api/market.ts` 只有 `overviewRt`/`overviewDaily`）；② `/overview/rt`、`/overview/daily` 补 `require_role("viewer")` | 后端负责人 | **P0-0（一行）** | 收窄核心 IP 匿名暴露，同时**堵住 B1 的匿名触发入口**（改动最小、阻断力最强，建议**第一步**做） |
| **A13** | 治 **B7（进程退出挂起）**：`socket.setdefaulttimeout(10)`（让阻塞 worker 最终可返回）+ 专用池改用 **daemon 线程**；同时把 `/overview/rt` 预算 15s → **≥30s**（消除"余量仅 1s"的薄余量风险） | 后端负责人 | **P0** | 恢复"重启即恢复"能力（`docker stop` 不再挂到 SIGKILL）；消除 `/overview/rt` 正常路径的薄余量脆弱性（**注**：原"坏天气自爆"叙事已由主理人实测证伪，本条不再是"消灭必然泄漏"） |
| **A7** | 把 `wait_for` 预算提到 ≥ 真实成本（`market.py:943` 6s→≥120s 或去掉同步预算、`etf.py:207` 4.5s→≥8s、`market.py:782` 15s→≥30s）；为每个块级预算补"实测真实成本"注释与回归断言 | 后端负责人 | P1 | 根治"预算 < 成本 = 泄漏发生器"（≥3 处） |
| **A8** | `.env` 显式 `DEBUG=false`+`LOG_LEVEL=INFO`；`diagnose` 按 ENV 收口；生产 `docs_url=None` 或加鉴权；`backup.py` 的 `extractall` 加 `filter='data'` | 安全负责人 | P1 | 消除敏感信息泄漏面 + 归档穿越（后两者各一行） |
| **A9** | `read_parquet_columns` 改 `pl.scan_parquet(files).select(...)`（保留逐文件 try/except）；图表加 `sampling:'lttb'`；统一 18 处 sqlite 连接 `close()`；`backup.py` 加 `--help` 早退 | 后端 + 前端 | P1 | 19.8s→3.3s、5.2s→0.77s（实测）；消除句柄泄漏与误触发 |
| **A10** | **裁决 `RBAC_ENFORCE`**：确认全面放开是否仍为目标姿态；若维持，在 `validate_runtime_safety` 加 prod 告警 | 产品/安全负责人 | **P0（裁决）** | 明确权限边界，避免"任一账号=管理员"的隐性风险 |

**验收建议**：A1 落地后，由 QA 复跑其"22 路并发 → 立即打 `/health/ready`"实验（脚本已备，约 1 分钟），要求探针**全程 200 且耗时回落到基线量级**，并确认压力结束后**收敛**（不再振荡）。

---

## 5. 📝 报告修订记录（成员自我更正 + 交叉质证）

本轮多位成员在收尾阶段**主动更正了自己的结论**（R1–R13），主理人逐条复核后采信；**另有 1 处是主理人实测推翻了成员结论**（R14）。如实记录如下，供后续读者判断证据强度：

| # | 谁更正 | 原结论 | 更正后 | 依据 | 主理人处置 |
|---|--------|--------|--------|------|-----------|
| R1 | 调查员 | 怀疑 `main.py:269` 的 `BaseHTTPMiddleware` 会缓冲 SSE | **证伪自己**：ASGI 级实验显示不缓冲、逐条放行；SSE 风险**只在 Nginx `proxy_buffering on`** | 记录每次 `http.response.body` 的 send 时刻：裸 FastAPI `['11ms','214ms','422ms']` vs 套中间件 `['2ms','207ms','415ms']` | 采信。**报告不主张"BaseHTTPMiddleware 破坏 SSE"** |
| R2 | 安全官 | 线程池 DoS 需"低权账号" | **升级**为"**未认证**即可"，并要求 QA 复测匿名路径 | `market.py:767,831,896` 三端点无鉴权（主理人已核实） | 采信，B1 描述已更新 |
| R3 | 安全官 | F-11 影响包含"可用于口令爆破" | **自我更正**：**不构成**口令爆破（handler 未执行，`verify_password` 从不运行，不泄漏口令正确性与用户名存在性）；真实影响是**无节流的日志洪泛**与 F-11 可无限复现 | `_check_rate_limit` 在 `auth.py:105` handler 内，body 校验在其之前 | 采信，已单列为 F-13 并加粗警示"勿误读" |
| R4 | 产品评审员 | 同 R3，措辞为"无限次探测" | 更正为"不受限地重复触发 F-11 的日志写入" | 同上 | 采信 |
| R5 | QA | 该 DoS"需登录" | 更正为"**匿名**，攻击门槛是任何能连端口的人" | 无 Authorization 头实测 22/22 成功 + `/stock/search` 对照回 40100 | 采信，B1 已更新 |
| R6 | QA | 触发条件是"高并发" | 更正为"**缓存未命中**"（5s 防抖 + `rebuild_lock_ttl=180` 会把并发风暴折叠成 1 次重建；仅缓存为空时才同时走 6s 预算） | 代码路径 + 实验前缓存预热过 | 采信，B1 已更新 |
| R7 | 产品评审员 | 三个 overview 端点"疑似刻意公开" | 更正为**遗漏**（同文件 `:29` 已导入 `require_role`、`:648`/`:679` 已使用，三层均无兜底） | 代码核查 | 采信，F-12 已定性为遗漏 |
| R8 | QA | 业务码未记录 | 更正为 **22/22 业务码全为 `0`** ⇒ 基于状态码的告警**系统性失效** | 实验记录 | 采信，B1 已加强 |
| R9 | 产品评审员 | — | 补充：两个 sink 有 rotation（10/20 MB）但**均未设 `compression`** ⇒ 30 天累积总量无上界，"rotation 会保护磁盘"是错觉 | `logging.py:60-79` | 采信，并入 F-13 |
| R10 | 安全官 | 提出"日志洪泛可被放大"但**明确标注为待验证假设**，且其 Bash 通道已失效、无法自验 | **主理人代为验证并确认**：body 顶层类型不匹配时 `input` 保留**整个 body**（1 MB 输入 ⇒ `errors` 序列化 **1,000,215 字符**） | 主理人脚本 `verify_f13_hypothesis.py`：`type=model_type, loc=(), input_len=1000000, 完整保留=True` | 采信并升级 F-13 为"已确认" |
| R11 | 安全官 | `/docs`、`/redoc` 暴露判为 🔴**P0** | **自我降级为 🟡P2**（做了**可达性核查**后发现自己"只看 `docs_url` 就断言生产必须关闭"）：compose 生产 `aqp-api` 只 `expose` **无 `ports`**，nginx **只反代 `/api/`** ⇒ `/docs` 落 `location /` → 回 SPA HTML ⇒ **不可达**；dev 模式 vite 也只代理 `/api`、`/health` | `docker-compose.yml:73-74,98-99`、`nginx.conf:24`、`vite.config.ts:17-26` | 采信。**同时记入一条重要副作用**：nginx 的 `/api/` 白名单是一个**隐式的、意外的安全边界** —— 它既挡住了 `/docs`、`/redoc`、`/openapi.json`、`/metrics`、`/health*`，也**同时打断了监控数据管道**（见 #11）⇒ 报告中"暴露 /docs"与"抓不到 metrics"**并不矛盾** |
| R12 | 调查员 | 头号 P0 表述为"**必然**全站挂死" | **修正为"条件性全站挂死"**：有界成本会**收敛**（S1 峰值 3 后回落），**无界成本**（或外部源半开连接）才**不收敛**（S2 单调 1→4→7→11→14→15） | 三组对照实验 + 稳态公式 `稳态泄漏线程数 ≈ 真实成本/(预算+请求间隔)`（预测 2.67 vs 实测 2~3 ✅） | 采信，B1 与 #1 描述已改为"条件性" |
| R13 | 调查员 | 建议 QA 压**兼容端点** `?refresh=1` 复现 | **自我修正**：那不是用户路径；**更有说服力的是 ⟳ 按钮路径** `/overview/rt?refresh=1`（前端**从不**调兼容端点） | 前端调用面核查：`api/market.ts:55-62`、`pages/MarketOverview/index.tsx:75` | 采信，已单列为 #1c，并把兼容端点建议改为**直接下线** |
| **R14** | **主理人（证伪调查员的 R13 推论）** | 调查员据"**降级成本 24s** > 15s 预算"断言 ⟳ 路径"每次点击必泄漏"，并命名为"**坏天气自爆**"正反馈 | **实测证伪并降级**：`24s` 是 `market.py:718` 的「**修复前**：~21 次外呼 ⇒ ~24s」旧值，被误引为降级成本。主理人**直接计时 `_build_rt`** ⇒ **12.41s（冷启动）/ 7.95s（熔断稳态）**，**均 < 15s ⇒ 降级路径不泄漏**（与代码注释自述的 12.6 / 8.5s 吻合）。真实残余风险只是"正常路径 ~14s vs 15s、**余量 ~1s**"的**薄余量脆弱性** | 主理人脚本 `D:\tmp_aqp_perf\lead_verify_rt_cost.py`（真实 `_build_rt`；本机东财不可达 ⇒ 走熔断降级路径，日志可见 `DataSourceUnavailable('已熔断，冷却中')`） | **纠正**：报告全篇已改写（TL;DR、调查员核心结论、#1c、#6、B1、A13）；**保留修复建议**（预算 15s→30s 仍是低成本加固），但**不再声称"必然泄漏"**，#1c 由 🟠P1 降为 🟡P2 |

> **主理人独立复现**：B1（线程池泄漏，协议层 `lead_verify_threadleak.py` → 22/22 超时、短任务 STARVED）、**B7（进程退出挂起，`verify_exit_hang.py` → `[1] main() 已正常返回` 后 20s 内进程不退出、`rc=124`）**、F-11（`loguru._defaults.LOGURU_DIAGNOSE = True`；`LoginRequest` 超长密码 ⇒ `errors()` 含完整明文口令 `in` 判定 True）、F-12（三端点无鉴权依赖）、F-13（`_check_rate_limit` 位于 handler 内）。
> **主理人实测证伪（R14）**：`_build_rt` 真实墙钟 **12.41s（冷）/ 7.95s（熔断稳态）**，**均 < 15s 预算** ⇒ 调查员"降级 24s ⇒ ⟳ 必泄漏 / 坏天气自爆"的推论**不成立**，已从报告撤回（脚本 `D:\tmp_aqp_perf\lead_verify_rt_cost.py`）。
> **主理人裁决**：安全官对 `RBAC_ENFORCE` 的 🔴P0 评级下调为 🟠P1-需产品裁决（依据 `config.py:203-214` 记载的用户刻意裁决）。

---

## ⚠️ 待完善 / 已知局限

- **头号 P0 已在真实应用上端到端复现**（QA 于 :8012，22/22），并被主理人独立复现（协议层 STARVED）；但**"自维持泄漏"的机制解释（≤15s 短 TTL 与 6s 预算的组合）为推断**，实测到的只是"30s 内不收敛"这一现象。**风险定性为"条件性"**（有界成本收敛 / 无界成本不收敛）基于调查员三组对照实验与稳态公式，属**受控实验推断**，未在真实生产负载下验证。
- **~~"坏天气自爆"~~ 已撤回**：调查员原据"前端 ⟳ 按钮发 `refresh=1`"+"后端降级成本 24s"推出"东财一挂就每次点击泄漏"的正反馈。主理人实测证伪（见 R14）：**降级路径实测 12.41/7.95s，低于 15s 预算，不泄漏**；24s 系「修复前」旧值。**保留的只是** `/overview/rt` 正常路径"余量 ~1s"的**静态薄余量脆弱性**（未做正常路径实测，因本机东财不可达 ⇒ 无法复现 ~14s 的正常路径）。
- **B7（进程退出挂起）为主理人最小化脚本复现**（`D:\tmp_aqp_perf\verify_exit_hang.py`），**非生产容器实测**：未在 `docker stop` 真实场景下测量 SIGKILL 等待时长；且退出挂起与 Python 版本的 `shutdown_default_executor` 行为相关，**未跨版本核对**。
- **Nginx 缺陷为静态推导，未实测复现**：本次审计时未启动 Nginx，缺陷 #2 由"前端预算 / 服务端预算 / Nginx 常量"三方比对得出。
- **性能数字受外部源影响**：探测时东财源不可达（`DataSourceUnavailable / RemoteProtocolError`，熔断生效），部分端点耗时是"降级快路径"数字，**低于真实生产**；生产有源环境下 overview 冷算达 23.5~113.5s 级，故 6s 降级与随之而来的泄漏会更频繁。
- **manifest 冷重建总时长为外推**：按串行实测（daily_bar 107.8s / hfq 29.4s / qfq 146.6s）推算，**未跑整轮 force 重建**。
- **未覆盖范围**：写操作端点（下单/同步/训练）、backtest 与 ops/dag 的真实长任务端到端、SSE 实时推送实测（仅静态审查）、Redis 开启态下的缓存路径、跨浏览器/a11y/并发压测、`npm audit`（本机镜像源 `registry.npmmirror.com` 未实现 audit 端点，报 `NOT_IMPLEMENTED`，需在可访问官方 registry 的环境补跑）。
- **代码审查的深读边界**：`datacenter.py`(1754行)、`etf.py`(1212行)、`backtest/engine.py`(632行)、`ml/*` 数值内核（除零/空序列）、`domain/`、`data/` 抓取源、前端 `pages/*` 与 `components/*` 仅做模式扫描，未逐文件读。
- **两次审计的差异说明**：2026-09-29 曾做过一轮同类全检（结论 🟡 条件 Go / 0 P0），其 2 项 P1 中"裸机管理端静默暴露"**已在本轮确认修复**（`.env` 现含 `ALLOW_ADMIN_TOKEN_LOGIN=false` + `API_HOST=127.0.0.1`），"依赖漏洞未扫描"**已由本轮完成**（pip-audit 实跑）。本轮结论更严（🔴）的原因是**审查更深**：新增了线程池泄漏、Nginx 超时链、akshare 无超时、manifest 串行扫描、归档穿越 PoC、版本基线缺失等此前未覆盖的面。
- **测试副作用**：QA 为取得鉴权覆盖在本地 dev 库创建了临时账号 `qa_smoke_admin`（admin 角色）；因 `backup.py --help` 触发了一次真实备份。**全程未修改任何生产源码**；实验脚本在 `D:\tmp_aqp_perf\` 与临时目录，服务已停。

---

## 📚 成员产出索引

- **产品评审员**（`gstack-product-reviewer`）原始产出：全仓库代码审查（AST 工具化全量扫描 115 端点鉴权/响应契约/async 阻塞/SQL 拼接/清理配对 + 逐行深读 core/db/cache/main/auth/export）→ 10 项发现（1×P1、7×P2、2×P3），结论 🟡 无 P0。
- **安全官**（`gstack-security-officer`）原始产出：OWASP Top 10 逐条检查表 + STRIDE 威胁表 + pip-audit 实跑（147 包）+ 备份归档 symlink PoC + symbol 路径穿越验证 + SSE ticket 防重放 PoC + 容器/密钥/前端产物核查 → 10 项发现（F-1~F-10）+ 4 项 P0 封堵清单，结论 🔴；**收尾追加**：F-11（明文口令落盘）+ F-12（未授权面）+ F-13（限速绕过 / 监控管道断裂），并**自我降级 F-3（`/docs` P0 → 🟡P2）**、指出 nginx `/api/` 白名单是"既挡攻击也挡监控"的**隐式安全边界**。
- **QA 与发布**（`gstack-qa-lead`）原始产出：后端离线回归实测（1903 passed / 552.75s）+ 前端构建与 bundle 门禁 + 9 端点 API 冒烟与 P50/max 延迟 + 超时配置表 + 发布就绪清单 11 项 + 回滚预案核查；**修订轮**：22 路并发池耗尽实验（探针挂死 12008ms / 30s 不收敛 / 零 5xx）→ 结论由 🟡 下调为 🔴。证据：`qa-lead-poolexhaust.log`、`qa-lead-recovery.log`、`qa-lead-backend-8012.log`（含 22 条 `market.py:949`）。
- **调查员**（`gstack-investigator`）原始产出：超时四类根因定位（AST 扫描确认 async 阻塞 0 处）+ **线程泄漏协议层实验**（黑洞 TCP server，22/22 worker 仍占用）+ 数据湖规模实测 + 串行 vs 并行 footer 扫描基准（283.8s vs 8.3s）+ `read_parquet_columns` vs `scan_parquet` 基准 + 性能热点 Top5 + ROI 行动表 + 超时预算建议表 + 对"已落地 9 项优化"的存活核实 + **对 `BaseHTTPMiddleware` 缓冲 SSE 假设的自我证伪**；**收尾追加**：**退出挂起验证**（`verify_exit_hang.py` → `rc=124`）+ **稳态泄漏三组对照实验与公式** `真实成本/(预算+间隔)` + **前端调用面核查**（跑步机入口 A/B 分类、"坏天气自爆"正反馈）+ **修复优先级排序**。脚本：`D:\tmp_aqp_perf\{bench,bench2,bench3,bench4_threadleak,bench6_sse_asgi,scan_eventloop,verify_exit_hang,frontend_overview}.py|out`。
- **主理人核实**：`.git` 仓库健康度实测（0 提交 / `refs/heads` 缺失 / 9 个松散对象）、**线程泄漏独立复现**（`D:\tmp_aqp_perf\lead_verify_threadleak.py` → 22/22 超时、短任务 STARVED、threads 24）、**进程退出挂起复现**（`verify_exit_hang.py` → `rc=124`）、**`_build_rt` 真实成本实测**（`lead_verify_rt_cost.py` → 12.41s/7.95s，证伪"坏天气自爆"）、`backup/` 归档存在性、`data/parquet` 36,500 文件计数、本地服务 502 探测、`www.workbuddy.ai` 网络连通性。

---

> 本报告由软件工坊 AI 协作生成（4 位专家独立调查 + 主理人汇编与交叉质证 + 主理人独立复现）。关键决策请由工程负责人复核。
> 本次对成员结论做过六处主理人裁决：① 安全官的 `RBAC_ENFORCE` 🔴P0 → 调整为 🟠P1-需产品裁决（依据 `config.py:203-214` 记载的用户刻意裁决）；② 将"裸机管理端暴露"与"依赖漏洞"两项 09-29 遗留 P1 标记为**已在本轮修复/完成**；③ 采信调查员对 `BaseHTTPMiddleware` 缓冲 SSE 的**自我证伪**，报告中不主张该结论；④ 采信安全官对 `/docs`（F-3）的**自我降级**（P0 → 🟡P2），并采纳其"nginx `/api/` 白名单是隐式安全边界"的判断；⑤ 采信调查员对头号 P0 的**风险定性修正**（"必然"→"条件性全站挂死"）；⑥ **实测证伪**调查员"⟳ 路径降级必泄漏 / 坏天气自爆"的推论（`_build_rt` 实测 12.41/7.95s < 15s），#1c 降为 🟡P2 —— 这是本轮**唯一由主理人推翻成员结论**的一处（其余 13 处均为成员自我更正）。
