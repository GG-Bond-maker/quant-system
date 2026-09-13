# AQP 前后端一致性审查报告

> 审查时间：2026-09-02  
> 审查范围：`frontend/`（React + TypeScript）与 `backend/app/api/v1/`（FastAPI）  
> 方法：路由/导航对照、API 客户端与后端端点逐一对照、页面数据流追踪、缓存与进程内状态排查

---

## 一、总体结论

| 维度 | 结论 |
|------|------|
| 导航功能覆盖率 | **高**：侧边栏 16 个入口均有对应页面与路由，无 `disabled` 占位项 |
| 前后端 API 对齐 | **整体良好**：主流程页面均调用真实后端；类型定义与响应字段大体一致 |
| 死数据 / 假数据 | **少量且大多有降级说明**；未发现大面积硬编码图表数据 |
| 未接线后端能力 | **存在 2 条回测相关 API、1 条搜索能力、若干设置项未落地** |
| 缓存与内存 | **设计较完整**（Redis + LRU + TTL + 后台预热）；长任务与多实例部署仍有风险点 |

**一句话**：平台核心链路（市场概览、选股、ETF、策略回测、组合回测、研究、因子工作室、执行中心、数据中心、运维看板）已打通；主要缺口集中在 **Top-K 模型回测 UI**、**信号分析**、**全局搜索**、**用户偏好未驱动刷新频率**、**侧边栏/顶栏体验不一致**。

---

## 二、功能模块对照表

### 2.1 前端导航 ↔ 页面 ↔ 后端 API

| 导航 | 路由 | 主要 API 模块 | 实现状态 |
|------|------|---------------|----------|
| 市场概览 | `/` | `GET /market/overview` | ✅ 已实现；盘中 5s 轮询 |
| 个股分析 | `/stock/:symbol` | `GET /stock/{symbol}/*` | ✅ 已实现；面板分块降级 |
| 选股中心 | `/screener` | `GET /screener`、`/screener/watchlist`、`/export/screener` | ✅ 已实现 |
| ETF 中心 | `/etf` | `GET /etf/*` | ✅ 已实现 |
| ETF 分析 | `/etf/:code` | `GET /etf/detail/{code}` | ✅ 已实现 |
| 策略回测 | `/backtest` | `POST /backtest/strategy-run`、`/export/strategy-backtest` | ✅ 已实现（趋势跟踪 MA 策略） |
| 组合回测 | `/portfolio` | `POST /portfolio/backtest`、`GET /portfolio/search` | ✅ 已实现 |
| 策略研究 | `/research` | `POST/GET /research/*`（四象限 10+ 端点） | ✅ 已实现 |
| 因子工作室 | `/studio` | `POST/GET /studio/mining/*`、`/studio/alpha-eval` | ✅ 已实现 |
| 执行中心 | `/desk` | `GET/POST /desk/*` | ✅ 已实现 |
| 容量与归因 | `/capacity` | `GET /desk/capacity`、`POST /desk/attribution` | ✅ 已实现 |
| 数据中心 | `/data` | `GET/POST /datacenter/*` | ✅ 已实现 |
| 数据质量 | `/dataquality` | `POST /ops/quality-scan`、`GET /ops/lineage` | ✅ 已实现 |
| 任务调度 | `/pipeline` | `GET /ops/dag`、`POST /ops/dag/rerun` | ✅ 已实现 |
| 我的收藏 | `/watchlist` | `GET /watchlist/dashboard`、`/watchlist/correlation` | ✅ 已实现 |
| 系统设置 | `/settings` | `GET/PUT/POST /settings/*` | ✅ 已实现（需登录） |

### 2.2 后端已实现但前端无页面 / 无调用的 API

| 端点 | 说明 | 影响 |
|------|------|------|
| `POST /api/v1/backtest/run` | Top-K 模型信号回测（含摩擦、权重方案、Deflated Sharpe 等） | 仅 `exportApi.backtest()` 预留，**无 UI** |
| `POST /api/v1/backtest/signal-analysis` | 信号 IC 衰减 / 分层多空分析 | **完全无前端调用** |
| `GET /api/v1/auth/me` | 校验 Token 并拉取用户信息 | 定义在 `authApi.me`，**应用启动未调用** |
| `GET /health`、`/metrics`、`/health/*` | 运维探针 | 合理：供部署监控，不需前端 |

