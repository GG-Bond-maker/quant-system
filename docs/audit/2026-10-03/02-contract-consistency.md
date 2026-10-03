# AQP 前后端接口契约一致性审查报告

- 审查日期：2026-10-03
- 审查人：寇豆码（Engineer）
- 审查范围：`backend/app/api/v1/*`（22 个路由模块）+ `backend/app/data|services|domain` 生产侧 +
  `frontend/src/api|types|pages|components` 消费侧
- 基线约定：统一信封 `{code,message,data,trace_id,ts}` / HTTP 恒 200 / `code=0` 为成功 /
  选股类状态在 `data.status`（ok / degraded / unavailable）/ 分数列名 `score`
- 方法：逐条"后端怎么产出 → 前端怎么消费 → 用户看到什么"三段式核对，全部结论附文件路径 + 行号 + 代码片段。
  **本次审查未修改任何源码。**

---

## 结论速览

| 严重度 | 数量 | 一句话概括 |
|---|---|---|
| P0 | 1 | ETF「跟踪误差时序」拿累计收益率当价格做除法，数值与符号全错 |
| P1 | 7 | 参数寻优 NaN 目标值 ⇒ HTTP 500 破坏恒 200（间歇性）/ 最大回撤三页两种符号 / 自选 100 只静默截断 / 资金流 degraded 被当不可用 / 降级态伪造"零告警" / 同页两套行情基准日 / K 线 null OHLC 兜 0 恒染红 |
| P2 | 16 | 披露字段零消费、类型缺字段、真值 0 当缺失、缺失当 0/100%、死链接、NaN 净化口径不一致等 |

> 第二轮补充（回测 / 组合 / 模拟盘模块）见文末「第二轮补充」章节，已并入上表计数。

正面结论（已核对无误，供 QA 排除）：
- 全后端**没有**裸 `raise HTTPException(5xx)`：`backend/app/core/auth.py:67/176/178/231/283` 与
  `api/v1/notify.py:65` 全部是 `status_code=200` + 业务码，信封约定成立。
- 前端 `client.ts` 拦截器对非 200 / 网络异常有统一 `ApiError(code=-1)` 翻译，并对 GET/HEAD 做受限重试
  （`client.ts:120-139, 187-223`），超时与取消明确不重试。
- `/screener`（top_k ≤ 200、客户端分页）与 `/screener/stocks`（服务端排序+分页）**分页语义未被混用**：
  `pages/Screener/index.tsx:272-279` 客户端切片，`pages/Screener/StockList.tsx:98-108` 把
  `page/page_size/sort/dir` 全部回传服务端，两侧都未在对方语义上做二次排序。
- `>200` 只标的静默截断**已修复**：`data/quotes_hub.py:36,97-103` 已改分片抓取并在总上限 800 时
  显式给出 `truncated/dropped_count` + WARNING；`api/v1/screener.py:663-699` 也按 200 分片。
  遗留问题见 P2-08（前端类型层把这个契约整段丢了）。
- 金额/百分比主口径（元 / 亿元 / 百分比）在 `data/screening.py:770-771`、`types/p1.ts:93`
  两侧有显式声明且实现一致；`utils/format.ts` 的 `fmtPct/fmtNum/fmtAmountYi/fmtVol` 全部先判空。

---

## P0

### [P0] ETF 详情页「跟踪误差时序」把累计收益率当价格做除法 —— 数值量级与方向全错

**模块**
- 后端：`backend/app/api/v1/etf.py:1107-1112`（`_build_tracking_block`）
- 前端：`frontend/src/pages/EtfDetail/index.tsx:191-200`（`TrackingErrorChart`）
- 类型：`frontend/src/types/etf.ts:213-219`（`EtfTrackingPoint`）

**现象**

后端产出的 `points[i]` 是**累计涨跌幅 %**（相对共同起始日），不是价格：

```python
# backend/app/api/v1/etf.py:1107-1112
for d in dates:
    e_close = etf_map[d]["close"] or base_etf
    i_close = idx_map[d]["close"] or base_idx
    e_ret = (e_close / base_etf - 1) * 100      # ← 累计收益率，%
    i_ret = (i_close / base_idx - 1) * 100
    points.append({"date": d, "etf": round(e_ret, 2), "index": round(i_ret, 2)})
```

前端类型自己也这么写的（`types/etf.ts:215`：`/** 累计涨跌幅 % */ etf: number;`），
但 `TrackingErrorChart` 却按"价格序列"求日收益：

```tsx
// frontend/src/pages/EtfDetail/index.tsx:192-200
const diffs: { date: string; value: number }[] = [];
for (let i = 1; i < points.length; i++) {
  const e = (points[i].etf / (points[i - 1].etf || 1) - 1) * 100;   // ← 累计% 相除
  const b = (points[i].index / (points[i - 1].index || 1) - 1) * 100;
  diffs.push({ date: points[i].date, value: e - b });
}
```

两处叠加错误：

1. **量纲错误**：日收益 ≠ `cum_i / cum_{i-1} - 1`。正确式应是
   `(1 + cum_i/100) / (1 + cum_{i-1}/100) - 1`。
   算例（真实日收益各约 +0.50%）：累计 0% → 0.50% → 1.00% 时，
   - i=1：`(0.5 / (0 || 1) - 1) * 100 = -50%`
   - i=2：`(1.0 / 0.5 - 1) * 100 = +100%`
   真实值应为 ≈ +0.50% / +0.497%，本图给出 **-50% 与 +100%**。
2. **`|| 1` 踩中真实 0**：后端构造上 `points[0].etf` 恒等于 0.00（首日 `e_close == base_etf`），
   `0 || 1 → 1`，第一个差值被放大约 100 倍。这正是任务里点名的"有界/合法 0 被 `!x` 判空"形态。
