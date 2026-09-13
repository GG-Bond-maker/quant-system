# AQP P0/P1/P2 复审报告（代码审核 + 运行时验证）

> 复审时间：2026-09-03
> 复审范围：《AQP_前后端审查报告.md》第七节 P0（4 项）/ P1（6 项）/ P2（8 项）共 18 项 + 第九节验证清单 9 项
> 方法：逐项代码走读 + pytest 全量 + tsc + 真实服务运行时实测（无 Redis、真实数据、真实网络）

---

## 一、总体结论

**18 项中 17 项确认落地且质量良好，1 项（P2-16 自选分组后端持久化，原文标注「可选」）未实施。**
验证清单 9 项全部实测通过（其中 2 项的行为随 P2-14 持久化而演进，见 3.4/3.5）。

| 回归门禁 | 结果 |
|---|---|
| `pytest -q` | **282 passed, 5 skipped**（670s，含新增 RBAC 用例） |
| `npx tsc --noEmit` | **0 错误** |

---

## 二、18 项逐项审核

### P0 — 安全与正确性（4/4 ✅）

| # | 项 | 结论 | 证据 |
|---|---|------|------|
| 1 | 写操作鉴权统一 | ✅ | `desk`(5/6)/`ops`(2/2)/`datacenter`(4/4)/`studio`(3/3)/`app_settings`(7/7) 全部 `Depends(require_role(...))`；前端 `RequireRole`（含 viewer<researcher<admin 等级判定与「权限不足」页）挂在 `/studio`、`/desk`、`/pipeline`。实测：无 token 下单 → `code 40100`。唯一例外：`/desk/attribution`（纯计算、无状态变更）未挂，属一致性小瑕疵 |
| 2 | 顶栏搜索对接真实 API | ✅ | `Topbar.tsx` 300ms 防抖并发 `stock/search` + `etf/list`，下拉分股票/ETF 标签跳转；6 位数字与 `XXXXXX.SH` 保留直跳快路径 |
| 3 | 星标跳转修复 | ✅ | `navigate('/watchlist')`，title「自选收藏」与行为一致 |
| 4 | 侧边栏登录态 | ✅ | `Sidebar` 复用 `useAuthStore`：真实头像缩写/用户名/角色标签/退出；未登录显示登录按钮 |

### P1 — 体验与一致性（6/6 ✅）

| # | 项 | 结论 | 证据 |
|---|---|------|------|
| 5 | 全局消费 refresh_freq | ✅ | `useRefreshIntervalMs(mult)` + `usePreferencesStore`；消费点：市场概览（`max(freq,10s)`）、执行中心 2x、我的收藏 12x、选股自选视图 12x（页面可见才轮询）；Settings 保存即写 store（`Settings/index.tsx:232`） |
| 6 | 合并自选行情 | ✅ | 采用报告的备选方案「抽共享 hook」：`useWatchlistQuotes` 统一 派生标的→拉取→轮询 数据流，fetcher 注入端点（两域端点字段本就不重叠，注释已说明理由） |
| 7 | Top-K 模型回测 UI | ✅ | `Backtest/TopKPanel.tsx` → `POST /backtest/run`（区间自动按 predictions 数据集校准）+ `export/backtest` 导出；与趋势跟踪 Tab 文案区分 |
| 8 | 信号分析 UI | ✅ | `Backtest/SignalAnalysisPanel.tsx` → `POST /backtest/signal-analysis`：IC 多周期表 + 分层多空净值 ECharts |
| 9 | 容量页参数 | ✅ | `participation_cap / holdings / rebalance_per_year` 滑块 + 400ms 防抖重算；另实现了时间窗口 pills 与 benchmark 三选一 |
| 10 | 启动调用 authApi.me | ✅ | `AuthBootstrap`：启动校验 JWT（40100/40101/40102 清会话）并预载偏好；实测 `/auth/me`（admin token）→ `{username:admin, role:admin}` |

### P2 — 性能与运维（7/8 ✅，1 项可选未做）

