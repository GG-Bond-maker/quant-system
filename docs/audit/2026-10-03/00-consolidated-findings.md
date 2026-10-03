# AQP 全方位审查 · 合并问题清单

- 审查日期：2026-10-03（周六，休市日）
- 方式：静态审查 + 只读取证，**未修改任何源码**
- 团队：架构师（模块对应性）/ 工程师（接口契约）/ QA（数据流程与时间边界）
- 分报告：
  - `01-architecture-mapping.md`（前后端模块对应矩阵）
  - `02-contract-consistency.md`（接口契约一致性）
  - `03-data-time-boundary.md`（数据流程与交易时间边界）
- 去重后合计：**P0 5 条 / P1 17 条 / P2 29 条 = 51 条**
- 已剔除重复：工程师「自选 100 只上限口径不一」与 QA「`/watchlist/dashboard` 静默截断 100 只」为同一处，合并为 P1-06

---

## 一、P0（5 条：产生错误数据 / 功能实质不可用 / 数据污染）

### P0-1　autoSync 无交易日门禁：休市日照跑全市场同步，并把自然日写进 `data_jobs.trade_date`
- **模块**：`backend/app/services/sync_service.py:668-693`（触发判据）、`:649-660`（trade_date 写入）、`api/v1/datacenter.py:701-704`（健康灯）
- **现象**：触发判据只有"到点 + 当日未跑过"，无交易日判断；对比 `evening_routine.py:98-103` 是有门禁的。作业 `trade_date` 写的是 `date('now','localtime')` 自然日。
- **实证**：`data_jobs` 表存在 `sync_incremental` 2026-09-19(六) FAILED / 09-26(六) SUCCESS / 09-27(日) FAILED。
- **影响**：数据中心健康灯取"最新一条 data_jobs" ⇒ 周六 SUCCESS 时显示绿「AKShare 连接正常，最近同步成功」，而当日 `daily_bar` 实际停在 09-24（09-25 缺失）被掩盖；周日 FAILED 时又误报「最近同步异常」。休市日还白跑全市场约 2499 只同步。

### P0-2　AI 日报在非交易日生成：把周日标成"交易日"，并误报 predictions 落后
- **模块**：`backend/app/api/v1/report.py:259-283`
- **现象**：`day = day or date.today()` 无交易日判断；`:281-283` 用 `date.today()` 与 predictions 比对。
- **实证**：`app_state['daily_reports']` 中 2026-09-27（周日）那份写「交易日：2026-09-27」「近期流水线：2026-09-27 FAILED@-；2026-09-26 SUCCESS@-」（两条都是周末 autoSync 行），并给出 tip「predictions 落后于当日」。
- **影响**：休市日生成的日报内容自相矛盾且误导决策；国庆 10-01~10-07 只要后端重启一次即复现（`main.py:125` startup_catchup 同样无门禁）。

### P0-3　ETF 详情页「跟踪误差时序」把累计收益率当价格做除法
- **模块**：`frontend/src/pages/EtfDetail/index.tsx:195-196` ← 后端 `backend/app/api/v1/etf.py:1110-1112`
- **现象**：后端产出的是**累计涨跌幅 %**，前端按 `(points[i].etf / (points[i-1].etf || 1) - 1) * 100` 当价格求日收益；`|| 1` 还踩中首点 `etf` 恒等于 0。
- **影响**：数值量级与方向全错 —— 真实日收益 +0.5% 会被算成 -50% 或 +100%。同页 `:764` 又显示后端算的真实年化跟踪误差，同一语义出现两个数字。

### P0-4　自选股 `watchlist` 表零写入，"我的自选"预警规则永不触发
- **模块**：`backend/app/db/models.py:78`（定义）、`backend/app/api/v1/alerts.py:304`（唯一读取处）、`frontend/src/stores/useWatchlistStore.ts:63`
- **现象**：该表全仓**零处写入**；`alerts.py:306` 遇空集合静默 `return []` 且无告警日志。前端自选只存 localStorage，而 UI 已暴露"我的自选"预警选项（`pages/Alerts/index.tsx:29,256`）。
- **影响**：用户在预警规则里选"我的自选"后，规则永不触发且无任何提示 —— 属于静默失效的功能承诺。