### 2.3 前端 API 客户端与后端路径一致性

抽查全部 `frontend/src/api/*.ts`：**路径、HTTP 方法、参数名与后端路由一致**，未发现明显 404 级错配。

已知历史问题已在代码注释中修复：
- `screener.ts` 曾误挂 `/backtest/run`，已删除
- `export.ts` 已接线选股与策略回测导出

---

## 三、前后端不匹配与不合理现象

### 3.1 【中】两套「自选行情」API 并存，能力重叠

| API | 使用页面 | 能力 |
|-----|----------|------|
| `GET /screener/watchlist` | 选股中心右栏 | 名称/行业/收盘/涨跌/预测分 |
| `GET /watchlist/dashboard` | 我的收藏 | 上述 + K 线形态/预警/迷你 K 线/资金流汇总 |

**现象**：同一「自选」概念在两个页面走不同接口，字段与刷新逻辑不一致。  
**建议**：收藏页统一走 `/watchlist/dashboard`；选股页右栏简表可改为 dashboard 的子集或废弃 screener/watchlist。

### 3.2 【中】顶栏搜索未对接 `GET /stock/search`

当前逻辑（`Topbar.tsx`）：
- 6 位数字 → ETF 详情
- `XXXXXX.SH/SZ` → 个股详情
- 其他 → 跳转 ETF 列表（**不做名称模糊搜索**）

后端 `GET /stock/search` 与 `GET /portfolio/search` 均已实现，顶栏 placeholder 写「搜索 ETF 代码 / 名称」但**名称搜索未生效**。

### 3.3 【中】顶栏「星标」按钮跳转错误

- 按钮 `title="自选收藏"`，实际 `navigate('/etf')`
- 自选收藏页面在 `/watchlist`

属于 UX 与文案不一致，易造成用户困惑。

### 3.4 【中】侧边栏用户区仍为写死数据

- `Topbar` 已对接 `useAuthStore`（用户名、角色、退出）
- `Sidebar` 底部固定显示 **「Hello, Quant Pro」**，与登录态脱节

### 3.5 【中】系统设置中的偏好项未全局生效

| 设置项 | 后端持久化 | 前端消费 |
|--------|------------|----------|
| `refresh_freq`（默认 3s） | ✅ SQLite | ❌ 市场概览写死 5s；收藏 60s；执行中心 8s |
| `notify_backtest` | ✅ | ❌ 无任何通知逻辑 |
| `theme` / `language` | ✅ | ✅ 仅设置页生效 |

用户修改「刷新频率」后，其他页面行为不变，属于**配置与行为脱节**。

### 3.6 【中】鉴权策略前后不一致

- 仅 `/settings` 使用 `RequireAuth` 路由守卫
- `desk`（下单/熔断/禁买池）、`ops`（DAG 重跑）、`datacenter/sync` 等**写操作后端未强制 `require_role`**
- 未登录用户可直接操作模拟盘与数据同步（若部署在公网存在风险）

### 3.7 【低】策略回测前后端能力不对等

后端 `POST /backtest/strategy-run` 支持：
- `optimize_params` / `optimize_method` 参数寻优
- `strategy_type` 多策略类型
- `slippage_bps` 等扩展字段

前端 `strategyBacktest.ts` 与 `Backtest/parts.tsx` **仅暴露 MA 交叉 + 移动止损**，未提供寻优 UI；类型定义也未覆盖扩展字段。当前不算 bug，但属于**后端能力未完全暴露**。

### 3.8 【低】容量评估页参数不可调

`GET /desk/capacity` 支持 `participation_cap`、`holdings`、`rebalance_per_year` 查询参数。  
`CapacityAttribution` 页面仅调用默认值，用户无法交互调整假设。

