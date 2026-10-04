# AQP 上线前全检报告（代码审查 + 安全审计 + QA 测试）

**日期**：2026-10-04
**场景**：上线前全检（代码审查 / 安全审计 / QA 测试 + 超时与性能专项）
**参与成员**：产品官（产品评审员） · 安全卫士（安全官） · 质量门神（QA 与发布） · 排障手（调查员）
**项目**：AQP · A股智能量化预测与分析平台（FastAPI + React 18 / Vite / TS）
**范围**：后端 118 个端点（20 个路由文件）· 前端 109 条调用路径 / 19 个页面

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟡 **条件 Go（Conditional Go）** —— 架构与安全基本面健壮，但存在 **3 条发布阻塞项** 与 **8 条 P1**，须处置后再上线。
- **阻塞项**：① 前端测试门禁**非确定**（默认并行静默丢 1 个测试文件，"绿灯"不可信）；② 后端默认门禁**红 1 例**（`test_monitor_trigger_caliber.py:348`，PSI 基线断言 0.5149 > 0.25）；③ ETF 详情 K 线 `null→0`，会画出"价格 0"假影线。
- **接口契约**：**PASS** —— 前端 109 条调用 ↔ 后端 118 条注册路由（111 唯一路径），**零 404、零方法错配**；后端仅 1 个已标注 `DEPRECATED` 的死接口（`GET /market/overview`，刻意为启动预热共用而保留）。
- **数据真实性**：**未发现系统性伪造/硬编码数据**；前端展示层整体由后端返回驱动，Redis 不可用等降级态如实披露（`redis=degraded`）。
- **安全**：鉴权面 ✅ 干净（20 个路由文件全量穷举，匿名仅 7 条且全部登记在白名单，54 个写端点全部鉴权）；无 🔴P0，最高为 1 条 🟠P1（匿名重计算端点惊群/DoS）。
- **下一步**：先清 3 条阻塞项 → 再处置 P1（线程池隔离、匿名端点 single-flight、白烧预算、缓存惊群）→ 走发布。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **Go / No-Go** | 🟡 **条件 Go** |
| **严重度分布** | 🔴 1 / 🟠 8 / 🟡 14 / 🟢 10（去重合并后共 **33** 条） |
| **发布阻塞项** | **3** 条（前端门禁非确定 · 后端门禁红 · K 线 null→0） |
| **关键行动项** | **10** 条（阻塞 3 + P1 4 + 加固 3） |
| **建议负责人** | 后端运行时 → 排障手清单；前端门禁与展示 → 前端；匿名端点加固 → 安全 |
| **接口契约** | ✅ 通过（0 个前端→后端缺失；2 个后端→前端未用，其中 1 个为 EventSource 误报） |
| **测试基线** | 后端 `2211 passed / 1 failed / 8 skipped`；前端串行 `16 文件 / 143 tests 全通过`；`tsc --noEmit` exit 0 |

---

## 1. 各成员核心结论

### 🔍 产品官（代码审查 + 接口契约覆盖）
- **核心判断**：接口契约 **PASS**——前端 109 条路径 ↔ 后端 118 条路由（111 唯一），**零 404、零方法错配**；后端唯一死接口 `GET /market/overview` 代码内已标 `DEPRECATED`、被启动预热共用，**非缺陷**。历史反模式（裸 `except:` / async 内同步阻塞 / `create_task` 泄漏）**已基本清零**（裸 except = 0，`TODO/FIXME` = 0）。
- **关键建议**：最高仅 🟡P2。优先修 `/desk/orders` N+1（单请求 202 条 SQL）、OrderDesk 每 tick 4 请求风暴、`refresh_freq` 缺范围校验、`instrument` 全表入内存无缓存、3 处静默降级无日志（`research.py:142` 会把 `model_version_count` 静默显示 0，误导"无模型"）。给死接口补 OpenAPI `deprecated=True`。

### 🛡️ 安全卫士（OWASP Top 10 + STRIDE）
- **核心判断**：🟡 条件 Go。认证/密钥/注入/超时面健壮，历史高危项多已修（`.env` 未入 git、PBKDF2-HMAC-SHA256 100k、SQL 全参数化、所有外呼带 timeout、prod fail-fast 齐备、prod 关 `/docs`）。
- **关键建议**：**F-001（P1）** 匿名重计算端点缺 single-flight + 计算池排队队列**无界** ⇒ 冷缓存惊群/缓存穿透 DoS（`market.py:1197/1240` 未传 `single_flight=True`；`compute_pool.py:126` 6 槽但队列无界）。首个迭代补：3 处开启 `single_flight` + 计算池有界信号量 + 匿名端点按 IP 限速；随后 F-002 请求体大小限制、F-003 SSE 连接上限、F-004 前端站点 CSP/HSTS。