3. **符号翻转**：累计收益为负时，负数相除得正（如 -2% → -1% 得到 `-50%`，真实是 +1.01%），
   跑赢/跑输方向反了。

**影响**

用户在 ETF 详情页看到的「跟踪误差时序（日收益差）」是一条**量级放大到几十~上百个百分点、
方向随机翻转**的曲线，tooltip 还精确标注为"偏离: xx%"（`EtfDetail/index.tsx:209`）。
用户会据此判断"该 ETF 某日大幅跑输基准 50%"，而真实偏离只有零点几个百分点。

**同页自相矛盾**：`EtfDetail/index.tsx:761-764` 同时展示后端算的真实值
`年化跟踪误差 {tracking.tracking_error.toFixed(2)}%（近 1 年）`
（`etf.py:1118-1123`，由真实日收益差标准差年化）。同一页面、同一"跟踪误差"语义，
出现两个完全不同口径的数字，且其中一个是错的 —— 属于任务 5 的"前后端重复计算且口径不一致"。

**建议**

- 前端不再从累计收益率反推日收益：改为直接展示已给的 `tracking_error` 标量，
  或让后端在 `points` 里**新增** `etf_daily/index_daily` 两个真实日收益字段（纯增量、不改既有键），
  前端消费新字段；
- 若短期内必须前端派生，按 `((100 + cum_i) / (100 + cum_{i-1}) - 1) * 100` 计算，并删除 `|| 1`；
- 给 `EtfTrackingPoint` 增加 `etf_daily/index_daily?: number | null`，把"派生"收回后端单一事实源。

---

## P1

### [P1] 自选标的 100 只上限：收藏页静默截断、选股页直接报错 → 右栏谎报"暂无自选"

**模块**
- 后端：`backend/app/api/v1/watchlist.py:275`；`backend/app/api/v1/screener.py:514-518`
- 前端：`frontend/src/hooks/useWatchlistQuotes.ts:54-69`；
  `frontend/src/pages/Screener/index.tsx:185-193, 700-750`

**现象**

同一份自选集合，两个端点两种完全不同的处理：

```python
# backend/app/api/v1/watchlist.py:275  —— 静默截断
raw = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))[:100]
```

```python
# backend/app/api/v1/screener.py:514-518 —— 直接业务报错
syms = list(dict.fromkeys(s.strip() for s in symbols.split(",") if s.strip()))
if not syms:
    raise AQPException(ERR_PARAMS, "symbols 不能为空")
if len(syms) > 100:
    raise AQPException(ERR_PARAMS, "自选股一次最多查询 100 只")
```

前端两侧都**不做分片**（`useWatchlistQuotes.ts:59` 只有一次 `fetcherRef.current(symbols)`），
把全部分组去重后的全集一次性发出（`pages/Screener/index.tsx:185-188`）。

**影响**

1. **我的收藏页**：`summary.count = len(items)`（`watchlist.py:343`）是**截断后**的数字。
   用户有 137 只自选，卡片显示「自选资产总数 100 只」，其余 37 只永不出现、无任何提示
   —— 静默数据丢失，且与"总数"语义直接冲突。
2. **选股中心 · 自选 Tab**：>100 时 `ApiError(40000)` → `watchError` 有值，表格区正确显示错误文案
   （`pages/Screener/index.tsx:502-503`），但**右栏「我的自选股」卡片**走的是
   `watchItems.length ? ... : <PanelEmpty text="暂无自选" />`
   （`pages/Screener/index.tsx:721-746`），`watchItems` 为 `[]` ⇒ 渲染出「暂无自选」。
   用户明明有 137 只自选，界面却说"暂无自选" —— 这是把"取数失败"渲染成"零数据"。
3. 两个端点对同一约束（100 只）一个报错、一个静默，属于同族接口契约不自洽。

**建议**
- 前端在 `useWatchlistQuotes` 里按 100 分片并合并结果（与 `screener.py:663` 的分片思路一致）；
- 后端 `/watchlist/dashboard` 在截断时参照 `quotes_hub` 的 §4.13.3 契约，返回
  `truncated / dropped_count / requested`，并把 `summary.count` 改为**请求总数**、
  新增 `summary.count_returned`；
- `/screener/watchlist` 与 `/watchlist/dashboard` 统一上限语义（都分片返回或都显式报错）。

---

### [P1] 资金流向面板把 `status='degraded'` 当"不可用"，已拿到的真实数字被整块丢弃

**模块**
- 后端：`backend/app/services/market_service.py:154-168`（P1-30 新增的三态：ok / degraded / unavailable）
- 前端：`frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:23, 81, 107, 133-136`

**现象**

后端在 P1-30 把资金块改成三态，degraded 时**仍然携带**已拿到的 `north_net_today` / `main_net_today`：

```python
# backend/app/services/market_service.py:154-167
n_ok = (1 if north is not None else 0) + (1 if main is not None else 0) + (1 if sector_ok else 0)
if n_ok == 0:
    return {"status": "unavailable", "reason": ...}
payload = {"north_net_today": north, "main_net_today": main, "sector_flows": sector_flows}
if n_ok < 3:
    return {"status": "degraded", "n_ok": n_ok, "reason": f"资金源部分可用({n_ok}/3)：...", **payload}
return {"status": "ok", "n_ok": n_ok, **payload}
```

前端只认 `ok`：

```tsx
// frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:23
const ok = money?.status === 'ok';
...
{ok && flows.length ? (...) : ok ? (/* 大盘汇总三行：主力/北向/成交额 */) : loading ? (...) : (
  <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
    {money?.reason ?? '资金数据暂不可用'}      // ← degraded 也落到这里
  </div>
)}
```

**影响**

- `degraded`（例如 1/3 或 2/3 子源可用）时，面板**整块降级为一行文字**，
  已经成功取到的北向/主力净流入数字一个都不显示 —— 可用数据被丢弃。
