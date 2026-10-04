# AQP 上线前问题修复 + 数据源可用性验证报告

**日期**：2026-10-04
**场景**：上线前全检问题的**按优先级修复** + **数据源可用性实测**
**参与成员**：产品官 · 安全卫士 · 质量门神 · 排障手（全检）｜be-pool · be-budget · be-services · fe-fix · test-red · ds-verify · verify-backend（修复与验证）
**工作区**：`D:/Python_Project/Alpha Quant Platform`（改动 **36 个文件 / +1352 −595**，新增 5 个文件；**全部未提交**）

---

## 📌 TL;DR

- **修复结论**：🟢 **全部 3 条发布阻塞项 + P0 + 主要 P1/P2 已修复并独立验证通过**。后端全量门禁由 **1 failed / 2211 passed** → **2221 passed / 0 failed**；前端门禁由**非确定** → **16 文件 / 143 用例稳定全绿**。
- **独立验证**：8/8 项通过，含**主动证伪**（把 PSI 计算换成恒返回 0，红例用例确实 FAILED ⇒ 用例未被改废）。
- **数据源结论**：🟢 美/日/韩大盘（道琼斯/纳斯达克/标普500/日经225/韩国KOSPI）**全部正常返回**；A 股 5 大指数**全部正常**。唯一异常是 `/market/overview/rt` 的「异动」子块（东财 `clist/get` 源站断开），**已通过"保留已完成子块 + 异常子块守卫"显著降低其影响**。
- **改动性质**：本轮**未提交 git**（多人并行改同一工作区，避免索引冲突），工作区即为交付物。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **修复完成度** | 🟢 3 阻塞 + 1 P0 + 5 P1 + 8 P2 已修；**4 项残留**（见 §5） |
| **后端门禁** | 🟢 `2221 passed / 8 skipped / 0 failed`（修复前 1 failed / 2211 passed；历史基线 1971 passed） |
| **前端门禁** | 🟢 `16 files / 143 tests` 稳定通过（修复前默认并行**每次静默丢 1 个文件**） |
| **独立验证** | 🟢 8/8 通过，含 3 项反证/证伪 |
| **数据源** | 🟢 美/日/韩大盘全绿；⚠️ `/overview/rt` 异动子块上游不可用 |
| **建议** | 🟢 **可 Go**（3 条阻塞项已清）；残留 4 项均为非阻塞，建议首个迭代处理 |

---

## 1. 各成员核心结论

### 🔧 be-pool（P0 线程池隔离 + N+1）
- **核心判断**：5 个文件共 **32 处** `asyncio.to_thread` → `run_in_compute_pool`，重活不再占 22 槽默认池（与 `/health/ready` 探针争抢的根因消除）。desk N+1 用 `IN` 批取 + Python 分组（`PaperOrder` 无 relationship，未动模型）。
- **关键建议**：迁移时踩到 `run_in_compute_pool` **不收关键字实参**的陷阱（已修，并促使 core 层对齐 `to_thread` 的参数契约）。

### ⏱️ be-budget（超时预算 + 惊群）
- **核心判断**：`CAPPED_PATHS` 与 `_PATH_MAX_BUDGETS` **各 +22 条**（白烧方向首次成体系）；`RT_BUILD_TIMEOUT_SECONDS` 15→**24s**（余量 7%→42%）；3 个匿名 overview 端点全部开启 `single_flight`；`quotes_hub` 冷缓存惊群用 per-key 任务表 + `shield` 消除。
- **关键建议**：single-flight 等待预算默认值下调会波及非重活路径，已逐条核实"全仓仅 5 个调用点且全部显式覆盖"。

### 🛠️ be-services（P2 服务加固）
- **核心判断**：6 项全做 —— `refresh_freq` 加 `ge=3/le=600`；`instrument` 全表查询抽成共享 TTL 缓存（`services/instrument_cache.py`，600s）；3 处静默降级补日志且 `model_version_count` 失败由 **0 改 null**；新增**原始 ASGI 请求体限额**（8MB → 413/`41300`）；SSE **每用户 5 / 全局 200** 连接上限；nginx 加 **CSP + HSTS**。
- **关键建议**：新防线必须配回归守卫，已补 **9 个用例**。

