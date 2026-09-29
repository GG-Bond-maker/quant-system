# P4 前后端契约对账（批次：P4-contract）

- 审核日期：2026-09-18（批次文档落盘 2026-09-21）
- 范围：`frontend/src`（93 文件 / 约 19k 行） ↔ `backend/app/api/v1`（19 模块 / 114 路由）
- 方法：**全量**（非抽样）。后端端点清单由 AST 静态扫描 + 运行时 `app.routes` 内省双向交叉验证（两侧均为 114 条）；前端调用清单由手写平衡括号扫描器提取（111 处调用点）；随后做六项对账（路径/方法、请求参数、响应字段、错误处理、权限、错误码）。
- 运行时验证：`fastapi.testclient` 直连 ASGI，无 HTTP 服务、无写操作（只发 GET），凭证由 `create_jwt_token("probe_viewer","viewer")` 自签（`require_auth` 不查库，见 `backend/app/core/auth.py:183-202`）。
- 未运行全量 pytest（父审计员正在跑）。

## 0. 结论摘要

1. **路径与方法维度干净**：111 处前端调用全部命中已存在的后端路由（归一化 `/api/v1` 前缀与 `{param}` 路径模板后，0 处“前端调用不存在的端点”）。
2. **权限维度是本次最严重的一簇**：后端角色表经运行时证明（viewer 探针），而 `/settings`、`/data`、`/screener`（导出）、`/portfolio`（回测）四个 **viewer 可进** 的页面里，存在 **16 处**“后端 researcher/admin、前端无任何角色限制”的按钮/自动调用（逐条见 4.4），viewer 一旦触发**必然报错**，其中 `/settings` 的连接测试是**静默失败**（`Promise.allSettled` 吞掉 403，无任何提示）。
3. **存在 1 处真正的越权**：`GET /api/v1/datacenter/train/readiness` 完全无鉴权依赖（`runtime_routes.json` 证明），无凭证即返回 `features`（含文件系统目录）、`torch_*`、样本量等环境元数据；前端只在登录后的 `/data` 页消费它。按 §6「前端限制 + 后端不校验 = 越权」判据可定 P0；本次按实际影响（内部环境元数据、无用户数据）记 **P1**，理由见 3.4。
4. **错误码是分裂的两套**：`errors.py` 注册 21 个码，但 `datacenter.py` 另有 4 个 4 位码（4002/4003/5000/5001）、`backtest.py` 另有 8 个 5 位码（40010~40017），前端一个都不认识；`5001` 与 `ERR_PANIC_CONTAINED=50001` 仅差一位，极易混淆。
5. **所有角色不足的报错都以英文机器串 `"FORBIDDEN"` 直出用户界面**（运行时实证），因为 `ERR.FORBIDDEN` 在前端虽已定义却零引用，且 `sanitizeApiMessage` 的正则匹配不到 `FORBIDDEN`（不含 `Error/Exception` 后缀）。
6. **响应字段错位 2 处确定性缺陷**：`nav_tail`（前端读、后端从不返回，`rg` 全仓 0 命中）与 `watchlist/dashboard` 的降级态字段（后端返、前端类型与页面 0 引用）。另有 1 处跨层性能缺陷：`app_settings.py:157` 直调 `overview()` 把 `Query` 对象当 `refresh` 传入（`bool(Query(0)) is True`，已实证），导致**每次 `/settings` 都击穿数据中心统计缓存**。

---

## 1. 一致性问题表（§6 要求格式）