### 3.9 【低】选股 `strategy` 参数

后端仅支持 `alpha_basic_v1`；前端未传 `strategy`（使用默认）。因目前只有一种策略，**无功能缺失**；若后续增加模型版本，需在筛选器增加策略选择器。

### 3.10 【低】通知铃铛为纯装饰

顶栏铃铛带红点，无点击行为、无后端通知接口，属于**死 UI 元素**。

### 3.11 【低】数据同步双入口

- 数据中心：`POST /datacenter/sync`
- 系统设置：`POST /settings/data/sync`（内部复用 datacenter 任务）

功能重复但无害；建议在 UI 文案中说明二者等价，避免用户困惑。

---

## 四、「死数据」与降级数据说明

### 4.1 不属于死数据（合理设计）

| 位置 | 说明 |
|------|------|
| `StockDetail` `RowPlaceholder` | 某面板块 `status !== 'ok'` 时显示「—」，数据来自 `/stock/{symbol}/panels` 分块降级 |
| `DistributionCharts` | 注释明确不做假数据填充 |
| `DataQuality` 血缘图 | 拓扑 `topology_source: static` 为代码声明；节点状态为实扫结果（已在 `production.ts` 类型中标注） |
| 各页 `DEFAULT_ASSETS` | 预填示例标的，提交后走真实计算 |
| `Watchlist` SEED | 首次访问 localStorage 种子组合，用户可删改 |

### 4.2 需关注的「局部不可用」数据

| 场景 | 表现 | 根因 |
|------|------|------|
| 个股资金流向/北向/内外盘 | 单行占位「—」 | 外部源失败或该标的无数据 |
| 因子工作室任务历史 | localStorage 存 task_id，服务重启后 404 | 后端 GP 任务在进程内存，**重启即丢失**（前后端均有提示） |
| 自选分组 | 仅存浏览器 localStorage | 换设备/清缓存即丢失，**无后端持久化** |

### 4.3 已消除的历史死代码

- `screener.ts` 中未使用的 `backtestApi.run` 已删除
- `export/screener`、`export/strategy-backtest` 已在选股页、回测页接线

---

## 五、缓存与内存问题分析

### 5.1 后端缓存架构（摘要）

```
请求 → Redis（优先）→ 进程内 LRU（memory.py，maxsize=100_000）→ 实时计算
```

| 模块 | 机制 | TTL / 备注 |
|------|------|------------|
| `market/overview` | Redis + LRU；`lifespan` 每 240s 后台预热 | 300s |
| `screener` | Redis | 按 date+strategy+top_k+board 键 |
| `backtest/*` | Redis | strategy-run 600s |
| `stock/panels` | Redis 分块缓存 | 块级 TTL（quote 5min 等） |
| `research` | `_FEAT_CACHE` 进程内 | 600s；按 `features:{days}` 键 |
| `datacenter` | 模块级 `_cache` dict | overview/quality 等 |
| `data/etf.py` | 进程级 `_cache` | 列表/K 线/资金流分 TTL |
| `calendar_store` | 启动预加载 | 常驻内存 |

### 5.2 内存风险点

| 风险 | 严重度 | 说明 |
|------|--------|------|
| `studio` 挖掘启动 `_load_snapshot()` | **高（瞬时）** | 每次 `mining/start` 读取并 concat 全部 features parquet → pandas，大内存峰值 |
| `gp_miner._TASKS` | 中 | 最多保留 20 个任务状态；单任务 `history` 列表随代数增长 |
| `datacenter._sync.completed` | 中 | 全市场同步时 set 可达数千 symbol；进程重启丢失 |
| `memory.LRUCache(100_000)` | 中 | 无 Redis 时大量不同 key 可能占用较多内存（单值可能很大，如 market overview JSON） |
| `research._FEAT_CACHE` | 低 | 无 LRU 上限，仅按 horizon key 少量条目 |
| `datacenter._cache` | 低 | 无容量上限，键数量有限 |

### 5.3 前端轮询与资源

