# 涨跌配色方向一致性 人工评审清单

**产出日期**：2026-10-01
**产出人**：color-auditor（gstack-aqp-cover 团队）
**用途**：本清单用于人工核对每个含涨跌语义的视觉区「文字 / 图形 / 图例」三方方向是否一致。
**为什么需要**：样式闸门 `frontend/scripts/check-style.mjs` 只能查硬编码色值，查不出方向
错误（见该文件第 18–30 行「已知盲区」：*"本脚本只保证不新增硬编码，不保证颜色语义正确……
闸门通过 ≠ 颜色没骗人"*）。本清单补的就是这个盲区。

**审计范围**：`frontend/src/**/*.{ts,tsx}` 全量（含 pages / components / lib / utils）。
**审计方式**：Python 正则全量提取（脚本见附录）→ 逐文件人工比对三方方向语义。
**只读审计**：本清单不修改任何源码。

---

## 判定口径

| 语义 | 色值 | 来源 |
|---|---|---|
| 涨（正 / 买 / 盈） | 红 `#D92B2B`（暗色 `#F87171`） | `up` token / `chartPalette().UP` / `.t-up` / `bg-up` |
| 跌（负 / 卖 / 亏） | 绿 `#12995B`（暗色 `#34D399`） | `down` token / `chartPalette().DOWN` / `.t-down` / `bg-down` |
| 空值 / 无数据 | **中性灰** `#94A3B8`（`FLAT` / `t-flat` / `ink-muted`） | **不得染成涨或跌** |

**正交解耦约定**（`chartTheme.ts` 头注释 + `index.css:83-89`）：
- 状态系 `DANGER #E11D48` / `SUCCESS #0D9488` / `WARN #D97706` **与**涨跌系**正交**——
  `danger ≠ 跌`，`success ≠ 涨`。按钮 / 徽章 / 提示条用状态系；价格数字 / K 线用涨跌系。
- 类别色 `CATEGORY_COLORS`（MA5/MA10 等）**不表达方向**，不参与本清单判定。

**判定符号**：`✅一致` / `🔴疑似不一致` / `🟡语义歧义(需产品决定)` / `⚪不适用(无方向语义)`

---

## 一、判定基元（被全站复用的「单一事实源」，先审它们）

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | `pctClass()` 涨跌文本类 | `utils/format.ts:4-7` | `p>0→t-up` / `p<0→t-down` / `≈0→t-flat` | — | — | ✅一致 | 唯一文本方向来源，`null` 正确走 `t-flat` |
| 2 | `upDownColor()` 图表取色 | `lib/chartTheme.ts:130-134` | — | `>=0→UP` / `<0→DOWN` / `null→FLAT` | — | ✅一致 | `null` 走中性 FLAT，未替"未知"表态 |
| 3 | `chartPalette().UP/DOWN` | `lib/chartTheme.ts:79-80` | — | 运行时读 CSS 变量 | — | ✅一致 | 与 `tailwind.config.js:23-24` 同值 |
| 4 | `Sparkline` 迷你走势 | `components/charts/Sparkline.tsx:39-119` | — | `color` 由调用方传 | — | ⚪不适用 | **纯容器**：色值由消费方决定，正因如此**每处调用点都要单独核**（见 §2、§3） |
| 5 | `MiniSpark` 迷你走势 | `MarketOverview/pieces.tsx:44-79` | — | `up ? pal.UP : pal.DOWN` | — | ✅一致 | 但 `up` 为 `boolean` 而非 `boolean\|undefined`（见 §2-8 隐患） |
| 6 | `PctCell` 涨跌单元格 | `components/ui/index.tsx:165-169` | `>1e-9→t-up` / `<-1e-9→t-down` / 否则 `t-flat` | — | — | ✅一致 | 用 epsilon 判零，比 `pctClass` 更严，方向无冲突 |
| 7 | `KpiCompare` 较昨日对比行 | `components/charts/KpiBits.tsx:100-107` | `delta>0→t-up` / `delta<0→t-down` | — | — | ✅一致 | `null` 显 `—` 不染色，正确 |
| 8 | `ScoreBadge` 评分徽章 | `components/ui/index.tsx:110-117` | `>=0.7 bg-up-bg text-up` | — | — | 🟡语义歧义 | 见 §4-1 |
| 9 | `FactorBar` 因子贡献条 | `components/ui/index.tsx:145-162` | `pos ? t-up : t-down` | `pos ? bg-up : bg-down` | — | ✅一致 | 图形与文字同判据 `value >= 0`，同色 |
| 10 | `SplitBar` 内外盘条 | `components/ui/index.tsx:194-225` | 左 `t-up` / 右 `t-down` | 左 `bg-up` / 右 `bg-down` | — | ✅一致 | 买盘红/卖盘绿，三方同向 |
| 11 | `ProbabilityBar` 概率条 | `components/ui/index.tsx:121-142` | — | `>=0.6 bg-up` / `>=0.4 bg-warn` / else `bg-down` | — | 🟡语义歧义 | 见 §4-2 |
| 12 | `.t-up/.t-down/.t-flat` CSS 类 | `index.css:79-81` | 读 `--up/--down/--flat` | — | — | ✅一致 | 真源 |

---

## 二、逐页清单