| 序号 | 严重度 | 不一致类型 | 前端位置 | 后端位置 | 现象 | 修复建议 |
|---|---|---|---|---|---|---|
| 1 | P1 | 权限 | `frontend/src/pages/DataCenter/index.tsx:404,446,463,474,481,494`；路由门 `App.tsx`（`/data` 仅 viewer） | `backend/app/api/v1/datacenter.py:594,677,721,750,763,776,927`（均 `require_role("researcher")`） | viewer 打开 `/data`：`logs`/`status`/`autoStatus` 全部 `code=40300`（实证），页面弹出英文 `FORBIDDEN`，状态面板空白且 1.5s 轮询循环持续失败 | 页内按 `hasMinimumRole(role,'researcher')` 隐藏运维按钮/面板；或把只读运维端点降为 viewer |
| 2 | P1 | 权限 | `frontend/src/pages/Settings/index.tsx:588-592,609-612,618-621`；`App.tsx`（`/settings` 仅 viewer） | `backend/app/api/v1/app_settings.py:269`(researcher)、`:279`(admin)、`:309`(admin) | viewer 点「立即同步日线 / 清理所有缓存 / 备份策略数据」必然 40300 | 按 `isAdmin`/`hasMinimumRole` 分别隐藏（`isAdmin` 已存在于 `Settings/index.tsx:137`，当前只用在 `:477` 的引擎卡） |
| 3 | P1 | 权限 | `frontend/src/pages/Settings/index.tsx:193-206` 的 `testAll()`，由 `:218`（`load(true)`）在 `:224` 自动触发 | `backend/app/api/v1/app_settings.py:238`（researcher） | 任何角色每次进 `/settings` 都自动发 2 次 `POST /settings/connectors/test`；viewer 被 403 后由 `Promise.allSettled` **静默吞掉**，连接器卡片永远空白且**无任何提示**（静默失败，比 1 更差） | viewer 不自动测；或失败时把 `reason.message` 透出到卡片 |
| 4 | P1 | 权限 | `frontend/src/pages/Report/index.tsx:89`（按钮）、`:5`（注释已知情） | `backend/app/api/v1/report.py:469`（researcher） | viewer 点「重新生成」必然 40300；前端注释已声明该取舍，故非疏忽，但按钮仍应隐藏 | 隐藏按钮（或禁用+tooltip 说明需研究员角色） |
| 5 | P1 | 权限 | `frontend/src/pages/Screener/index.tsx:202,341`；路由 `/screener` 仅 viewer | `backend/app/api/v1/export.py:23`（researcher） | viewer 点「导出 Excel」必然 40300 | 隐藏导出按钮 |
| 6 | P1 | 权限 | `frontend/src/pages/Portfolio/index.tsx:251,389`；路由 `/portfolio` 仅 viewer | `backend/app/api/v1/portfolio.py:107`（researcher） | viewer 点「开始回测」必然 40300（页面主功能，用户会以为系统坏了） | 隐藏按钮或把 `/portfolio` 提升为 researcher 路由 |
| 7 | **P1** | 权限（越权） | 前端只在登录后的 `pages/DataCenter/TrainPanel.tsx:53` 消费 | `backend/app/api/v1/datacenter.py:1124-1129`（**无任何 Depends**） | 无凭证即可 `GET /api/v1/datacenter/train/readiness` → `code=0`，返回 `features`(含 `dir` 路径)/`torch_device`/`torch_install_hint`/`est_samples`/`gnn_edges`/`note`（**实证**），违反契约 §4「读接口最低 viewer」 | 加 `Depends(require_role("viewer"))`；同时评估 `features.dir` 是否应外发 |
| 8 | P2 | 错误 | `frontend/src/api/client.ts:52,62`；`frontend/src/types/api.ts:29`（`ERR.FORBIDDEN` 零引用） | `backend/app/core/auth.py:210`（`detail="FORBIDDEN"`）+ `backend/app/core/errors.py:94`（message 取 detail） | 所有角色不足的提示直接以英文 `FORBIDDEN` 显示给用户（实证：viewer 探针返回 `msg='FORBIDDEN'`）；`sanitizeApiMessage` 只屏蔽 `*Error/*Exception`，漏掉该串 | 后端 `_AUTH_DETAIL_CODES` 附中文 message；或前端补 `ERR.FORBIDDEN` 分支 |
| 9 | P2 | 码 | `frontend/src/api/client.ts:49`（只特判 40900）；`types/api.ts` ERR 表 | `backend/app/api/v1/datacenter.py:684,952`（`fail(4002, ...)`） | 同步任务忙时返回 4002，前端「数据流水线正在执行，请稍后重试」提示**永不触发**，用户看到的是后端原始文案 | `datacenter.py` 改用 `ERR_PIPELINE_BUSY(40900)` |
| 10 | P2 | 码 | 前端 ERR 表未定义 40010~40017 | `backend/app/api/v1/backtest.py:318,325,352,431,504,506,508,537,545,566` | 8 个未注册码（40010~40017）在 `errors.py` 与前端 ERR 表中均不存在，前端无法分支处理 | 在 `errors.py` 注册并同步前端 ERR |
| 11 | P2 | 码 | 同上 | `backend/app/api/v1/datacenter.py:689`(5000)、`:969`(4003)、`:1017`(5001) | 3 个越界码：`5000/5001` 落在系统码段（注释 `errors.py:70-71` 明令 5xxxx 为系统码），且 `5001` 与 `ERR_PANIC_CONTAINED=50001` 形近易混 | 归并到 `ERR_SYSTEM(50000)` / `ERR_PARAMS(40000)` |
| 12 | P2 | 响应 | `frontend/src/types/watchlist.ts:37-40`；`pages/Watchlist/index.tsx`、`stores/useWatchlistStore.ts`、`hooks/useWatchlistQuotes.ts`（`status`/`reason`/`flow_status` 全 0 命中） | `backend/app/api/v1/watchlist.py:327`（`summary.flow_status`）、`:343-365`（`status:"degraded"`+`reason`+逐行 `status:"unavailable"`+全 null 行情） | 后端如实标注的降级态**无任何前端消费者**：整行全 null 的行情按正常数据渲染，用户看不到「降级/不可用」横幅（契约 §5 的降级披露形同虚设） | 类型补 `status/reason`，`pages/Watchlist` 渲染降级横幅 |
| 13 | P2 | 响应 | `frontend/src/types/p1.ts:169` + `pages/Backtest/TopKPanel.tsx:112-117` | `backend/app/api/v1/backtest.py:186-224`（payload 无 `nav_tail`；`rg nav_tail backend/` = 0 命中） | `/backtest/run` 从不返回 `nav_tail`，「最近净值」块因可选链静默为 false 而**永不渲染**（无报错、无占位） | 后端补 `nav_tail`（`equity_curve` 末 5 点）或前端改读 `equity_curve`；同时删死代码 |
| 14 | P2 | 响应 | `frontend/src/pages/Backtest/TopKPanel.tsx`（`equity_curve`/`drawdown_curve`/`annual_returns`/`holdings`/`trades`/`friction_costs`/`liquidity`/`universe_scope` 全 0 命中） | `backend/app/api/v1/backtest.py:209-223` | 后端返回的 8 组字段无任何前端消费者：Top-K 回测**没有任何净值/回撤曲线**，只有 8 张 KPI 卡；`universe_scope`（含退市/生存偏差披露）与 `liquidity`（冲击成本口径）**无法到达用户** | 补净值/回撤图表；`universe_scope`/`liquidity.note` 以 tooltip 或脚注如实展示 |
| 15 | P3 | 响应（健壮性） | `frontend/src/api/client.ts:111-112` | — | 非统一信封的 200 响应（SPA `index.html` 回退、代理错误页）被**原样放行**，`get<T>()` 把 HTML 字符串当业务 data 返回，调用方拿到垃圾数据而非报错。注：`:111` 注释表明“放行非包装响应”是**有意为之**（为静态文件），本条只指出其副作用，非独立缺陷 | 收敛为“仅静态资源放行”或增加 `content-type` 校验 |
| 16 | P2 | 一致性（跨层性能） | `frontend/src/components/AuthBootstrap.tsx:26`、`stores/usePreferencesStore.ts:28`、`pages/Settings/index.tsx:210`、`api/settings.ts:63-65`（注释称冷算 27~30s） | `backend/app/api/v1/app_settings.py:157`（`await overview()`）+ `datacenter.py:461`（`if refresh: invalidate_stats_cache()`） | 直调时 `refresh` 实参是 `Query(0)` 对象，`bool()` 为 **True**（已实证）→ **每次 `/settings` 都清空数据中心统计缓存**，下次 `/datacenter/overview` 必需全量重扫（前端注释与 `overview` 60s 超时即为该现象） | 改为 `await overview(refresh=0)` 或抽出不带 `Depends/Query` 的内部函数 |
| 17 | P3 | 死代码 | `api/alerts.ts:64`（`updateRule`）、`api/market.ts:55,83`（`overview`/`quotes`）、`api/datacenter.ts:39`（`task`）、`api/datacenter.ts:83`（`trainStatus`，端点另经 `TrainPanel.tsx:44` 裸 URL 存活） | `alerts.py:189`、`market.py:805`、`datacenter.py:726-729`、`datacenter.py:1149-1152` | 5 个前端包装无调用点；其中 `GET /market/overview` 与 `GET /market/quotes` 因此**整条端点无前端消费者**（`market.py:673` 的 6s 兼容聚合与 `quotes_hub` 亦然） | 清理包装；端点交死代码批次判定 |
| 18 | P3 | 响应 | `frontend/src/types/p1.ts:112`（`basis`/`basis_fields`，仅 `basis_desc` 被消费）；`types/stock.ts`（`kind:"platform"` 零引用，`AiPicksPanel.tsx:87` 用硬编码兜底文案） | `backend/app/api/v1/screener.py:647`、`backend/app/api/v1/market.py:512` | 机读口径字段（逐字段口径表、平台自研标记）无消费者，只剩自由文本；硬编码兜底文案与 `basis` 内容存在漂移风险 | 消费 `basis_fields`/`kind`，或删字段避免伪契约 |
| 19 | P3 | 一致性 | `pages/Alerts/index.tsx:200,251` | `alerts.py:248`（events，researcher）vs `:271`（events/read，**viewer**） | 同一功能组内 `read` 的角色门槛低于 `list`，与「读=viewer、写=researcher」的分层不符（当前无实际越权，因 `/alerts` 路由本身是 researcher） | 统一为 researcher |

