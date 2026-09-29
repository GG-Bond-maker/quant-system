# B9a · 前端 · 市场与个股页（审核报告）

审核日期：2026-09-18
审核范围：`frontend/src/pages/MarketOverview/*`、`pages/StockDetail/index.tsx`、`pages/Screener/*`、
`pages/Watchlist/index.tsx`、`components/charts/KLineChart.tsx`、`components/charts/MarketHeatmap.tsx`，
以及相关 `api/{market,stock,screener,watchlist}.ts`、`types/{stock,p1,watchlist}.ts`、`hooks/useWatchlistQuotes.ts`。
跨层核对：`backend/app/api/v1/screener.py`、`export.py`、`watchlist.py`、`market.py`、
`app/data/screening.py`、`app/services/market_service.py`、`app/data/ingest/akshare_adapter.py`。

## 0. 运行/验证基线（本批实际执行）

| 命令 | 结果 |
| --- | --- |
| `cd frontend; npx tsc --noEmit` | `EXIT=0`（无类型错误；未重复跑父审核员的 `--noUnusedLocals --noUnusedParameters`） |
| `rg -n "AbortController\|\.signal\|cancelToken" frontend/src` | 仅 `api/client.ts`（定义 `RequestOptions.signal`）、`pages/Research`、`pages/Topbar`、`pages/Backtest`；**B9a 全部页面 0 处使用** |
| `rg -n "MarketHeatmap" .`（排除 node_modules） | 仅 `MarketHeatmap.tsx:97` 自身定义 + `docs/` 提及；**0 处 import** |
| `rg -n "up loading" src/pages/MarketOverview/KpiCards.tsx` | `58: <Kpi label="上证指数 (SSEC)" value="—" up loading />`、`66: <Kpi label="沪深300 (CSI300)" value="—" up loading />` |
| `rg -n "exportApi\.\|hasMinimumRole" frontend/src` | `exportApi.` 3 处（Backtest×2、Screener×1）；`hasMinimumRole` 仅 `StockDetail:42` 使用 |
| 后端只读核对 | `backend/app/api/v1/export.py:23 require_role("researcher")`；`watchlist.py:259 [...][:100]`；`screener.py:448 len(syms) > 100 → AQPException`；`screening.py:457 STOCK_SORT_FIELDS`；`market.py:43-70 _build_indices`；`akshare_adapter.py:298 CORE_INDICES` |

> 说明：本批为前端批次，未启动 dev server、未改源码、未跑 pytest/重型测试。

## 1. 逐文件结论行

| 文件 | 结论 |
| --- | --- |
| `pages/MarketOverview/index.tsx` | 浅合并契约遵守良好（子组件均用全链路 `?.`）；⟳ 轻刷新失败时静默且产生 unhandled rejection（#17） |
| `pages/MarketOverview/KpiCards.tsx` | **2 个 P2**：字面量 `loading` 导致永久骨架屏（#6）；沪深300 兜底 `items[1]` 导致错标指数（#7） |
| `pages/MarketOverview/BreadthPanel.tsx` | 未发现 P0–P2；空/降级路径齐全。仅装饰性瑕疵（见 §3 观察） |
| `pages/MarketOverview/MoneyFlowPanel.tsx` | **1 个 P2**：板块行「走势」列画的是指数 sparkline（#8） |
| `pages/MarketOverview/HotSectorsPanel.tsx` | 未发现 P0–P2（`pct` 为 NaN 时 `Math.max` 会整体退化，但后端 `float(nan)` 会先破坏 JSON，属后端问题） |
| `pages/MarketOverview/AiPicksPanel.tsx` | AI 标签绝对阈值与后端相对分位口径不一致（#13）；情绪仪表在不可用时画 0（#14） |
| `pages/MarketOverview/pieces.tsx` | 未发现 P0–P2（`data.filter(Number.isFinite)` + `<3` 短路，空数据安全） |
| `pages/StockDetail/index.tsx` | **2 个 P1**：跨 symbol 残留 + 请求竞态（#1、#2）；面板降级/空态处理正确 |
| `pages/Screener/index.tsx` | 竞态（#3）、客户端过滤只覆盖截断后榜单（#11）、导出未按角色禁用（#12）、分页行号（#18） |
| `pages/Screener/FilterPanel.tsx` | 参数与后端 Query 一一对应（见 §2 对账）；未发现 P0–P2 |
| `pages/Screener/StockList.tsx` | 竞态（#4）；排序键全部在 `STOCK_SORT_FIELDS` 白名单内；未发现 P0–P2 其它问题 |
| `pages/Screener/StatsCards.tsx` | 未发现 P0–P2（`prev=null`、`win_rate=null` 等均有显式降级） |
| `pages/Screener/DistributionCharts.tsx` | 未发现 P0–P2（三图独立判空，直方图边界处理正确） |
| `pages/Screener/PerformanceChart.tsx` | 未发现 P0–P2；仅「各序列日期数组可能不等长」的理论错位风险（见 §3 观察） |
| `pages/Watchlist/index.tsx` | 不消费 `status=degraded`（#9）；>100 标的行为不一致（#10）；KPI 恒 0 兜底（§3 观察） |
| `components/charts/KLineChart.tsx` | 潜在实例复用 bug（#16）；指标/聚合/格式化对 null 处理良好；快捷键与清理正确 |
| `components/charts/MarketHeatmap.tsx` | **死代码**：0 处 import（#15） |
| `api/market.ts` `api/stock.ts` `api/screener.ts` `api/watchlist.ts` | 未发现 P0–P2；`get()` 支持 `signal` 但本批无人使用（正是 #1–#5 的根因） |
| `types/stock.ts` | 未发现 P0–P2；`MarketOverviewData` 的浅合并告警注释准确 |
| `types/p1.ts` `types/watchlist.ts` | `WatchDashboard` **缺 `status`/`reason` 字段**（#9 的类型侧成因）；`ScreenerResult.status` 三态正确 |
| `hooks/useWatchlistQuotes.ts` | 竞态（#5）；`fetch_freq` 轮询清理正确，但无卸载守卫（React 18 下为 no-op） |