### P0-5　设置页「量化引擎」参数是只写不读的死控件
- **模块**：`backend/app/api/v1/app_settings.py:213-223`（持久化 `commission_pct/slippage_pct/risk_indicators/name`）、`backend/app/domain/trading_rules.py:25`、`backend/app/api/v1/backtest.py:54,59,60`、`frontend/src/pages/Settings/index.tsx:535-543,248`
- **现象**：持久化的引擎参数**全后端零消费者**；真实生效的是常量 `COMMISSION_RATE_DEFAULT=0.0003` 与随请求传入的参数（单位 bps，且 `BacktestRequest` 根本无 commission 字段）。
- **影响**：用户改了佣金/滑点后回测结果完全不变，且无任何提示 —— 配置轨与执行参数轨双轨且配置轨无消费者。

---

## 二、P1（17 条）

| # | 模块（文件:行号） | 现象 | 影响范围 |
|---|---|---|---|
| P1-01 | `data/parquet_store.py:560-586` vs `domain/calendar.py:83-88` | `today_trade_date_or_last()` 无"已收盘"截断，盘中/盘前把今天当最新交易日 | 首页显示「交易日 <今天>（最新）」，但 heat/sectors 的 data_date 实为上一交易日 |
| P1-02 | `data/calendar_store.py:63-68` | 日历空快照被缓存后**永不重载**（`_CACHE is None` 判据） | is_trade_day 恒 False ⇒ 晚间例行每晚静默 skipped、ETF 归档永久停摆，且不可自愈 |
| P1-03 | `data/calendar_store.py`，节假日表 6543 行 | 覆盖 2000-01-04~**2026-12-31**，仅剩约 3 个月；刷新只在同步前（`sync_service.py:197-218`） | 过期后降级为"只认周末"：2027-01-01(周五) 不回退 ⇒ 分区不存在 ⇒ 涨跌分布/板块整块 unavailable |
| P1-04 | `frontend/src/hooks/useMarketSession.ts:25-31`、`api/v1/alerts.py:729-735` | "盘中"判定仅 weekday 09:15-15:05，**不含节假日**，且不排除午休 11:30-13:00 | 国庆 5 个工作日每 30s 轮询（单标签页约 5400 次/假期）、预警调度同样触发，且**不显示"已休市"** |
| P1-05 | `api/v1/datacenter.py:727-731` | `stale_days` 用**自然日差** `(date.today()-end).days > 5` | 10-08 开盘前必误报「行情已 8 天未更新」（实际落后 0 个交易日）；春节必命中 |
| P1-06 | `api/v1/watchlist.py:275` / `api/v1/screener.py:517-518` | 自选 100 只上限两端口径不一：收藏页静默 `[:100]`，选股页超限直接 `ERR_PARAMS` | 用户 137 只时选股中心右栏渲染「暂无自选」；收藏页"自选资产总数"显示被截断后的数。前端 `hooks/useWatchlistQuotes.ts:59` 无分片。**全仓唯一未披露的截断** |
| P1-07 | `pages/MarketOverview/MoneyFlowPanel.tsx:23/81/107` ← `services/market_service.py:161-167` | 面板只认 `status==='ok'`，而 degraded 仍携带真实 north/main 数字 | 面板整块空白，同页 `KpiCards.tsx:96` 却照常显示「资金流向 +XX 亿」⇒ 一屏两个相反结论 |
| P1-08 | `api/v1/watchlist.py:372` | 降级路径写死 `alert_count: 0`（不是 null） | 绕过前端 `Watchlist/index.tsx:348` 的 `!= null` 守卫，KPI 卡显示红色「0 只（暂无）」，把"完全不可得"说成"零告警" |
| P1-09 | `data/screening.py:326` vs `api/v1/screener.py:188-199` | Alpha 榜用 `read_prev_and_today(sym, 预测日)`，自选股用 daily_bar 最新两根（无 end 约束） | 同一只票在 Alpha 榜与右栏「我的自选股」现价/涨跌不同，且无口径标注 |
| P1-10 | `pages/EtfDetail/index.tsx:526-530` ← `data/etf.py:545-550` | null OHLC 兜 0（`open/close/high/low ?? 0`） | 画出从 0 拉起的假 K 线；`close>=open` 恒真 ⇒ 恒染红（涨）。同文件 `:249` VolumeChart 对同一批数据做了正确判空 |
| P1-11 | `api/v1/market.py:828-833` vs `:795` | 日频块**无顶层 `status`**（rt 块有）⇒ `swr._effective_ttl()` 不生效；且 `freshness/data_freshness` 被固化进缓存（最长 3 天） | 降级态按 `_daily_ttl()`（≤3 天）落地；TTL 轮换点次日 15:30 **早于** 17:30 流水线产出点。缓解因素仅靠 `_overview_warmer` 每 240s 覆盖 |
| P1-12 | `api/v1/portfolio.py:70-71,100-102` | SQLite 缺失/异常时静默 `return []`，响应体无 degraded 标记 | 故障被伪装成"查无此股"，用户无法区分"真没有"与"库挂了" |
| P1-13 | `api/v1/screener.py:524/703/508` vs `api/v1/watchlist.py:270` | 同 `/screener` 前缀塞 3 个语义无关能力（榜单/全市场列表/自选快照），真自选在 watchlist.py | 端点命名与领域边界错位，是 P1-06 等缺陷的成因之一 |
| P1-14 | `api/v1/research.py:139-148` vs `domain/attribution.py:122-146` | 策略容量两套公式：前者硬编码 1%×20，后者可传参且÷调仓换手；字段名也不一致（`capacity_estimate_yi` vs `aum_threshold`） | 前端 `Research/index.tsx:372` 与 `CapacityAttribution/index.tsx:140` 各显示一个数字 |
| P1-15 | `backtest/broker.py:64`、`trading/paper.py:38+291`、`domain/research.py:232+270` | 平方根冲击模型写了三份，系数 10.0 各写一份，clamp 不一致（paper 有、research 无） | 回测 / 模拟盘 / 研究三处冲击成本口径不同，结果无法互相验证 |
| P1-16 | `api/v1/screener.py:100` vs `api/v1/watchlist.py:120` 等 | 自选行情两套实现，连"加载名称"都重复（`screener.py:49` vs `watchlist.py:165`） | 与 P1-09 同源：两处各自取数必然出现口径漂移 |
| P1-17 | `pages/EtfDetail/index.tsx:109,118`；`pages/MarketOverview/BreadthPanel.tsx:72` | null 渲染成 "0.00%"（同行 mv_yi 却显示 —）；Donut 仍用 `?? 0`，与同文件 `:104-108` 已修正口径相反 | 同一页面内对"缺失"的呈现方式自相矛盾，用户无法判断哪个是真值 |

