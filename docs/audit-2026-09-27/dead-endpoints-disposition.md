# 死端点处置报告（2026-09-27）

> 背景：2026-09-27 审计发现 6 个「死端点」（后端已实现、前端无任何入口）。
> `PUT /api/v1/alerts/rules/{id}` 已在本轮修复（预警规则编辑 UI 上线），不在本报告范围。
> 本报告处置剩余 5 个端点：逐个评估「独立业务价值 / 是否与已接入端点重复」，
> 给出「补前端入口」或「建议后端下线」的结论，并实施高价值项。
>
> 负责人：寇豆码（kou-dead-endpoints）｜范围：仅前端（`frontend/src/**`），未改 `backend/`。

---

## 一、结论速览

| # | 端点 | 独立业务价值 | 是否冗余 | 结论 | 实施 |
|---|---|---|---|---|---|
| 1 | `GET /api/v1/alerts/health` | 高 | 否 | **补 UI** | ✅ 预警中心顶部健康横幅 + 状态芯片 |
| 2 | `POST /api/v1/settings/apikeys/rotate` | 无（故意返回失败） | — | **建议后端下线** | ❌ 不补 UI |
| 3 | `GET /api/v1/datacenter/sync/tasks/{task_id}` | 中 | 否 | **补 UI** | ✅ 数据中心「最近任务详情」弹窗 |
| 4 | `GET /api/v1/market/overview` | 低 | 是（被 rt+daily 覆盖） | **建议后端下线** | ❌ 不补 UI |
| 5 | `GET /api/v1/market/quotes` | 中 | 部分（自选/选股已覆盖） | **补 UI** | ✅ 个股详情页「实时价」徽标 |

实施 3 处（#1 / #3 / #5），建议下线 2 处（#2 / #4）。

---

## 二、逐个评估

### #1 `GET /api/v1/alerts/health` —— 补 UI（高价值）

- **返回什么**：`score_topk` 数据源健康快照
  `{degraded, reason, detail, snapshot_date, checked_at, last_ok_at}`
  （`backend/app/api/v1/alerts.py:314` 的 `_TOPK_HEALTH`，经 `:134` 端点暴露）。
- **独立业务价值：高**。`score_topk`（Top-K 迁移）规则在**股票池快照缺失**时会跳过本轮判定——
  「既不触发、也不报错、也不告警」地**静默失效**（见 `alerts.py:305-350` 的修复注释）。
  该端点是这一失效状态**唯一的前端可见入口**；没有它，用户会以为预警在正常值守。
- **是否重复**：否。前端 `alertsApi` 原先只有 rules / events 两组，无任何健康查询。
- **交互形态**：预警中心（`/alerts`）页
  - 顶部状态芯片：`checked_at` 非空且未降级 → 绿色「数据源正常」（title 含最近检查/正常时间、快照日期）；
    `checked_at` 为空 → 中性「数据源未评估」（**不把「还没跑过」当成「正常」**）。
  - 降级横幅（红色）：明示「Top-K 迁移规则本轮已被跳过，不会触发」+ 原因/快照日期/检查时间/明细。
  - 不可读横幅（琥珀色）：健康状态读取失败时显式提示并提供「重试」，**不静默隐藏**。
  - 与既有 30s 触发历史轮询同频刷新健康态（降级可能随时发生/恢复）。

### #2 `POST /api/v1/settings/apikeys/rotate` —— 建议后端下线

- **返回什么**：**恒为失败** —— `ERR_PARAMS`「API Key 功能未启用：平台当前不验证此类密钥，
  不能生成可用凭证」（`backend/app/api/v1/app_settings.py:257-266`，注释说明是**保留的兼容路由**）。
- **独立业务价值：无**。它不是「已实现但没入口」，而是**故意禁用**的历史入口：历史实现只把掩码
  写入用户偏好，任何认证链路都不校验明文，返回「成功」会误导用户以为密钥可用于鉴权。
- **是否重复**：不适用（无功能可比）。
- **结论：建议后端下线**（或保留为显式 `410 Gone` 语义的兼容路由）。**不补 UI**——补了只会让用户
  点出一个报错，误导性比没有入口更强。

### #3 `GET /api/v1/datacenter/sync/tasks/{task_id}` —— 补 UI（中价值）

- **返回什么**：`task_store`（SQLite）持久化的任务记录
  `{task_id, task_type, status, progress, result, error_message, created_at, started_at, finished_at, ...}`
  （`backend/app/api/v1/datacenter.py:868`）。
- **独立业务价值：中**。后端 docstring 明确「供页面刷新后恢复查看」。前端现状：
  只轮询**内存态** `/datacenter/sync/status`，页面刷新/进程重启后进度归零；
  且 `startSync` 拿到的 `task_id` 被直接丢弃，无任何入口回查。
- **是否重复**：否，与 `/sync/status` 互补——后者是「当前进程内实时进度」，前者是「持久化的
  任务终态（含成功/失败/取消与错误信息）」。实测 `/sync/status` 在进程重启后无法还原历史任务。
- **交互形态**：数据中心（`/data`）「同步控制」卡内
  - 触发同步时把返回的 `task_id` 写入 `localStorage`（键 `aqp.lastSyncTaskId`），刷新后仍可恢复入口。
  - 卡片底部「最近任务 <id>」一行：`查看详情` 打开弹窗（status 中文+配色、进度、创建/完成时间(UTC)、
    错误信息、result JSON），`清除` 仅移除本地记录（后端历史保留）。
  - 弹窗内含 loading / 错误（红框 + 重试）/ 空态，**查询失败不显示成「无此任务」**。