### ✅ 质量门神（QA 验证 + 数据一致性 + 伪造数据排查）
- **核心判断**：🟠 有条件 Go。**未发现系统性伪造/硬编码数据**；数据展示层高度加固，降级态如实披露；历史 F2（ETF 日期口径）**本轮确认已修复**。
- **关键建议**：**两条门禁不可信** —— ① 前端 `vitest` 默认并行**每次静默丢 1 个测试文件**（131/137 vs 143，丢的文件每轮不同）⇒ 绿灯可能漏跑用例；② 后端默认回归 **红 1 例**（`test_monitor_trigger_caliber.py:348`，实测 `max=0.5149` vs 断言 `<=0.25`）。另有 ETF K 线 `null→0`、若干 `??0`/死类型/类型绕过等 🟡P2。

### 🔧 排障手（超时与性能根因取证）
- **核心判断**：前端 ↔ 后端超时对齐这条线**已修好**；剩余三类风险：① 重活线程池隔离**只做了一半**；② "白烧方向"不变量只登记 8 条路径；③ `RT` 预算仅 7% 余量。
- **关键建议**：**F1（P0）** `desk/screener/monitor/studio/report` 仍用 `asyncio.to_thread`（22 槽**默认池**，与 `/health/ready` 共用）⇒ 重开 2026-09-30 已定性的 P0（实测 22 路并发 ⇒ 就绪探针挂死 12008ms）。统一替换为 `run_in_compute_pool` + `compute_slot`。其次补登 `_PATH_MAX_BUDGETS`（≥12 条重活端点仍落 240s 白烧）、`quotes_hub` 冷缓存 single-flight、`RT` 预算上调或拆限速域。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 模块 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🔴 P0 | 运行时/并发 | `desk.py:65..503`、`screener.py:519..768`、`monitor.py:39`、`studio.py:73..436`、`report.py:500` | 重活用 `asyncio.to_thread` 占 22 槽**默认池**，与 `/health/ready` 共用；实测 22 路并发 ⇒ 就绪探针挂死 12008ms | 统一改 `run_in_compute_pool` + `compute_slot`（`market/etf/research/backtest/export/datacenter/ops` 已是范式） | 排障手 |
| 2 | 🟠 P1 **阻塞** | 测试门禁 | `frontend/vitest.config.ts` | 默认并行**静默丢 1 个测试文件**（131=143−12、137=143−6），每轮丢的不同 ⇒ 门禁不可信 | 固定 pool（singleThread/forks）或修 worker 临时写权限；加"文件计数断言" | 质量门神 |
| 3 | 🟠 P1 **阻塞** | 测试门禁 | `backend/tests/test_monitor_trigger_caliber.py:348` | `assert base["max"]<=0.25` 实测 **0.5149**（docstring 记录写入时 0.2024） | 先判定"数据真漂移"还是"用例基线过期"，再定改数据面或用例 gate | 质量门神 |
| 4 | 🟠 P1 | 安全/DoS | `market.py:1197,1240`；`core/compute_pool.py:126`；`cache/swr.py:412` | 匿名重计算端点未传 `single_flight=True` + 计算池**队列无界** ⇒ 冷缓存惊群/缓存穿透 | 3 处开 `single_flight`（`_build_lineage` 已是范式）+ 有界信号量 + 匿名按 IP 限速 | 安全卫士（与排障手 F4 同源） |
| 5 | 🟠 P1 | 超时预算 | `core/timeout_guard.py:151` `_PATH_MAX_BUDGETS` | 只登记 8 条；未命中回落 240s。≥12 条重活端点（前端仅 15~180s）⇒ 潜在白烧 60~225s。`config.py:107` 注释"其余路径前端最长 180s"**是错的**（存在 15/20/30s 档） | 补登 `CAPPED_PATHS`（前后端 + 闸门三处同改）+ 修注释 | 排障手 |
| 6 | 🟠 P1 | 超时预算 | `market.py:1065` `RT_BUILD_TIMEOUT_SECONDS=15.0` | 真实 ~14s（9 次 akshare 外呼被 1.2s 全局限速串行化）⇒ **仅 7% 余量** | 上调预算或拆限速域；**新数据块继续禁止并入 RT 预算**（做法已正确） | 排障手 |
| 7 | 🟠 P1 | 性能/惊群 | `data/quotes_hub.py:116-142` | 冷缓存无 single-flight，锁只护 cache 读写不护 `_fetch_payload` ⇒ N 并发 miss = N 次外呼，且该路径无预算 | 加 single-flight + 预算 | 排障手 |
| 8 | 🟠 P1 | DB/性能 | `desk.py:230-233`（=评审 P2-1） | 对每个 order 单独 `SELECT fills`（`ORDER_WINDOW=200`）⇒ 单请求 **202 条 SQL** | `selectinload(PaperOrder.fills)` 或 `WHERE order_id IN (...)` 一次取回分组 | 排障手 + 产品官（双源印证） |
| 9 | 🟡 P2 | 安全 | `main.py:294-327`；`Dockerfile:59`；`datacenter.py:1824` | 无全局请求体大小限制 ⇒ 大 body 内存 DoS（全仓 `content-length\|max_body` 0 命中） | 加 ASGI body 限制中间件（1–8MB）；uvicorn 加 `--limit-*` | 安全卫士 |
| 10 | 🟡 P2 | 安全 | `notify.py:83-153`；`timeout_guard.py:107-113` | SSE 流无连接上限，且被超时豁免 ⇒ 连接永不回收；viewer 级账号即可批量建连耗尽内存 | 每用户 + 全局连接上限 + 空闲超时 | 安全卫士 |
| 11 | 🟡 P2 | 安全 | `stores/useAuthStore.ts:55`；`index.html`；`nginx.conf:27-29` | 前端 JWT 存 localStorage 且**无 CSP/HSTS**（历史 F-003 未修；API 侧已有安全头，前端站点没有） | 网关加 CSP + HSTS | 安全卫士 |
| 12 | 🟡 P2 | 前端/性能 | `frontend/src/pages/OrderDesk/index.tsx:71-73,91-96` | 每 tick `Promise.all` 4 请求含 2 个重接口（account 需重建净值序列 + orders 200 条 N+1），默认 6~10s 一轮"重接口风暴" | 重/轻数据拆不同刷新档，或 SWR `refreshInterval` 分级 | 产品官 |
| 13 | 🟡 P2 | 契约/校验 | `app_settings.py:188` | `refresh_freq: int\|None` 无 `ge/le`；前端仅 `Math.max(1,…)` ⇒ 直连 API 可写 0/1/负数，令轮询页 1s 打后端 | `Field(ge=3, le=600)` | 产品官 |
| 14 | 🟡 P2 | 性能/内存 | `watchlist.py:165`、`market.py:429`、`datacenter.py:1590` | `SELECT symbol[,name] FROM instrument` 全表入内存、无 LIMIT、无 lru_cache/TTL（docstring 自称"一次全量入内存"但未实现） | 进程级 TTL 缓存 或 `WHERE symbol IN (?)` 按需取 | 产品官 |
| 15 | 🟡 P2 | 静默降级 | `datacenter.py:848`、`datacenter.py:908`、`research.py:142` | `except Exception: pass`；`research.py:142` 会把 `model_version_count` 静默显示 **0**（误导"无模型"） | 补 `logger.warning`；失败返回 `null`+note 而非 0 | 产品官（与 QA 数据真实性主题呼应） |
| 16 | 🟠 P1 **阻塞** | 前端/数据 | `EtfDetail/index.tsx:814-825` | `klineBars` 把 null OHLC `?? 0` 兜底 ⇒ 渲染"价格 0"的**假 K 线**，且 `close>=open` 恒真 ⇒ 还会被判成"涨"。可达性已证：类型 `EtfKlineBar` = `number\|null`（`types/etf.ts:189`），后端 `data/etf.py:345 _num()` 对 `'-'`（停牌）返回 None 并直接喂给 OHLCV（`etf.py:549-551`）。**同文件 `:449/:504/:660` 三处明令禁止 `??0`，唯独此处漏网** | 保持 null 透传（缺口跳过），不得用 0 代替不可得 | 质量门神 + 产品官（复核升级为 P1） |
| 17 | 🟢 P3 | 契约/类型 | `types/stock.ts:318` | `MarketHeat` 是**零引用死类型**，且把可选字段声明为必需（真类型 `HeatBlock` 字段全可选）⇒ 契约失真 | 直接删除 | 质量门神 + 产品官（复核降级） |
| 18 | 🟡 P2 | 前端/脆弱 | `StockDetail/index.tsx:542` | `(mainNet??0)>=0` 造方向写法（当前被 `mainOk` 门控不可达，但脆弱） | 显式判空后再判方向 | 质量门神 |
| 19 | 🟢 P3 | 契约/类型 | `MarketOverview/index.tsx:88` | daily+rt 合并用 `as unknown as MarketOverviewData` 绕过类型检查。逐字段复核后"同名不同型"假设**不成立**（同名仅 `trade_date`/`from_cache`，类型一致）；真实问题是浅合并必然缺**必需块**、断言掩盖缺失字段，**该风险已在 `types/stock.ts:562-568` 注释登记**（commit e2283c9）⇒ 已知遗留，非新阻塞 | 显式归一化映射，去掉双重断言 | 质量门神 + 产品官（复核降级） |
| 20 | 🟡 P2 | 前端/一致性 | `DataCenter/index.tsx:967/971/975` | 进度 `??0` 与同文件 `:993/:1006` 的 null 纪律不一致 | 统一 null 语义 | 质量门神 |
| 21 | 🟡 P2 | 前端/数据 | `BreadthPanel.tsx:72` | `Donut` 仍 `heat.up??0`（受 `status==='ok'` 门控，低可达） | 与 F16 同法治理 | 质量门神 |
| 22 | 🟡 P2 | 性能 | `market.py:317,413` | 日频块每轮 glob 5,223 symbol 目录 ×2（实测 `daily_bar` 5,223 目录 / 25,214 文件） | 缓存目录清单或改按需分区读取 | 排障手 |
| 23 | 🟡 P2 | 性能 | `alerts.py:607-622` | 预警成交量**串行** N 读 | 批量读 / 并发 + 预算 | 排障手 |
| 24 | 🟡 P2 | 监控项 | `market.py`（OVERVIEW_COMPAT 6s vs 23.5~113.5s；ETF 4.5s vs 6.22s） | 请求路径 **100% 超时**，靠无预算后台重建自愈（**有意设计，非缺陷**） | 保持监控 + 文档化"请求路径必降级" | 排障手 |
| 25 | 🟡 P2 | 注释漂移 | `frontend/src/api/market.ts:11` | 仍写"日频冷 22.2s"，实测 7.6s | 更新注释 | 排障手 |
| 26 | 🟢 P3 | 前端/数据 | `BreadthPanel.tsx:53` | `out.slice(0,240)` 各桶 round 后总和可 >240，**静默丢格**致热力图比例失真且无披露 | 按比例合并入"其他"并如实计入 | 产品官 |
| 27 | 🟢 P3 | 前端/UX | `Topbar.tsx:179` | 搜索结果 `slice(0,12)` 无"还有 N 条"提示 | 补总数提示 | 产品官 |
| 28 | 🟢 P3 | 契约 | `market.py:1326` `GET /market/overview` | 唯一死接口（代码已标 `DEPRECATED`，被启动预热共用 + 有回归套件） | 补 OpenAPI `deprecated=True` / sunset 说明 | 产品官 |
| 29 | 🟢 P3 | 安全 | `auth.py:61-78` | 登录限速按 `username` 计数、未按 IP（ip 仅记日志）；进程内内存（多 worker 失效，当前 `--workers 1` 缓解） | 改按 IP + 用户名双键；多 worker 时迁 Redis | 安全卫士 |
| 30 | 🟢 P3 | 安全/配置 | `config.py:343-346`；`docker-compose.yml:86` | prod CORS 黑名单只拦 `:5173`，而 compose 默认 `:8080` ⇒ 未显式设 `CORS_ORIGINS` 时生产带 localhost 通过校验 | 黑名单扩到 localhost / 127.* | 安全卫士 |
| 31 | 🟢 P3 | 安全/观察 | `data/etf.py:1128` | `js.eval(hash_code)`，输入来自 akshare 非用户可控 | 观察项；可换纯 Python 实现 | 安全卫士 |
| 32 | 🟢 P3 | 前端/数据源 | `AiPicksPanel.tsx:50-56` | 前端按阈值把 `pred_score` 分档为「强烈看多/看空」（分值真实，仅标签客户端生成） | 确认标签单一来源，避免与后端双源分歧 | 质量门神 |
| 33 | 🟢 P3 | 性能 | `market.py:55-57` `_build_indices` | 5 次外呼**串行**占 ~11.5s（RT 濒临的根因） | 并发化（注意 1.2s 全局限速域） | 排障手 |