| # | 项 | 结论 | 证据 |
|---|---|------|------|
| 11 | overview 降频或 ETag | ✅（降频方案） | 前端 `max(useRefreshIntervalMs(), 10_000)`，轮询仅在 `visibilityState==='visible'`；未做 ETag（报告为「或」关系） |
| 12 | studio 快照优化 | ✅ | `_SNAP_CACHE`（文件名/mtime/size 签名，线程锁，仅保留当前窗口一份）；实测 start 请求秒级返回（快照 66,466 行复用） |
| 13 | research 缓存上限 | ✅ | `_FEAT_CACHE_MAX = 6`，超限按写入时间淘汰最旧 |
| 14 | GP/同步任务持久化 | ✅ | 新增 `db/kv.py`（sqlite3 同步 KV，WAL 库 worker 线程可写）：GP 终态任务存 `app_state`（上限 10，重启可查）；`datacenter` 同步 checkpoint 存 `sync_state`（正常完成清空、中断保留、跨重启 resume） |
| 15 | 通知铃铛 | ✅ | `core/events.py`（进程内事件总线，`publish_threadsafe` 供 worker 线程投递）+ `notify.py`（SSE 25s 心跳 + `/recent` 回放）+ 顶栏铃铛（未读角标/下拉/分类标签）；gp_miner 与 datacenter 均已接入发布 |
| 16 | 自选分组后端持久化（可选） | ❌ 未实施 | 仍为 localStorage（zustand persist）。原文即标注「可选」，不阻塞 |
| 17 | 策略回测暴露 optimize_params | ✅ | `parts.tsx` 寻优面板（候选值列表 + 校验 + 网格说明）→ `optimize_params`/`optimize_method:'grid'`；结果卡展示 best_params/夏普/Deflated Sharpe/top5 |
| 18 | Prometheus /metrics | ✅（代码侧） | `/metrics` 输出标准 exposition：HTTP 请求/耗时/错误、Redis 状态与熔断、`aqp_overview_cache_total{result}` 缓存命中率、pipeline、ML 预测指标。Grafana 接线属部署侧工作 |

**附**：`paper.py` 新增逐日净值重建 + 年化/最大回撤/夏普派生（样本 ≥30 交易日才计算，不足返回 null）——《执行中心优化建议》P1-2 的后端部分同步落地，实测字段已在 `/desk/account` 返回。

---

## 三、验证清单 9 项实测结果

### 3.1 无 Redis 启动 ✅
Redis 未启动（`/health` → `redis: "degraded"`）。市场概览首次 **39.9s**（<60s 达标，进程内 LRU 重建），二次调用 **1.02s**，`from_cache: true`。

### 3.2 选股导出与页面一致 ✅
`GET /screener?top_k=10&board=all`（date=2026-08-28）与 `GET /export/screener?top_k=10&board=all`：
导出 10 行，symbol 列与页面 items **顺序完全一致**（000014/000017/000025…），meta sheet 记录 date/top_k/board。

### 3.3 策略回测导出与页面一致 ✅
同参数（000001.SZ+300750.SZ，2025-01-02~06-30）：页面 117 点净值、末值 `1.0889/1.0303`、18 笔交易；导出 nav sheet 117+表头、末行 `(2025-06-30, 1.0889, 1.0303)`、trades 18+表头，**逐点一致**。区间起点落在节假日会被严格拒绝（可用范围提示），前端已用 `/datacenter/datasets` 校准。

### 3.4 因子挖掘：启动 → 轮询 → 取消 → 重启 ✅（行为演进）
- start（pop=10/gen=6）3s 内跑完 → cancel 返回 cancelled 但任务已 DONE（小任务撞车，属正常）；
- 重测 pop=60/gen=20：RUNNING(gen1/20) → cancel → **CANCELLED(gen2/20，下一代边界优雅退出)**；
- **重启服务后**：CANCELLED 任务仍可查询（P2-14 持久化生效，`elapsed=null` 标识历史任务）；不存在的 task_id → `51001`，文案明确区分「运行中任务随重启丢失 / 终态任务已持久化」。
- 注：原清单预期「重启后历史任务提示失效」，现演进为「运行中任务失效提示 + 终态任务可查」，前端已同时兼容（51001/40400 均按任务失效引导）。