- **已知边界**：自定义抓取 `POST /datacenter/sync/fetch` 的响应**不含** `task_id`，故抓取任务不入
  本地记录（非本端点缺陷，仅记录）。

### #4 `GET /api/v1/market/overview` —— 建议后端下线

- **返回什么**：市场概览**聚合**接口（indices/heat/money_flow/anomalies/sectors/recommend/ai_stats/
  sentiment 一次返回；`backend/app/api/v1/market.py:890`）。
- **独立业务价值：低**。前端 `MarketOverview` 页已迁移到**拆分后**的
  `/market/overview/rt`（实时块）+ `/market/overview/daily`（日频块）两个端点
  （`frontend/src/pages/MarketOverview/index.tsx:45,52`），合并视图在前端完成；`marketApi.overview`
  虽保留但**无任何页面调用**。
- **是否重复**：**是**，功能被 rt + daily 完全覆盖。且该兼容端点在 6s 服务端预算下
  （`OVERVIEW_COMPAT_BUILD_TIMEOUT_SECONDS = 6.0`）冷路径**必然**超时并返回 `degraded` 载荷
  （`market.py:897+` 注释已说明），可用性反而劣于拆分端点。
- **结论：建议后端下线**（若需保留给外部 API 消费方，应标注 `deprecated`）。**不补 UI**。

### #5 `GET /api/v1/market/quotes` —— 补 UI（中价值）

- **返回什么**：批量实时行情快照（≤200 只；腾讯→新浪→`degraded` 降级链；
  `market.py:675`，底层 `core/quotes_hub.quotes_snapshot`）。
- **独立业务价值：中**。底层能力 `quotes_snapshot` 已被 `/watchlist/dashboard`、`/screener`、
  notify SSE 频道复用，但**个股详情页头部「现价」取的是日线收盘价**
  （`StockDetail` 的 `latest.close` / 筹码块 `current_price`），**盘中是过期的**——这是真实缺口。
- **是否重复**：部分。自选页/选股中心已有实时价（走各自聚合端点），但**个股详情页没有**；
  本端点恰好补齐单标的口径。
- **交互形态**：个股详情页（`/stock/:symbol`）头部价格旁「实时价」徽标
  - 盘中（周一~五 09:15–15:05）每 15s 轮询（与后端 `QUOTES_TTL=15s` 同频，命中进程缓存）；
    休市不轮询，仅取最近快照并标注「（休市）」。
  - 展示 `实时 <价> <涨跌幅>`，配色随涨跌；title 披露数据源/`as_of`/「仅展示不写入日线」。
  - 降级（`source=degraded`）或空数组 → 「实时行情不可用」；请求失败 → 「实时行情加载失败 · 点击重试」。
    三种异常态均**可见**，不用日线价冒充实时价。

---

## 三、实施清单（仅前端）

| 文件 | 改动 |
|---|---|
| `frontend/src/api/alerts.ts` | 新增 `AlertHealth` 类型与 `alertsApi.health()` |
| `frontend/src/pages/Alerts/index.tsx` | 健康态加载 + 30s 同频刷新；顶部状态芯片；降级/不可读横幅（含重试） |
| `frontend/src/api/datacenter.ts` | 新增并导出 `SyncTaskDetail` 类型；`task()` 改用该类型 |
| `frontend/src/pages/DataCenter/index.tsx` | 记录 `task_id` 到 localStorage；「最近任务」行 + 详情弹窗（loading/error/空态） |
| `frontend/src/pages/StockDetail/index.tsx` | 新增 `LiveQuoteChip`（盘中轮询 / 休市不轮询 / 降级与失败显式提示） |

**未改动**：`backend/**`（另两位工程师在改 `ops.py` 与磁盘清理）、测试文件、既有 UI 组件用法与代码风格。

### 自证

- `npx tsc -b --force` → **EXIT=0**
- `npx vite build --outDir dist-deadendpoints-verify` → **EXIT=0**（全新 outDir，规避 `node-safe-delete` 护栏）
- 后端（127.0.0.1:8000，`ADMIN_TOKEN` 直连）实测：
  - `GET /alerts/health` → `code=0`，`{degraded:false, checked_at:null}`（当前评估轮次尚未运行，
    故 UI 显「数据源未评估」而非「正常」）
  - `GET /market/quotes?symbols=600519.SH,000001.SZ` → `code=0`，`source=tencent`，含真实价格/涨跌幅
  - `GET /datacenter/sync/tasks/2b411428...` → `code=0`，`status=cancelled`、`progress={done:983,total:2499}`；
    不存在 id → `code=40400 任务不存在`（UI 按错误态展示，不当作空态）
  - `POST /settings/apikeys/rotate` → `code=40000 API Key 功能未启用`（印证 #2 为禁用桩）
- **未做**：Chromium 端到端实测——本机未安装 playwright（`node_modules/.bin` 无 playwright，
  Python 侧亦无），未强行引入新依赖。故渲染层验证止于 `tsc` + 生产构建通过 + 后端接口直连实测。

---

## 四、后续建议（不属本次实施）

1. **#2 / #4 后端下线**：建议由后端负责人评估移除（#4 若保留给外部消费方则标 `deprecated`）。
   本次**未删任何后端代码**，仅在此标注建议。
2. **实时行情的统一**：`/market/quotes`、`/watchlist/dashboard`、`/screener` 各自拉实时价，
   口径与缓存已由 `quotes_hub` 统一，但前端展示口径分散；后续可考虑收敛为共享 hook。
3. **`/datacenter/sync/fetch` 补 `task_id`**（后端）：当前抓取任务无法持久化回查，建议统一返回
   `task_id`，让「最近任务详情」覆盖全部同步类任务。**仅建议，本次未改后端。**