**空值访问链专项结论**：本批所有图表/面板属性访问链（`data?.indices?.status`、`heat?.buckets`、
`c?.curve.length`、`money?.sector_flows`、`data.symbols.length` 等）均未被发现缺少 `?.`；
唯一"看似完整实则缺数据"的入口是 #9（后端 degraded 载荷）与 #6（错误态伪装加载态），
不是 null 解引用。`api/stock.ts:panels` 的 30s 超时 + `StockDetail` 不重置 state 是白屏/错数据的真实成因。

## 2. 跨层对账：选股筛选参数与后端 Query（重点项，结论：一一对应）

前端 `Screener/index.tsx:177-208` 只发 4 个参数，后端 `screener.py:454-463`：

| 前端发送 | 后端 Query | 结论 |
| --- | --- | --- |
| `p.date`（`filters.day`，`<input type=date>` → `YYYY-MM-DD`） | `day: str \| None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")` | ✅ 名称/格式一致 |
| `p.top_k`（10/20/50/100） | `top_k: int = Query(50, ge=1, le=200)` | ✅ |
| `p.board`（`all/main/chinext_star/bse`，`BOARDS`） | `board: str = Query("all")` → `filter_universe` 校验 `BOARDS` | ✅ |
| `refresh` | `refresh: int = Query(0, ge=0, le=1)` | ✅ |

`exportApi.screener({date, top_k, board})` ↔ `export.py:20-22`（`alias="date"`）✅ 参数一致，**但角色要求不一致**（#12）。

`StockList.tsx:98-108` 发送 `board/industry/page/page_size/sort/dir/exclude_st/q/refresh` ↔
`screener.py:568-577` 全部存在；排序键 `close/pct/amount/total_cap_yi/float_cap_yi`
都在 `screening.py:457 STOCK_SORT_FIELDS` 白名单内 ✅（不存在"前端传了后端不认的字段"）。

**未发送的筛选（即 #11 的成因）**：`filters.minScore`、`filters.signal` **不进入请求**，
只在客户端对 `result.items`（已被后端 `top_k` 截断）过滤。

**三态消费核对**：
- `ScreenerResult.status` → `index.tsx:350` + `resultMessage`（`ok` 空榜文案"当前没有满足条件的有效信号"）✅
- `HeatBlock/MoneyFlowBlock/SectorsBlock/IndicesBlock/RecommendBlock.status` → 各面板均先判 `status === 'ok'` ✅
- `WatchDashboard.status` → **完全未消费**（#9）❌
- `IndicesBlock.failed` → 未消费（#7 的加重项）❌

## 3. 问题详述

