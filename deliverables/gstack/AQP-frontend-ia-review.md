# AQP 前端布局优化 · 产品与信息架构侧评审

> **评审人**：product（软件工坊产品评审员）
> **评审范围**：任务流 / 信息架构 / 页面布局主张 / 首屏信息密度 / 优先级 / 长时间使用机制
> **协作边界**：视觉（配色 / 字体 / 排版规范）由 designer 负责，本文不重复配色细节，聚焦「从使用者角度哪里别扭、该合并什么、该砍什么、优先级怎么排」。

## 已核对的真实代码（全部有据可查）

| 文件 | 用途 |
|---|---|
| `frontend/src/components/Sidebar.tsx` | 18 个导航项、图标定义（`ICON_BASE`）、`NavItem` 类型、`isActive` 逻辑 |
| `frontend/src/App.tsx` | 路由表（`<Routes>`）、页面懒加载、`collapsed` 侧栏状态 `useState(false)` |
| `frontend/src/components/ui/index.tsx` | 共享组件：`MetricCard` / `SectionCard` / `DegradedBadge` / `PanelEmpty` / `ErrorState` / `ViewToggle` / `Pager` / `Modal` |
| `frontend/src/stores/useUiStore.ts` | zustand + persist：`currentSymbol` / `rangeDays` / `adjust` / `period` |
| `frontend/src/pages/MarketOverview/index.tsx` + `KpiCards.tsx` | Row0 KPI、Row1 涨跌分布/资金流向、Row2 热门板块/AI 预测；`isMarketOpen()` |
| `frontend/src/pages/StockDetail/index.tsx` | 上部 `lg:grid-cols-3`、下部 3×2 研究模块、`EVENT_PAGE_SIZE=15` / `EVENT_FETCH_MAX=200` / `EVENT_LIST_MAX_H='max-h-[260px]'`；`isMarketOpen()`（重复实现） |
| `frontend/src/pages/OrderDesk/index.tsx` | `xl:grid-cols-12` 左4列账户+表单+熔断 / 右8列监控；`space-y-3`；Kill Switch 二次确认 modal |
| `frontend/src/pages/Research/index.tsx` | 头注释「左上 因子研发中心…」 |
| `frontend/src/pages/FactorStudio/index.tsx` | GP 因子挖掘 |
| `frontend/src/pages/Portfolio/index.tsx` | 头注释「左侧：资产搜索/权重…策略类型…」 |
| `frontend/src/pages/CapacityAttribution/index.tsx` | 340 行；策略容量上限评估 + Brinson 业绩归因 |

---

## 8. 红线声明（置于文首，全文遵守）

**本方案的第一条纪律**：**不得为美观隐藏任何失败 / 降级 / 不可用态。**

- `DegradedBadge`、`PanelEmpty`、`ErrorState` 三种降级态，以及 `kill == null → '状态不可读'`、`totalReturn == null → 中性色`（而非绿色）、`data?.indices?.status === 'ok'` 才渲染 等纪律，**在所有布局改动中必须原位保留、不得折叠、不得弱化、不得用"占位态样式"同化**。
- 允许被折叠 / 下移 / 移出首屏的，**只能是"正常数据展示"**；一旦某块进入降级态，它必须**原位可见**（可弱化视觉、但不可消失）。
- 本文凡涉及"折叠 / 移出首屏"的主张，均以「**降级态仍原位可见**」兜底。
- **占位态可以弱，降级态不可弱**：若视觉规范把 "empty/disabled" 与 "degraded" 画成同一种弱样式，必须拆开。

---

## 1. 用户场景与任务流

AQP 的使用者实际只做 **5 类任务**。但当前 18 个导航项是**按功能命名**而非**按任务命名**，导致每条任务路径都被切断。