---

## 3. 按模块汇总（用户指定视角）

| 模块 | 涉及发现 | 最高严重度 | 一句话结论 |
|------|---------|-----------|-----------|
| **运行时 / 并发 / 超时预算** | #1 #5 #6 #7 #22 #23 #24 #25 #33 | 🔴 P0 | 线程池隔离只做一半（P0）；预算白烧面 ≥12 条未收紧 |
| **测试与门禁** | #2 #3 | 🟠 P1 | 两条门禁不可信：前端静默丢文件、后端红 1 例 |
| **安全 / 鉴权** | #4 #9 #10 #11 #29 #30 #31 | 🟠 P1 | 鉴权面干净；风险集中在**匿名/低权端点的并发与资源上限** |
| **接口契约** | #28（+ 无 404） | 🟢 P3 | ✅ 双向覆盖通过，仅 1 个已登记死接口 |
| **桌面 / 交易台（desk）** | #8 #12 | 🟠 P1 | N+1（202 条 SQL）+ 前端 4 请求/ tick 风暴 |
| **市场 / 行情（market）** | #6 #7 #22 #24 #28 #33 | 🟠 P1 | RT 预算濒临、冷缓存惊群、每轮 glob 5,223 目录 |
| **ETF（etf / EtfDetail）** | #16 #24 | 🟠 P1 | K 线 `null→0` 画"价格 0"假影线（**阻塞**，产品官复核升级） |
| **选股 / 监控 / 研究 / 数据运维** | #1 #15 #23 | 🔴 P0 | 同受默认池拖累；3 处静默降级无日志 |
| **前端通用（Topbar / types / 类型）** | #17 #18 #19 #20 #21 #26 #27 #32 | 🟡 P2 | 死类型 + 双重断言绕过 + 若干 `??0` 纪律不一致 |