- **同页自相矛盾**：`KpiCards.tsx:96` 用
  `const flowVal = money?.main_net_today ?? money?.north_net_today ?? null;`
  照常显示"资金流向 +XX 亿"，而紧邻的「资金流向」面板同时显示"资金数据暂不可用"。
  同一份响应、同一屏幕，两个组件给出相反结论。
- 后端为 degraded 专门写的 `n_ok` 字段前端零消费（全项目 grep `n_ok` 只有后端一处）。

**建议**
- `MoneyFlowPanel` 判据改为 `money?.status === 'ok' || money?.status === 'degraded'`，
  并在 degraded 时把 `money.reason` 作为**黄条提示**叠加在数据之上（与
  `EtfDetail/index.tsx:806,814` 已有的 `flow?.status === 'ok' || flow?.status === 'degraded'` 写法一致）；
- `MarketMoneyFlowBlock` 类型补 `n_ok?: number`，并在提示中显示"资金源部分可用 (n/3)"。

---

### [P1] 自选看板降级路径把"告警数未知"写成 `alert_count: 0`，KPI 卡渲染成红色「0 只（暂无）」

**模块**
- 后端：`backend/app/api/v1/watchlist.py:364-374`（预算超时降级载荷）
- 前端：`frontend/src/pages/Watchlist/index.tsx:294-295, 345-350`

**现象**

后端超时降级时把计数型字段**写死成 0**（不是 null）：

```python
# backend/app/api/v1/watchlist.py:367-373
"summary": {
    "count": len(syms), "stock_count": ..., "etf_count": ...,
    "avg_pct": None, "flow_total_yi": None,
    "flow_status": "unavailable", "alert_count": 0,     # ← 0 而不是 None
    "alert_kinds": [], "quote_date": None,
},
```

前端这边其实**写对了守卫**，并在注释里明确点出了这个陷阱：

```tsx
// frontend/src/pages/Watchlist/index.tsx:345-350
// ⚠️ 不得用 `summary?.alert_count ?? 0`：summary 为 null（首载/取数失败）时会把
// "没有数据"写成「0 只 （暂无）」，伪造出"零条告警"。改为缺数显 '—' + 中性色。
{ label: '预警异动信号',
  value: summary?.alert_count != null ? `${summary.alert_count} 只 ${alertDesc}` : '—',
  tone: summary?.alert_count == null ? 'text-ink-muted' : 'text-red-500', ... }
```

但守卫只对 `null/undefined` 生效，后端给的是**字面量 0**，所以守卫被绕过：
`alert_count = 0` → `!= null` 为真 → 渲染 `0 只（暂无）`（`alertDesc` 见
`index.tsx:295`：`alert_kinds` 为空数组时回退 `'（暂无）'`）+ 红色 `text-red-500`。

**影响**

自选全部行情超时（后端明确"不伪造行情"）时，页面顶部虽有 degraded 黄条
（`index.tsx:321-326`），但「预警异动信号」KPI 卡给出的是一条**带方向的断言**：
"0 只（暂无）"且染红。用户读到的是"我持有的标的当前没有触发任何预警"，
而事实是"是否触发完全未知"。这正是任务里"用兜底值冒充指标"的形态，
只不过 0 是**后端**塞进去的，前端守卫生效不了。

**建议**
- 后端 `watchlist.py:372` 改为 `"alert_count": None`（与同载荷里 `avg_pct: None` / `flow_total_yi: None`
  保持同一口径：不可得即 null）；`count/stock_count/etf_count`（身份类计数，不依赖行情）保持数字。
- 前端可选加固：把"降级态"作为独立信号，
  `dash?.status === 'degraded' && flow_status === 'unavailable'` 时该卡强制走中性分支。

---

### [P1] 同一自选标的在选股中心两个视图里用**不同行情基准日**算最新价与涨跌幅

**模块**
- 后端：`backend/app/data/screening.py:326-331`（Alpha 榜口径）
- 后端：`backend/app/api/v1/screener.py:188-199`（`/screener/watchlist` 口径）
- 前端：`frontend/src/pages/Screener/index.tsx:625-630`（Alpha 榜表格）与 `721-738`（右栏「我的自选股」）

**现象**

Alpha 榜把行情**钉在预测日**（`trade_date` 来自 predictions 分区）：

```python
# backend/app/data/screening.py:326-331
bar = read_prev_and_today(sym, date_cls.fromisoformat(str(r["date"])[:10]))
if bar.get("close") is not None:
    close = float(bar["close"])
    prev_close = bar.get("prev_close")
    if prev_close:
        pct = round((close / prev_close - 1) * 100, 2)
```

`read_prev_and_today` 内部是 `read_symbol_dataset("daily_bar", sym, end=trade_date)`
（`backend/app/data/universe.py:556`），**只截到预测日**。

自选股接口取的是 `daily_bar` 的**最新两根**，不看预测日：

```python
# backend/app/api/v1/screener.py:188-199
df = read_symbol_dataset("daily_bar", sym)      # ← 无 end 约束
if df is not None and df.height >= 1 and not missing_columns(df, ("close", "date")):
    tail = df.tail(2).sort("date")
    close = float(tail["close"][-1])
    qdate = str(tail["date"][-1])[:10]
    if tail.height >= 2:
        prev = float(tail["close"][-2])
        if prev:
            pct = round((close / prev - 1) * 100, 2)
```

**影响**

当 `predictions` 落后于 `daily_bar`（流水线漏跑 / 晚间未跑，正是 `_freshness` 要披露的场景）时，
同一只票在**同一页面**出现两套数字：
- 左区「Alpha 榜」：现价/涨跌 = 预测日 T 的收盘与涨跌幅；
- 右区「我的自选股」：现价/涨跌 = 最新日 T+n 的收盘与涨跌幅。