---

## 2. 必须立刻修的 Top 10（按影响排序）

1. **`GET /datacenter/train/readiness` 无鉴权**（`datacenter.py:1124-1129`）：零成本加 `require_role("viewer")`，消除唯一实证越权（无凭证 `code=0`）。
2. **`/settings` 的 3 个 viewer 必错按钮 + 静默连接测试**（`Settings/index.tsx:193-224,588-621` ↔ `app_settings.py:238,269,279,309`）：按角色隐藏；至少把 `Promise.allSettled` 的 403 透出。
3. **`/data` 页 viewer 必错簇**（`DataCenter/index.tsx:404-494` ↔ `datacenter.py:594,677,721,750,763,776,927`）：隐藏运维面板；1.5s 轮询在 403 时应停止重试。
4. **`nav_tail` 契约错位**（`types/p1.ts:169` ↔ `backtest.py:186-224`）：改字段名或补字段，否则 Top-K 结果的净值块永久失效。
5. **`FORBIDDEN` 英文串直出**（`auth.py:210`+`errors.py:94` ↔ `client.ts:52`）：后端补中文 message，前端补 40300 分支。
6. **`app_settings.py:157` 缓存击穿**（→ `datacenter.py:461`）：一次 `refresh=0` 修正即可去除“每次进设置页清空数据看板缓存”的系统性慢查询。
7. **错误码分裂**（`datacenter.py:684,689,952,969,1017` + `backtest.py` 8 处）：把 4002 映射为 `ERR_PIPELINE_BUSY`，其余登记入 `errors.py` 并同步前端 ERR 表。
8. **`/portfolio` 与 `/screener` 导出按钮 viewer 必错**（`Portfolio/index.tsx:389`、`Screener/index.tsx:341`）：隐藏或提升路由角色。
9. **`watchlist/dashboard` 降级态无消费者**（`watchlist.py:343-365` ↔ `types/watchlist.ts:37-40`）：全 null 行情被当成正常数据展示，属“静默降级”，须补横幅。
10. **`/backtest/run` 的 `universe_scope`/`liquidity` 披露不可见 + 无净值曲线**（`backtest.py:196-223`）：生存偏差与冲击成本口径无法到达用户，违反 §5 派生指标口径披露要求。