### 3.1 重点排查项逐条回答（用户六问）

| 用户关注点 | 结论 | 证据锚点 |
|-----------|------|---------|
| **① 前后端是否存在超时风险** | ⚠️ **存在**，但已收敛到 3 处 | RT 预算 15s vs 真实 14s（仅 7% 余量，`market.py:1065`）；≥12 条重活端点落 240s 默认预算（前端只等 15~180s）⇒ 白烧 60~225s；ETF/overview 请求路径**有意** 100% 降级（后台自愈） |
| **② 性能是否可优化** | ✅ 可，收益明确 | 线程池隔离（P0，机械改动、最高收益）→ N+1（`desk.py:231`）→ 每轮 glob 5,223 目录（`market.py:317,413`）→ `instrument` 全表入内存（3 处）→ `_build_indices` 5 外呼串行 |
| **③ 前端调了后端未实现的接口** | ✅ **0 个** | 109 条前端路径 ⊆ 后端 118 路由；7 条"疑似"已逐条证伪（5 注释、2 模板字面量截断误报） |
| **③′ 后端实现了前端未调用的接口** | ⚠️ **2 个**，其中 1 个真死 | `GET /market/overview`（真死，已标 DEPRECATED）+ `/notify/stream`（**误报**，前端经 EventSource `notifyApi.streamUrl()` 消费） |
| **④ 前端功能表现 vs 后端返回数据是否一致** | ⚠️ 基本一致，**1 处实质偏差（P1）+ 4 处纪律不一致（P2/P3）** | 实质偏差：ETF K 线 `null→0`（#16，可画出"价格 0"假蜡烛且恒判涨）。纪律不一致：`DataCenter` 进度 `??0`、`BreadthPanel` `heat.up??0`、`StockDetail` `(mainNet??0)>=0`；另 2 处（`MarketOverview` 双重断言 #19、`types/stock.ts` 死类型 #17）经产品官逐字段复核已降为 🟢P3/已知遗留 |
| **⑤ 是否存在为美观伪造/硬编码数据** | ✅ **未发现系统性伪造** | 前端展示层由后端驱动；降级态如实披露（`redis=degraded`）；历史"0 冒充不可得"治理到位（多文件明令禁止 `??0`）。**残留 3 类**：K 线 `null→0`、`slice()` 静默截断（`BreadthPanel:53`/`Topbar:179`）、`research.py:142` 静默 0 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 修前端测试门禁非确定：固定 vitest pool（singleThread/forks）或修 worker 临时写权限，并加"测试文件计数断言" | 前端 | **P0 阻塞** | 上线前 |
| 2 | 裁决后端红例 `test_monitor_trigger_caliber.py:348`：先定性"数据真漂移 vs 用例基线过期"，再改数据面或用例 gate（**禁止为过门禁调阈值/删测试**） | 后端 + QA | **P0 阻塞** | 上线前 |
| 3 | 修 ETF K 线 `null→0`（`EtfDetail/index.tsx:818-822`）：缺口跳过，不得以 0 代不可得 | 前端 | **P0 阻塞** | 上线前 |
| 4 | 重活线程池隔离补齐：`desk/screener/monitor/studio/report` 由 `asyncio.to_thread` 改 `run_in_compute_pool` + `compute_slot` | 后端 | P1 | 上线前/紧随 |
| 5 | 匿名重计算端点加固：`market.py:1197/1240` 开 `single_flight=True` + 计算池**有界**信号量 + 匿名按 IP 限速 | 后端 | P1 | 上线前/紧随 |
| 6 | 补登 `_PATH_MAX_BUDGETS`/`CAPPED_PATHS`（≥12 条重活端点）+ 修 `config.py:107` 错误注释（前后端 + 闸门三处同改） | 后端 + 前端 | P1 | 紧随 |
| 7 | `RT_BUILD_TIMEOUT_SECONDS` 上调或拆 1.2s 全局限速域；`quotes_hub` 冷缓存加 single-flight | 后端 | P1 | 紧随 |
| 8 | 加全局请求体大小限制中间件（1–8MB）+ SSE 连接上限 + 前端站点 CSP/HSTS | 后端 + 运维 | P2 | 首个迭代 |
| 9 | 修 N+1：`desk.py:231` 改 `selectinload`/`IN` 批取；`instrument` 全表加 TTL 缓存；`refresh_freq` 加 `Field(ge=3,le=600)` | 后端 | P2 | 首个迭代 |
| 10 | 3 处静默降级补日志、`research.py:142` 由 0 改 `null`+note；清理 `types/stock.ts` 死类型与 4 处 `??0`/双重断言纪律不一致 | 后端 + 前端 | P2 | 首个迭代 |