| 场景 | 谁 | 任务 | 当前对应页 | 理想关键路径 | **当前断点（具体到页面与操作）** |
|---|---|---|---|---|---|
| **A. 盘前预案**（盘前 30min） | viewer / researcher | 看大盘→找机会→加自选→定预警 | MarketOverview → Screener → Watchlist / Alerts | 4 步 | ① `MarketOverview/index.tsx` 无「加自选 / 设预警」行内操作，看到热门股必须**跳走**再回来；② `Watchlist` 与 `Alerts` 分属两个导航项、互相无跳转，看自选后想设预警要**重新导航** |
| **B. 盘中盯盘**（9:15–15:05） | 全员 | 盯自选→看个股→看执行 | Watchlist → StockDetail → OrderDesk | 3 步 | ① **无「盯盘模式」**：`KLineChart` 的 F8 / Ctrl+Q / Ctrl+B 快捷键只在个股页生效，切页即失效；② `OrderDesk` 与个股页**无双向跳转**，看到异动股想下单要手输代码 |
| **C. 盘后研究**（收盘后，核心） | researcher / admin | 选因子→回测→归因→容量评估→入策略库 | FactorStudio → Backtest → CapacityAttribution | **当前 5+ 步且链路断裂** | ① `Research` / `FactorStudio` / `Backtest` / `Portfolio` / `Capacity` 是 **5 个平行入口**，无「上一步 / 下一步」；② 回测跑完的**结果页没有入口**通往容量与归因，用户要手动切导航再重填参数 |
| **D. 执行与风控** | admin | 看账户→下单→盯子单→熔断 | OrderDesk | 1 页（但页内混乱） | `OrderDesk/index.tsx` 把「看账户 / 看订单」与「下单表单」混在同一 `xl:grid-cols-12` 视觉流，下单时账户数字在**余光之外** |
| **E. 数据运维** | admin | 看数据质量→补数据→看调度 | DataQuality / DataCenter / Pipeline | 3 步 | 三者是 3 个平行入口，但**数据质量的缺口→补数→调度**是一条因果链，当前无链接串联 |

**核心结论**：18 项里真正被高频使用、横跨 A/B/C/D 的只有约 **9 项**；其余 9 项要么是链路节点（该聚合），要么是详情页（不该做导航），要么是运维低频页（该降权）。

---

## 2. 信息架构重组（重点）

### 2.1 完整新导航结构树：18 → 目标 9 项

按 **「用户任务」** 分组，组内项 **图标零复用**（新增 2 个图标 `IconFlask` / `IconGauge` 即可补齐）。

```
侧边栏（w-40 / 收起 w-14）
│
├─【组 1 · 盯盘】           ← 对应任务 A/B（盘前预案 + 盘中盯盘）
│   ├─ 市场概览      /                    IconOverview
│   ├─ 自选池        /watchlist           IconStar      （从「数据区」上移）
│   └─ 个股分析      /stock/:symbol       IconStock     （保留 prefix 动态路由）
│
├─【组 2 · 研究】           ← 对应任务 C（盘后研究，核心链路）
│   ├─ 选股中心      /screener            IconScreener
│   ├─ 因子实验室    /studio              IconFlask     ★新图标（合并 因子工作室 + 策略研究 因子象限）
│   ├─ 策略回测      /backtest            IconBacktest  （合并 组合回测 为模式切换）
│   └─ 执行与风控    /desk                IconGauge     ★新图标（合并 执行中心 + 容量与归因）
│
└─【组 3 · 数据资产】       ← 对应任务 E（数据运维）
    ├─ 数据中心      /data                IconDatabase  （合并 数据质量 + 任务调度 为页内 Tab）
    └─ 预警中心      /alerts              IconBell
│
└─【底部固定区】            ← 非导航计数
    ├─ 每日报告      /report              IconReport    （低频，移出主列表）
    └─ 系统设置      /settings            IconSettings
```

**图标去重对照（当前 4 组复用 → 新方案零复用）**：