| 页面 | 间隔 | 清理 |
|------|------|------|
| 市场概览 | 盘中 5s | ✅ `clearInterval` |
| 我的收藏 | 60s | ✅ |
| 执行中心 | 8s（可选） | ✅ |
| 因子工作室 | 挖掘中 2s | ✅ unmount 清理 |
| 数据中心 | 同步中轮询 status | ✅ |

**问题**：市场概览 5s 全量 `overview` 在 Redis 未命中时后端重建代价高（注释称可达 ~48s）。虽有后台预热，多用户同时访问仍可能触发惊群。

### 5.4 多实例部署注意

以下状态为**单进程内存**，多 worker / 多 Pod 不共享：

- GP 挖掘任务（`gp_miner._TASKS`）
- 数据同步进度与断点续传（`datacenter._sync`）
- 部分进程内 TTL 缓存

生产环境建议：**单 worker 跑同步与 GP**，或引入 Redis/DB 任务队列。

---

## 六、各页面实现完成度速查

| 页面 | 后端反馈 | 导出/联动 | 备注 |
|------|----------|-----------|------|
| 市场概览 | ✅ 分块降级 + `from_cache` | — | AI 推荐、板块、资金流依赖本地数据管线 |
| 个股分析 | ✅ panels 分块 | — | 搜索框仅跳转，未调 search API |
| 选股中心 | ✅ | ✅ Excel 导出 | 自选走 screener/watchlist |
| ETF 中心/详情 | ✅ | — | 外部源失败时块级降级 |
| 策略回测 | ✅ | ✅ Excel | 未覆盖 run/signal-analysis |
| 组合回测 | ✅ | — | 支持从收藏 import |
| 策略研究 | ✅ 四象限全接线 | — | 计算耗时，已设长超时 |
| 因子工作室 | ✅ | — | 任务重启丢失已处理 |
| 执行中心 | ✅ | — | screenCandidates 已用于禁买池扫描 |
| 容量与归因 | ✅ | — | 容量参数未暴露 |
| 数据中心 | ✅ | — | autoSync 已迁后端调度 |
| 数据质量 | ✅ | — | 扫描可指定 dataset/year |
| 任务调度 | ✅ | DAG 重跑 | — |
| 我的收藏 | ✅ | CSV、组合导入、相关性 | 分组仅 localStorage |
| 系统设置 | ✅ | 缓存清理/备份/同步 | 需登录 |

---

## 七、优化建议（按优先级）

### P0 — 安全与正确性 ✅（2026-09-02 已落地）

1. **写操作鉴权统一** ✅  
   `desk` / `ops` / `datacenter`（sync 写端点）/ `studio` 的 POST 已加 `require_role("researcher")`；前端 `/desk`、`/pipeline`、`/studio` 使用 `RequireRole` 守卫。

2. **顶栏搜索对接真实搜索 API** ✅  
   `Topbar` 防抖调用 `stock/search` + `etf/list`，下拉选择跳转。

3. **修复顶栏星标跳转** ✅  
   星标按钮改为 `navigate('/watchlist')`。

4. **侧边栏用户区同步登录态** ✅  
   `Sidebar` 复用 `useAuthStore`，与 Topbar 一致。

### P1 — 体验与一致性

5. **全局消费 `refresh_freq`**  
   抽 `useRefreshInterval()` hook，市场概览/收藏/执行中心读取设置（或默认值），保存设置后广播更新。

6. **合并自选行情 API**  
   选股页右栏改用 `/watchlist/dashboard` 或抽取共享 hook，减少重复请求与字段差异。

7. **新增 Top-K 模型回测页或并入选股/研究**  
   接线 `POST /backtest/run` + `export/backtest`；与现有「趋势跟踪」策略回测区分文案。

8. **信号分析最小 UI**  
   在策略研究或回测页增加 Tab，调用 `POST /backtest/signal-analysis`。

9. **容量页暴露假设参数**  
   滑块调整 `participation_cap`、`holdings`、`rebalance_per_year` 后调用 `deskApi.capacity(...)`。