数值不同且没有任何口径标注（右栏卡片只有"代码/名称/最新价/涨跌幅"四列，无日期列；
`watchQuoteDate` 只在自选 Tab 表头展示，`index.tsx:490`）。
用户从 Alpha 榜加入自选后回到右栏，会看到"我刚加的那只票价格变了"。

**建议**
- 两路径统一到"预测日"口径（`_watchlist_quotes` 也按 predictions 最新日期 `end=` 读取，
  与 `read_prev_and_today` 同源）；或在自选股响应里增加 `quote_basis`（`prediction_date` / `latest_bar`）
  并在右栏卡片补一行日期。
- 补一条一致性断言测试：同一 symbol 在两个端点的 `close/pct` 在同一基准日下必须相等
  （`data/screening.py` docstring 已自述"两路径数字必须一致"契约，目前只覆盖了 `signal_strength`）。

---

### [P1] ETF 详情 K 线把 null OHLC 兜成 0：画出从 0 拉起的假 K 线，且恒判为上涨（红）

**模块**
- 后端：`backend/app/api/v1/etf.py:1040-1050` + `backend/app/data/etf.py:545-550`（`_num` 可返回 None）
- 前端：`frontend/src/pages/EtfDetail/index.tsx:522-533`；渲染 `647`；消费 `components/charts/KLineChart.tsx`

**现象**

后端 OHLC 各字段都是 `_num()` 解析，**不可解析即为 null**（`data/etf.py:545-550`），
且类型契约也声明为可空（`types/etf.ts:190-196`：`open/close/high/low/volume: number | null`）：

```python
# backend/app/data/etf.py:545-550
out.append({
    "date": r[0],
    "open": _num(r[1]), "close": _num(r[2]),
    "high": _num(r[3]), "low": _num(r[4]),
    "volume": vol,
})
```

前端却一律 `?? 0`：

```tsx
// frontend/src/pages/EtfDetail/index.tsx:522-533
const klineBars = useMemo(() => {
  if (!kline?.bars) return [];
  return kline.bars.map((b) => ({
    date: b.date,
    open: b.open ?? 0,
    close: b.close ?? 0,
    high: b.high ?? 0,
    low: b.low ?? 0,
    volume: b.volume ?? 0,
    amount: null as number | null,
  }));
}, [kline]);
```

**自相矛盾的旁证**：紧邻 30 行的 `VolumeChart`（同一文件 `:242-249`）对同一批 bars 做了
**正确**的判空处理，并写了注释：

```tsx
// frontend/src/pages/EtfDetail/index.tsx:247-249
// 成交量柱按阳/阴线着色（收≥开=涨色红 / 否则跌色绿），与 K 线口径一致
// 方向未知（open/close 缺失）⇒ 中性色，不得用 `?? 0` 把未知染成「跌」绿
color: b.close == null || b.open == null ? p.FLAT : b.close >= b.open ? p.UP : p.DOWN,
```

即同一个文件里，成交量柱知道"open/close 缺失 ⇒ 不表态"，而 K 线本体把缺失当 0。

**影响**

`close >= open` 在 `open=0` 时**恒为真**（只要 close > 0）⇒ 该根 K 线恒染红（涨），
且 `low=0` 让整图纵轴被拉到 0，形成一根从 0 直冲现价的巨型"阳线"。
用户看到的是"该 ETF 当日暴涨"的假象，方向也是错的。

**建议**
- `klineBars` 保留 null（ECharts candlestick 对 null 不绘制），
  与 `VolumeChart` 的判空口径统一；
- 或者按 `kline.bars.filter(b => b.open != null && b.close != null && b.high != null && b.low != null)`
  整根剔除，并在图上标注"已过滤 N 根缺失 OHLC 的K线"（不静默丢数据）。

---

## P2

### [P2-01] `/screener/stocks` 有 11 个披露字段前端零消费

后端 `api/v1/screener.py:775-793` 返回
`trade_date/as_of/basis/basis_desc/basis_fields/source/degraded/quote_coverage/sort_applied/dir_applied/items/options/stale/from_cache`，
前端 `pages/Screener/StockList.tsx` 只消费了 `items / total / page(未用) / trade_date / degraded / basis_desc / options.industries`。
零消费字段：`basis_fields`（逐列口径说明，后端专门写在 `_local_basis_fields`/`_realtime_basis_fields` 里）、
`quote_coverage`（实时命中率）、`sort_applied` / `dir_applied`（非法排序回落后的实际生效值）、
`stale`、`from_cache`、`is_st`、`is_halted`、`quote_status`、`amount`（元，原始值）、`turnover`。
后果：后端为可解释性专门做的"口径披露"全部没有出口；排序非法静默回落后，UI 表头仍显示用户点的那一列。

### [P2-02] `QuotesData` 类型缺后端 §4.13.3 截断契约字段

`backend/app/data/quotes_hub.py:109-114` 恒返回 `requested / returned / truncated / dropped_count / limit`
（外加 `n_shards`），并在超 800 只时 WARNING + `truncated=True`。
前端 `frontend/src/api/market.ts:55-59` 只声明 `{as_of, source, quotes}` —— 契约字段在类型层被整段丢弃，
全项目无任何消费点。当前 `/market/quotes` 硬限 200（`market.py:912-913`）所以触发不到，
但这是一条"后端已实现披露、前端不可能感知"的哑通道。

### [P2-03] 资金流向面板点行业跳转 `/screener?board=<行业名>` —— 死链接，点了没反应

`frontend/src/pages/MarketOverview/MoneyFlowPanel.tsx:96`：
`onClick={() => navigate(`/screener?board=${encodeURIComponent(f.name)}`)}`
- `board` 参数白名单是 `all|main|chinext_star|bse`（`data/screening.py:966-976` 非法即 40000），
  传行业名（如"半导体"）语义完全不对；
- 且 `pages/Screener/index.tsx:134` 的 `board` 是 `useState('all')`，**从不读取 URL query**
  （全文件无 `useSearchParams`），所以跳转后板块仍是"全部"。