### #1 [P1][Bug][确定] StockDetail 切标的时不重置旧 state，失败请求后旧标的行情长期驻留
位置：`frontend/src/pages/StockDetail/index.tsx:55-76`（`load` 与 `useEffect`）
证据：
```
$ rg -n "setProfile|setKline|setPredict|setPanels|setErrors" src/pages/StockDetail/index.tsx
45/46/47/48: useState(...)                       ← 初始值
56: setLoading(true); setErrors({});            ← 只重置 loading/errors
65: if (p.status === 'fulfilled') setProfile(p.value);   ← 仅成功分支写状态
67: if (k.status === 'fulfilled') setKline(k.value);
69: if (m.status === 'fulfilled') setPredict(m.value);
71: if (pa.status === 'fulfilled') setPanels(pa.value);
```
`App.tsx:92` 的路由是 `path="/stock/:symbol" element={...<StockDetail />...}`，**没有 `key={symbol}`**，
所以 symbol 变化时组件实例被复用、`profile/kline/predict/panels` 全部保留上一个标的的值。

触发条件：`/stock/A` 正常加载 → 页面内搜索并跳转 `/stock/B`，而 B 的请求失败。
B 最典型的失败是 `/kline`：`backend/app/api/v1/stock.py:173-178` 在无数据时 **raise
AQPException(ERR_DATA_EMPTY)**（非空响应），前端走 `k.status === 'rejected'` 分支 → `setKline` 不被调用。
结果：头部/URL 已是 B（若 profile 成功），K 线图与「技术指标」仍是 A 的 bars，右栏「AI 预测」
（未来 5 日预期收益 + Top5 因子）也是 A 的模型输出——即**把 A 的预测挂到 B 的名称下**。
`/panels` 超时 30s（`api/stock.ts:13`），在慢路径下这种错配可持续数十秒甚至（请求失败时）不恢复。
最小验证：把 `frontend/src` 临时指向一个对 `/api/v1/stock/*/kline` 返回 `code!=0` 的 stub（或断开后端），
先访问 `/stock/600519.SH` 再跳到任一自选标的，观察 K 线是否仍是贵州茅台。

### #2 [P1][Bug][确定] StockDetail 请求竞态：旧响应覆盖新响应（无 Abort/序号守卫）
位置：`frontend/src/pages/StockDetail/index.tsx:55-76`（`load` 以 `useCallback([symbol, adjust, dateRange, canPredict])` 重建，`useEffect` 直接 `void load()`）
证据：`rg -n "AbortController|\.signal|requestId|seqRef" frontend/src` → B9a 页面 0 命中；
`api/client.ts:132-149` 明确提供 `RequestOptions.signal`，说明能力存在但未被使用。
触发条件：从选股榜/自选连续点两只标的（或搜索框连续回车两次），两次 `Promise.allSettled` 并发；
`/panels` 冷路径 30s、命中缓存 <100ms，两者返回顺序可任意 → 先发起的 A 请求后到，
把 A 的 kline/panels/predict 写回正在展示 B 的页面。
最小验证：DevTools 把 `/api/v1/stock/A/kline` 限速为 10s、B 不限速，快速切换 A→B 后观察图表是 A 的数据。

### #3 [P2][Bug][确定] 选股中心切板块 Tab 的竞态：旧榜单覆盖新榜单
位置：`frontend/src/pages/Screener/index.tsx:177-186`（`load`）+ `:193`（`useEffect(() => {void load(); void loadMarket();}, [load, loadMarket])`）
触发条件：快速点击「全部 → 主板 → 创业/科创」。每次 `setBoard` 都重建 `load` 并发起新请求；
`/api/v1/screener` 冷路径（无快照时 `_screen` 读 predictions + 全市场日线）与缓存命中（<200ms）
延迟差可达数量级 → 先点但慢的「全部」最后落地，页面出现：Tab 高亮「创业/科创」、
标题写 `Alpha Basic V1 · 创业/科创`，而表格是**全部板块**的 Top-K（且 `result.stats` 也是旧的）。
无任何防护：`rg` 确认本页无 `AbortController`，`load` 内无序号比对。
最小验证：给 `/api/v1/screener?board=all` 加 3s 网络延迟，连点三个板块，比较高亮 Tab 与表格内容。