### 🎨 fe-fix（前端门禁 + 展示层）
- **核心判断**：vitest 门禁非确定的根因是**宿主 fs shim 在多 worker 并发写同一临时文件时拒绝其一** ⇒ 改 `pool:'forks'` + `fileParallelism:false` 并加**真护栏** `scripts/check-test-count.mjs`（现算磁盘文件集合与 vitest 报告比对，缺一个即 exit 1）。ETF 假 K 线改为**剔除含 null 的 bar**。
- **关键建议**：额外发现 Research 页副标题**硬编码**「model_registry 已登记」——即使后端读取失败也照样断言"已登记"，已改为由后端 `model_version_note` 驱动。

### 🧪 test-red（红例定性）
- **核心判断**：**(C) 用例口径过期**（叠加局部值漂移），**不是**数据真漂移。决定性证据：**同文件第 297-298 行的姊妹用例断言 `psi["max"] > 0.25` 且通过**，两者对同一面板同一量自相矛盾；且 0.5149 来自 regime 因子 `rank_close_60`（**非注入因子**），并已排除 MAD=0 陷阱（剔除 |z|>100 与 winsorize 后 PSI 恒为 0.5149）。
- **关键建议**：改口径而非调阈值 —— 基线健康性改挂 body 统计量，注入灵敏度改挂**被注入因子自身的 per-factor PSI**。

### 🌏 ds-verify（数据源实测）
- **核心判断**：美/日/韩大盘 + A 股指数**全部可用**；`/overview/rt` 的「异动」子块因东财 `push2.eastmoney.com/api/qt/clist/get` **源站断开**而恒降级，且原实现会把**已算好的指数一并丢弃**。
- **关键建议**：超时降级应保留已完成子块；`anomalies` 不应无条件再打一次东财。

### ✅ verify-backend（独立验证）
- **核心判断**：8/8 通过；全量门禁 `0 failed`；独立解析超时预算 **30/30 条**符合 ×0.8；desk fills SQL **200→1**；413/41300 两形态均生效；SSE 计数**无泄漏**。
- **关键建议**：新增 body/SSE 两条防线**零测试覆盖**（已补 9 个用例）。

---

## 2. 逐问题处理情况（按优先级）

### 2.1 发布阻塞项（3 条）—— 全部 ✅ 已修

| # | 问题 | 处理 | 验证证据 |
|---|------|------|---------|
| B1 | 前端 vitest 门禁**非确定**（默认并行每次静默丢 1 个测试文件，131/137 vs 143） | `vitest.config.ts` 加 `pool:'forks'` + `fileParallelism:false`（试过 `singleFork` 会因 store/DOM 跨文件残留弄红 53 个用例，故弃用）；新增 `scripts/check-test-count.mjs` 护栏，`npm test` 已改走它 | `npm test` → **16 files / 143 tests** 稳定通过；**注入一个磁盘不存在的期望文件后护栏 exit 1 并点名** ⇒ 护栏是真护栏 |
| B2 | 后端门禁红 1 例（`test_monitor_trigger_caliber.py:348`，`0.5149 > 0.25`） | 定性为**用例口径过期**（姊妹用例自相矛盾 + 0.5149 来自非注入因子 + MAD=0 陷阱已排除）⇒ 改口径（基线挂 body 统计量、灵敏度挂注入因子自身 PSI），**未调阈值、未删用例** | 全量 `pytest -m "not network"` → **0 failed**；**证伪**：把 `compute_psi` 换成恒返回 0 ⇒ 该用例 **FAILED**（`assert 0.0 > 0.25`）⇒ 仍抓得住回归 |
| B3 | ETF 详情 K 线 `null → 0` 画"价格 0"假蜡烛（且 `close>=open` 恒真 ⇒ 恒判涨） | `EtfDetail/index.tsx:814` 改为**剔除**任一 OHLCV 为 null 的 bar（按缺口处理），不再 `??0` | `tsc --noEmit` exit 0；`vitest` 16/143 全绿 |

