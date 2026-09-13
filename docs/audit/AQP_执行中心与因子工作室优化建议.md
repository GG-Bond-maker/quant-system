# 执行中心 + 因子工作室 + 容量归因 优化建议

> 通读 `OrderDesk/index.tsx` (331 行) / `FactorStudio/index.tsx` (184 行) / `CapacityAttribution/index.tsx` (171 行)
> + 后端 `desk.py` (349 行) / `studio.py` (75 行) / `production.ts` (179 行)
> 共 31 项优化点：P0=4 / P1=20 / P2=7

---

## 一、执行中心 OrderDesk（12 项）

### P0 致命问题（1 项）

#### 1. 布局错位：合规禁买池被挤到左下角

**现象**：第 102-328 行 `<div className="grid grid-cols-1 gap-3 xl:grid-cols-12">` 内三个直接子 div：
- 行 104 `<div className="space-y-3 xl:col-span-4">`（账户+下单+Kill Switch，左侧栏）
- 行 193 `<div className="xl:col-span-8">`（订单监控）
- 行 250 `<div className="xl:col-span-8">`（合规禁买池）

xl 下，第一行 `col-span-4 + col-span-8 = 12` 满行。第二个 `col-span-8` 落到下一行的 col-span-8 位置，但下一行剩余 col-span-4 是空的，禁买池 SectionCard 实际宽度被挤到 col-span-8 的左侧 col-span-4 区域（视觉上看像被压扁到左侧）。

**修复方案**：把禁买池的 `xl:col-span-8` 改为 `xl:col-start-5 xl:col-span-8`，或更稳妥的做法是把禁买池与订单监控包到同一个 `xl:col-span-8` 内的 `space-y-3` 容器里。

### P1 改进（8 项）

#### 2. 账户卡缺关键指标

**现象**：`PaperAccount` 后端类型包含 `initial_cash`，但前端只显示 `cash/market_value/equity/n_fills/total_fees/total_impact_cost`，缺少：
- 初始资金（initial_cash，已可读但未显示）
- 累计收益率 = (equity - initial_cash) / initial_cash
- 年化收益率（按 n_fills 推算交易日数）
- 最大回撤、夏普比率（需后端补计算）

**修复方案**：前端补 4 张子卡（初始/累计收益/年化/回撤）；后端 `paper_account` 端点补 `total_return / annualized_return / max_drawdown / sharpe` 字段。

#### 3. 持仓 PnL 口径不明

**现象**：第 122-127 行持仓行只显示 `pnl 元`，没有：
- 收益率（pnl / cost_price / qty）
- 标识"浮动盈亏"还是"已实现+浮动"
- 当日盈亏（last_price 今 vs 昨）

**修复方案**：行内加 `(+X.XX%)`；增加 ⓘ tooltip 说明"pnl = 持仓浮动盈亏，不含已成交手续费与冲击"。

#### 4. 订单表无筛选/搜索/分页

**现象**：第 188-218 行后端 `list_orders(limit=50)` 写死，前端 `<tbody>` 直接渲染全部 orders。订单数 > 50 后丢数据，且无按 symbol/status/side 过滤。

**修复方案**：
- 前端表头加搜索框（symbol 模糊匹配）+ 状态筛选 pills + 分页器（每页 10/20/50）
- 后端 `list_orders` 改为支持 `?symbol=&status=&offset=&limit=` 查询参数

#### 5. market 算法未自动置 split_days=1

**现象**：第 152-154 行 `split_days` input 不区分算法；后端 OrderRequest 允许 `market+split_days>1`，注释写"market 恒为 1"但前后端均不强制。

**修复方案**：前端 `useEffect` 监听 `form.algo`，选 `market` 时自动 `setForm({...form, split_days: 1})` 并禁用 input；后端 `OrderRequest` 加 validator：`algo == "market" → split_days == 1`。

#### 6. Kill Switch 无二次确认

**现象**：第 181-184 行"一键熔断"按钮直接 `toggleKill(true)`，是高破坏性操作（撤销全部未完成母单 + 禁止新订单）但无确认。

**修复方案**：包一层 `<ConfirmModal>`，提示"将撤销 N 笔未完成母单，是否继续？"。

#### 7. 子单成交明细嵌在 td

**现象**：第 223-237 行 `o.fills` 直接渲染在订单行的最后一个 `<td>` 里，导致：
- 行高不齐（有 fills 的行变高）
- 子单多时表格难读

**修复方案**：改为可展开行（点击行展开 `<tr>` 子行显示 fills）或独立侧抽屉。

#### 8. 无自动刷新开关

**现象**：模拟盘订单状态需要手动点"刷新"，无 polling。子单成交在执行日行情同步后自动撮合，用户看不到实时变化。

**修复方案**：标题栏加"自动刷新"开关（默认关），开启后每 5-10s 调一次 refresh。

#### 9. 禁买池无批量操作

**现象**：第 251-326 行禁买池只有"前 10 候选导入"按钮，无：
- 全选/批量启用/批量停用/批量删除
- 按 category 筛选 / symbol 搜索