### [P2-04] `enrich_items` 注释与实现矛盾：换手率口径注释说"小数"，代码已 ×100

`backend/app/data/screening.py:332-334, 342-343`：

```python
if bar.get("turnover") is not None:
    turnover = round(bar["turnover"] * 100, 2)     # ← 已是百分比
...
"turnover": turnover,
# 换手率为小数口径（0.012 = 1.2%），前端换算百分比展示          ← 注释仍是"小数口径"
```

前端 `pages/Screener/index.tsx:643` 按百分比渲染 `it.turnover.toFixed(2)}%`，**当前是对的**。
但这条过期注释与 `build_market_stock_rows` 的 docstring（`screening.py:771`："pct / turnover = 百分比"）
自相矛盾，极易诱导后续维护者再乘一次 100 或让新消费方按小数处理。

### [P2-05] 组合优化"权重合计"用 `?? 1` 兜底 —— 缺失时编造出 100.0%

`frontend/src/pages/Research/index.tsx:619`：
`权重合计 {((optimize.weight_cap_info?.invested_ratio ?? 1) * 100).toFixed(1)}%`
`weight_cap_info` 缺失（旧后端 / 字段未返回）时显示"权重合计 100.0%"。
建议改为 `invested_ratio != null ? ... : '权重合计 —'`。

### [P2-06] 真实性 0 被当缺失：`limit_up / limit_down / up / down` 判据用 `> 0`

`frontend/src/pages/MarketOverview/BreadthPanel.tsx:145-150`：
`{n != null && n > 0 ? fmtNum(n, 0) : '—'}`
当日真的 0 家涨停 / 0 家跌停时，六格迷你网格显示"—"（缺失语义），
而同一格的环形图 `Donut`（`:79-81`）却用 `heat.limit_up` 的真实 0 参与饼图。
建议改为 `n != null ? fmtNum(n, 0) : '—'`，让 0 显 0。

### [P2-07] 两处"缺失当 0"的就绪度/质量判据

- `frontend/src/pages/DataCenter/TrainPanel.tsx:123`：`<GateDot ok={(readiness?.gnn_edges ?? 0) > 0} />`
  —— `gnn_edges` 为 null（未知）时判定为"未就绪"，与同页 `:861-874` 对 `autoSync === null`
  的"状态不可读"处理口径不一致。建议三态：`null ⇒ 未知（灰）`。
- `frontend/src/components/FactorHealthCard.tsx:104`：`((ks.over_crit_ratio ?? 0) * 100).toFixed(0)`
  —— 超限比未知时显示"超限比 0%"，即断言"零超限"。建议 `== null ⇒ '—'`。

### [P2-08] `signal_strength` 为 null（未知档位）被计入"弱信号"

`backend/app/data/screening.py:498-499` 快照路径
`"signal_strength": _translate_signal_strength(r["risk"])`，`risk` 列为 NULL 时返回 `None`
（`:52-56`）。前端 `ScreenerItem.signal_strength` 声明为非空枚举
（`types/p1.ts:28`），表格侧 `SignalStrengthBadge`（`Screener/index.tsx:78-82`）正确显示"—"，
但 `pages/Screener/StatsCards.tsx:80-83` 的构成环是：

```tsx
if (it.signal_strength === 'strong') strong += 1;
else if (it.signal_strength === 'neutral') neutral += 1;
else weak += 1;      // ← null / 未知档位落进 weak
```

"未知"被画成"弱信号"。建议 `else if (it.signal_strength == null) unknown += 1` 并单独成段
（或整环不画）。附带：`types/p1.ts:28` 应改为 `... | null`，与 `WatchlistQuote.signal_strength`
（`:149`，已声明 `| null`）对齐 —— 同一语义字段两处可空性声明不一致。

### [P2-09] `ScreenerItem.score` 声明非空但缺少守卫，与自选股侧不对称

`types/p1.ts:26` `score: number`（非空）⇒ `pages/Screener/index.tsx:472, 634` 直接
`it.score.toFixed(4)` / `it.score * 100`，无判空。
同一语义的 `WatchlistQuote.score` 声明 `number | null`（`:146`）并在 `:548-552` 判空显示"无预测"。
一旦快照/实时路径出现 null（schema 漂移、SQLite `pred_score` 空值），Alpha 榜会
`TypeError: Cannot read properties of null` ⇒ 触发 ErrorBoundary 整页报错。
建议统一为 `number | null` 并加守卫。

### [P2-10] 选股中心"榜单涨幅 TOP 10"与概览卡用了两个不同的聚合口径

`pages/Screener/index.tsx:323-329` 的 `topMovers` 基于 `items`（**客户端筛选 + 排序后**），
而概览 6 卡（`index.tsx:383`）刻意传 `raw`（未筛选的原始榜单）以保证与后端 `stats` 同源。
同一屏里"涨幅 TOP10"会随最小 Score / 预测强度 / 搜索框变化，而卡片数字不变，
用户难以判断哪个是"今日榜单"。建议 `topMovers` 也基于 `raw`，或在小标题注明"当前筛选结果内"。

### [P2-11] ETF 跟踪序列 `or base_etf` / `or 1.0` 把 0 收盘当缺失

`backend/app/api/v1/etf.py:1101-1109`：
`base_etf = etf_map[dates[0]]["close"] or 1.0`、`e_close = etf_map[d]["close"] or base_etf`
—— close 为 0（脏数据）时被替换为基准值，静默消掉了一个真实观测点。
建议显式 `if close is None` 判空，0 保留 0。

### [P2-12] 行业资金流把缺失分项 `fillna(0)`，主力净额被低估且不披露