| 原图标 | 原复用项 | 新分配 |
|---|---|---|
| `IconBacktest` | 策略回测 + 执行中心 | 策略回测 独占；执行中心改用 `IconGauge` |
| `IconPortfolio` | 组合回测 + 容量与归因 | 两者均已合并/移除，图标**退休** |
| `IconResearch` | 策略研究 + 因子工作室 | 两者合并为「因子实验室」，改用 `IconFlask`；`IconResearch` **退休** |
| `IconDatabase` | 数据中心 + 数据质量 | 数据质量并入数据中心，`IconDatabase` 独占 |
| `IconEtf` | ETF中心 + ETF分析 | ETF分析**移出导航**，`IconEtf` 独占 ETF中心 |

### 2.2 该降级为非导航项（从主导航移除）

| 移除项 | 理由（落到代码） | 改为 |
|---|---|---|
| **ETF分析** `/etf/510300` | `Sidebar.tsx:160` 硬编码 `to='/etf/510300'`，并靠 `matchBase:'/etf/'` 与 ETF中心**互斥高亮**——**这是在给错误建模打补丁**：详情页本不该是导航项 | 从 `/etf` 列表页点击进入；`IconEtf` 只留给 ETF 中心；ETF 中心可并入「选股中心」作资产类型 Tab |
| **AI 日报** `/report` | `Report/index.tsx` 仅 165 行、低频、非交易动作 | 移到底部区，并允许 `MarketOverview` 的 AI 卡跳入 |
| **系统设置** `/settings` | 全年点几次 | 底部固定区（现有 user 区可承载） |

### 2.3 该合并的页面（5 处，附代码依据）

1. **「策略研究」+「因子工作室」→ 合并为「因子实验室」**
   **依据**：`Research/index.tsx` 头注释明确写着「左上 **因子研发中心**：features 截面 Rank IC / 相关矩阵 / 分层净值」——**Research 内部已经包含因子研发**；`FactorStudio` 是 GP 因子挖掘。两者是**因子生命周期的两段（人工研究 / 自动挖掘）**，被拆成两个平行导航项。合并后 `FactorStudio` 成为实验室的一个 Tab。
   **成本提示**：`Research` 710 行 + `FactorStudio` 479 行，是**页面级重构**，成本最高 → P2。

2. **「组合回测」→ 并入「策略回测」**
   **依据**：`Portfolio/index.tsx` 头注释「左侧：资产搜索 / 权重…策略类型…」——它就是**多资产模式的回测**。回测页加一个 `单标的 / 组合` 模式切换即可。省一个导航项 + 一次参数重填。

3. **「容量与归因」→ 并入「执行与风控」/ 回测结果页**
   **依据**：`CapacityAttribution` 仅 340 行，内容是「策略容量上限评估 + Brinson 业绩归因」——**两块都是回测结果的下游分析**，不是独立任务。理想位置：回测结果页底部一个「容量与归因」Tab。

4. **「数据质量」+「任务调度」→ 并入「数据中心」**
   **依据**：`DataQuality` 264 行 + `Pipeline` 174 行，都是小页，且与数据中心构成「**质量缺口 → 补数 → 调度**」因果链。合并为 `/data` 的 `数据浏览 / 质量 / 调度` 三个 Tab；数据质量页的缺口行应可直接跳调度补数。

5. **「ETF中心」→ 可并入「选股中心」作资产类型 Tab**（可选）
   **依据**：ETF 与个股的"列表→详情"结构同构（`Etf` 830 / `EtfDetail` 760），属同类对象。此项为**可选**（保留独立项也可接受），核心诉求是**详情页必须移出导航**。

### 2.4 该拆的页面（1 处）

- **`StockDetail`（890 行）拆出「公告 / 事件」独立路由 / 抽屉**
  **依据**：`StockDetail/index.tsx` 中 `EVENT_PAGE_SIZE=15` + `EVENT_FETCH_STEP=15` + `EVENT_FETCH_MAX=200` + 独立分页滚动区 `EVENT_LIST_MAX_H='max-h-[260px]'`——公告/事件**已经是重度独立功能**，被硬塞进 3×2 网格。
  **建议**：研究区保留事件**摘要卡（近 3 条）**，点「查看全部」→ `/stock/:symbol/events` 或右侧抽屉。**降级态仍原位可见**（`PanelEmpty` 保留在摘要卡里）。