### 2.1 市场概览 `/market`（涨跌语义最密集，重点页）

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | Hero 卡（上证指数）主值+涨跌 | `MarketOverview/KpiCards.tsx:60,73,102` | `up?t-up:t-down`，`sub` 同色 | `MiniSpark up={up??false}` 同向 | — | ✅一致 | 文字/图形共用 `up` 单一来源 |
| 2 | 次级 KPI 卡（沪深300/资金/AI RankIC） | `KpiCards.tsx:43,112-132` | `up==null→中性`，否则涨跌 | 无图形 | — | ✅一致 | `flowVal/rank_ic` 均显式判空后传 `up` |
| 3 | 涨跌分布·红盘/绿盘文字 | `BreadthPanel.tsx:127-128` | `text-up 红盘` / 绿盘中性灰 | — | — | ✅一致 | 绿盘文字用 `ink-muted` 而非 `text-down`（见 §3-1 备注） |
| 4 | 涨跌分布·密度方块网格 | `BreadthPanel.tsx:19-32,58-64` | — | 9 档：`≤-7%→DOWN`…`≥7%→UP` | 图例色块与方块同色 | ✅一致 | 发散色阶两端锚定 `DOWN`/`UP`，中点为 `FLAT` |
| 5 | 涨跌分布·红绿盘环形图 | `BreadthPanel.tsx:79-81` | 文字「红盘(涨)」 | `p.UP` | 图例名「红盘(涨)=UP」 | ✅一致 | 三分量名/色对应正确 |
| 6 | 涨跌分布·市场概况 6 宫格 | `BreadthPanel.tsx:145-152` | `bg-up`/`bg-down` 色块含数字 | 同 | — | ✅一致 | `n==null→'—'` 不染色，正确 |
| 7 | 涨跌分布·红盘/绿盘占比文字 | `BreadthPanel.tsx:155-157` | `text-up` / `text-down` | — | — | ✅一致 | `null→ink-muted`，正确 |
| 8 | 资金流向·主力/散户双向柱 | `MoneyFlowPanel.tsx:52,58` | — | 主力 `>=0→UP`；散户同向浅色 `withAlpha(UP,0.5)` | 图例「主力/散户」中立名 | ✅一致 | 散户**不换色相**只降透明度，方向与主力一致（设计得当） |
| 9 | 资金流向·板块金额列表 | `MoneyFlowPanel.tsx:98` | `total_yi>=0?t-up:t-down` | — | — | ✅一致 | 与同图柱色同判据 |
| 10 | 资金流向·降级汇总行 | `MoneyFlowPanel.tsx:116` | `null→ink-muted`；`amount` 项走 `text-ink` | — | — | ✅一致 | 成交额**刻意不染色**（非方向指标），正确 |
| 11 | 热门板块·涨跌比例条 | `HotSectorsPanel.tsx:41-42` | — | `pct>=0→bg-up 左半` / 否则 `bg-down 右半` | — | ✅一致 | 双向从中心发散，方向与符号一致 |
| 12 | 热门板块·涨跌幅列 | `HotSectorsPanel.tsx:46` | `pctClass` | — | — | ✅一致 | |
| 13 | 热门板块·领涨股涨跌幅 | `HotSectorsPanel.tsx:51` | `pctClass` | — | — | ✅一致 | |
| 14 | AI 精选·AI 标签分档徽章 | `AiPicksPanel.tsx:51-55` | `强烈看多→bg-up text-white` / `看多→bg-danger-bg text-up` / `看空→bg-down-bg text-down` / `强烈看空→bg-down text-white` | — | — | 🟡语义歧义 | 「看多」档底色用 `danger-bg`（见 §4-3） |
| 15 | AI 精选·未来5日预测列 | `AiPicksPanel.tsx:176` | `pred_score>=0?t-up:t-down` | — | — | ✅一致 | |
| 16 | AI 精选·迷你K线 | `AiPicksPanel.tsx:33-34` | — | `close>=open→UP` | — | ✅一致 | 阳线红/阴线绿，与全站 K 线口径一致 |
| 17 | AI 精选·资讯情绪着色 | `AiPicksPanel.tsx:191-192` | `positive→text-up` / `negative→text-down` | — | — | ✅一致 | 情绪正=红=涨，向一致 |
| 18 | AI 精选·情绪仪表盘刻度 | `AiPicksPanel.tsx:77-81,111` | 图例「恐慌=绿 / 中性=灰 / 贪婪=红」 | 色带 `0→DOWN` … `100→UP` | 文字图例同向 | ✅一致 | 三方严格对应，`score==null` 不画指针，正确 |