10. **应用启动调用 `authApi.me`**  
    Token 有效时刷新用户信息；无效则清会话，避免过期 Token 仅本地判断。

### P2 — 性能与运维

11. **market overview 前端降频或 ETag**  
    盘中轮询可改为 10–15s，或后端支持 `If-None-Match` / 轻量 delta 接口。

12. **studio 挖掘快照加载优化**  
    按日期分区增量读取 features，或 mmap；避免每次 start 全量 concat。

13. **`research._FEAT_CACHE` 增加 LRU 或 maxsize**  
    防止长期运行多 horizon 键堆积。

14. **GP / 同步任务持久化**  
    task_id、进度、completed 写入 SQLite 或 Redis，支持重启续跑与多实例。

15. **通知铃铛**  
    要么接入 WebSocket/SSE 推送（回测完成、同步完成），要么移除红点避免误导。

16. **自选分组后端持久化（可选）**  
    `watchlist` 模块增加 CRUD，与 localStorage 双向同步。

17. **策略回测暴露 `optimize_params`**  
    高级面板可选网格搜索，与后端能力对齐。

18. **Prometheus `/metrics` 接入 Grafana**  
    监控 overview 耗时、缓存命中率、同步队列（已有 HTTP 指标埋点）。

---

## 八、后端端点全量清单（供回归测试）

### auth
- `POST /auth/login`、`GET /auth/me`

### market / stock / etf / screener / watchlist
- `GET /market/overview`
- `GET /stock/search`、`/stock/{symbol}/profile|kline|predict|panels`
- `GET /etf/overview|list|hot|performance|scale|flow`、`/etf/detail/{code}`
- `GET /screener`、`GET /screener/watchlist`
- `GET /watchlist/dashboard`、`GET /watchlist/correlation`

### backtest / portfolio / research / studio
- `POST /backtest/run`、`/backtest/strategy-run`、`/backtest/signal-analysis`
- `POST /portfolio/backtest`、`GET /portfolio/search`
- `GET/POST /research/*`（overview、factor-icir、factor-corr、factor-quantile、experiments、cv-folds、feature-importance、optimize、impact-sim、stress-test）
- `POST /studio/mining/start`、`GET /studio/mining/status/{id}`、`POST /studio/mining/cancel/{id}`、`POST /studio/alpha-eval`

### desk / ops / datacenter / settings / export
- `GET/POST /desk/*`（kill-switch、exclusion、orders、fills、account、capacity、attribution）
- `POST /ops/quality-scan`、`GET /ops/lineage`、`GET /ops/dag`、`POST /ops/dag/rerun`
- `GET/POST /datacenter/*`（overview、datasets、quality、logs、task-stats、sync、instruments、auto）
- `GET/PUT/POST /settings/*`
- `GET /export/screener`、`POST /export/backtest`、`POST /export/strategy-backtest`

---

## 九、建议的验证清单（Test Plan）

- [ ] 无 Redis 启动：市场概览首次 <60s，二次 `from_cache: true`
- [ ] 选股导出与页面 Top-K、board、date 一致
- [ ] 策略回测导出与页面净值曲线一致
- [ ] 因子挖掘：启动 → 轮询 → 取消 → 服务重启后历史任务提示失效
- [ ] 数据中心：incremental → cancel → resume 断点续传（同进程）
- [ ] 执行中心：下单 → run fills → 账户净值变化
- [ ] 设置：改 `refresh_freq` 后（实现 P1-5 后）各页间隔变化
- [ ] 未登录访问 `/desk` 下单（评估是否应 401）
- [ ] 顶栏搜索「茅台」「510300」期望行为（实现 P0-2 后）

---

## 十、附录：相关已有文档

- `AQP_执行中心与因子工作室优化建议.md`：OrderDesk / FactorStudio / CapacityAttribution 页面级优化（布局、指标展示等），可与本报告 P1 项合并排期。

---

*本报告由代码静态审查生成，未包含运行时压测与 E2E 自动化结果。建议在修复 P0 项后补充一轮接口契约测试（pytest + 前端 smoke）。*