---

## 三、P2（29 条，精简表）

| # | 模块 | 现象与影响 |
|---|---|---|
| P2-01 | `api/v1/screener.py:703` | `/screener/stocks` 的 11 个披露字段（basis_fields/quote_coverage/sort_applied/is_st/is_halted/quote_status/stale/from_cache…）前端零消费 |
| P2-02 | `frontend/src/types` QuotesData | 类型缺后端已返回的截断契约字段（`quotes_hub.py:109-114`） |
| P2-03 | `MoneyFlowPanel.tsx:96` | 点行业跳 `/screener?board=<行业名>` 是死链接（Screener 页不读 URL query） |
| P2-04 | `data/screening.py:342-343` | `enrich_items` 注释仍写"换手率小数口径"，代码已 ×100 |
| P2-05 | `pages/Research/index.tsx:619` | `invested_ratio ?? 1` 编造出"权重合计 100.0%" |
| P2-06 | `BreadthPanel.tsx:150` | 真实 0 涨停显示"—"（`limit_up/down/up/down` 判据用 `> 0`） |
| P2-07 | `TrainPanel.tsx:123`、`FactorHealthCard.tsx:104` | 未知就绪度/质量当 0 |
| P2-08 | `StatsCards.tsx:83` | `signal_strength=null`（未知档位）被计入 weak |
| P2-09 | `types/p1.ts:26` | `ScreenerItem.score` 声明非空无守卫（`:472/:634` 直接 `toFixed`，null 会整页 ErrorBoundary） |
| P2-10 | 选股中心 | "榜单涨幅 TOP 10"与概览卡用了两个不同聚合口径 |
| P2-11 | `EtfDetail` 跟踪序列 | `or base_etf` / `or 1.0` 把 0 收盘当缺失 |
| P2-12 | 行业资金流 | 缺失分项 `fillna(0)`，主力净额被低估且不披露 |
| P2-13 | `pages/Alerts/index.tsx:310` | 缺字段时渲染 "NaN%" |
| P2-14 | `api/v1/market.py:196,294` | 见 P1-03 连带：节假日表过期后涨跌分布/板块 unavailable |
| P2-15 | `orchestrator.py:833-842` | `_last_completed_trade_date` 名不副实（无收盘截断）+ 三份重复的 15:30 常量 |
| P2-16 | `api/v1/etf.py:125-130` | `data_freshness` 恒 `fresh`，休市日也一样 |
| P2-17 | 前端全局 | 缺统一的"休市 / 未开盘 / 数据截至 X 日"提示 |
| P2-18 | `api/v1/screener.py:547` | `k_screener("latest")` 无日期维度，"最新榜"最长滞后 35 分钟 |
| P2-19 | 时区 | 本地时间为主，`background_tasks`(UTC) 与 `data_jobs`(localtime) 两种口径并存 |
| P2-20 | parquet/SQLite/Redis | 三处日期一致性无统一校验 |
| P2-21 | `db/task_store.py:109-149` | 只回收 `background_tasks`，`data_jobs` 的 RUNNING 行无回收 |
| P2-22 | `evening_routine.py:66-79` | 卡在 running 时不受 `_MAX_ATTEMPTS` 约束，每 60s 重跑 |
| P2-23 | `data/etf.py:364` | 美股 ETF 批量快照静默截断到 50 只 |
| P2-24 | `api/v1/app_settings.py:307-312` | 持久化键白名单靠人工登记，缺新增守卫（仍是 Redis 唯一副本） |
| P2-25 | `api/v1/market/overview` | 兼容聚合端点前端未调用（功能已由拆分块覆盖） |
| P2-26 | `components/charts/MarketHeatmap.tsx:97` | 全仓 0 处 import 的死组件（147 行，含 2 个 ECharts 实例） |
| P2-27 | 前端 5 处轮询 | 无 `AbortSignal`、无 in-flight 去重；`FactorStudio/index.tsx:129` 未 `clearInterval` 即覆盖（定时器泄漏） |
| P2-28 | `api/*.ts` | 49 个调用点裸用 15s 默认超时，缺 TIMEOUT 分级常量表 |
| P2-29 | 架构层 | 业务逻辑大量驻留路由层（`api/v1` 11.5k 行 vs `services/` 仅 5 个文件），是多项 P0/P1 的结构性成因 |