### 2.5 导航项目标值与理由

**目标：9 项（3 个分组）+ 底部固定区 2 项。**

理由：
1. **「7±2」是硬约束**。18 项在 160px 侧栏（`Sidebar.tsx:250` `w-40`）里已需滚动（`:270` `overflow-y-auto`），**关键入口会滚出视野**。
2. 9 项 ≈ 一屏高内全可见、**零滚动，眼睛扫一遍 1 秒内完成**——这是盯盘场景的硬需求。
3. 剩余功能用「**组内 Tab**」承载：**深度增加但宽度收敛**，符合量化终端（Bloomberg / 同花顺）的成熟范式。
4. 导航是"任务入口"不是"页面索引"；详情页、低频页不该占用导航宽度。

---

## 3. 三个核心页面的布局重构主张

### 3.1 MarketOverview —— 「一眼定风格，三秒抓重点」

| 项 | 主张 |
|---|---|
| **主任务** | 3 秒内判断「今天该不该关注 / 钱往哪流」 |
| **首屏必看** | 「指数 KPI 带」+「涨跌分布」+「资金流向」 |
| **折叠 / 下移** | Row2（热门板块 \| AI 预测精选）下移、可折叠 |
| **视觉权重** | 指数 KPI 卡（最重）> 涨跌分布 / 资金流向 > 热门板块 > AI 预测 |

**具体改动与取舍理由**：
1. **Row0 的 5 张等权 KPI 卡（`KpiCards.tsx:58` `xl:grid-cols-5`）是最大问题——没有主角。** 改为 **「1 大 + 4 小」**：上证指数卡横跨 2 列、数值字号 `text-lg` → `text-2xl`，其余 4 张缩小。这是首屏**唯一的视觉主角**。
   **理由**：指数是"开盘第一眼"的事实，等权呈现等于没告诉用户先看哪个。
2. **Row1 保留 1/2 分栏**（`MarketOverview/index.tsx:143`）——涨跌分布 + 资金流向是盘前决策核心，**权重第二**。
3. **Row2 下移 / 可折叠**：AI 预测是"锦上添花"而非"开盘依据"，**不该与资金流向同权**。
   **理由**：前者是"事实"，后者是"观点"，**事实优先**。

### 3.2 StockDetail —— 「先看价，再看研究」

| 项 | 主张 |
|---|---|
| **主任务** | 看这只票**现在怎么样**，然后决定要不要研究 / 下单 |
| **首屏必看** | K 线图（`h-[520px]`）+ 右侧行情卡 + **一个明确的行动区** |
| **折叠 / 独立** | 「近期事件」抽成独立抽屉或折叠（见 2.4） |
| **视觉权重** | K 线（最重）> 右侧行情 / AI > 研究模块 |

**具体改动与取舍理由**：
1. 上半 `lg:grid-cols-3`（`:267` 左 K 线 / 右 8 张 `compact` MetricCard + AI 预测 + 技术指标）**结构正确，保留**。
2. **拆分研究区**：3×2 六格（`:357` 资金流向 / 近期事件 / 风险 / 筹码 / 股东 / 基本面）里，把**「近期事件」抽成独立抽屉 / 折叠**，其余 5 格保留在下方。
3. **新增「行动条」**：K 线图下方一行 `[加自选] [设预警] [去下单]`——把断掉的**任务路径 B** 接回来。**低成本高收益**。
   **理由**：个股页 90% 的时间在盯 K 线，不能让 6 格研究模块把它挤到需要滚动；且当前页面**完全没有**把"看到的机会"转化为"动作"的出口。