---

## 3. 反向清单

### 3.1 前端调用了不存在的后端端点
**空。** 111 处调用点全部匹配到已注册路由。归一化规则见 `reconcile.py`（剥 `/api/v1` 前缀、`{p}` → 参数、模板字面量按前缀匹配）。

> 说明：首次对账曾报 4 处「MISSING」，逐条核实均为工具假阳性：3 处是 `client.ts` 的 `download()` 语义（无 payload 用 GET、有 payload 用 POST），1 处是 `/report/daily${date?...}` 模板字面量在归一化时被切断（该端点确由 `api/monitor.ts:100-101` 调用）。

### 3.2 后端端点未被前端调用（交死代码清单）
| 端点 | 位置 | 角色 | 证据 |
|---|---|---|---|
| `GET /api/v1/alerts/health` | `alerts.py:133-141` | researcher | `rg "alerts/health" frontend/src` → 0 命中（该端点 docstring 明确写着“若没有本端点，该失效对外完全不可见”，即**为可见性而加，却无人消费**） |
| `POST /api/v1/settings/apikeys/rotate` | `app_settings.py:255` | admin | `rg "apikeys" frontend/src` → 0 命中 |
| `GET /api/v1/market/overview` | `market.py:805` | 公开 | `api/market.ts:55` 的 `marketApi.overview` 无调用点；页面只用 `/overview/rt`、`/overview/daily`；`market.py:673` 的 `OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS=6.0` 兼容聚合随之闲置 |
| `GET /api/v1/market/quotes` | `market.py`（`marketApi.quotes`，`api/market.ts:83`） | 公开 | 同上，0 调用点 |
| `PUT /api/v1/alerts/rules/{rule_id}` | `alerts.py:189-209` | researcher | 前端仅有 `deleteRule`/`createRule`（`pages/Alerts/index.tsx:230,244`），无编辑 UI |
| `GET /api/v1/datacenter/sync/tasks/{task_id}` | `datacenter.py:726-729` | researcher | `datacenterApi.task`（`api/datacenter.ts:39`）0 调用点 |
| `GET /api/v1/datacenter/train/status`（包装死、端点活） | `datacenter.py:1152` | researcher | 包装 `api/datacenter.ts:83` 死，但 `TrainPanel.tsx:44` 用裸 URL `useTaskPolling` 调用 |
| `GET /api/v1/notify/stream?channels=quotes,alerts` | `notify.py:83-153` | viewer（ticket） | 前端订阅**未传 `channels`**（`rg "channels=" frontend/src` → 0 命中），`stores/useNotifyStore.ts` 只用默认 `notify` 频道；`quotes`/`alerts` 两个 SSE 频道与 `quotes_hub` 订阅链路无消费者 |

### 3.3 前端零消费者的后端响应字段（伪契约）
`/backtest/run` 的 `equity_curve`、`drawdown_curve`、`annual_returns`、`holdings`、`trades`、`friction_costs`、`liquidity`、`universe_scope`（`backtest.py:209-223`）；`/watchlist/dashboard` 的 `status`/`reason`/逐行 `status`/`summary.flow_status`（`watchlist.py:327,343-365`）；`/screener/stocks` 的 `basis`/`basis_fields`（`screener.py:647`）；`/market/overview/daily` 的 `kind:"platform"`（`market.py:512`）。