### #4 [P2][Bug][确定] 股票列表（全市场）竞态：排序箭头与数据不一致
位置：`frontend/src/pages/Screener/StockList.tsx:94-117`（`load` 依赖 `[board, industry, kw, excludeSt, sort, page]`）、`:120`（变更即回第一页）
触发条件：连续点击同一表头两次（降序→升序），或快速切换「行业」下拉。`screenerApi.stocks` 超时 30s
（`api/screener.ts:12`），后端冷路径要串行分片抓全市场行情（`screener.py:527-563`），
命中 60s 行缓存时才 <200ms → 旧响应后到时，表头箭头指向新列、行数据按旧列排序。
另注：`:111` 在失败时 `setRes(null)`，会把上一页数据清空并显示「无匹配结果」，
与 `Pager` 的 `total=0 → totalPages=1` 叠加后用户看不到真实失败原因（有 error 条，尚可接受）。

### #5 [P2][Bug][确定] 共享自选 hook 竞态：已移出的标的会重新出现
位置：`frontend/src/hooks/useWatchlistQuotes.ts:50-64`
```
50: const load = useCallback(async () => { ... await fetcherRef.current(symbols); setData(d); ... }, [symbols]);
64: useEffect(() => { void load(); }, [load]);
```
触发条件：`/watchlist` 页「移出分组」或勾选后 `removeSelected`（`Watchlist/index.tsx:268-276`）
→ store 变化 → `symbols` 变化 → 新 `load`；若上一轮 `load`（含被移除标的）仍在飞行
（`watchlistApi.dashboard` 后端预算 5.5s、客户端 30s），后到的旧响应 `setData` 会把已移除标的
写回表格（`Watchlist/index.tsx:187 items = dash?.items` 完全由响应决定，不与 store 对账），
直到下一次轮询（`refresh_freq×12`，默认 60s）才消失——用户会认为"移出没生效"。
`Screener` 页右栏「我的自选股」与 `tab==='watch'` 表格共用同一份 `data`，同样受影响。

### #6 [P2][Bug][确定] 指数卡在数据不可用时渲染"永久骨架屏"（错误态伪装成加载态）
位置：`frontend/src/pages/MarketOverview/KpiCards.tsx:58` 与 `:66`
```
58: <Kpi label="上证指数 (SSEC)" value="—" up loading />
66: <Kpi label="沪深300 (CSI300)" value="—" up loading />
```
`loading` 是 JSX 布尔简写 = `true`（`Kpi` 内 `loading ? <脉冲骨架> : <value>`）。
该分支的进入条件是 `!loading`（父组件已结算），因此骨架**永久**转圈。
触发条件：`_build_rt` 的 indices 块降级 —— `backend/app/api/v1/market.py:66-69`
在指数源全部失败时返回 `{"status":"unavailable"}`（这是 P0 背景块明确认可的"外部数据源独立降级"设计），
或 `/market/overview/rt` 请求本身失败（`data.indices` 为 undefined）。
此时「今日两市成交额/资金流向/AI RankIC」三卡都用 `loading={loading && !heat}` 正确显示「—」，
只有两张指数卡永远闪烁——同一卡片组内自相矛盾。
最小验证：把 `CORE_INDICES` 全部指向不可达代码（或断网重试）访问 `/`，观察两张指数卡一直骨架。

### #7 [P2][Bug][确定] 沪深300 卡兜底到 `items[1]`，会把"深证成指"标成沪深300
位置：`frontend/src/pages/MarketOverview/KpiCards.tsx:41-43`
```
41: const items = data?.indices?.status === 'ok' ? data.indices.items ?? [] : [];
42: const sh = items.find((i) => i.name === '上证指数') ?? items[0];
43: const hs300 = items.find((i) => i.name === '沪深300') ?? items[1];
```
`CORE_INDICES`（`akshare_adapter.py:298-304`）顺序为 上证指数 / 深证成指 / 创业板指 / 科创50 / 沪深300，
且 `_build_indices`（`market.py:47-65`）**逐个指数独立 try/except**：任一指数抓取失败只记入 `failed` 并跳过。
因此当 `sh000300` 失败而前几只成功时，`items[1]` = **深证成指**，却按 `:61`
`<Kpi label="沪深300 (CSI300)" value={fmtNum(hs300.close)} ...>` 渲染 —— 数值、涨跌幅、sparkline
全是深证成指，标签却是沪深300。前端也没有消费 `IndicesBlock.failed` 做任何提示。
最小验证：在 `_build_indices` 里临时把 `sh000300` 的 `fetch_index_daily` 替换为抛异常（或仅让该代码不可达），
响应 `items` 少一项后观察第二张 KPI 卡的名称与数值。