### 2.2 选股中心 `/screener`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 概览卡·今日胜率 | `Screener/StatsCards.tsx:124-129` | `>=50→t-up` / `<50→t-down` / `null→中性` | `Sparkline color={pal.UP}` **常量红** | — | 🟡语义歧义 | 见 §4-4（已修复历史矛盾，但图形仍为常量色） |
| 2 | 概览卡·平均涨跌幅 | `StatsCards.tsx:132-137` | `>=0→t-up` / `null→中性` | `Sparkline color={pal.UP}` **常量红** | — | 🟡语义歧义 | 见 §4-4 |
| 3 | 概览卡·平均 Score | `StatsCards.tsx:140-144` | — | `Sparkline color={pal.BRAND}` 品牌蓝 | — | ⚪不适用 | Score 无涨跌语义，用品牌色**正确** |
| 4 | 概览卡·强信号构成环 | `StatsCards.tsx:76-87,152-153` | `占比 xx%` 用 `t-up` | `Donut` 用 `DANGER/WARN/SUNKEN` | — | 🟡语义歧义 | 见 §4-5（两个问题：环形色系 + 占比文字用涨色） |
| 5 | 概览卡·股票数量构成环 | `StatsCards.tsx:119` | — | `Donut [BRAND, SUNKEN]` | — | ⚪不适用 | 计数无方向，正确 |
| 6 | 概览卡·覆盖行业构成环 | `StatsCards.tsx:162-164` | — | `Donut [SUCCESS, SUNKEN]` | — | ⚪不适用 | 集中度无方向，正确 |
| 7 | 分布图·涨跌幅分布柱 | `Screener/DistributionCharts.tsx:196-201` | — | 桶上界 `hi<=0→DOWN` else `UP` | — | ✅一致 | 用**桶上界**判定而非标签首字符，稳健 |
| 8 | 分布图·行业分布柱 | `DistributionCharts.tsx:166` | — | `BRAND` | — | ⚪不适用 | 行业计数无方向，正确 |
| 9 | 结果表·涨跌幅列 | `Screener/StockList.tsx:240`、`Screener/index.tsx:470,544,629,736` | `pctClass` | — | — | ✅一致 | 全表统一走基元 |
| 10 | 结果表·Score 百分比列 | `Screener/index.tsx:638` | `score>=0?t-up:t-down` | — | — | ✅一致 | |

### 2.3 ETF 中心 `/etf`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 概览卡·ETF 数量 | `Etf/OverviewCards.tsx:84-91` | — | `Sparkline BRAND` | — | ⚪不适用 | 计数无方向，正确 |
| 2 | 概览卡·总规模 | `OverviewCards.tsx:93-96` | — | `Sparkline WARN` | — | ⚪不适用 | 规模无方向，正确 |
| 3 | 概览卡·今日平均涨跌幅 | `OverviewCards.tsx:105-111` | `avg_pct>=0→t-up` / `null→中性` | `Sparkline pal.UP` 常量红 | — | 🟡语义歧义 | 见 §4-4 |
| 4 | 概览卡·资金净流入 | `OverviewCards.tsx:113-125` | `net_inflow>=0→t-up` / `null→中性` | `Sparkline pal.UP` 常量红 | — | ✅一致 | **历史缺陷已修复**：曾硬编码绿 `#16A34A` 与文字红相反；现与文字同为涨色红，方向自洽 |
| 5 | 概览卡·成交额 | `OverviewCards.tsx:127-129` | — | `Sparkline CATEGORY_COLORS.C3` | — | ⚪不适用 | 成交额无方向，正确 |
| 6 | ETF 列表·涨跌幅列 | `Etf/index.tsx:605,733,810` | `pctClass` | — | — | ✅一致 | |
| 7 | ETF 列表·主力净流入列 | `Etf/index.tsx:650` | `>=0→t-up` / `null→不染色` | — | — | ✅一致 | `null` 显 `—` 且中性，正确 |
| 8 | ETF 列表·流入占比列 | `Etf/index.tsx:656` | `>=0→t-up` / `null→不染色` | — | — | ✅一致 | |
| 9 | ETF 对比曲线 | `Etf/PerformanceChart.tsx:56-57` | — | `COLORS[i%n]` 类别色 | 图例中立名 | ⚪不适用 | 多标的对比是类别色，非方向色，正确 |
| 10 | ETF 规模柱+均线 | `Etf/index.tsx:478-480` | — | `CATEGORY_COLORS.C1/C2` | — | ⚪不适用 | 类别色，正确 |

### 2.4 ETF 详情 `/etf/:code`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 头部·最新价 / 涨跌幅 | `EtfDetail/index.tsx:447,453` | `pctClass(header?.pct)` | — | — | ✅一致 | 价格与涨跌幅同色，正确 |
| 2 | 成交量柱 | `EtfDetail/index.tsx:217` | — | `close>=open→UP` | — | ✅一致 | 与 K 线口径一致 |
| 3 | 归一化净值曲线 | `EtfDetail/index.tsx:160-161` | — | `CATEGORY_COLORS.C1/C2` | 图例「ETF / 基准指数」 | ⚪不适用 | 类别色，正确 |
| 4 | 回撤面积图 | `EtfDetail/index.tsx:196-198` | — | `CATEGORY_COLORS.C3` | — | ⚪不适用 | 类别色（回撤恒负，用类别色避免误导），可接受 |
| 5 | 估值/费率/规模 仪表盘组 | `EtfDetail/index.tsx:302-305` | — | `BRAND/SUCCESS/WARN/C3` | 各自单色 | 🟡语义歧义 | 见 §4-6 |
| 6 | 公告情绪仪表 | `EtfDetail/index.tsx:733` | — | `WARN` 常量 | — | 🟡语义歧义 | 见 §4-6 |