### 2.2 P0（1 条）—— ✅ 已修

| # | 问题 | 处理 | 验证证据 |
|---|------|------|---------|
| P0-1 | 重活线程池隔离**只做一半**：`desk/screener/monitor/studio/report` 仍用 `asyncio.to_thread` 占 **22 槽默认池**（与 `/health/ready` 共用），实测 22 路并发 ⇒ 探针挂死 12008ms | 5 文件 **32 处**全部迁到 `run_in_compute_pool`；按惯例给重活 POST 加 `compute_slot` 闸门（desk POST /fills/run、monitor POST /run、studio 5 个、report POST /daily/generate）；清理无用 `import asyncio` | 5 文件 `asyncio.to_thread` **0 命中**、无用 import **0 命中**；**7 处位置传参逐参数核对无错序**；kwargs 路径微探针 4 种形态返回值全对 |

### 2.3 P1（5 条）—— ✅ 已修（1 条部分缓解）

| # | 问题 | 处理 | 验证证据 |
|---|------|------|---------|
| P1-1 | **白烧方向**无防线：`_PATH_MAX_BUDGETS` 只登 8 条 ⇒ ≥12 条重活端点落 **240s**，用户早断服务端仍烧 | `CAPPED_PATHS` 与 `_PATH_MAX_BUDGETS` **各 +22 条**（均 = 前端超时 × 0.8），逐条回前端源码核实调用点；修 `config.py:107` 错误注释（"其余路径最长 180s"是错的） | `npm run timeout:check` **PASS**；验证方**独立解析**：两侧键集合一致、**30/30** 条 = ×0.8、与 `LONG_PATHS` 无交集 |
| P1-2 | `RT_BUILD_TIMEOUT_SECONDS=15.0` vs 真实 ~14s（**仅 7% 余量**） | 上调至 **24.0**（余量 ≈42%，且 < 前端 120s） | 未动其他常量；闸门 PASS |
| P1-3 | 匿名重计算端点**缺 single-flight**（冷缓存惊群/缓存穿透 DoS） | `_overview_block_response` 加 `single_flight` 透传；`/overview/rt`、`/overview/global`、`/overview/daily` 均开启（逐个核实 `_build` 无请求级私有状态）；并发 6 路实测**只构建 1 次** | 验证通过；⚠️ **部分缓解**：计算池**队列本身仍无界**（见 §5） |
| P1-4 | `quotes_hub` 冷缓存无 single-flight ⇒ N 并发 miss = N 次外呼（且该路径无预算） | per-cache_key `(loop, task)` 表 + `asyncio.shield`（任务独立于请求，断连不丢回写）；等待方**有界等待 5s**，超时/leader 失败则自行抓取兜底 | 并发 8 路实测**抓取 1 次**、耗时 0.34s；`_disclose` 截断口径按本次调用保持 |
| P1-5 | `desk.py:230-233` N+1：每母单单独 `SELECT fills` ⇒ 单请求 **202 条 SQL** | 先取 orders，再一次 `WHERE order_id IN (...)` + Python 分组（`PaperOrder` 无 relationship，未动模型） | **实测 fills 相关 SQL 200 → 1 条**（总 202→3，多出的是 `COUNT(*)`）；返回 JSON 逐字段/母单顺序/每单 fills 顺序**完全一致** |

### 2.4 数据源实测新发现（2 条）—— ✅ 已修