### #8 [P2][一致性][确定] 资金流向面板的「走势」列画的是指数 sparkline，不是板块走势
位置：`frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:70` 与 `:101`
```
70:  const spark = indices[0]?.sparkline ?? [];                       // 第一个指数（上证）的近 60 日收盘
101: <MiniSpark data={spark.slice(-20)} up={f.total_yi >= 0} ... />   // 每个板块行都用它
```
`SectorFlow` 类型（`types/stock.ts:328-333`）= `{name, main_yi, retail_yi, total_yi}`，
后端 `services/market_service.py:49-55` 也只产出这三个字段 —— **板块自身没有任何时序数据**。
于是 8 个板块行显示 8 条完全相同的上证指数曲线，颜色却按该板块资金流符号红/绿，
列头写着「走势」，面板头写着「全市场 · 行业口径」。用户会把它读成"该板块近期走势"。
这属于把平台自算/无关数据冒充板块行情（P0 契约第 5 条口径披露的对立面）。
最小验证：`/` 页面观察右侧榜单 8 行的走势路径是否逐像素相同（对比上证 sparkline）。

### #9 [P2][Bug][确定] 我的收藏不消费 `dashboard.status='degraded'`，整表全 null 仍当完整数据展示
位置：`frontend/src/pages/Watchlist/index.tsx:186-188`（`const items = dash?.items ?? []; const summary = dash?.summary ?? null;`）
跨层证据：`backend/app/api/v1/watchlist.py:335-365` 在 `_WATCHLIST_BUDGET_SECONDS = 5.5`（`:33`）
超预算时返回 **`status:"degraded", reason:...`，且 items 的 close/pct/amount_yi/kline_state/alert/closes 全为 None/[]**。
前端类型 `types/watchlist.ts:37-40` 的 `WatchDashboard` 连 `status`/`reason` 字段都没声明，
页面也不读 `dash.status`（`rg` 仅 187/188 两处 `dash.`）。
触发条件：自选只数较多 + 外部行情源慢（5.5s 预算），或腾讯批量估值接口慢。
现象：KPI 显示「自选资产总数 N 只」（用 `summary.count`，看起来正常）、「今日平均涨跌幅 —」、
表格 N 行全是「—」，无任何超时提示——正是本批重点里"看起来完整其实缺数据"的列表。
而 `:305-307` 只渲染 `error`（请求异常），degraded 是 HTTP 200 成功响应，不会进 `error`。
最小验证：临时把 `_WATCHLIST_BUDGET_SECONDS` 调成 0.01，请求 `/api/v1/watchlist/dashboard` 观察响应
`status=degraded` 与页面表现。

### #10 [P2][一致性][确定] 自选 >100 只：dashboard 静默截断、screener/watchlist 直接报错，前端均无提示
位置：`frontend/src/pages/Watchlist/index.tsx:187`、`frontend/src/pages/Screener/index.tsx:663-713`
跨层证据：
- `backend/app/api/v1/watchlist.py:259` `raw = [...][:100]` —— **静默只取前 100**，`summary.count` 也只统计这 100 只；
  前端没有任何总数比对或提示。
- `backend/app/api/v1/screener.py:448-449` `if len(syms) > 100: raise AQPException(40000, "自选股一次最多查询 100 只")`
  —— 同一份自选列表在选股页走的是**报错**路径。
- 选股页右栏「我的自选股」卡（`index.tsx:684`）只判 `watchItems.length`，**不消费 `watchError`**（error 只在 `tab==='watch'` 的表格 `:465` 显示），
  于是报错时右栏显示 `PanelEmpty("暂无自选")`，且加载中同样显示"暂无自选"。
触发条件：分组去重后自选标的总数 > 100（多分组累积即可）。此时三个入口三种行为：
收藏页少 100 只但看起来完整、选股页右栏谎称"暂无自选"、选股页自选视图显示错误。
最小验证：在 store 里预置 101 个 symbol（`localStorage` 的 `aqp-watchlist-groups`）后访问两页。