### 2.5 个股详情 `/stock/:code`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 头部·最新价/涨跌幅 | `StockDetail/index.tsx:319-320` | `pctClass(latest.pct)` | — | — | ✅一致 | |
| 2 | 头部·预测收益 | `StockDetail/index.tsx:325,418` | `pctClass(pred_return*100)` | — | — | ✅一致 | |
| 3 | ST 徽章 | `StockDetail/index.tsx:313` | `danger-bg + text-up` | — | — | 🟡语义歧义 | ST 是风险状态却用涨色文字，见 §4-7 |
| 4 | 资金流向·主力净流入条 | `StockDetail/index.tsx:553` | `value` 带 `+/-` 号 | `RatioBar tone={inflow?'bg-up':'bg-down'}` | — | ✅一致 | `inflow` 用 `?? 0` 兜底（见 §3-3 隐患） |
| 5 | 资金流向·北向持股条 | `StockDetail/index.tsx:565` | — | `bg-brand-500` | — | ⚪不适用 | 持股比例中性，正确 |
| 6 | 风险度量·最大回撤 | `StockDetail/index.tsx:794-797` | `t-down`（有值）/ `null→中性` | — | — | ✅一致 | 回撤定义性为负，绿=跌语义正确 |
| 7 | 风险度量·夏普比率 | `StockDetail/index.tsx:799` | `>=0→t-up` / `null→中性` | — | — | ✅一致 | |
| 8 | 风险度量·波动率分位条 | `StockDetail/index.tsx:810-811` | — | `>=80→bg-up` / `<=20→bg-down` / else `BRAND` / `null→BRAND` | — | 🟡语义歧义 | 见 §4-8 |
| 9 | 筹码分布·平均成本 | `StockDetail/index.tsx:842` | `cost<=现价→t-down`（获利）/ 否则 `t-up` | — | — | ✅一致 | 成本低于现价=盈利=跌色绿；**方向按盈亏定义，与涨跌同向**（见 §3-2 讨论） |
| 10 | 筹码分布·获利/套牢条 | `StockDetail/index.tsx:855-861` | 左「获利」`t-up` / 右「套牢」`t-down` | 左 `bg-up` / 右 `bg-down` | — | ✅一致 | |
| 11 | 筹码分布·密度曲线 | `StockDetail/index.tsx:864` | — | 现价下方 `bg-up/50`（获利）/ 上方 `bg-down/50`（套牢） | — | ✅一致 | 与图例文案方向一致 |
| 12 | 基本面·盈利指标条/数值 | `StockDetail/index.tsx:929,977` | `v>=0→t-up` / `null→中性` | `bg-up`/`bg-down` | — | 🟡语义歧义 | ROE/毛利率/净利率是**盈利能力**，非涨跌（见 §4-9） |
| 13 | 基本面·估值类 PE/PB | `StockDetail/index.tsx:924` | `bg-brand-500` | — | — | ⚪不适用 | `better:'low'` 刻意不染涨跌色，**正确设计** |
| 14 | 自选按钮 / 加入自选态 | `StockDetail/index.tsx:366` | `danger-bg text-up` | — | — | 🟡语义歧义 | 收藏态用涨色，见 §4-10 |
| 15 | 公告列表·涨跌 | `StockDetail/index.tsx:1031` | `change_ratio>0→t-up` / `null→中性` | — | — | ✅一致 | |

### 2.6 回测 `/backtest`（历史缺陷重灾区）

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | KPI 卡·年化收益/夏普 | `Backtest/resultParts.tsx:30-39,55,60-65` | `up?t-up:t-down`，`undefined→ink-muted` | `SparkArea color = up?UP:DOWN` | — | ✅一致 | **历史缺陷已修复**：曾 `up?蓝:红`（蓝涨红跌）与文字红涨绿跌相反；现文字/图形同源 |
| 2 | KPI 卡·基准年化 | `resultParts.tsx:33-36,63` | 同上 | `color = WARN`（常量） | — | 🟡语义歧义 | 基准线刻意用 WARN 区分策略，但文字仍随涨跌染色（见 §4-11） |
| 3 | KPI 卡·最大回撤 | `resultParts.tsx:42-43` | `null→中性`，有值恒 `t-down` | `color = up(false)→DOWN` | — | ✅一致 | 回撤定义性为负，绿正确 |
| 4 | 净值曲线·买卖标记点 | `resultParts.tsx:145-147` | 标签文字「买入/卖出」 | `买入→UP` / `卖出→DOWN` | — | ✅一致 | 买红卖绿，与全站同向 |
| 5 | 净值曲线·策略/基准线 | `resultParts.tsx:135,153` | — | `BRAND` / `WARN` | 图例「策略净值/基准净值」 | ⚪不适用 | 两条线的区分色，非方向色，正确 |
| 6 | 月度收益柱图 | `resultParts.tsx:211-218` | tooltip 带 `+` 号 | `value>=0→UP` else `DOWN` | — | ✅一致 | **历史缺陷已修复**：曾「正蓝/负橙」，现红盈绿亏 |
| 7 | 交易记录·方向徽章 | `resultParts.tsx:274-275` | `买入→bg-danger-bg text-up` / 卖出→`bg-success-bg text-down` | — | — | 🟡语义歧义 | 买入档底色用 `danger-bg`（见 §4-3 同类问题） |
| 8 | 交易记录·单次盈亏 | `resultParts.tsx:282` | `pnl>=0→t-up` / `null→中性` | — | — | ✅一致 | |
| 9 | 风险指标表 | `resultParts.tsx:298-305,316` | 全部 `text-ink`（**不染色**） | — | — | ⚪不适用 | Alpha/Beta/胜率统一中性——可接受但偏保守（见 §3-4 观察） |
| 10 | 过拟合比 | `Backtest/index.tsx:166-171` | `null→中性` / `>1.5→text-danger` / else `text-up` | — | — | 🟡语义歧义 | 见 §4-12 |
| 11 | 折外夏普 vs IS 夏普 | `Backtest/index.tsx:194` | `oos>=is→text-up` else `text-down` | — | — | 🟡语义歧义 | 见 §4-13 |
| 12 | 最优夏普 / Top5 首行 | `Backtest/index.tsx:218,244` | `text-up` 常量 | — | — | 🟡语义歧义 | 见 §4-14 |
| 13 | 信号分析·净值线 | `Backtest/SignalAnalysisPanel.tsx:65` | — | `BRAND` | — | ⚪不适用 | 正确 |
| 14 | TopK 面板 | `Backtest/TopKPanel.tsx` | 未命中涨跌关键词 | — | — | ⚪不适用 | 无方向语义 |