### 3.4 无鉴权端点全表（运行时实证）
| 端点 | 位置 | 判定 |
|---|---|---|
| `POST /auth/login`、`POST /auth/register`、`GET /auth/register/status` | `auth.py` | 设计如此 ✓ |
| `GET /market/index/kline` | `market.py:606-611` | 与 `App.tsx`「市场概览是唯一公开业务页」一致 ✓ |
| `GET /market/overview`、`/market/overview/rt`、`/market/overview/daily` | `market.py:706-709,749,805` | 同上 ✓ |
| `GET /datacenter/train/readiness` | `datacenter.py:1124-1129` | **契约 §4 违反 + 环境信息泄露**（P1，见下） |

探针输出（无 `Authorization` 头）：
```
GET /api/v1/datacenter/train/readiness   HTTP=200 code=0 msg='ok'
    data=['torch_ready','torch_device','torch_install_hint','features','est_samples',
          'min_samples','sample_ok','gnn_edges','tft_ready','gnn_ready','relation_note','note']
GET /api/v1/market/index/kline?code=sh000001&limit=30  HTTP=200 code=0 msg='ok' data=['code','name','bars']
GET /api/v1/market/overview/rt           HTTP=200 code=0 msg='ok'
GET /api/v1/datacenter/overview          HTTP=200 code=40100 msg='UNAUTHORIZED' data=None
GET /api/v1/alerts/health                HTTP=200 code=40100 msg='UNAUTHORIZED' data=None
GET /api/v1/datacenter/sync/status       HTTP=200 code=40100 msg='UNAUTHORIZED' data=None
GET /api/v1/screener                     HTTP=200 code=40100 msg='UNAUTHORIZED' data=None
```
同一次探针也顺带证实：**后端 HTTP 恒 200、错误走 `code`** 的信封约定在全量端点上成立（`code=40100`、`data=None`），`client.ts` 的集中解包是正确且必要的。

**为何记 P1 而非 P0**：暴露面是构建/环境元数据（特征目录路径、torch 设备与安装提示、样本量、关系边数），不含任何用户数据或可写能力；但它是**零成本可修**的显式契约违反，若审计口径按 §6「前端限制 + 后端不校验 = 越权」从严，可直接升为 P0——请父审计员按终稿分级裁量。

---

## 4. 权限对账明细（运行时实证）

### 4.1 探针输出（自签 viewer JWT）
```
=== viewer token ===
/api/v1/datacenter/logs?limit=60      code=40300 msg='FORBIDDEN'   <- DataCenter /data
/api/v1/datacenter/train/status       code=40300 msg='FORBIDDEN'   <- TrainPanel
/api/v1/export/screener               code=40300 msg='FORBIDDEN'   <- Screener 导出
/api/v1/alerts/health                 code=40300 msg='FORBIDDEN'
/api/v1/alerts/rules                  code=40300 msg='FORBIDDEN'   <- Alerts 页
/api/v1/desk/orders?limit=50          code=40300 msg='FORBIDDEN'   <- OrderDesk
/api/v1/research/experiments          code=40300 msg='FORBIDDEN'   <- Research 页
/api/v1/datacenter/sync/status        code=40300 msg='FORBIDDEN'   <- DataCenter :404,446
/api/v1/datacenter/sync/auto          code=40300 msg='FORBIDDEN'   <- DataCenter :405
/api/v1/settings                      code=0     msg='ok'          <- viewer ✓
/api/v1/datacenter/quality?limit=5    code=0     msg='ok'          <- viewer ✓
/api/v1/datacenter/datasets           code=0     msg='ok'          <- viewer ✓
/api/v1/datacenter/text/status        code=0     msg='ok'          <- viewer ✓
/api/v1/datacenter/mirror/status      code=0     msg='ok'          <- viewer ✓
/api/v1/datacenter/task-stats         code=0     msg='ok'          <- viewer ✓
/api/v1/report/daily                  code=0     msg='ok'          <- viewer ✓
/api/v1/screener/stocks?page=1&page_size=20  code=0 msg='ok'        <- viewer ✓
/api/v1/market/overview/daily?days=5  code=0     msg='ok'          <- 公开 ✓
=== researcher token（对照组）===
/api/v1/datacenter/logs?limit=5       code=0     msg='ok'
/api/v1/alerts/health                 code=0     msg='ok'
/api/v1/export/screener               (返回文件流，非信封)
```
→ 同时确证了两件事：**后端角色表与 `runtime_routes.json` 完全一致**；**`FORBIDDEN` 会以英文原样进入前端**（`client.ts:52` → `:62` 的 `sanitizeApiMessage` 匹配不到该串）。

### 4.2 「前端隐藏但后端不校验」的 P0 方向：**未发现**
前端仅有两处页内角色门，且都与后端一致，无越权：
- `pages/StockDetail/index.tsx:41-42` `canPredict = hasMinimumRole(role,'researcher')` ↔ `/stock/predict*` 后端 researcher ✓
- `pages/Settings/index.tsx:137,477` `isAdmin` 门 ↔ `PUT /settings/engine` 后端 admin（`app_settings.py:199`）✓