### #11 [P2][一致性][确定] 预测强度 / 最小 Score 是客户端过滤，只作用于 `top_k` 截断后的榜单，且未披露
位置：`frontend/src/pages/Screener/index.tsx:219-232`（`filtered` 的客户端过滤）、`:180`（请求只带 `top_k/board/date`）、`:715-727`（数据口径卡）
证据：`raw = result?.items` 来自 `screenerApi.screen`，后端 `screener.py:262-264 _build_items` → `enrich_items(df, top_k)`
→ `screening.py:222 for idx, r in enumerate(df.head(top_k)...)`，即响应已被截断到 `top_k`。
所以 `Top-K=20` + 「弱信号」的语义是"这 20 只里信号较弱的那几只"，不是"全市场的弱信号标的"；
`最小 Score` 同理。用户看到的是"结果 3 条"，会理解成全市场只有 3 只。
数据口径卡（`:715-727`）里"搜索为当前榜单内筛选"这条**主动披露了**榜单内搜索，
却完全没有对应披露 `预测强度/最小 Score` 的截断语义 —— 同一张卡内两种同类过滤，只披露了一个。
最小验证：选 `Top-K=20` 且 `预测强度=弱信号`，把 `Top-K` 改成 100 后观察"结果 N"变大而筛选条件未变。

### #12 [P2][一致性][确定] 「导出 Excel」未按角色禁用：viewer 点击必失败
位置：`frontend/src/pages/Screener/index.tsx:338-342`
```
disabled={exporting || tab !== 'top' || items.length === 0}
```
跨层证据：`backend/app/api/v1/export.py:23` `_user: dict = Depends(require_role("researcher"))`；
而选股页路由只要求 `viewer`（`frontend/src/App.tsx:93`）。
项目内本有正确的角色门控范例：`StockDetail/index.tsx:42`
`const canPredict = hasMinimumRole(role, 'researcher')`（全项目仅此 1 处使用，`rg` 证据）。
触发条件：以 viewer 身份登录（页面完全可用）→ 点击「导出 Excel」→ 收到 403 业务码 →
`catch` 把 `ApiError.message` 写进 `error` 横幅（`:203-205`）。
最小验证：`curl -H "Authorization: Bearer <viewer token>" '/api/v1/export/screener?top_k=20&board=all'` 应返回非 0 业务码。

### #13 [P2][一致性][疑似] AI 标签用绝对阈值，与后端已改为相对分位的口径不一致
位置：`frontend/src/pages/MarketOverview/AiPicksPanel.tsx:45-51`
```
45: function tagOf(score) {   // 强烈看多 >=0.02 / 看多 >=0.008 / 震荡 >-0.008 / 看空 >-0.02 / 强烈看空
```
后端在 `app/data/screening.py:250-254` 明确记录了**为什么不能用绝对阈值**：
"生产模型 max pred_score=0.0398 < 0.1 ⇒ 全市场恒为 weak，零信息量；且阈值与模型分数尺度强耦合，
每次换模型都可能失效"，因此信号强度改为按名次分位（`signal_strength_by_rank`）。
而 `AiPicksPanel` 的注释（`:44`）自称"与选股中心风险口径一致"，实际是两套口径：
`Screener`/`WatchlistQuote` 用 `weak/neutral/strong`（相对分位），`AiPicks` 用 0.02/0.008 绝对阈值。
置信度：口径不一致=确定（代码可读）；"换模型后整体误标为强烈看多/看空"=疑似（需模型分数尺度变化才暴露）。
最小验证：把该标签与同一批 `symbol` 在后端 `signal_strength_reference_map` 下的标签并列比对，
或换一个分数尺度不同的 `feature_version` 后观察标签分布是否整体塌向一端。

### #14 [P3][一致性][确定] 情绪仪表在 `sentiment.status='unavailable'` 时画出 0（极端恐慌）
位置：`frontend/src/pages/MarketOverview/AiPicksPanel.tsx:55-56`（`const score = sentiment?.score ?? 0`）、`:72-74`（`formatter: () => \`${score} · ${label}\``）、`:91`（刻度"恐慌/中性/贪婪"）
后端 `market.py:502-503` 在 heat 不可用时返回 `{"status":"unavailable","reason":...}`（**无 `score` 字段**），
`:790`/`:856`/`:873` 也会整体把 sentiment 置为 unavailable。
于是"无数据"被画成指针指向 0 的仪表盘 + 刻度左侧"恐慌"，只有中间小字 `0 · —` 暗示缺失。
最小验证：让 heat 块 unavailable（本地无日线）后访问 `/`，观察情绪仪表指针位置。