### 2.7 组合 `/portfolio`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 净值曲线 | `Portfolio/index.tsx:112-116` | — | 两条线**未显式指定色**（ECharts 默认调色板） | 图例「组合净值/基准净值」 | ⚪不适用 | 见 §3-5 隐患（默认色不跟随主题） |
| 2 | 年度收益柱图 | `Portfolio/index.tsx:135,142` | — | `portfolio>=0→UP` else `DOWN` | 柱标签 `fmtPct` | ✅一致 | 正收益红/负收益绿，正确 |
| 3 | 持仓漂移堆叠面积 | `Portfolio/index.tsx:162` | — | `areaStyle:{}`（默认色） | `legend` 为股票代码 | ⚪不适用 | 类别堆叠，非方向色 |
| 4 | 收益指标表·总收益/年化 | `Portfolio/index.tsx:186-187` | `pctClass` | — | — | ✅一致 | |
| 5 | 风险四宫格·阿尔法 | `Portfolio/index.tsx:220` | `alpha>=0→t-up` / else `t-down` | — | — | ✅一致 | |
| 6 | 权重告警 / 日期告警 | `Portfolio/index.tsx:421,440` | `text-up` | — | — | 🟡语义歧义 | 校验错误用涨色红，见 §4-15 |
| 7 | 回测结果卡·年化 | `Portfolio/index.tsx:529` | `pctClass(cagr)` | — | — | ✅一致 | |

### 2.8 研究 `/research`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 因子相关性热力图 | `Research/parts.tsx:104` | — | `inRange [DOWN, CARD, UP]` | 右侧 visualMap 色带 | ✅一致 | 正相关红/负相关绿，与全站同向 |
| 2 | 因子 IC 曲线组 | `parts.tsx:156` | — | `palette[i%n]` 类别色 | 图例因子名 | ⚪不适用 | 类别色，正确 |
| 3 | 训练/清洗/测试 Gantt 条 | `parts.tsx:194-200` | — | `GANTT_TRAIN/PURGE/TEST` | — | ⚪不适用 | 阶段类别色，正确 |
| 4 | 分层收益曲线 | `parts.tsx:235-236,282-284` | — | `C3` / `BRAND` | — | ⚪不适用 | 类别色 |
| 5 | 成本冲击·成交价散点 | `parts.tsx:323` | — | `side==='buy'→UP` else `DOWN` | 图例「成交价」 | ✅一致 | 买红卖绿 |

### 2.9 容量归因 `/capacity`

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 风格贡献柱 | `CapacityAttribution/index.tsx:118-119` | 标签 `+/-` 号 | `data>=0→UP` else `DOWN` | — | ✅一致 | 正向贡献红/负向绿 |

### 2.10 数据中心 / 数据质量（状态语义为主）

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 训练门禁点 | `DataCenter/TrainPanel.tsx:37` | — | `gateOk→bg-down` else `bg-up` | — | 🟡语义歧义 | **门禁通过=绿/不通过=红**，与涨跌无关（见 §4-16） |
| 2 | 同步进度条 | `DataCenter/index.tsx:931-932` | 文案「失败N只」 | `null→hair2` / `>0→warn` / else `down` | — | 🟡语义歧义 | 见 §4-17（`else→bg-down` 与"零失败"语义绑定） |
| 3 | 确认弹窗按钮 | `DataCenter/index.tsx:1179` | — | `bg-up` 白字 | — | 🟡语义歧义 | **危险操作按钮用涨色红**——本地用红=危险，与"红=涨"同色值不同语义（见 §4-18） |
| 4 | 模型质量雷达/曲线 | `DataCenter/index.tsx:118-201` | — | `BRAND/INKM/SUNKEN` | — | ⚪不适用 | 无方向语义 |
| 5 | 数据质量图 | `DataQuality/index.tsx:74-81` | — | `HAIR2/CARD` | — | ⚪不适用 | 无方向语义 |

### 2.11 自选 / 预警 / 设置 / 订单 / 其他