### 4.3 匿名可见页 `Topbar` 的静默失败（P2，补充）
公开页 `/`（`App.tsx:86-88` MarketOverview）无条件渲染 `Topbar`，而 `Topbar.tsx:146-147` 调 `stockApi.search`（`stock.py:84` viewer）与 `etfApi.list`（`etf.py:280` viewer）；匿名访客两者均 `code=40100`，被 `.catch(() => [])` / `.catch(() => ({items: []}))` 吞掉 → 搜索框看似可用但永远无结果、无提示。公网页若要保留搜索，需在 401 时给出「请登录」而非静默空集。

### 4.4 viewer 必然被 40300 拒绝的前端调用点（16 处，逐条）
| # | 触发处（前端 file:line） | 端点 | 后端角色 |
|---|---|---|---|
| 1 | `pages/Settings/index.tsx:193-206`（经 `:218`/`:224` 自动） | `POST /settings/connectors/test` | researcher（`app_settings.py:238`）**静默失败** |
| 2 | `pages/Settings/index.tsx:588-592` → `:276` | `POST /settings/data/sync` | researcher（`:269`） |
| 3 | `pages/Settings/index.tsx:609-612` → `:310` | `POST /settings/data/cache/clear` | **admin**（`:279`） |
| 4 | `pages/Settings/index.tsx:618-621` → `:319` | `POST /settings/db/backup` | **admin**（`:309`） |
| 5 | `pages/DataCenter/index.tsx:404` | `GET /datacenter/logs` | researcher（`datacenter.py:594`） |
| 6 | `pages/DataCenter/index.tsx:404,446` | `GET /datacenter/sync/status` | researcher（`:721`） |
| 7 | `pages/DataCenter/index.tsx:392,405` | `GET /datacenter/sync/auto` | researcher（`:750`） |
| 8 | `pages/DataCenter/index.tsx:463` | `POST /datacenter/sync` | researcher（`:677`） |
| 9 | `pages/DataCenter/index.tsx:474` | `POST /datacenter/sync/cancel` | researcher（`:776`） |
| 10 | `pages/DataCenter/index.tsx:481` | `POST /datacenter/sync/fetch` | researcher（`:927`） |
| 11 | `pages/DataCenter/index.tsx:494` | `POST /datacenter/sync/auto` | researcher（`:763`） |
| 12 | `pages/DataCenter/TrainPanel.tsx:44`（裸 URL 轮询） | `GET /datacenter/train/status` | researcher（`:1149-1152`） |
| 13 | `pages/DataCenter/TrainPanel.tsx:76,86` | `POST /datacenter/train/start` / `train/cancel` | researcher（`:1135`/`:1162`） |
| 14 | `pages/DataCenter/TextDataPanel.tsx:70,114,165` | `POST /datacenter/text/import` / `text/build-factor` / `mirror/rebuild` | researcher（`:1041`/`:1053`/`:1087`） |
| 15 | `pages/Report/index.tsx:89` | `POST /report/daily/generate` | researcher（`report.py:469`） |
| 16 | `pages/Screener/index.tsx:341` → `:202`；`pages/Portfolio/index.tsx:389` → `:251` | `GET /export/screener`；`POST /portfolio/backtest` | researcher（`export.py:23`；`portfolio.py:107`） |

> 说明：5~14 全部落在 **viewer 可进** 的 `/data` 页，因此该页对 viewer 而言几乎只有 `datasets`/`quality`/`text-status`/`mirror-status`/`task-stats` 五块可用，其余面板与按钮全部报错——建议要么把 `/data` 提升为 researcher 路由，要么在页内分角色渲染（二者都必须做其一，当前状态是“页面看起来能用、点下去就报错”）。

---

## 5. 已核查且**未发现问题**的部分（避免重复劳动）