### 3.3 OrderDesk —— 「下单是第一动作，账户是背景」

| 项 | 主张 |
|---|---|
| **主任务** | **下单**（看账户 / 订单是辅助） |
| **首屏必看** | 母单提交表单 |
| **下移 / 压缩** | 账户卡压缩为一行关键数字 |
| **视觉权重** | 提交表单（最重）> 子单监控 > 账户背景 > 禁买池 |

**具体改动与取舍理由**：
1. **调整左列内部顺序**：当前 `OrderDesk/index.tsx:250` `xl:col-span-4` 左列 = 账户卡 + **提交表单** + Kill Switch。建议**提交表单提到左列顶部**，账户卡**压缩为一行关键数字**（现金 / 权益 / 累计收益率），不再占满。
2. 右 8 列（母单 / 子单监控 `max-h-[520px]` 10 行/页 + 合规禁买池）**保留**——这是下单后要盯的。
3. **Kill Switch 是最高危操作**：产品侧主张 **熔断入口必须始终可见、绝不折叠**（与普通按钮视觉拉开交给 designer）。
   **理由**：执行页的 KPI 是「下单速度」，账户收益是结果不是动作。

---

## 4. 首屏信息密度目标

| 区域 | 当前 | 目标 | 依据 |
|---|---|---|---|
| MarketOverview KPI 卡 | 5 张等权 | **5 张（1 大 4 小）** | 数量不减（信息不丢），但**权重分级**解决"无主角" |
| 板块 / 资金榜表格 | 未定行数 | **每表 ≤ 8 行** | 8 行 ≈ 一屏可见，超过需滚动就丧失"扫一眼"能力 |
| 个股研究模块 | 6 格同权 | **首屏 5 格，事件折叠** | 6 格 > 一屏需滚动，砍 1 格正好 |
| 子单监控表 | `max-h-[520px]` 10 行/页 | **保持 10 行/页** | 已有 `Pager`（`ui/index.tsx:291`），合理，不动 |
| 页面根间距 | `space-y-3`(10) / `space-y-4`(1) / `space-y-2`(1) **三种混用** | **全站统一 `space-y-3`** | 一致性是专业感的地基，成本极低 |

- **信息过量处**：`StockDetail` 一屏塞 4 类任务（行情 / AI / 研究 / 公告）；`MarketOverview` Row2 与 Row1 同权。
- **空间浪费处**：`MarketOverview` 无"行动区"；`StockDetail` 无"行动条"；`OrderDesk` 账户卡占据左列顶部黄金位。

---

## 5. 优先级排序（12 条）+ 成本量级

成本量级：**① 改 config / ② 改共享组件 / ③ 重构页面**。排序依据：效率增益 ÷ 成本。

| # | 优先级 | 改动 | 解决什么问题 | 改哪个文件 | 成本 |
|---|---|---|---|---|---|
| 1 | **P0** | 删除「ETF分析」导航项，详情从列表进入 | 修掉 `to='/etf/510300'` 硬编码架构异味 | `Sidebar.tsx:160` | ① |
| 2 | **P0** | 侧栏折叠状态持久化 | 现为 `App.tsx:62` `useState(false)` 临时态，刷新即丢 | `useUiStore.ts` + `App.tsx:62` | ①（store 已有 persist 范式） |
| 3 | **P0** | 全站页面根间距统一 `space-y-3` | 三种混用破坏一致性 | 各 `pages/*/index.tsx`（2 处偏离） | ① |
| 4 | **P0** | MarketOverview KPI 改「1 大 4 小」 | 首屏无主角 | `MarketOverview/KpiCards.tsx:58` | ② |
| 5 | **P0** | StockDetail 新增「行动条」`[加自选/设预警/去下单]` | 接回断掉的任务路径 B | `StockDetail/index.tsx`（K 线下方） | ③ |
| 6 | **P1** | 「组合回测」并入「策略回测」作模式切换 | 省导航项 + 参数重填 | `Sidebar.tsx:162` + `Backtest` | ③ |
| 7 | **P1** | 「数据质量」「任务调度」并入「数据中心」Tab | 三页合一，接因果链 | `Sidebar.tsx:171-173` + `DataCenter` | ③ |
| 8 | **P1** | 导航图标去重（新增 `IconFlask`/`IconGauge`） | 4 组图标复用 → 辨识度为零 | `Sidebar.tsx:51-96` | ② |
| 9 | **P1** | StockDetail 事件面板抽为抽屉 | 890 行页面塞 4 类任务 | `StockDetail/index.tsx`（`:550-574`） | ② |
| 10 | **P1** | 「容量与归因」并入回测结果 Tab | 340 行下游分析不该独立 | `Sidebar.tsx:166` + `Backtest` | ③ |
| 11 | **P2** | 「策略研究」+「因子工作室」合并为「因子实验室」 | 因子生命周期两段被拆散 | `Sidebar.tsx:163-164` + `Research` | ③（最贵） |
| 12 | **P2** | 「自选池」从数据区上移到盯盘区 | 任务分组归位 | `Sidebar.tsx:174` | ① |