### #15 [P3][死代码][确定] `MarketHeatmap.tsx` 全项目 0 处引用
位置：`frontend/src/components/charts/MarketHeatmap.tsx:97`（`export default function MarketHeatmap`）
证据：
```
$ rg -n "MarketHeatmap" .   (排除 node_modules)
.\docs\audit-2026-09-14\frontend-architecture-review.md:21
.\docs\chatgpt-full-review-prompt.md:371
.\frontend\src\components\charts\MarketHeatmap.tsx:97      ← 仅自身定义
```
`App.tsx` 无引用，无页面 import。替代实现是 `MarketOverview/BreadthPanel.tsx` +
`MoneyFlowPanel.tsx`（ECharts 玫瑰图/双向柱状图）。属于"写了但没有入口能触达"的未接线组件，
148 行（含 `heatOption`/`moneyOption`）维护成本为 0 收益。

### #16 [P3][Bug][疑似] KLineChart 在 `bars` 由非空→空→非空时复用已卸载 DOM 上的旧实例（图表空白）
位置：`frontend/src/components/charts/KLineChart.tsx:386-403`（`if (!chartRef.current) { chartRef.current = echarts.init(host); ... }`）、`:669-675`（`bars.length === 0` 时 early return 另一棵子树）
`bars=[]` 时 host div 被卸载，但 `chartRef.current` 未清空、实例仍绑定在**已脱离文档的节点**上；
再次非空时 `hostRef.current` 是新节点，而 `chartRef.current` 为真 → 跳过 `echarts.init`，
对新数据 `setOption` 到一个不可见节点 → 图表区域空白。
可达性：当前两个调用方都不会让 `bars` 变空 —— `StockDetail/index.tsx:177` 传 `kline?.bars ?? []`
且 `kline` 只被成功响应写入（失败时保留旧值，见 #1）；`EtfDetail/index.tsx:477` 用
`kline?.status === 'ok' && klineBars.length > 0` 作为渲染条件（组件随条件挂/卸，是新实例）。
因此这是**潜在**缺陷：任何后续接入"可切换区间/周期而使 bars 暂时为空"的调用方都会命中。
最小验证：在 `StockDetail` 临时改为 symbol 变化时 `setKline(null)`，再给新标的返回空 bars（或先空后非空），
观察 K 线区是否空白且控制台无报错。

### #17 [P3][Bug][确定] 市场概览 ⟳ 轻刷新失败时静默（unhandled rejection，无错误提示）
位置：`frontend/src/pages/MarketOverview/index.tsx:55-61` + `frontend/src/components/DataFreshness.tsx:47-58`
```
55: const lightRefresh = useCallback(async () => {
56:   await mutate([...rt key...], async (cur) => {
58:     const fresh = await marketApi.overviewRt(true);   // 抛 ApiError 时 mutate 返回 rejected promise
60:   }, { revalidate: false });
61: }, [mutate, open]);
...
DataFreshness.tsx:53-57: try { await onRefresh(); } finally { setRefreshing(false); }   // 无 catch
DataFreshness.tsx:65: <button onClick={() => void doRefresh()} ...>
```
`overviewRt(true)` 失败（后端不可达/超时）时，rejection 穿过 `doRefresh` 的 `finally`，
最终成为未处理的 Promise rejection（控制台报错），用户侧只看到转圈停止、没有任何失败提示。
`_t` 键与 `useApi` 的 `[url, params]` 一致，所以命中键本身没问题。
最小验证：断网（或让 `/api/v1/market/overview/rt` 返回 500）后点 ⟳，观察控制台
`Uncaught (in promise) ApiError` 且界面无提示。

### #18 [P3][一致性][确定] Alpha 榜分页后「#」列从 01 重新开始，不是全局名次
位置：`frontend/src/pages/Screener/index.tsx:576-579`
```
576: {pagedItems.map((it, i) => (
579:   <td ...>{String(i + 1).padStart(2, '0')}</td>
```
`i` 是页内下标（`pagedItems = items.slice((safePage-1)*20, ...)`，`:249-252`），
第 2 页第一行仍显示 `01`。列头 `#` 在"Alpha 榜排名"语境下应表达全榜名次，
且与右栏「结果 N」/`StatsCards` 的排名口径不一致（序数误导，非崩溃）。
最小验证：`Top-K=50` 时翻到第 2 页，首行 `#` 为 `01`。