`backend/app/services/market_service.py:135-138`：
`_main = num("f66").fillna(0) + num("f72").fillna(0)`、`_retail = num("f78").fillna(0) + num("f84").fillna(0)`
—— 超大单/大单字段缺失时按 0 计，与本项目"缺失即 None，不得 0 兜底"红线不一致
（对照同仓 `market.py:235-243` 对成交额做了完整的"有效观测数"判据）。
建议：分项缺失时该行业整条标 `partial=True`，或至少在响应里给 `n_missing_components`。

### [P2-13] 事件摘要 `Number(p.pct).toFixed(2)` 缺字段时渲染 "NaN%"

`frontend/src/pages/Alerts/index.tsx:310`：
`bits.push(`涨跌 ${Number(p.pct).toFixed(2)}%（阈值 ${Number(p.threshold_pct)}%）`)`
`payload.pct` 缺失 ⇒ `Number(undefined) = NaN` ⇒ 界面显示"涨跌 NaN%（阈值 NaN%）"。
建议先 `Number.isFinite` 判空，否则整句省略。同类：`:314` `p.volume_hand as number`
绕过类型检查后直接进 `fmtNum`（`fmtNum` 有 `Number.isFinite` 守卫，安全）。

---

## 第二轮补充（回测 / 组合 / 模拟盘模块深度核对）

第一轮结论集中在行情 / 选股 / ETF / 自选；本轮补查 `backtest`、`portfolio`、`desk` 三条链路，
新发现 2 条 P1、3 条 P2。

### [P1] 参数寻优把"目标值 = NaN"的试验塞进 `top5` ⇒ 响应序列化抛错、**HTTP 500 破坏「恒 200 + 信封」契约**，且缓存路径反而正常（间歇性故障）

**模块**
- 后端：`backend/app/backtest/param_search.py:102-107`（grid）、`:247-252`（optuna）、`:53-58`（`SearchResult.top`）
- 后端：`backend/app/api/v1/backtest.py:638, 657, 670`（`evaluate` 返回 `sharpe`）、`:820-824`（`optimization` 块）
- 序列化（**已实测核实**）：`backend/.venv/Lib/site-packages/starlette/responses.py:196-203`
  `json.dumps(..., allow_nan=False)`；`fastapi/responses.py:8` 直接 re-export starlette 的 `JSONResponse`
- 前端：`frontend/src/api/strategyBacktest.ts:94`（`top5?: Array<{params, objective: number}>`）、
  `frontend/src/pages/Backtest/index.tsx:243`

**现象**

寻优时**只有"选最优"过滤了非有限值，收集 trials 没有过滤**：

```python
# backend/app/backtest/param_search.py:102-108
obj = float(out.get(objective, float("nan")))          # 退化 ⇒ NaN
t = Trial(params=params, objective=obj, metrics={...})
trials.append(t)                                        # ← NaN 试验照样入库
if np.isfinite(obj) and (best is None or obj > best.objective):   # ← 只有这里过滤
    best = t
```

`top()` 又无条件把 NaN 送进响应：

```python
# backend/app/backtest/param_search.py:53-58
ranked = sorted(self.trials, key=lambda t: t.objective, reverse=True)
return [{"params": t.params, "objective": round(t.objective, 6), **t.metrics}
        for t in ranked[:k]]        # round(nan, 6) 仍是 nan
```

而 `evaluate` 本身就**很容易**给出 NaN 目标值：

```python
# backend/app/api/v1/backtest.py:657（:638 / :670 同）
return {"sharpe": float(res.risk.get("sharpe", float("nan"))), ...}
```

`summarize_risk` 对"全程无交易 / 净值恒定"的参数组合（网格里 MA 5×5、RSI 阈值永不触发这类
**必然存在**的退化组合）算出 `std == 0` ⇒ sharpe = NaN。

NaN 一旦进入 `optimization.top5`，路由已经 `return ok(data)`，**错误发生在路由返回之后的响应渲染阶段**——
`AQPException` / 全局业务错误码全部来不及介入：

```python
# backend/.venv/Lib/site-packages/starlette/responses.py:196-203（已核实）
def render(self, content: Any) -> bytes:
    return json.dumps(content, ensure_ascii=False, allow_nan=False, ...).encode("utf-8")
# ⇒ ValueError: Out of range float values are not JSON compliant ⇒ ServerErrorMiddleware ⇒ 500
```

**更隐蔽的是间歇性**：`strategy-run` 先写 Redis 缓存再返回，而缓存用的是 **orjson**（把 NaN 写成 `null`）：

```python
# backend/app/api/v1/backtest.py:934-943
cached = await RedisClient.get(key)
if cached:
    data = orjson.loads(cached); data["from_cache"] = True; return ok(data)   # NaN 已变 null ⇒ 200 正常
data = await asyncio.to_thread(_run_strategy, req)
await RedisClient.set(key, orjson.dumps(data), ex=600)   # orjson: NaN → null，写缓存成功
return ok(data)                                          # ← 这一步才渲染 ⇒ 500
```

⇒ **同一请求：首次（未命中缓存）HTTP 500 + 纯文本 `Internal Server Error`；10 分钟内重试命中缓存 → 200 正常。**

**影响**

- 用户点"参数寻优"跑回测，界面得到的是 `api/client.ts` 对非 JSON / 5xx 的统一翻译
  （`ApiError(code=-1)`，网络异常文案），**不是业务错误码**，也不是"回测失败"——排障方向完全错。
- 前端 `types` 把 `objective` 声明为非空 `number`，即便拿到 `null` 也无从判断"这组参数无效"。
- 附带正确性缺陷：含 NaN 时 `sorted(reverse=True)` 的比较全为 False（NaN 与任何值比较都假），
  排序结果**不保证是"目标值最高的 5 组"**——展示给用户的"最优 5 组"可能名不副实。

**建议**
- `param_search.py` 收集阶段即过滤：`if np.isfinite(obj): trials.append(t)`（并在响应里给
  `n_invalid_trials` 披露被丢弃的组合数，不静默）；