| # | 视觉区 | 文件:行 | 文字方向 | 图形方向 | 图例方向 | 判定 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 自选·迷你走势 | `Watchlist/index.tsx:73` | 行内涨跌幅 `pctClass:435` | `up==null→FLAT` / `up→UP` / else `DOWN` | — | ✅一致 | 未知走中性灰，三方自洽 |
| 2 | 自选·相关性热力图 | `Watchlist/index.tsx:131` | — | `inRange [DOWN, CARD, UP]` | visualMap | ✅一致 | 与 Research 同口径 |
| 3 | 自选·KPI 图标（bars/trend/flow/bell） | `Watchlist/index.tsx:99` | — | `UP` 描边 | — | 🟡语义歧义 | 装饰性图标用涨色，见 §4-19 |
| 4 | 自选·K线状态徽章 | `Watchlist/index.tsx:38-45` | 均线多头=`success` / 空头=`danger` | — | — | 🟡语义歧义 | 见 §4-20（多头=涨却用 success 青绿） |
| 5 | 预警·删除确认按钮 | `Alerts/index.tsx:687` | — | `bg-up` 白字 | — | 🟡语义歧义 | 同 §4-18 |
| 6 | 预警规则列表 | `Alerts/index.tsx`（其余） | 未命中涨跌词 | — | — | ⚪不适用 | 无方向语义 |
| 7 | 订单台·下单按钮/曲线 | `OrderDesk/index.tsx:249,419,203-205` | — | `bg-up` / `BRAND` | — | 🟡语义歧义 | 见 §4-21 |
| 8 | 设置·连接状态点 | `Settings/index.tsx:80,86,557` | 「已连接」`text-success` | `bg-down` 点 | — | 🟡语义歧义 | **成功=绿点** 用 `bg-down`（见 §4-22） |
| 9 | 设置·AKShare 徽标 | `Settings/index.tsx:415` | — | `bg-down` 白字「AK」 | — | 🟡语义歧义 | 品牌标识用跌色绿，见 §4-22 |
| 10 | 设置·危险确认按钮 | `Settings/index.tsx:118` | — | `danger?bg-up:brand` | — | 🟡语义歧义 | 同 §4-18 |
| 11 | 顶栏·未读角标 | `Topbar/index.tsx:303` | — | `bg-up` 白字 | — | 🟡语义歧义 | 角标红用 `bg-up`，见 §4-23 |
| 12 | 因子工坊·特征/收益曲线 | `FactorStudio/index.tsx:240,243` | — | `p.UP` / `p.DOWN` 两条曲线 | 图例 | 🟡语义歧义 | 见 §4-24 |
| 13 | 因子工坊·运行按钮 | `FactorStudio/index.tsx:311` | — | `bg-up` 白字 | — | 🟡语义歧义 | 同 §4-18 |
| 14 | 因子健康卡 | `components/FactorHealthCard.tsx` | 未命中涨跌词 | — | — | ⚪不适用 | 无方向语义 |
| 15 | 数据新鲜度 | `components/DataFreshness.tsx` | 未命中涨跌词 | — | — | ⚪不适用 | 无方向语义 |

---

## 🔴 疑似不一致（需立即修）

> 结论：**未发现"文字与图形方向相反"的硬矛盾**。上一轮实证的 4 处（`resultParts` KPI sparkline
> 蓝涨红跌、月度柱正蓝负橙、`OverviewCards` 红字配绿 sparkline、`StatsCards` win_rate/avg_pct
> 方向不一致）经逐行复核**均已修复且自洽**。
>
> 但本轮发现 **1 处"参数方向自相矛盾"**，虽不改变已渲染像素，仍属应先修的隐患：

- **🔴 1. `MiniSpark` 的 `up` 参数与调用方的中性语义冲突** — `MarketOverview/pieces.tsx:44-45` + `KpiCards.tsx:77`
  - `MiniSpark({ data, up }: { up: boolean })` 声明 `up` **不可空**；但调用处 `KpiCards.tsx:77` 传的是
    `up={up ?? false}` —— 当 `up === undefined`（方向未知，`HeroKpi` 已用 `text-ink-muted` 表态为中性）时，
    **图形被强制落到 `false` 分支 = 染成跌色绿**。
  - 后果：文字说「未知」（灰），图形说「跌」（绿）——**正是本轮要消灭的"替未知表态"模式**，
    且与同文件 `Watchlist/index.tsx:73` 的正确写法（`up == null ? FLAT : up ? UP : DOWN`）不一致。
  - 当前实际影响有限（HeroKpi 的 `up` 来自 `sh.pct >= 0`，恒非 undefined），
    但**契约本身已写错**，一旦上层改成可空即静默出错。
  - 建议：`MiniSpark` 的 `up` 改 `boolean | undefined`，`undefined → pal.FLAT`，调用处去掉 `?? false`。

---

## 🟡 语义歧义（需产品决定）

> 这类位置的**方向本身不矛盾**（文字/图形同色），但**色值承担了两种语义**：
> 一处把 `up`(红)/`down`(绿) 用来表达"危险/成功/状态"，与"涨/跌"混用同一色值。
> 这正是 `index.css:83-89` 已记录的「语义冲突债」，需产品决定是否引入**第三组色**或改文案。

### A. 状态/操作类误用涨跌色（红=危险 撞 红=涨）

- **4-18 危险操作按钮统一用 `bg-up`（红）** — 5 处：
  `DataCenter/index.tsx:1179`、`Alerts/index.tsx:687`、`Settings/index.tsx:118`、
  `FactorStudio/index.tsx:311`、`OrderDesk/index.tsx:249,419`
  - 语义实为"危险/删除/确认"，红是**危险语义**而非**涨语义**。色值与 `up` 完全相同 ⇒ 同一红色在
    行情区=涨、在按钮区=危险。用户可能把"红色确认按钮"误读为"看多"。
  - 建议：改走 `danger` token（`#E11D48` 玫红），与 `up`(`#D92B2B` 纯红)色相区分开——
    `index.css:27-28` 声明的解耦意图正是为此，此处却仍用 `bg-up`。