**修复方案**：复用数据中心质量表的全选模式（已实现），加筛选 chips + 全选 checkbox 列。

### P2 锦上添花（3 项）

#### 10. 下单前无 mini K 线参考

**现象**：提交母单时只填代码，看不到当前价位/技术位，决策无依据。

**修复方案**：下单卡内嵌一个 mini KLineChart（最近 60 日），symbol 变化即刷新。

#### 11. 持仓 PnL 缺超额收益对比

**现象**：第 123-125 行已对（红涨绿跌），但只显示绝对 pnl，没有"vs 大盘超额"。

**修复方案**：每个持仓加一行"vs 沪深 300 +X.XX%"，需后端补基准收益率计算。

#### 12. 无今日成交汇总卡

**现象**：账户卡显示了 `n_fills` 累计，但无"今日"维度。

**修复方案**：标题栏下加一行今日汇总卡：笔数 / 金额 / 平均冲击 bps / 平均基差 bps。

---

## 二、因子工作室 FactorStudio（12 项）

### P0 致命问题（2 项）

#### 1. 任务重启丢失无引导

**现象**：第 36-40 行 polling 失败时 `setErr(...)`，但后端 studio.py:67 返回 40400 "任务不存在（进程重启后任务历史清空）"。用户看到"状态查询失败"也不知道是任务丢了，无法判断要不要重启。

**修复方案**：捕获 ApiError，code === 40400 时显示"任务已失效（服务重启或任务过期）→ 请重新启动挖掘任务"+ 重启按钮。

#### 2. 无任务历史列表

**现象**：每次启动新任务后，旧 task_id 在前端 state 里被覆盖（`setStatus(null)`），无法回看。后端 `gp_miner` 是内存态任务字典，重启即丢。

**修复方案**：
- 前端：localStorage 持久化最近 10 个 task_id + 启动参数 + 启动时间，列表展示在配置卡下方
- 后端（可选）：`gp_miner` 加 SQLite 持久化表（task_id/params/started_at/status/best_expr）

### P1 改进（7 项）

#### 3. 字段缩写无含义提示

**现象**：第 10-13 行 `ALL_FIELDS` 22 个字段（ret_5/vol_20/rsi_14/boll_pos/skew_ret_20/atr_14/hl_range/ma_slope_20...）用户不知含义。

**修复方案**：每个字段 button 加 `title="..."` 属性或 hover popover。例：`ret_5` → "5 日收益率"、"vol_20" → "20 日波动率"、"boll_pos" → "布林带位置（0=下轨，1=上轨）"。

#### 4. 曲线只有 fitness 单值

**现象**：第 56-68 行 ECharts 只画 fitness_curve，看不到同期 MeanIC/ICIR。fitness = |MeanIC| - 复杂度惩罚，单看 fitness 看不出真实 IC 水平。

**修复方案**：后端 GpStatus 加 `mean_ic_curve: number[]` 和 `icir_curve: number[]`；前端改双 Y 轴（左 fitness / 右 MeanIC）+ tab 切换 ICIR。

#### 5. 表达式无"应用"按钮

**现象**：第 165-176 行 Top 表达式表只读，挖掘出的 Alpha 无法直接进入回测/选股流程。

**修复方案**：每行加"应用"按钮 → 跳转到 `/backtest?expr=...` 或 `/research?alpha=...`，需后端补 expression 字符串到 ml/predict 的注入路径。

#### 6. 表达式无一键复制

**现象**：第 168 行 `font-mono` 表达式要手动选中复制。

**修复方案**：加复制按钮（`navigator.clipboard.writeText(e.expr)`）+ toast 提示。

#### 7. 参数无预设/历史

**现象**：第 18-22 行每次启动都用默认值（pop=20, gen=6, horizon=5），无推荐预设或"上次参数"快捷恢复。

**修复方案**：localStorage 持久化上次启动参数 + 提供 3 个预设（快速试算 pop=10/gen=3 / 标准 pop=30/gen=8 / 深度 pop=60/gen=15）。

#### 8. 任务无法中途取消

**现象**：第 116-120 行启动按钮在 running 时变为 disabled 文案，没有取消按钮。长任务只能等死或关浏览器。

**修复方案**：后端 gp_miner 加 cancel flag（worker 每代检查）；前端"启动"按钮在 running 时变为"取消任务"。

#### 9. 进度条假设线性

**现象**：第 141 行 `width: ${(generation / total) * 100}%` 假设代数是线性进度，但 GP 早期收敛快、后期慢。

**修复方案**：进度条改为显示"已完成 X 代 / Y 代 + 当前最佳 fitness=Z"，去掉百分比或加 caveat 文字"进度按代数计算，不代表剩余时间"。

### P2 锦上添花（3 项）

#### 10. 无表达式语法树可视化

**现象**：GP 生成的表达式如 `(ret_5 + vol_20) * rsi_14` 是树形结构，UI 只显示字符串。