## 4. 未达 P2 的观察（记录，不计入结论表）

1. `BreadthPanel.tsx:37-40`：方块网格的比较器对 `b5`「平」桶标签 `'平'.charCodeAt(1)` 返回 `NaN`
   （单字符标签），排序行为依赖引擎；且 `out.slice(0, TARGET)` 在 `Math.round` 累计超过 240 时会丢尾块。
   纯装饰性图元，不影响数值展示（数值来自 `fmtNum(heat?.up)` 与家数标注）。
2. `PerformanceChart.tsx:79-98`：`xAxis.data = normalized[0].dates`，而各序列 `values` 长度由
   `filter(p => new Date(p.date) >= cutoff)` 独立决定；若两条序列在窗口内的交易日集合不同，
   ECharts 按索引对齐会导致曲线横向错位。5 个核心指数交易日集合当前一致，故**未观察到**真实错位。
3. `Watchlist/index.tsx:323`：`summary?.alert_count ?? 0` 在 summary 为 null（加载中/请求失败）时显示
   `0 只（暂无）`，而同排其它卡片显示「—」，属口径不一致（P3 级）。
4. `Watchlist/index.tsx:261`：CSV 拼接未做引号转义，名称含逗号会错列。
5. `Screener/index.tsx:273-292`：切到 `watch` 视图时 `cancelled` 早退跳过 `setPerfLoading(false)`，
   `perfLoading` 会残留 true；但该卡片仅在 `tab==='top'` 渲染，故无可观察影响。
6. `KLineChart.tsx:650-667`：`F8/Ctrl+Q/Ctrl+B` 注册在 `document` 上并 `preventDefault`，
   在页面头部搜索框输入时也会触发（同页无第二个图表实例，影响有限）。
7. `MarketOverview/index.tsx:25-30`：`isMarketOpen()` 用浏览器本地时区判断盘中（09:15–15:05），
   非东八区用户会得到错误的轮询窗口（低频、无数据损坏）。
8. 降采样：`indexKline(limit=400)`、日 K 区间约 1 年（≤250 根）、榜单 ≤100 行，
   本批未发现需要客户端降采样的数据量；`DistributionCharts` 三图均对空数组判空。
9. `useWatchlistQuotes.ts:50-64` 无卸载守卫（卸载后 setState），React 18 下为 no-op，不单列。

## 5. 与本批重点的对照小结

| 本批重点 | 结论 |
| --- | --- |
| 图表空/null 渲染安全（`data.x.y` 缺 `?.`） | 未发现缺 `?.` 的访问链；真实风险是 #6（错误态伪装加载态）与 #9（degraded 载荷当完整数据） |
| 大量数据点降采样 | 不需要；数据量 ≤400 点/序列（见 §4.8） |
| 数值格式化 null/NaN/极值 | `utils/format.ts` 的 `fmtNum/fmtPct/pctClass` 均做 `Number.isFinite` 与 `== null` 处理 ✅；未发现 NaN 泄漏到界面 |
| 涨跌颜色红涨绿跌 | **方向正确**（`pieces.tsx:52`、`AiPicksPanel.tsx:29`、`BreadthPanel.tsx:10-20`、`StatsCards.tsx:179-180`、`format.ts:4-7` 全部红涨绿跌）；未发现反色 |
| 筛选参数与后端一一对应 | 全部对应（§2 表）；**不存在前端传后端不认的字段** |
| 响应 `score` / `data.status` 三态消费 | `score` ✅（后端 `enrich_items` 保证为数值，`ScreenerItem.score: number` 成立）；三态中 `WatchDashboard.status` 未消费 ❌（#9） |
| 快速切换的请求竞态 | **存在 4 处**：#2（个股页，P1）、#3（选股榜）、#4（全市场列表）、#5（共享自选 hook） |
| useEffect 依赖与清理、定时器/订阅泄漏 | `useWatchlistQuotes` 定时器、`KLineChart` 的 `ResizeObserver`/防抖 timer、`MarketHeatmap` 的 RO、各 ECharts 实例 dispose 均已正确清理；仅 #17 的 rejection 未处理 |
| 空态/加载态/错误态缺失 | #6、#9、#10 三处属于"看起来完整其实缺数据"；其余页面空态/降级文案齐全 |