- **4-22 设置页「成功/已连接」用 `bg-down`（绿）** — `Settings/index.tsx:80,86,415,557`
  - 成功语义走 `down`(绿)，与"跌"同色值。文字已用 `text-success`（青绿），**图形点却用 `bg-down`**
    ⇒ 同一徽章内文字色(青绿)与圆点色(股市绿)**色相不一致**。
  - 建议：圆点改 `bg-success`。
- **4-17 同步进度条 `else→bg-down`** — `DataCenter/index.tsx:932`
  - "零失败 ⇒ 绿"用 `bg-down`；同屏失败态用的是 `bg-warn`。三态（未知灰/失败琥珀/成功股市绿）
    中成功色应走 `success`。

### B. 文案用涨色但语义非涨

- **4-15 表单校验错误用 `text-up`（红）** — `Portfolio/index.tsx:421,440`
  - "权重超限 / 日期顺序错"是**校验错误**，应走 `text-danger`。当前红与涨色同值。
- **4-23 顶栏未读角标 `bg-up`（红）** — `Topbar/index.tsx:303`
  - 未读数是**通知计数**，非涨。红在此是"提醒"，应走 `danger`。
- **4-19 自选 KPI 装饰图标 `stroke={p.UP}`** — `Watchlist/index.tsx:99`
  - 注释自陈"装饰性，不表示涨跌"（`:82-84`），却直接用了涨跌 token 的红。
    注释说不用，代码在用 —— 属"注释约束不住代码"的典型。应改用 `BRAND`/`danger`。
- **4-7 ST 徽章 `bg-danger-bg text-up`** — `StockDetail/index.tsx:313`
  - ST 是**风险标记**，底色已用 `danger-bg` 表意危险，文字却用 `text-up` 涨色 ⇒ 底色/文字分属两套语义系。
- **4-10 自选态 `bg-danger-bg text-up`** — `StockDetail/index.tsx:366`
  - "已加入自选"用危险底色 + 涨色文字，语义混杂。

### C. 指标阈值被染成涨跌色（方向定义可疑）

- **4-1 `ScoreBadge` 高分用 `bg-up-bg text-up`** — `components/ui/index.tsx:111`
  - Score ≥0.7 是"模型评分高"，非"涨"。用涨色表"好"= 把评分当收益方向。
  - 注意：同文件 `StatsCards.tsx:154-157` 的**强信号环**已刻意改用 `DANGER/WARN`
    （注释明言"避免把强信号说成上涨"）—— 两处口径**不一致**，`ScoreBadge` 未跟进。
- **4-5 强信号占比文字用 `t-up`** — `StatsCards.tsx:151`
  - 环形已解耦为 `DANGER/WARN/SUNKEN`（正确），但紧邻的"占比 xx%"文字仍 `t-up` 红
    ⇒ **同一张卡内环形(琥珀/玫红)与文字(红)色系不一致**。建议文字改 `text-ink` 或 `t-danger`。
- **4-9 基本面盈利能力条用 `bg-up/bg-down`** — `StockDetail/index.tsx:929,977`
  - ROE / 毛利率 / 净利率是**盈利能力**，正负 ≠ 涨跌。同文件 PE/PB（`better:'low'`）
    已刻意用 `bg-brand-500` 中性色（`:924`），**口径分裂**：估值类中性、盈利类涨跌。
  - 建议：盈利类也走中性或 `success`，或产品明确定义"高盈利=红"为可接受约定。
- **4-8 波动率分位条 `>=80→bg-up`** — `StockDetail/index.tsx:810-811`
  - "高波动"用涨色红。高波动是**风险**不是**上涨**。应走 `danger`/`warn`。
- **4-12 过拟合比 `else→text-up`** — `Backtest/index.tsx:169`
  - 比值 ≤1.5 走 `text-up`(红=涨)，但该值语义是"过拟合程度低=**好**"，非"涨"。
    异常态已用 `text-danger`，正常态混入涨色。建议 `text-ink`/`text-success`。
- **4-13 折外夏普 `>=is→text-up`** — `Backtest/index.tsx:194`
  - "OOS ≥ IS"是**泛化质量**判断，非涨跌。用涨色红表"好"。
- **4-14 最优夏普/Top5 首行 `text-up` 常量** — `Backtest/index.tsx:218,244`
  - 常量红表"最优/第一名"，与涨跌无关。属强调色滥用。
- **4-16 训练门禁点 `gateOk→bg-down`** — `TrainPanel.tsx:37`
  - 门禁通过=绿、不通过=红。此处绿/红是**通过与否**，非涨跌。虽直观但复用涨跌 token。
- **4-20 自选 K 线状态徽章** — `Watchlist/index.tsx:38-45`
  - 「均线多头」=`success`(青绿)、「均线空头」=`danger`(玫红)。
    多头(看涨)配青绿、空头(看跌)配玫红 —— 与全站**红涨绿跌相反**。
  - 这是**状态色系**（success/danger）与**涨跌色系**方向相反的固有冲突：
    多头是好事→success，但按涨跌口径多头该是红。**需产品定夺**用哪套。
  - ⚠️ 这是全清单**最接近"方向矛盾"的一处**，虽三方一致（文字/底色/图标都用状态系），
    但**与相邻的涨跌幅列（红涨绿跌）并排显示时，同一行可能出现"绿徽章+红数字"**。