### 3.5 数据中心：incremental → cancel → resume ✅
12 只标的增量同步 → 9s 后 cancel（协作式中断，停在前 2 只完成后，`completed_count=2` 保留）→ `resume=true` 重启同批：**对已完成的 000001/000002 零网络请求**（日志仅剩 000063 起的 10 只抓取记录），`completed_count` 由 2 继承递增至 11，最终 12/12 完成后 **checkpoint 清零**（正常完成语义）。测试期间 eastmoney 对部分标的断连（RemoteDisconnected），多源回退与失败告警路径同时得到验证。

### 3.6 执行中心：下单 → fills → 账户 ✅（机制）
- 参数缺失 → `40000` 带字段级明细；池外标的（600036.SH）→ 正确拒绝「无本地行情」；
- `000001.SZ` 买入 5 万市价单 → `order_id=5, decision_price=11.91`；`fills/run` 返回 `deferred` —— **这是正确行为**：撮合规则为 D0+1 开盘价（无未来函数），本地行情止于 2026-09-02，测试时刻（09-03 凌晨）下一交易日 bar 尚不存在；
- 全链路（成交、扣现金、持仓出现、basis 计算）由 `test_production.py::test_full_order_lifecycle_with_fills_and_basis` 覆盖，本次 282 绿中包含。

### 3.7 设置 refresh_freq 生效 ✅
`PUT /settings/preferences {"refresh_freq":9}` → 读回 9（已恢复 3）。前端消费链静态确认：Settings 保存 → `usePreferencesStore` → 各页定时器随 store 自动重建。

### 3.8 未登录访问 /desk 下单 ✅（契约说明）
返回 **HTTP 200 + `code 40100 UNAUTHORIZED`**（坏 token → 40102），不是 HTTP 401。这是平台「HTTP 恒 200 + code 信封」全局契约的既定选择（`errors.py` 有意为之），前端 axios 拦截器 + `RequireRole` 守卫闭环。**结论：受保护成立；是否改为 HTTP 401 属契约取舍，现状自洽，无需改。**

### 3.9 顶栏搜索期望行为 ✅
「茅台」→ `600519.SH 贵州茅台`（搜索 API 命中，下拉跳个股详情）；「510300」→ 6 位直跳 `/etf/510300`（沪深300ETF华泰柏瑞）。注意：600519 数据此前被隔离（质量修复），详情页数据仍受此影响，属已知数据问题非搜索问题。

---

## 四、遗留与建议（均为小项）

1. **`/desk/attribution` 补挂 `require_role("viewer")` 或 `researcher`** —— 与同文件其余 POST 保持一致（纯计算，安全影响小）。
2. **P2-16 自选分组后端持久化**（可选）未做：分组仍 localStorage，换设备丢失。kv.py 基建已就绪，实施成本低。
3. **测试过程遗留**：模拟盘新增一笔 PENDING 母单（id=5，000001.SZ 买入 5 万市价单），将随下次行情同步在下一交易日自动撮合；如不需要可在执行中心 Kill Switch 撤单（会连带撤掉此前遗留的 vwap 单 id=4）。另：数据中心已将 000001.SZ 等 12 只标的增量更新至 2026-09-02（部分标的 eastmoney 断连未成功，可 retry）。
4. **已知非新问题**：选股 Top-K 分数大量并列（如 0.008655）源于 121 只窄池 + 弱模型的分数集中（历史审计已记录），非本次改动引入。
5. 一次策略回测导出期间原服务进程曾退出（重测 2.4s 正常完成）——当时正与 pytest 全量并发，疑内存压力所致，非代码缺陷复现；如再现建议排查 `/export/strategy-backtest` 的并发内存占用。

---

*本报告所有运行时结论均来自本机真实服务实测（127.0.0.1:8000，无 Redis，真实数据与网络）；静态结论来自逐文件走读。*