| # | 问题 | 处理 | 验证证据 |
|---|------|------|---------|
| D-1 | `/overview/rt` 超时降级把**整块**换成 stub ⇒ 冷启动撞预算时**已算好的 `indices`/`money_flow` 被连坐丢弃**（实测第 1 次三个子块全 unavailable） | `_build_rt` 用 `as_completed` 按完成顺序写入 `partial`；新增 `_rt_degraded_payload` **只标缺失子块**、已完成的原样保留、顶层 `degraded` + 如实 reason，**无占位 0/假数据** | 实测（heat sleep 5s + 预算 1s）：`indices`/`money_flow` **保留为 ok**，仅 `anomalies=unavailable`；日志佐证"保留已完成子块 ['indices','money_flow']" |
| D-2 | `_build_anomalies` **无条件**再打一次东财（注释实测单请求 6 次调用、第二次 ~10s 级） | 加守卫：**仅当 `heat.source == "em"` 才外呼**，heat 本地回退/不可用时直接返回 degraded 空名单 | heat=local ⇒ 东财外呼 **0 次**（原为无条件再打）；heat=em ⇒ 仍恰好 1 次；并发时序不变 |

### 2.5 P2（8 条）—— ✅ 已修

| # | 问题 | 处理 |
|---|------|------|
| P2-1 | `refresh_freq` 无范围校验（可写 0/1/负数 ⇒ 1s 轮询打后端） | `Field(None, ge=3, le=600)`；实测 0/1/2/-5/601 拒、3/10/600/None 收，与前端下拉 `[3,5,10]` 相容 |
| P2-2 | `instrument` 全表入内存无缓存（3 处） | 新建 `services/instrument_cache.py`：唯一共享实现、超集快照、**600s TTL**、返回不可变 tuple、失败不缓存并上抛；`watchlist.py` 与 `datacenter.py` 改用（`market.py` 那处由 be-budget 一并处理） |
| P2-3 | 3 处静默降级（`datacenter.py:848/908`、`research.py:142`） | 前两处补 `logger.warning`（含异常类型+上下文）；`research.py` 失败由 **0 改 `None`** + 新增 `model_version_note`；前端类型同步 `number \| null`，消费点 `?? '—'`；并修掉 Research 卡副标题的**硬编码断言** |
| P2-4 | 无全局请求体大小限制（大 body 内存 DoS） | 新增 `core/body_limit_guard.py`（**原始 ASGI**，未用被禁的 `BaseHTTPMiddleware`）：有 `content-length` 直接 O(1) 回 **413/`41300`**；chunked/无 CL 包装 `receive` 计数、超限回 413 并让应用见 disconnect，**不缓冲整 body**；错误码 `41300` 在 `errors.py` ↔ `types/api.ts` **双向登记** |
| P2-5 | SSE 流无连接上限且被超时豁免（连接永不回收） | `notify.py`：**每用户 5 / 全局 200**；超限**建连前**回 429/`40103`（不先建流再关，避免重连风暴）；`finally` 归还名额 |
| P2-6 | 前端站点无 CSP/HSTS | `nginx.conf` server 级加 CSP（`default/script/style-src 'self'`、`img-src 'self' data:`、`object-src 'none'`、`frame-ancestors 'self'` 等，**不含 `unsafe-inline`/`eval`**）+ HSTS 1y |
| P2-7 | 前端 `??0` 纪律 / 死类型 / 静默截断 | DataCenter 进度改 null 语义；BreadthPanel Donut 三值各自判 null；BreadthPanel BlockGrid 改**最大余数法**使 9 档之和恰为 240（**彻底消除 slice 静默丢格**）；StockDetail 造方向写法改显式判空；删除零引用死类型 `MarketHeat`；Topbar 补"还有 N 条"提示 |
| P2-8 | `market.ts:11` 注释漂移（写"冷 22.2s"，实测 7.6s） | 注释更新为 2026-10-04 实测值 |

### 2.6 新补的回归守卫（9 个用例）