1. **路径/方法**：111/111 命中，0 处不存在端点（3.1）。
2. **请求参数**：逐字段核对全部一致。已核实一致的模型↔类型对：`RuleIn`↔`AlertRulePayload`（含 `RULE_TYPES` 六类与 `_PARAM_KEYS` 白名单，`alerts.py:40-52` ↔ `pages/Alerts/index.tsx:19-40`）、`SyncRequest`/`AutoSyncRequest`/`FetchRequest`/`TextImportRequest`/`TrainStartRequest`、`LoginRequest`/`RegisterRequest`、`PortfolioBacktestRequest`（`portfolio.py:27-55` ↔ `types/portfolio.ts:20-34`）、desk 的 `KillSwitchRequest`/`ExclusionAddRequest`/`ExclusionToggleRequest`/`OrderRequest`/`AttributionRequest`（`desk.py` ↔ `api/production.ts:226-264`）、`researchApi` 全部 9 个 POST 请求体（`api/research.ts`）、`strategyBacktestApi.run` 的 `optimize_params/optimize_method/walk_forward/wf_folds`、`exportApi` 两个导出体。
3. **响应字段（结构性一致）**：`/screener/stocks`（`screener.py:639-657` ↔ `types/p1.ts:100-125`，含 `score` 非空、分页 `total/page/page_size`、无 `total_pages` 漂移）、`stats.today` 七项（`data/screening.py:272-282` ↔ 前端）、`/etf/list`（`etf.py:306-310` ↔ `types/etf.ts:80-95`）、`/etf/detail/{code}` 九块（`etf.py:742-759` ↔ `types/etf.ts:253-269`）、`/datacenter/overview`（`datacenter.py:444-459` ↔ `types/datacenter.ts:5-24`）、`/datacenter/sync/status`（`services/sync_service.py:102-120`，多出的 `started_at`/`failed_count` 无害）、`/datacenter/task-stats`、`/stock/search`（裸数组 ↔ `SearchHit[]`）、`/watchlist/correlation`、`/desk/capacity`（`domain/attribution.py:135-142` 的 `aum_threshold`/`aum_yi`/`formula` ↔ `api/production.ts:252`）、`/settings`（`:163-164` 含 `system` ↔ `SettingsBundle`）、`/alerts/*`、`/notify/recent`。
4. **`data.status` 三态**：`degraded`/`unavailable`/`ok` 在前端类型与页面判定中一致（`OverviewCards.tsx:124` 等）。唯一的 `total==0 → status:"ok"`（`screener.py:305-310`）属 **2026-09-14 已裁定并落测试** 的既有设计（`docs/audit-2026-09-14/data-freshness-degradation-fix-report.md`、`backend/tests/test_data_freshness_degradation.py`），**不作为新问题**。
5. **`client.ts` 写操作重复执行风险**：**不存在**。`client.ts` 无任何重试逻辑；重试只出现在 SWR 的 GET 读路径（`api/swr.ts` `errorRetryCount: 2`），不覆盖 POST/PUT/DELETE。信封解析、401 清会话、`40900` 友好文案均集中且正确，无调用点绕过。
6. **`turnover` 单位嫌疑已排除**：`data/screening.py:236` 的 `bar["turnover"]*100` 是**必要**换算——实测 `data/parquet/daily_bar/.../year=2025.snappy.parquet` 的 `turnover` 中位数 `0.005045`（小数口径），前端 `Screener/index.tsx:606` 渲染 `.toFixed(2)%` 正确。同文件 `:245` 注释「换手率为小数口径」与代码不符，但**仅为注释陈旧，无功能性影响**，按简报「不报注释问题」不列为缺陷。
7. `datacenterApi.mirrorStatus` 45s / `overview` 60s / `settingsApi.all` 60s / `researchApi.*` 120~180s 的超时放宽与后端实测耗时匹配，非缺陷。

---

## 6. 复现命令

```powershell
# 端点清单（AST + 运行时内省，两侧均应输出 114）
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\dump_backend.py" "$env:TEMP\aqp_p4\backend_routes.json"
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\runtime_routes.py" "$env:TEMP\aqp_p4\runtime_routes.json"

# 前端调用清单（111 处）与对账
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\dump_frontend.py" "$env:TEMP\aqp_p4\frontend_calls.json"
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\reconcile.py" "$env:TEMP\aqp_p4\reconcile.json"

# 权限矩阵运行时实证（只发 GET，无写操作）
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\probe_roles.py"
& backend\.venv\Scripts\python.exe "$env:TEMP\aqp_p4\probe_public.py"

# 关键静态证据
rg -n "nav_tail" backend\app            # 0 命中（前端 types/p1.ts:169 + TopKPanel:112 有）
rg -n "alerts/health" frontend\src      # 0 命中
rg -n "updateRule" frontend\src         # 仅 api/alerts.ts:64 定义
rg -n "sanitizeApiMessage" frontend\src # 仅 client.ts:41,62
```

## 7. 未覆盖 / 留给后续

- 未做浏览器端到端验证（无 GUI 会话），所有前端结论均为静态代码路径 + 类型层证据；涉及「渲染结果」的判断（如降级横幅缺失）依据的是「类型未声明 + 页面 0 引用」的静态事实，属**确定**级。
- `/webhook` 渠道的真实投递、`/export/*` 的 Excel 内容正确性、SSE 心跳在实际代理下的行为，均超本轮范围。
- 建议父审计员把 3.2 的 8 条并入死代码批次，3.3 的字段清单并入「伪契约/未接线功能」批次。

## 8. 逐文件结论（本轮实际逐行核对过的文件）