---

## ⚠️ 待完善 / 已知局限

- **未覆盖响应体字段级契约**：前端 TS 类型 vs 后端实际返回字段的**运行时**校验超出只读静态审计范围（路径级契约已全覆盖）。QA 的数据一致性环节已抽样核对，但未做全页面字段级对账。
- **未做浏览器 E2E / 并发压测 / live 接口冒烟**：线程池 P0 的 12008ms 探针挂死来自历史实测与代码路径推演，本轮未重新压测复现。
- **flaky 观察样本偏小**：前端门禁"丢文件"结论基于 3 次运行（131/137/143 三者互洽），建议加计数断言后长期观察。
- **`RT` 预算余量 7%** 为静态推演（9 次外呼 × 1.2s 限速 ≈ 11.5s + 开销），未在冷启动实测计时。
- **"有意降级"需持续监控**：ETF/overview 请求路径 100% 超时靠后台重建自愈是**设计选择**，但一旦后台重建失败即无兜底。
- 安全 P3 项（#29–#31）当前由 `--workers 1` 单进程部署缓解，**迁移多 worker 时必须重评**。

---

## 📚 成员产出索引

- gstack-product-reviewer（产品官）原始产出：`deliverables/gstack/_inbox_codereview-2026-10-04.md`（证据脚本 `deliverables/gstack/_tmp/contract_diff.py` 等）
- gstack-security-officer（安全卫士）原始产出：`deliverables/gstack/_inbox_security-2026-10-04.md`
- gstack-qa-lead（质量门神）原始产出：`deliverables/gstack/_inbox_qa-2026-10-04.md`
- gstack-investigator（排障手）原始产出：`deliverables/gstack/_inbox_perf-2026-10-04.md`
- 关联历史：`deliverables/gstack/p8-async-task-queue-assessment-2026-10-04.md`、`coverage-backend-frontend-2026-10-01.md`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
> 性质：**只读全检**，四位成员均未改动任何生产代码、未变更 API 契约、未新增依赖。