| 文件 | 用例 | 覆盖 |
|------|------|------|
| `tests/test_body_limit_guard.py`（新） | 5 | 接线与默认值、小请求不误伤、CL 超限 413/41300、**chunked 分支** 413/41300、**边界**（恰好=上限放行 / +1 拒绝） |
| `tests/test_notify_sse_limits.py`（新） | 4 | 每用户建 5→第 6 拒 429/40103、**反泄漏**（断 3→再建 3 全 OK）、**异常断开**归还名额、全局上限 |
| `tests/test_market_quotes.py`（改） | — | 原 SSE 用例改为显式传 fake `_user` + `aclose` + 断言 `_active_conns==0` |

---

## 3. 数据源可用性验证（逐项）

**取证方式**：离线直调适配器/构建函数 + **in-process ASGI 端到端**（不绑端口、不跑 lifespan）；辅以东财主机直连定性。沙箱网络**可用**（baidu 200 / qt.gtimg.cn 200 / 东财 ulist 200，DNS 全解析）。

### 3.1 外盘块 `GET /api/v1/market/overview/global` —— 🟢 全部正常

| 数据项 | 前端字段 | 能否返回 | 样本值（收盘 / 涨跌） | data_date | 数据源 |
|---|---|---|---|---|---|
| **日经225** | 日经225指数 | ✅ | 68309.46 / −0.94% | 2026-10-02 | 新浪 EOD |
| **韩国KOSPI** | 首尔综合指数 | ✅ | 7003.74 / +0.46% | 2026-10-02 | 新浪 EOD |
| **道琼斯** | .DJI | ✅ | 51176.96 / +0.49% | 2026-10-02 | 腾讯报价 + 新浪托底 |
| **纳斯达克** | .IXIC | ✅ | 27190.86 / +1.19% | 2026-10-02 | 腾讯报价 + 新浪托底 |
| **标普500** | .INX | ✅ | 7722.72 / +0.73% | 2026-10-02 | 腾讯报价 + 新浪托底 |

`status=ok` / `failed=[]`，冷构建 6.10s，HTTP 200 `code=0`。**每项带自己的 `data_date`**（外盘与 A 股交易日/时区不同，未错标为"A 股当日"）。

### 3.2 A 股指数（`_build_indices`，新浪）—— 🟢 全部正常

| 数据项 | 能否返回 | 样本值（收盘 / 涨跌） | data_date |
|---|---|---|---|
| 上证 sh000001 | ✅ | 3842.195 / +0.31% | 2026-09-30 |
| 深证 sz399001 | ✅ | 12887.62 / −0.11% | 2026-09-30 |
| 创业板 sz399006 | ✅ | 3135.277 / −0.23% | 2026-09-30 |
| 科创50 sh000688 | ✅ | 1530.006 / −2.51% | 2026-09-30 |
| 沪深300 sh000300 | ✅ | 4357.616 / +0.29% | 2026-09-30 |

### 3.3 ⚠️ 无法正常返回的数据项

**`GET /api/v1/market/overview/rt` 的「异动」（anomalies）子块 —— 恒 `degraded`**

| 次数 | 耗时 | indices | money_flow | anomalies |
|---|---|---|---|---|
| #1 冷 | 15.05s（撞预算） | unavailable | unavailable | unavailable |
| #2 | 12.09s | ok | ok | **degraded** |
| #3 `refresh=1` | 9.65s | ok | ok | **degraded** |

**根因（实测，非推断）**：
1. 东财 `push2.eastmoney.com/api/qt/clist/get`（全市场列表）**源站侧断开** —— `RemoteProtocolError: Server disconnected`，0.24s 即断。
2. **同主机的 `ulist.np/get`（指定指数）正常 200** ⇒ **不是沙箱无网络**，是**该重接口自身不可用**（代码注释已记载其曾因源站封 IP 而失效）。
3. 失败后回退**本地** heat 计算耗时 **10.50s**（`market.py:395`）；叠加全局 `_throttle()` 把 5 次新浪指数外呼（≈6s）**串行化** ⇒ 冷/慢时超 15s 预算。
4. `anomalies` 依赖 `stock_zh_a_spot_em`（= 同一个 clist 接口）⇒ 恒降级。