- `top()` 内二次兜底：`objective: round(t.objective, 6) if t.objective == t.objective else None`；
- 与同函数已有的 `deflated_sharpe` 净化（`:821-823`）保持同一写法；
- 建议加一条**响应体净化中间件**（递归把 NaN/Inf 转 `null`），一次性堵住"NaN ⇒ 500"这类契约破坏，
  而不是在每个路由手写 `v == v`。

---

### [P1] 「最大回撤」同站三处两种符号：组合回测页显示 **+25.00%**（带正号），另两页显示负号

**模块**
- 后端：`backend/app/domain/metrics.py:91`（策略回测，**正值**）、
  `backend/app/domain/portfolio.py:152-153`（组合回测，**正值**）、
  `backend/app/trading/paper.py:455-460`（模拟盘，**负值**）
- 前端：`frontend/src/pages/Backtest/resultParts.tsx:42`（手加 `-`）、
  `frontend/src/pages/Portfolio/index.tsx:531-532`（直接 `fmtPct`）、
  `frontend/src/pages/OrderDesk/index.tsx:310-312`（原样显示 + 绿色）
- 格式化：`frontend/src/utils/format.ts:12`（`const sign = p > 0 ? '+' : ''`）

**现象**

```python
# backend/app/domain/portfolio.py:152-153  —— 正的量级
drawdown = 1.0 - nav / running_peak
max_dd = float(drawdown.max())
```
```python
# backend/app/trading/paper.py:455-460    —— 负的收益率
mdd = min(mdd, (e - peak) / peak)
```

三处前端三种渲染：

```tsx
// Backtest/resultParts.tsx:42 —— 正确：手加负号 + 绿色
value: kpi.max_drawdown != null ? `-${(kpi.max_drawdown * 100).toFixed(1)}%` : '—'

// OrderDesk/index.tsx:310-312 —— 正确：后端已是负数，绿色直显
<div className="num font-semibold text-success">{(account.max_drawdown * 100).toFixed(2)}%</div>

// Portfolio/index.tsx:531-532 —— 错：正值 + fmtPct 的 p>0 ⇒ '+'
value={result.metrics.max_drawdown != null && Number.isFinite(result.metrics.max_drawdown)
  ? fmtPct(result.metrics.max_drawdown * 100) : '—'}
```

`fmtPct(25)` ⇒ `"+25.00%"`，且这一格**没有 tone**（不染色）。

**影响**

用户在组合回测页读到「最大回撤 **+25.00%**」——一个带正号的"回撤"。同一产品内，
策略回测页显示 `-25.00%`（绿）、模拟盘页显示 `-12.00%`（绿），组合页却显示 `+25.00%`（无色）。
同一指标名、同一语义，三个屏幕两个符号，且组合页这个"+"最容易被误读为正收益。

**建议**
- 组合页改为 `-${(x * 100).toFixed(2)}%` + 绿色（与 `resultParts.tsx:42` 完全一致）；
- 更彻底：后端统一量纲（`paper.py` 的负值口径是"收益率"，最不易误读），
  并在 `types` 上把 `max_drawdown` 的符号写进注释，禁止再对回撤使用 `fmtPct`。

---

### [P2-14] 组合回测 8 个"可解释性披露"字段前端零消费，其中 `data_warnings` 在另一页有展示 → 同类披露两页不一致

**模块**：`backend/app/domain/portfolio.py:493-520` vs `frontend/src/types/portfolio.ts:85-104`

后端恒返回但 `PortfolioBacktestResult` **完全没有声明**、页面也零消费的字段：
`weighting`、`weights_normalization`（Σw≠1 时的归一因子）、`rebalance_log`（每次调仓的实际目标权重与回退标记）、
`friction_costs`（佣金/印花税/滑点，元）、`data_warnings`（晚上市 / 停牌缺口 / 疑似退市）、
`metrics.probabilistic_sharpe`、`metrics.deflated_sharpe`、`metrics.rf_basis`。

**同类披露的不一致**（这才是关键）：`data_warnings` 在研究页是**一等公民**——

```tsx
// frontend/src/pages/Research/index.tsx:686-688
{impact?.data_warnings?.length ? (<div>数据质量提示：{impact.data_warnings.join('；')}</div>) : null}
```

而组合回测页对同一类"数据质量提示"零出口。后果：组合里混入了**晚上市或疑似退市**的标的时，
回测净值照常渲染、结论照常展示，用户无从得知输入池有问题（这正是后端专门写 `data_warnings` 要防的事）。

**建议**：`PortfolioBacktestResult` 补齐上述字段；`data_warnings` 非空时在结果区顶部渲染黄条，
与 `Research/index.tsx:686` 同一组件/同一文案风格。

### [P2-15] `/backtest/strategy-run` 的 `signals[].nav` 用 `float("nan")` 兜底，与同函数 `curve` / `risk_out` 的 `v == v` 净化口径不一致

**模块**：`backend/app/api/v1/backtest.py:845` vs `:848`、`:851`

```python
signals = [{..., "nav": round(float(nav_map.get(s["date"], float("nan"))), 4)}  # ← 未净化
           for s in res.signals}]
curve = [{"benchmark": (round(float(b), 4) if b == b else None) ...}]           # ← 已净化
risk_out = {k: (round(float(v), 4) if v == v and isinstance(v, (int, float)) else None) ...}  # ← 已净化
```

同一个响应构造里三种 NaN 处理口径。只要 `res.signals[].date` 与 `res.nav_df["date"]` 的**键类型不完全一致**
（`nav_df` 经 `pd.DataFrame(rows)` 后日期列可能被规整为 `datetime64`/`Timestamp`，而信号里的 `date`
是策略状态机保存的**原始对象**，`strategy_base.py:85` `"date": self.date`），`nav_map.get()` 即 miss ⇒ NaN ⇒
按上一节的机制直接 **HTTP 500**。
相邻两行都已净化、唯独这行没有，属于明确的实现疏漏（运行时是否触发取决于日期类型，但风险敞口客观存在）。