### D. 图形常量色 vs 文字动态色（三方中的"图形"未跟随方向）

- **4-4 `Sparkline` 在涨跌卡里用常量 `pal.UP`（红）** — 3 处：
  `StatsCards.tsx:129,137`（今日胜率 / 平均涨跌幅）、`OverviewCards.tsx:111`（今日平均涨跌幅）
  - 文字 `tone` 随数值变红/绿，但**图形恒为红**。
  - ⚠️ 该设计**有意为之**：`Sparkline` 画的是 30 日**历史序列**，一条序列没有单一"方向"，
    故用固定色。**不构成"文字与图形方向相反"**（红既可能配红字也可能配绿字）。
  - 但用户可能误读"恒红趋势线=涨"。建议：产品决定是否改为按序列首尾方向取色，
    或统一改中性 `BRAND` 色避免暗示方向。
- **4-11 基准净值卡文字染涨跌色、图形用常量 `WARN`** — `resultParts.tsx:33-36,63`
  - 文字随 `annual_benchmark` 正负染红/绿，图形恒为琥珀。同上，属可接受的两难。
- **4-6 详情页仪表盘/情绪表** — `EtfDetail/index.tsx:302-305,733`
  - PE 分位=`BRAND`、PB=`SUCCESS`、费率=`WARN`、规模=`C3`、情绪=`WARN`：
    五个仪表盘五种色，**纯装饰性配色，无统一语义**。用户无法从颜色推断"好/坏"。
    建议统一为"低=好"的中性色或单一品牌色。

---

## 附录 A：评审方法（便于下次复跑）

### A.1 提取脚本
位于 `deliverables/gstack/_tmp/scan_updown.py`（本仓，Python 标准库，无需 pip）。
关键词模式（正则）：`t-up/t-down`、`text-up/down`、`bg-up/down`、`border-up/down`、
`upDownColor(`、`\.(UP|DOWN)\b`、`pctClass(`、`text-red-*`、`text-emerald-*`、`text-green-*`、
`bg-red-*`、`bg-emerald-*`、`bg-green-*`、`text-blue-*`、`text-amber/orange-*`、
硬编码 hex `#(DC2626|16A34A|EF4444|22C55E|D92B2B|12995B|3B82F6|F59E0B)`、
`color={`、`itemStyle`、`lineStyle`、`areaStyle`。

复跑命令：
```bash
cd "D:/Python_Project/Alpha Quant Platform"
"C:/Users/HY/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe" \
  deliverables/gstack/_tmp/scan_updown.py
```

### A.2 本次扫描结果
- 命中文件：39 个；命中行：281 行（含注释行，需人工剔除）。
- 原生 Tailwind 红绿色类（`text-red-*` / `bg-emerald-*` 等）残留：**0 处**
  （验证命令：`grep -rn "text-red-\|text-emerald-\|text-green-\|bg-red-\|bg-emerald-" --include="*.tsx" src | wc -l` → 0）。
  ⇒ 上一轮引入 `t-up/t-down` + `chartPalette()` 后，**"双语义色值"已从类名层清除**，
  残余歧义全部集中在 **`bg-up/bg-down` 被用于状态语义**（见 §4）。

### A.3 三方核对步骤（每个视觉区）
1. **文字**：找到显示数值的元素，看它用 `t-up`/`t-down`/`pctClass`/`text-ink-muted` 中的哪个，
   记录它在 `value > 0` 与 `value < 0` 时分别染什么色。
2. **图形**：找到同一卡片/面板内的图形（sparkline / 柱 / 线 / 环），看它的 `color` 是否
   **与文字用同一判据**。⚠️ 重点抓"文字随值变、图形写常量"或"两者判据符号相反"。
3. **图例**：若有图例，看图例色块是否与它标注的系列同色；再看图例**文字**（如"红盘(涨)"）
   的方向是否与色块一致。
4. **空值**：单独检查 `value == null` 时三方是否都走中性 —— 凡见 `?? 0` 兜底方向，
   一律标为疑似问题（本项目红线）。

### A.4 本清单未覆盖（明确排除）
- **暗色主题下的色值对比度**（属可访问性审计，非方向语义）。
- **类别色** (`CATEGORY_COLORS`：MA5/MA10、多标的对比线) —— 不表达方向，不判定。
- **后端返回的数据方向正确性**（如 `pct` 字段本身正负号是否与真实涨跌一致）——
  属数据层审计，本清单只审"前端拿到值后染色是否自洽"。

### A.5 已有防护（本轮确认有效）
- `check-style.mjs` 的 hex 规则可防"新增硬编码色值"（opt-in，默认关）。
- `index.css:83-89` 的 `.t-danger/.t-success/.t-warn/.t-info` 已在**类名层**把状态系与涨跌系分开。
- `chartTheme.ts` 的 `upDownColor()` 已把"`>=0` 红 / `<0` 绿 / `null` 灰"收敛为单点，
  新图表应优先用它而非手写三元表达式。

---

*清单结束。共 11 个页面分区 + 1 个基元分区、134 条逐项判定行；
其中 🔴疑似不一致 1 处、🟡语义歧义 32 处（归并为 20 个编号议题）、✅一致 58 处、⚪不适用 28 处。*