**本轮已做的缓解**（见 §2.4）：超时降级不再丢弃已完成的 `indices`/`money_flow`；`anomalies` 不再无条件二次外呼（heat 走本地回退时外呼 0 次）。**残留**：`anomalies` 本身在上游恢复前仍会是空名单 —— 这是**上游不可用**，不是本地 bug。

---

## ✅ 行动清单（剩余项，建议首个迭代）

| # | 行动 | 负责方 | 紧急度 |
|---|------|--------|--------|
| 1 | 计算池**队列无界**：重活端点已靠 `compute_slot` 闸门限流 + 匿名端点靠 single-flight，但池内排队队列本身仍无上限。建议提交处复用 `compute_slot_ctx` 有界信号量 | 后端 | P1 |
| 2 | SSE 名额**窄窗泄漏**：`stream()` 已 return 但生成器**尚未被 `__anext__` 启动**就取消 ⇒ CPython 对未启动生成器 `close()` 不跑 `finally`，漏 1 名额（实测复现）。触发窗口微秒级、单次仅漏 1，现实威胁低；若要"零泄漏"需包幂等异步迭代器 | 后端 | P2 |
| 3 | `anomalies` 上游（东财 `clist/get`）不可用：建议缩短该接口重试/退避，或在源站长期不可用时改为显式"该子块已下线"披露 | 后端 | P2 |
| 4 | 既有 `BaseHTTPMiddleware`（timing）与项目"严禁 `BaseHTTPMiddleware`"注释冲突（**既有状态，非本轮引入**）；本轮新增的 BodySizeLimit 位于其**内层**，不影响拦截时机 | 后端 | P3 |
| 5 | 安全 P3 三项（登录限速按 username 非 IP、prod CORS 黑名单只拦 `:5173`、`data/etf.py` 的 `js.eval`）未修，当前由 `--workers 1` 单进程缓解，**迁移多 worker 时必须重评** | 后端/运维 | P3 |
| 6 | 响应体**字段级**契约（前端 TS 类型 vs 后端实际返回）未做运行时全页面对账 | QA | P3 |

---

## ⚠️ 待完善 / 已知局限

- **本轮改动未提交 git**（36 文件 / +1352 −595 + 5 新文件）：多人并行改同一工作区，提交会冲突，故全部留在工作区。**上线前需由你 review 后自行提交**。
- 数据源验证**未起真实 uvicorn**（用 in-process ASGI 替代），**未验证网关层**；`global` 首次 HTTP 0.12s 疑为 SWR 缓存命中（冷构建 6.10s 已由离线直调独立证实）。
- 东财 `clist/get` 断开是**当下实测状态**，可能随源站恢复而消失。
- 未覆盖 `/market/quotes` 与 `/overview/daily` 的端到端（本地 parquet，零外部依赖，预期无碍）。
- 前端 vitest 的 `EPERM` 根因是**宿主 fs shim 行为**（非项目可修），已用串行 + 计数护栏规避并在注释中如实记录。
- 线程池 P0 的"22 路并发 ⇒ 探针挂死 12008ms"是**历史实测**，本轮未重新压测复现（本轮以"0 命中 + 逐参数核对"静态证明迁移完整性）。
- 未做浏览器 E2E / 并发压测 / 生产环境冒烟。

---

## 📚 成员产出索引

- 全检（上一轮）：`deliverables/gstack/pre-launch-check-aqp-2026-10-04.md` 及 `_inbox_{codereview,security,qa,perf}-2026-10-04.md`
- 数据源验证：`deliverables/gstack/_inbox_datasource-2026-10-04.md`（探针 `deliverables/gstack/_tmp/ds_probe_2026-10-04.py` 等）
- 独立验证：`deliverables/gstack/_inbox_verify-2026-10-04.md`
- 红例取证脚本：`deliverables/gstack/_tmp/test_red_probe{,2,3}.py`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
> 改动性质：**仅修改工作区文件，未 commit / 未 push / 未变更对外 API 契约（除新增 `41300` 错误码与 `model_version_note` 字段）**。