---

## 四、历史缺陷复检结论（QA 线）

| 历史问题 | 现状 |
|---|---|
| 缓存键日期口径与业务截断不同源 | ✅ 已修（watchlist/stock/portfolio/etf/market 键日全部同源），残留仅 `screener "latest"`（P2-18） |
| 清缓存误删 Redis 持久存档 | ✅ 已修且到位（`app_settings.py:307-312` 先过 `is_persistent`；全仓无 flushdb/flushall，`scan_iter` 仅此一处）。残留风险：白名单人工登记（P2-24） |
| freshness 进缓存 | ⚠️ **部分残留**：选股端点已剥离（`screener.py:594-597`），日频块仍带（P1-11） |
| 空 DataFrame / 空序列 | ✅ 已闭合（`write_partition:844-845`、`write_year_batch:867-868` 对空 df 抛错；`_heat_payload:159-168`、`screener.py:137-145/192-202` 均有降级） |
| 时间戳单位混用 | ✅ 未发现 |
| 静默降级 / 静默截断 | ⚠️ 基本闭环（6/7 端点有截断三件套），例外：P1-06、P2-23、P2-16 |

## 五、契约层已确认无问题的项（避免重复排查）

- 前端调用 → 后端缺失（必然 404）：**0**；未发现 mock / 假数据文件
- 全仓无裸 5xx（6 处 `HTTPException` 全部是 200 + 业务码）
- `client.ts` 对非 200 / 网络异常 / 超时 / 取消的处理完整
- `/screener` 与 `/screener/stocks` 分页语义未被混用
- >200 只静默截断已修复（`quotes_hub` 分片 + `truncated` 披露）
- 金额 / 百分比主口径一致；情绪仪表 0=极度恐慌的有界刻度判空正确