**修复方案**：点击表达式行展开一个 SVG 语法树（节点=算子/字段，边=参数顺序）。

#### 11. ALL_FIELDS 硬编码

**现象**：第 10-13 行 22 个字段写死前端，与后端 features parquet 实际列无联动。

**修复方案**：后端 studio.py 加 `GET /studio/fields` 端点扫描 features parquet 列名 + 元数据（含义/覆盖率），前端启动时拉取。

#### 12. 无参数推荐/智能引导

**现象**：种群/代数/字段数有最佳配比，UI 不引导新手。

**修复方案**：参数 input 旁加 ⓘ 提示"种群≥30 时 IC 统计才稳定"、"字段数 3-5 个 IC 最优"，并提供"推荐配置"按钮一键填充。

---

## 三、容量归因 CapacityAttribution（7 项，顺带分析）

### P0 致命问题（1 项）

#### 1. 组合编辑只能改 2 行

**现象**：第 12-15 行 `DEFAULT_ASSETS` 写死 2 个标的，第 76-87 行 map 渲染固定 2 行，无增删按钮。后端 `AttributionRequest` max_length=20 但前端只支持 2 行。

**修复方案**：用 useState 数组 + "添加资产"按钮 + 每行末尾"删除"按钮；权重和动态显示在底部。

### P1 改进（5 项）

#### 2. 权重和无归一化提示

**现象**：第 82-85 行用户填 0.5 + 0.3 看不到总和，后端 desk.py:266 自动归一化但不告知用户。

**修复方案**：底部加"权重合计 X.XX（≠ 1 时自动归一化）"提示。

#### 3. benchmark 不可选

**现象**：第 347 行后端硬编码"本地 universe 等权"作 benchmark，前端无切换选项。

**修复方案**：后端加 `benchmark: 'universe_equal' | 'hs300' | 'csi500' | 'custom'` 参数；前端下拉选择。

#### 4. Brinson 表无合计行

**现象**：第 149-154 行"分解合计"是单行 `<div>` 文本，不与表头对齐。

**修复方案**：表内加 `<tfoot>` 合计行，按 allocation/selection/interaction/total 列对齐。

#### 5. 风格贡献柱图无数值标签

**现象**：第 49-52 行 ECharts bar 无 label，只能 hover 看。

**修复方案**：series.itemStyle 加 `label: { show: true, position: 'right', formatter: '${(v*100).toFixed(2)}%' }`。

#### 6. 无时间窗口选择

**现象**：第 33 行 `deskApi.attribution(assets, 120)` 写死 120 日。

**修复方案**：标题栏加 pills：30 / 60 / 120 / 250 日。

### P2 锦上添花（2 项）

#### 7. 无滚动归因时序

**现象**：只有全窗口归因，看不到归因随时间变化。

**修复方案**：后端加 `/attribution/timeseries` 端点（按月滚动归因）；前端加 ECharts 折线图（X=月，Y=超额收益，stack=Brinson 各项贡献）。

#### 8. 超额收益无贡献分解加和验证

**现象**：Brinson 分解 + Style 分解是两套独立体系，无法加和验证 = 超额收益。

**修复方案**：UI 加一个对比卡"Brinson 总超额 vs Style 总超额 vs 实际超额"，三者应相近（差异说明口径不同）。

---

## 实施建议

**第一档（P0，必做）**：
1. 执行中心布局修复（5 分钟，纯前端）
2. 因子工作室任务失效引导（10 分钟，纯前端）
3. 因子工作室任务历史列表（30 分钟，前端 + localStorage）
4. 容量归因组合编辑增删行（20 分钟，纯前端）

**第二档（P1，按价值排序）**：
1. 执行中心账户卡补关键指标（前端 + 后端补字段）
2. 执行中心订单表筛选/分页（前端 + 后端补查询参数）
3. Kill Switch 二次确认（纯前端，5 分钟）
4. 因子工作室字段含义 tooltip（纯前端，10 分钟）
5. 因子工作室表达式复制/应用按钮（纯前端 + 后端注入路径）
6. 因子工作室曲线多指标（前端 + 后端 GpStatus 补字段）
7. 容量归因 benchmark 选择（前端 + 后端补基准数据）

**第三档（P2，可选）**：
- 因子工作室语法树可视化、字段动态获取
- 容量归因滚动归因时序
- 执行中心 mini K 线、今日汇总卡

---

## 总体评价

三个页面代码质量**整体良好**，无造假数据，无硬编码指标冒充真实（与之前审计结论一致）。主要问题是：
- **UI 细节打磨不足**（布局错位、口径提示缺失、操作无确认）
- **任务生命周期管理弱**（GP 任务内存态、无历史、无取消）
- **数据可读性差**（缩写无解释、曲线单维度、表达式无操作）

P0 4 项都是"功能不可用"或"数据丢失"级别，建议优先修复；P1 20 项是体验与口径问题，按价值排序逐项推进；P2 7 项是锦上添花，时间允许再做。