**建议**：`n = nav_map.get(s["date"]); "nav": round(float(n), 4) if n == n else None`，与 `:848` 对齐。

### [P2-16] 组合回测前端从不传 `n_trials`，却消费不到后端算好的 `deflated_sharpe` —— 防过拟合指标成了摆设

**模块**：`backend/app/api/v1/portfolio.py:54`（`n_trials: int = Field(1, ...)`）、
`backend/app/domain/portfolio.py:173-186`、`:185-186`、
`frontend/src/types/portfolio.ts:20-31`（请求类型无 `n_trials`）、`:36-46`（`PortfolioMetrics` 无 PSR/DSR）

后端为组合回测**专门**实现了 Deflated Sharpe（多重试验惩罚），并把 NaN 净化成 `null`；
但前端请求里根本没有 `n_trials` ⇒ 恒按 `1` 计算（等于不做惩罚），且算出来的
`probabilistic_sharpe` / `deflated_sharpe` 在类型和页面上都查无此键。
对照策略回测页是**展示**这个值的（`pages/Backtest/index.tsx:228`
`{result.optimization.deflated_sharpe ?? '—'}`），同一产品的两个回测页对"防过拟合"披露不一致。

**建议**：请求类型补 `n_trials?`，`PortfolioMetrics` 补 `probabilistic_sharpe: number | null` /
`deflated_sharpe: number | null` / `rf_basis?: string`，并在指标区展示（缺失显 '—'）。

---

## 附：已核对但判定"无问题"的项（避免重复排查）

| 项 | 证据 |
|---|---|
| HTTP 恒 200 / 无裸 5xx | `core/auth.py:67,176,178,231,283`、`api/v1/notify.py:65` 全为 `status_code=200` + 业务码；全仓 `raise HTTPException` 仅此 6 处 |
| 前端网络异常/非 200 处理 | `api/client.ts:187-223`（`toNetworkApiError` + ApiError(code=-1) + 受限重试） |
| `/screener` vs `/screener/stocks` 分页语义 | `pages/Screener/index.tsx:272-279`（客户端切片，top_k ≤ 100 ≤ 200）；`StockList.tsx:98-108`（服务端分页，page/page_size/sort/dir 全回传） |
| `>200` 只静默截断 | `data/quotes_hub.py:36,97-103` 已分片 + 超限显式 `truncated/dropped_count` + WARNING；`api/v1/screener.py:663-699` 按 200 分片 |
| 金额/百分比主口径 | `data/screening.py:770-771`、`types/p1.ts:93`、`utils/format.ts:26-30`；`amount_yi/total_cap_yi/float_cap_yi`=亿、`pct/turnover`=%、`close/amount`=元 |
| 涨跌方向判空（高危项） | `utils/format.ts:4-13` 的 `pctClass/fmtPct` 先判空；`Etf/index.tsx:680-687`、`Etf/OverviewCards.tsx:105-120`、`Watchlist/index.tsx:333-343`、`Backtest/resultParts.tsx:28-38`、`OrderDesk/index.tsx:289-291` 均显式 `== null ⇒ 中性` |
| 情绪仪表 0 = 极度恐慌的有界刻度 | `MarketOverview/AiPicksPanel.tsx:58-96` 用 `score ?? null` + `Number.isFinite` + `hasScore` 三重守卫，未知时不画指针不显示数值 |
| ETF 概览 `net_inflow_yi` 可空 | `api/v1/etf.py:388-393` 源不可达置 None；`Etf/OverviewCards.tsx:117-120` 判空显"—" |
| 选股 `status` 降级提示 | `pages/Screener/index.tsx:387-392`：`result.status !== 'ok'` 时渲染黄条 + `message` |
| 推荐榜 `rank_pct` / `pred_score` | `market.py:429-432` 真实秩百分位；`AiPicksPanel.tsx:176-180` 消费一致（历史上"自造信心指数"已移除） |
| 模拟盘母单列表 P2-8 截断契约 | 后端 `desk.py:250-251` 给 `items/total/returned/limit/truncated`；前端 `OrderDesk/index.tsx:74-75, 453-457` 完整消费并显式提示"筛选与分页仅在此窗口内成立" |
| 模拟盘账户派生指标判空 | `OrderDesk/index.tsx:289-323`：`totalReturn / annualized_return / max_drawdown / sharpe` 全部 `!= null` 守卫 + 缺数显"样本不足"，未出现 `?? 0` |
| 组合回测指标判空 | `Portfolio/index.tsx:186-220, 527-534`：所有比率/百分数均 `!= null && Number.isFinite` 双守卫（唯一例外是回撤符号，见 P1） |
| 回测/组合/数据中心页面 `?? 0` 用法 | 对 `pages/{Portfolio,Backtest,OrderDesk,Report,DataCenter,Pipeline,CapacityAttribution,FactorStudio,DataQuality,Settings}` 全量 grep `?? 0` / `\|\| 0` / `?? 1`：**零命中** |

---

## 附：建议的回归防护（不在本次改动范围）

1. **契约级单测**：为 `/screener/stocks`、`/screener/watchlist`、`/etf/detail/{code}` 的响应键集与
   前端 TS 类型做"键名双向对账"断言（后端键 ⊆ 前端类型声明，前端读取的键 ⊆ 后端产出）。
2. **派生指标单一事实源**：`EtfTrackingPoint` 的日收益、`watchlist` 的涨跌幅，
   凡前端需要"日收益/日涨跌"的，一律由后端在响应里给出原始字段，禁止前端从累计值反推。
3. **兜底值静态检查**：对 `?? 0` / `|| 0` / `> 0` 加 lint 规则，
   要求其左侧表达式的静态类型必须是非空数值（本次 P2-06/P2-07/P2-13 均可被此规则捕获）。