| 文件 | 结论 |
|---|---|
| `frontend/src/api/client.ts` | 信封解包/401 清会话/无写重试均正确；`code=40300` 无分支 → 英文 `FORBIDDEN` 直出（表 8）；非信封放行有副作用（表 15） |
| `frontend/src/api/swr.ts` | 未发现 P0–P2 问题（重试仅作用于 GET 读路径） |
| `frontend/src/api/research.ts` | 未发现 P0–P2 问题（9 个请求体与 `research.py` 模型逐字段一致） |
| `frontend/src/api/strategyBacktest.ts` | 未发现 P0–P2 问题（寻优/walk-forward 字段齐备） |
| `frontend/src/api/settings.ts` | 未发现 P0–P2 问题（类型与 `/settings` 一致）；`:63-65` 注释暴露的是后端缓存击穿（表 16） |
| `frontend/src/api/datacenter.ts` | `task`/`trainStatus` 两个包装为死代码（表 17）；其余 URL/参数/类型一致 |
| `frontend/src/api/market.ts` | `overview`/`quotes` 为死包装，导致两条端点无消费者（表 17） |
| `frontend/src/api/alerts.ts` | `updateRule` 死包装（表 17），其余一致 |
| `frontend/src/pages/DataCenter/*` | **P1**：viewer 必错簇（表 1）+ 静默降级；`TrainPanel.tsx` 消费无鉴权端点 |
| `frontend/src/pages/Settings/index.tsx` | **P1**：3 个无角色门按钮 + 静默连接测试（表 2、3） |
| `frontend/src/pages/Backtest/TopKPanel.tsx` | **P1/P2**：`nav_tail` 永不渲染（表 13）；8 组返回字段无消费者、无曲线（表 14） |
| `frontend/src/pages/Report/index.tsx` | **P1**：viewer 可见 researcher 按钮（表 4，注释已知情） |
| `frontend/src/pages/Screener/index.tsx` | **P1**：导出按钮 viewer 必错（表 5）；榜单渲染与 `stats.today` 完全一致 |
| `frontend/src/pages/Portfolio/index.tsx` | **P1**：回测按钮 viewer 必错（表 6） |
| `frontend/src/pages/Watchlist/index.tsx` | **P2**：降级态无消费者（表 12）；其余字段一致 |
| `frontend/src/pages/Alerts/index.tsx` | 未发现 P0–P2 问题（规则类型/参数白名单/渠道与后端逐项一致） |
| `frontend/src/types/api.ts` | `ERR.FORBIDDEN` 定义后零引用（表 8） |
| `frontend/src/types/p1.ts` | `nav_tail`（表 13）、`basis`/`basis_fields`（表 18）、Top-K 曲线字段（表 14）三处伪契约 |
| `frontend/src/types/watchlist.ts` | 缺 `status`/`reason`/`flow_status`（表 12） |
| `backend/app/core/auth.py` | 角色表正确；`FORBIDDEN` 无中文文案（表 8）；`GET /datacenter/train/readiness` 缺 `require_role`（表 7，属 datacenter.py） |
| `backend/app/api/v1/datacenter.py` | **P1**：`train/readiness` 无鉴权（表 7）；4 个越界错误码（表 9、11）；researcher 端点被 viewer 页调用（表 1） |
| `backend/app/api/v1/app_settings.py` | **P1**：`:157` 缓存击穿（表 16）；admin/researcher 端点无前端角色门（表 2、3）；`apikeys/rotate` 死端点 |
| `backend/app/api/v1/backtest.py` | **P1/P2**：`nav_tail` 缺失（表 13）、披露字段无消费者（表 14）、8 个未注册码（表 10） |
| `backend/app/api/v1/watchlist.py` | **P2**：降级态字段无消费者（表 12） |
| `backend/app/api/v1/screener.py` | 未发现 P0–P2 问题（`_finalize_screener_payload` 的 `total==0→ok` 为 2026-09-14 已裁定设计）；`basis`/`basis_fields` 无消费者（表 18） |
| `backend/app/api/v1/portfolio.py` | 未发现契约问题（表 6 的属性是前端缺角色门） |
| `backend/app/api/v1/etf.py` | 未发现 P0–P2 问题（列表/详情块字段逐项一致） |
| `backend/app/api/v1/notify.py` | 未发现 P0–P2 问题；`quotes`/`alerts` SSE 频道无前端订阅（表 17） |
| `backend/app/api/v1/market.py` | 公开端点与「市场概览唯一公开页」一致；`overview`/`quotes` 无消费者（表 17）；`kind:"platform"` 无消费者（表 18） |
| `backend/app/api/v1/export.py` / `report.py` | 端点本身未发现问题；被 viewer 页无条件调用（表 4、5） |
| `backend/app/api/v1/desk.py` / `research.py` / `alerts.py` | 请求/响应契约逐字段一致；`alerts/health` 与 `PUT /alerts/rules/{id}` 无前端调用 |
| `backend/app/api/v1/router.py`、`stock.py`、`monitor.py`、`studio.py`、`ops.py` 及其余模块 | 仅经自动化清单覆盖（端点级：路径/方法/角色/参数名，两侧 114 条交叉验证一致）；未逐行核对，**不作结论**，如需可交后续批次 |