> **P0 = 半天到一天内可完成、且立刻可感知的 5 条**，建议独立提交。

---

## 6. 长时间使用机制（产品侧建议，非配色）

1. **会话恢复（防手滑）**：`useUiStore` 已 persist `symbol / range / adjust / period`，但**未记录「上次所在页面」**。刷新后总是回首页，盯盘时是灾难。→ 在 `useUiStore` 增加 `lastPath`，`App.tsx` 首次挂载时恢复。
2. **布局记忆**：侧栏折叠（见 P0#2）+ `StockDetail` 事件区展开态，都应持久化。
3. **快捷键体系化**：`KLineChart` 已有 F8 / Ctrl+Q / Ctrl+B，但**只在个股页、无全局提示**。→ 抽 `useHotkeys`，让「数字键 1–9 跳导航组」「/ 聚焦搜索」「Esc 关抽屉」全局可用，并在 `?` 面板列出。直接服务"长时间盯盘手不离键盘"。
4. **告警免打扰**：`Topbar` 有 SSE + 未读角标，但**无免打扰 / 静音时段**。→ `Settings` 增「静音时段 + 分级阈值」，避免长时使用时被噪声淹没。
5. **注意力管理（盘前 / 盘中 / 盘后预设）**：`MarketOverview` 可加三种预设布局（同一数据、不同权重）。
   **顺带消除重复代码**：`isMarketOpen()` 在 `MarketOverview/index.tsx:27` 与 `StockDetail/index.tsx:50` **各写了一份**——抽到共享 hook `useMarketSession()`。
6. **防误操作**：`OrderDesk` 已有 Kill Switch 二次确认 modal（做得对，保留）。建议对「提交母单」也加**金额阈值二次确认**（超过 N 万弹确认）。

---

## 附录：给 designer 的接口（结构交接，不重复配色）

- **视觉权重分级**需 designer 的**字号 / 留白阶梯**配套：对齐 `text-lg` vs `text-2xl`、卡片 padding 中 / 大两档。
- 新增图标 `IconFlask`（因子实验室）、`IconGauge`（执行与风控），沿用现有 `ICON_BASE`（`viewBox 24` / `stroke 1.6` / 无填充）。
- 组标题范式（现 `Sidebar.tsx:278` `text-2xs uppercase` 的 `数据` 一行）建议保留并扩展到 3 组。
- 根间距取值以 designer 排版规范为准，product 主张 `space-y-3`。

---

**红线重申（全文遵守）**：`DegradedBadge` / `PanelEmpty` / `ErrorState` 及 `kill == null → '状态不可读'`、`totalReturn == null → 中性色` 等纪律，在以上所有布局改动中**原位保留、不许折叠、不许为美观隐藏**。可折叠的只能是"正常数据展示"；降级态必须始终占位可见。
