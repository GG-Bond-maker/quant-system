# AQP 前端界面设计优化方案（Design Consultant Spec）

> 面向 AQP「Alpha Quant Platform」的布局 / 视觉 / 排版 / 交互优化方案。
> 所有建议对应真实代码，行号与数字均逐一 grep 复核：
> `frontend/tailwind.config.js`、`frontend/src/index.css`（invert hack 在 27-36）、
> `frontend/src/App.tsx`（main 无 max-w、整页滚动）、
> `frontend/src/components/ui/index.tsx`（338 行）、
> `frontend/src/components/Sidebar.tsx`（NAV_MAIN 12 + NAV_DATA 6 = 18）、
> `frontend/src/components/charts/KLineChart.tsx`（UP/DOWN 在 18-23）、
> `frontend/src/pages/{MarketOverview,StockDetail,OrderDesk}`。
> 复核数字：`text-2xs`=521、`text-red-600`=78、`text-emerald-600`=39、`text-amber-700`=39。

> **协作边界**：产品信息架构（任务流 / 页面合并主张）由 product 负责，已见
> `deliverables/gstack/AQP-frontend-ia-review.md`；本文聚焦视觉语言、配色 token、排版规范、
> 栅格数值、交互模式，并**遵守 product 的红线**：不得为美观隐藏任何失败 / 降级 / 不可用态
> （`DegradedBadge` / `PanelEmpty` / `ErrorState` / `kill==null` 状态不可读 等**原位保留、不折叠、不弱化**）。

---

## 0. 一句话诊断

AQP 的问题不是「不够好看」，而是**没有一套被遵守的规范**：标题字号 4 种、间距 3 种、11px 521 处、
`text-red-600` 78 处同时承担「涨 / 危险 / 错误」。**先立 token，再谈美化**——第一原则是「收敛 > 装饰」。

---

## 1. 风格定位

### 1.1 参考与取舍

| 来源 | 学什么 | 不学什么 / 为什么不适合 A 股 |
|------|--------|------------------------------|
| **Bloomberg Terminal** | 信息密度、F 键功能带、等宽数字对齐、键盘优先、状态栏 | 深蓝黑底 + 高饱和荧光色；A 股用户白天办公环境 + 需长时间连续盯盘，高对比荧光色更累 |
| **自研 HFT 面板** | 面板化（panel）+ 独立滚动、颜色只表达状态、一屏即全部 | 极简到无价格上下文；AQP 有研究 / 回测等非实时页，需要更丰富排版 |
| **TradingView** | 图表交互（滚轮缩放 / 拖拽 / F8 周期）、浅色主题可读性、抽屉式详情 | 默认深色 + 大面积留白不适合信息密集的中文终端（中文更占宽，需更紧凑行高） |

**结论**：AQP 视觉语言 = **「浅色为主、密度优先的专业研究终端」**。不是 Bloomberg 的「黑底霓虹」，
而是「纸质财经终端电子化」——白 / 浅灰底、深墨字、克制的红绿、等宽数字。

### 1.2 五条可判定的设计原则

1. **颜色只承担语义，不承担装饰**：每个有颜色的像素必须能回答「它代表涨 / 跌、危险、警告、成功，还是链接」。禁止「为了好看加点蓝」。→ 解决 `text-red-600` 78 处「涨」与「危险」混淆。
2. **数字必须等宽且右对齐**：会跳动 / 需纵向比较的数字走 `.num`（tabular-nums）。→ 解决表格滚动时价格列左右抖动。
3. **一屏之内信息自洽**：每个面板独立滚动（`overflow-auto` + `max-h`），页面不整页滚动，顶栏与左栏恒定。→ 解决 `main` 无独立滚动、DataCenter 1265 行整页滚导致上下文丢失。
4. **层级靠 3 个维度区分，不靠字号堆叠**：字重、颜色墨度、留白间距。字号阶梯全局只有 6 档。→ 解决「页面主标题 4 种字号并存」。
5. **危险操作必须有「距离 + 确认 + 唯一的红」**：Kill Switch / 熔断只能用 `danger` token 的红，不与「涨」的红同源。→ 解决 OrderDesk 里 `text-red-600` 同时是「买入方向」和「熔断」。

> 附加红线（承接 product）：**降级态永不弱化**。失败 / 不可用 / 降级必须与原位数据同样可见，
> 视觉规范必须把 `empty/disabled`（可弱）与 `degraded`（不可弱）画成**两种不同样式**。

---

## 2. 配色方案（可直接粘贴进 `tailwind.config.js`）

### 2.1 核心判断：红绿冲突怎么解

A 股用户心智里 **红 = 涨、绿 = 跌**，不可动（`up: #DC2626` / `down: #16A34A` 的**色相方向保留**）。
冲突根源不是红绿本身，而是**把「红绿」拿去当了「危险/成功」**（`text-red-600` 兼作错误提示、
`bg-emerald-50 text-emerald-700` 兼作"正常运行"）。

**解法 = 语义解耦（两套正交色系）：**
- **涨跌系**（`up/down/flat`）：表达**价格变动**，仅用于数字、K 线、涨跌箭头、方向标签。
- **状态系**（`success/warn/danger/info`）：表达**系统与操作状态**，仅用于按钮、徽章、提示条、熔断。
- 关键：状态系**故意选与涨跌系不同的色相 / 明度**，让用户一眼区分「这是价格」还是「这是状态」。

### 2.2 完整颜色 token 表（`theme.extend.colors`，可直接粘贴）

```js
// frontend/tailwind.config.js
theme: {
  extend: {
    colors: {
      // ── 背景层级（3 级：页面底 / 卡片 / 次级块）──────────────
      canvas:  '#F1F4F8',                        // 页面底（比现 surface #F5F7FA 略深，让白卡浮起）
      surface: { DEFAULT: '#FFFFFF', alt: '#F8FAFC', sunken: '#EEF2F7' },
      // surface=DEFAULT 卡片白；alt 斑马纹/表头；sunken 内嵌代码/日志块

      // ── 文字层级（3 级）─────────────────────────────────────
      ink: {
        DEFAULT:   '#0F172A',   // 主文字（价格、标题）
        secondary: '#475569',   // 次文字（标签、说明）—— 由 #64748B 加深提对比度
        muted:     '#94A3B8',   // 弱文字（单位、占位、时间戳）
        inverse:   '#FFFFFF',   // 深底上的文字
      },

      // ── 边框 ────────────────────────────────────────────────
      hair:  '#E2E8F0',   // 常规分割线
      hair2: '#CBD5E1',   // 强调边框（表格外框、输入 focus 前）

      // ── 涨跌语义系（A 股：红涨绿跌）─────────────────────────
      up:   { DEFAULT: '#D92B2B', soft: '#FEE2E2', bg: '#FEF2F2', text: '#B91C1C' },
      down: { DEFAULT: '#12995B', soft: '#DCFCE7', bg: '#F0FDF4', text: '#047857' },
      flat: { DEFAULT: '#94A3B8', soft: '#F1F5F9', bg: '#F8FAFC', text: '#64748B' },

      // ── 系统状态语义系（与涨跌解耦！）───────────────────────
      danger:  { DEFAULT: '#E11D48', soft: '#FFE4E6', bg: '#FFF1F2', text: '#BE123C' }, // 偏玫红，区别 up 纯红
      success: { DEFAULT: '#0D9488', soft: '#CCFBF1', bg: '#F0FDFA', text: '#0F766E' }, // 偏青绿，区别 down 股市绿
      warn:    { DEFAULT: '#D97706', soft: '#FEF3C7', bg: '#FFFBEB', text: '#B45309' },
      info:    { DEFAULT: '#2563EB', soft: '#DBEAFE', bg: '#EFF6FF', text: '#1D4ED8' },

      // ── 品牌交互色 ──────────────────────────────────────────
      brand: {
        50:'#EFF6FF',100:'#DBEAFE',200:'#BFDBFE',300:'#93C5FD',
        500:'#2563EB',600:'#1D4ED8',700:'#1E40AF',
      },
    },
  },
}
```

**迁移映射（替换现有 200+ 处硬编码色）：**

| 现写法 | 语义判断 | 替换为 | 主要涉及 |
|--------|---------|--------|----------|
| `text-red-600` 78 处 | 涨跌/买卖方向 → `t-up`；错误/熔断 → `text-danger` | 拆成两个 token | OrderDesk、错误提示 |
| `text-emerald-600/700` 56 处 | 成功/正常 → `text-success`；表示跌 → `t-down` | 拆开 | 状态徽章 |
| `text-amber-600/700` 70 处 | 警告 → `text-warn` | 统一 | 告警、风险 |
| `bg-red-50 text-red-700` | 错误条 | `bg-danger-bg text-danger-text` | 全局错误态 |
| `bg-emerald-50 text-emerald-700` | "正常运行" | `bg-success-bg text-success-text` | 状态徽章 |

### 2.3 亮色 vs 暗色：**推荐亮色为默认**

**依据**：A 股交易时段（9:30–15:00）是**白天**，用户在日光 / 办公照明下；暗底 + 红绿文字在明亮环境下
因瞳孔收缩导致辨识度下降，且红绿在暗背景「发光溢出」，长时间看更累。**暗色仅作可选（夜间复盘），不做默认。**

**现有 `html.theme-dark { filter: invert(1) hue-rotate(180deg) }`（`index.css:27-36`）必须删除**——
反色让照片 / 图表色相偏移、K 线红绿被 hue-rotate 扭成错误色相、白底图谱反成黑底层次全乱。
**替换为真正的 token 化暗色**（CSS 变量驱动，组件不必满地写 `dark:`）：

```css
/* frontend/src/index.css —— 删除整段 invert hack，改为变量驱动 */
:root{
  --bg-canvas:#F1F4F8; --bg-card:#FFFFFF; --bg-sunken:#EEF2F7;
  --ink:#0F172A; --ink2:#475569; --inkm:#94A3B8; --hair:#E2E8F0;
  --up:#D92B2B; --down:#12995B; --flat:#94A3B8;
  --danger:#E11D48; --success:#0D9488; --warn:#D97706; --info:#2563EB;
  color-scheme: light;
}
html.theme-dark{
  --bg-canvas:#0B1120; --bg-card:#111827; --bg-sunken:#1F2937;
  --ink:#E5E7EB; --ink2:#94A3B8; --inkm:#64748B; --hair:#1F2937;
  --up:#F87171; --down:#34D399; --flat:#64748B;   /* 暗色下红绿降饱和，防发光溢出 */
  --danger:#FB7185; --success:#2DD4BF; --warn:#FBBF24; --info:#60A5FA;
  color-scheme: dark;
}
```
> 成本：**改 config（2.2）+ 改 1 处 index.css（2.3）**。风险点：`KLineChart.tsx` 背景写死 `BG='#ffffff'`，暗色下需读变量（见 2.6）。

### 2.4 配色比例（60/30/10）

- **60% 中性面**：`canvas` + `surface` 白 + `hair` 边框（全部信息背景）。
- **30% 墨度**：`ink` 三级文字（承载绝大多数信息）。
- **10% 强调**：其中 **涨跌红绿占 ~7%**（数字色）、**品牌蓝 + 状态色合计 ≤3%**。
- **强调色只用在**：可点击链接 / 按钮（brand）、涨跌数字（up/down）、危险按钮（danger）、告警徽章（warn）。
  **永不用于卡片背景、标题、装饰条。**

### 2.5 `#DC2626` / `#16A34A` 是否保留？—— 色相保留、色值微调

**结论：方向保留，数值小改。**
- `up: #DC2626 → #D92B2B`：略降饱和 + 一点点橙。`#DC2626`（Tailwind red-600）偏刺，久看累；`#D92B2B` 更接近同花顺 / 东财的「行情红」，更温和。
- `down: #16A34A → #12995B`：`#16A34A` 在浅底偏亮，略降到 `#12995B` 提升与白底对比、减少荧光感。
- 成本：**改 config 2 行**，全局 `t-up/t-down` 自动生效。

### 2.6 K 线图色值统一（`KLineChart.tsx:18-23`）

现状 `UP='#ee1515' / DOWN='#1dbf1d'` 与全局不一致。**统一为读 CSS 变量**：

```ts
// KLineChart.tsx —— 不再写死；保证图表与全站同色，暗色自动跟随
const cssVar = (n: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const UP   = cssVar('--up')   || '#D92B2B';
const DOWN = cssVar('--down') || '#12995B';
const BG   = cssVar('--bg-card') || '#ffffff';   // 阳线空心填充跟随卡片色，暗色下不穿帮
const TEXT_C = 'var(--ink2)';                     // 轴文字 #8c8c8c → ink-secondary 提对比
const AXIS  = '#CBD5E1';   // 轴，替代 #d9d9d9
const SPLIT = '#EEF2F7';   // 网格，替代 #f0f0f0
```
> 成本：**改 1 组件（约 6 行）**。解决「图表红绿 ≠ 全站红绿」+ 暗色穿帮。

---

## 3. 布局结构

### 3.1 解决两个结构性问题（P0）

**(A) 「无最大宽度约束」**（`App.tsx` main 注释「去掉 max-w-terminal 限制」）：
- 1280px 以上给内容区加**居中上限 + 侧边留白**：`mx-auto w-full max-w-[1600px]`。
- **关键区分**：**信息型页面**（表格 / 研究）用 `max-w-[1600px]` 居中；**行情型页面**（OrderDesk / StockDetail 的 K 线）**允许铺满** `max-w-none`。不要一刀切——铺满对表格是灾难，对 K 线是优点。
- 成本：**改 1 个共享层**（App.tsx main 的 class）。

**(B) 「整页滚动」**（`App.tsx`：Sidebar sticky + 右侧 flex-col + main 跟随 body 滚）：

```tsx
// App.tsx 改为三栏固定 + 主区独立滚动
<div className="flex h-screen overflow-hidden bg-canvas">
  <Sidebar ... />
  <div className="flex min-w-0 flex-1 flex-col">
    <Topbar />                                              {/* 恒定 */}
    <main className="min-h-0 flex-1 overflow-y-auto px-4 py-3 lg:px-5">
      <Routes>...</Routes>
    </main>
    <footer className="shrink-0 border-t border-hair py-2 text-center text-2xs text-ink-muted">...</footer>
  </div>
</div>
```
- 效果：Topbar / Sidebar 恒定，**主区自己滚**；长表格页（DataCenter 1265 行 / StockDetail 890 行）滚动时顶栏、上下文不丢。
- 成本：**改 1 个共享层**（App.tsx）。

### 3.2 面板优先级 + 空间分配（8 模块）

| 模块 | 优先级 | 同屏 / 独立 | 空间建议 | 依据 |
|------|--------|-------------|----------|------|
| 实时行情面板（价格 / 涨跌 / 盘口） | P0 | 同屏（顶部常驻条） | 高 **56–72px** 常驻 | 盯盘第一信息，必须永远可见 |
| K 线 / 分时 | P0 | 同屏（主区左上，最大块） | `col-span-8`，高 **480–520px** | 现 StockDetail 已 520px，保留；视觉重心 |
| 下单区（Order Entry） | P0 | 同屏（右侧固定） | `col-span-4`，宽 **320–360px** | 高频操作，必须「零导航到达」 |
| 持仓与实时盈亏 | P0 | 同屏（右侧，下单区上方） | `col-span-4` | 下单需参照持仓，放一起 |
| 订单簿与深度 | P1 | 同屏（可折叠） | `col-span-4` 或抽屉 | 中频；盘中展开、复盘收起 |
| 策略信号 | P1 | 同屏（下方） | `col-span-8`，高 **200–260px** | 需与 K 线对照看 |
| 日志与警报 | P1 | **独立抽屉 / 底部栏** | 抽屉 **360px** / 底栏 **32px** | 高频产生但低频阅读，占屏浪费 |
| 深度研究（因子 / 回测 / 归因） | P2 | **独立页面** | 全宽 | 非实时，不与盯盘争屏 |

### 3.3 三页具体栅格改造（Tailwind class 级）

**MarketOverview**（现 `space-y-3` + Row1/2 各 `lg:grid-cols-2`，**全卡片同权重、无主次**）：

```tsx
<div className="mx-auto max-w-[1600px] space-y-3">
  {/* 顶部：指数 KPI —— 提高权重（主指数更大数字） */}
  <KpiCards data={data} />                         {/* 内部：主指数 text-2xl，其余 text-lg */}
  {/* 主次分层：热点 / 资金 7:5 */}
  <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
    <div className="lg:col-span-7"><HotSectorsPanel /></div>
    <div className="lg:col-span-5"><MoneyFlowPanel /></div>
  </div>
  <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
    <div className="lg:col-span-7"><BreadthPanel /></div>
    <div className="lg:col-span-5"><AiPicksPanel /></div>
  </div>
</div>
```
→ 解决「全部卡片同权重、无主次视觉层级」。

**StockDetail**（现 `lg:grid-cols-3`，左 `col-span-2` K 线）：

```tsx
<div className="mx-auto max-w-[1720px] space-y-3">     {/* 行情页允许更宽 */}
  <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
    <div className="min-w-0 space-y-3 lg:col-span-8">  {/* K 线主区 8/12 */}
      <KLinePanel />                                    {/* 高度 520 保留，图表 BG=var(--bg-card) */}
      <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">{/* 8 个 MetricCard compact */}</div>
    </div>
    <div className="space-y-3 lg:col-span-4">           {/* 侧栏 4/12 */}
      <AiPredictCard />                                 {/* 置顶：决策相关 */}
      <TechIndicatorCard />                             {/* 10 项技术指标，两列 */}
    </div>
  </div>
  <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">{/* 研究模块 3×2 → 12 栅格，每块 col-span-4 */}</div>
</div>
```

**OrderDesk**（现 `xl:grid-cols-12`，左 4 右 8 —— **方向反了**）：

> 现状：账户 + 下单 + Kill Switch 在**左 4**，母 / 子单表在**右 8**。但下单是主操作、表格是监视。

```tsx
<div className="grid h-[calc(100vh-8rem)] grid-cols-1 gap-3 xl:grid-cols-12">
  {/* 主区（左）：母 / 子单监控 + 合规池 —— 各自独立滚动 */}
  <div className="flex min-h-0 flex-col gap-3 xl:col-span-8">
    <SectionCard bodyClassName="p-0">
      <div className="max-h-full overflow-auto">{/* 母 / 子单表 */}</div>
    </SectionCard>
    <SectionCard>{/* 合规禁买池 (max-h-52) */}</SectionCard>
  </div>
  {/* 操作区（右）：持仓 / 账户 → 下单 → Kill Switch 三段 */}
  <div className="flex min-h-0 flex-col gap-3 xl:col-span-4">
    <AccountCard /> <OrderForm /> <KillSwitch />
  </div>
</div>
```
- 依据：右侧放「下单 / 熔断」符合「高频操作集中在固定位置」原则，且不遮挡主表。
- 关键：整块 `h-[calc(100vh-8rem)]` + 子项 `min-h-0` + 各自 `overflow-auto` → 表格在其内滚动，Kill Switch 永远固定可见。

### 3.4 多屏 / 小屏降级

- **超宽屏 ≥1920**：内容 `max-w-[1600px]/[1720px]` 居中，两侧留白给「备注便签 / 自选浮窗」；三栏仍成立，不拉长阅读行。
- **多显示器**：提供 `?detach=chart` 独立窗口路由（K 线 / 订单簿单独开窗），主窗只留行情 + 下单。低成本：路由参数 + 复用组件。
- **1366×768（小屏，P1）**：`xl`(1280) 以下 OrderDesk 左 4 右 8 会挤，**降为单列 + 底部 action bar**：
  ```tsx
  {/* 下单区在小屏变底部固定条 */}
  <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-surface p-2
                  xl:static xl:z-auto xl:rounded-lg xl:border">
  ```
  表格 `max-h-[520px]` → 小屏 `h-[42vh] xl:max-h-[520px]`。
- **折叠断点**：Sidebar 在 `<md` 改抽屉（现始终 `sticky w-40` 占宽）。

---

## 4. 信息层级（收敛混乱的核心）

### 4.1 统一排版规范表

| 角色 | 字号(px) | Tailwind | 字重 | 行高 | 颜色 token | 字距 | tabular-nums |
|------|---------|----------|------|------|-----------|------|--------------|
| 页面主标题 | 18 | `text-lg` | 600 | 1.4 | `text-ink` | -0.01em | — |
| 卡片 / 区块标题 | 13 | `text-[13px]` | 600 | 1.4 | `text-ink` | 0 | — |
| 大数值（KPI 主） | 24 | `text-2xl` | 600 | 1.1 | `text-ink` / `t-up` / `t-down` | -0.02em | ✓ `.num` |
| 中数值（卡内） | 16 | `text-base` | 600 | 1.2 | 同上 | -0.01em | ✓ |
| 小数值（表格） | 12 | `text-xs` | 500 | 1.4 | `text-ink` | 0 | ✓ |
| 标签 / 说明 | 12 | `text-xs` | 400 | 1.5 | `text-ink-secondary` | 0 | — |
| 单位 / 时间戳 / caption | 11 | `text-2xs` | 400 | 1.4 | `text-ink-muted` | 0.02em | ✓ |
| 表头 | 11 | `text-2xs` | 500 | 1.4 | `text-ink-secondary` | 0.06em uppercase | — |
| 代码 / 日志 | 12 | `text-xs` | 400 | 1.5 | `text-ink-secondary` | 0 | ✓ mono |

**收敛动作：**
- **页面主标题唯一化**：删除 `text-xl font-semibold`(9) / `text-base font-semibold`(4)，全部归 **`text-lg font-semibold text-ink`**。→ 解决「4 种主标题字号」。
- **11px 使用降级**：`text-2xs` 现 **521 处**，只允许出现在「单位 / caption / 表头 / 时间戳」。正文与标签提到 `text-xs`(12px)。
- **裸 px 全清**：`text-[8px]`(4) / `text-[10px]`(3) / `text-[9px]`(1) / `text-[11px]`(1) 归入 6 档阶梯（8–11 合并到 `text-2xs`）。
- **数字规范**：所有会跳动数字加 `.num`（tabular-nums + JetBrains Mono）并右对齐。`.num` 已存在 `index.css`，需扩大覆盖。

### 4.2 间距刻度（4px 基数）

`0 / 0.5(2) / 1(4) / 1.5(6) / 2(8) / 3(12) / 4(16) / 5(20) / 6(24) / 8(32)`。**禁用 5px / 7px / 9px 等非 4 倍数值。**

- **页面根容器**：统一 **`space-y-3`（12px）**。→ 解决现状 `space-y-3`(10) / `space-y-4`(1) / `space-y-2`(1) 并存。
- **卡片内 padding**：标准 `p-4`；紧凑 `p-3`；表格态 `p-0`（表头 / 单元格自带 padding）。
- **栅格 gap**：跨面板统一 `gap-3`；卡内网格 `gap-2`。

### 4.3 卡片分组规则（padding / 圆角 / 分割线）

- **圆角**：卡片 `rounded-lg`(8px)，内部小块 `rounded-md`(6px)，徽章 `rounded`(4px)。**全局只用 3 档**（页面里偶发 `rounded-sm` 需清理）。
- **边框**：**只用边框不用阴影**（专业终端感）。统一 `border border-hair`；悬停行 `bg-surface-alt`，不用阴影浮起。
- **分割线**：卡片标题区 `border-b border-hair`（SectionCard 现状保持，`ui/index.tsx` L27）；**禁止用 `<hr>` 装饰**，用间距代替。
- **卡片标题栏**：统一 `px-4 py-2.5`，标题 `text-[13px] font-semibold`（SectionCard 现为 `text-sm`=14px，微调更省），action 居右。
- **降级 / 占位（承接红线）**：`PanelEmpty`/`DegradedBadge`/`ErrorState` 三态必须**与原位数据同可见**；视觉规范把 `empty/disabled`（可弱）与 `degraded`（不可弱）画成两种样式。

---

## 5. 交互体验（可访问性 + 操作效率）

### 5.1 全局快捷键表（P0，现仅 K 线图内有 F8 / Ctrl+Q / Ctrl+B）

采用 Bloomberg 式**上下文无关全局功能键**，避开图表内已用键：

| 键 | 功能 | 依据 |
|----|------|------|
| `/` | 聚焦顶部搜索 | 最高频入口，通用约定 |
| `g` `d` | go dashboard（市场概览） | GitHub 式两段导航 |
| `g` `p` / `g` `o` / `g` `w` | 持仓 / 执行中心 / 自选 | 高频跳页 |
| `n` | 打开通知 | 迎合铃铛 |
| `?` | 快捷键帮助浮层 | 可发现性 |
| `Esc` | 关抽屉 / 弹窗 / 取消输入 | `Modal` 已用，扩展 |
| `Ctrl+K` | 命令面板（跳页 + 搜索合一） | 现代终端标配，P1 |

实现：`useEffect` 监听 `keydown` + 顶层 `HotkeyProvider`；**输入框聚焦态避让**（`e.target` 是 input 时不触发单键）。

### 5.2 订单 / 持仓相关（P0）

- **下单区常驻（P0）**：任意页面右侧可拉出「快速下单」抽屉（点持仓行 → 预填标的）。依据：现下单需导航到 `/desk`，丢上下文。
- **悬浮预览（P1）**：表格里股票代码 hover → 浮出迷你 K 线 + 最新价（复用 `Sparkline`）。依据：选股 / 持仓表来回跳个股页成本高。
- **右键菜单（P1）**：表格行右键 → 加自选 / 建预警 / 复制代码 / 快速下单。依据：批量操作起点。

### 5.3 数字告警闪烁 —— 克制用法（P1）

**规则**：**只有「本面板当前最高优先级事件」允许闪烁**，且用**背景脉冲**而非文字闪烁，频率 ≤1.2s，持续 ≤6s 后转静态强调色。

```css
@keyframes pulse-bg{0%,100%{background:var(--danger-bg)}50%{background:var(--danger-soft)}}
.flash-danger{animation:pulse-bg 1.2s ease-in-out 5;}      /* 5 次后停 */
@media (prefers-reduced-motion: reduce){.flash-danger{animation:none}}  /* 无障碍必加 */
```
依据：现预警 / 熔断无统一强调；闪烁滥用会淹没真正危险。

### 5.4 止损止盈 / 实时盈亏可访问性（P0）

- **盈亏数字置于 Topbar 常驻 Badge**，实时涨跌色；点击展开持仓抽屉。
- **止损止盈**：持仓行内联可编辑，失焦乐观更新；触及止损时该行整行 `bg-danger-bg` + 图标。
- **Kill Switch**：保持独立卡片 + 二次确认（`OrderDesk` 现已有 modal，补唯一 `danger` 红）。

### 5.5 布局可调 + 批量（P2）

- **可拖拽面板**：K 线 / 订单簿可拖拽调宽，存 `localStorage`。依据：不同用户偏重不同。
- **批量操作**：表格多选（Shift 范围选）→ 批量加自选 / 批量预警 / 批量平仓。

---

## 6. 模块整合建议（专章）

**现状侧边栏 12（主）+ 6（数据）= 18 项**（`Sidebar.tsx` `NAV_MAIN` / `NAV_DATA`），已超载，且存在「列表页 / 详情页」重复入口（ETF中心 vs ETF分析、组合回测 vs 策略回测语义重叠）。建议重排为 **5 组、一级项 ≤12，并补分组图标**。

### 6.1 建议导航结构（分组名 + 组内项 + 图标分配）

沿用现有 24×24 线性图标规范（`ICON_BASE`，stroke 1.6）；括号为复用 / 新增图标。

```
▎盯盘
  · 市场概览        （/，/market 合并 —— 现二者同组件，删别名入口）  [IconOverview]
  · 个股分析        （/stock/:symbol）                              [IconStock]
  · 自选与预警      ← 合并「我的收藏 + 预警中心」                   [IconStar + IconBell 组合/用 IconStar]
▎交易
  · 执行中心        （/desk）                                        [IconBacktest→改 IconDesk]（新增：闪电/终端图标）
  · 持仓与组合      ← 合并「组合回测 + 实时持仓」                    [IconPortfolio]
▎研究
  · 选股中心        （/screener）                                    [IconScreener]
  · 策略评估        ← 「策略回测 + 组合回测 + 容量归因」收进一个工作台 Tab  [IconBacktest]
  · 因子工作室      （/studio）                                      [IconResearch]
  · 策略研究        （/research）                                    [IconResearch]
▎资产池
  · ETF 中心        ← 「ETF中心 + ETF分析」合并（列表 + 详情子路由，单入口）  [IconEtf]
  · AI 日报         （/report）                                      [IconReport]
▎数据与设置（可折叠）
  · 数据中心        ← 「数据中心 + 数据质量」合并为 Tab           [IconDatabase]
  · 任务调度        （/pipeline）                                    [IconMenu→改 IconSchedule]
  · 系统设置        （/settings）                                    [IconSettings]
```

### 6.2 合并 / 拆分依据

- **合并** `Watchlist + Alerts`：都是「我关注的标的触发的」，同一心智；预警是自选的增强。
- **合并** `DataCenter + DataQuality`：同属数据运维，一页两 Tab。
- **合并** `Backtest + Portfolio + Capacity`：同为「策略评估」链路（回测 → 组合 → 容量归因），一条流水线，用 Tab 而非 3 个一级项。→ 现 3 项各占导航，且 `Portfolio`「组合回测」与「实时持仓」命名混淆。
- **合并** `Etf + EtfDetail`：现侧栏两个 ETF 入口，实为列表 / 详情；详情应由列表进入，保留一个一级项。
- **拆出** `Settings` 到页面底部 / 用户菜单（低频，不占导航首屏）。
- **新增一级项**「盯盘」分组，聚合 `个股分析 / 自选 / 预警`。→ 解决「无实时盯盘入口、18 项平铺无分组语义」。

> 成本：**改 1 个组件**（`Sidebar.tsx` 数据数组 + 新增 2 个图标 SVG），不新增页面。
> 与 product 的 `AQP-frontend-ia-review.md` 结论方向一致，最终分组以 product 的任务流评审为准。

---

## 7. 执行清单（按优先级）

### P0（先做，收益最大，多为改 config / 共享层）

| # | 动作 | 成本 | 解决的现状问题 |
|---|------|------|----------------|
| P0-1 | 落地 **2.2 颜色 token**（涨跌 / 状态解耦） | **A 改 config** | `text-red-600` 78 处红义冲突 |
| P0-2 | 删除 `invert` 暗色 hack，换 CSS 变量真暗色 | **A 改 config + 改 index.css:27-36** | 反色致图表色相错乱 |
| P0-3 | K 线色值改读 CSS 变量 | **B 改 1 组件**（`KLineChart.tsx:18-23`） | 图表红绿 ≠ 全站 |
| P0-4 | App.tsx 改 `h-screen` 三栏固定 + main 独立滚动 | **B 改共享层**（`App.tsx:76-119`） | 整页滚动丢上下文 |
| P0-5 | main 加 `mx-auto max-w-[1600px]`（表格页） | **B 改共享层** | 超宽屏行过长 |
| P0-6 | 页面主标题统一 `text-lg font-semibold` | **C 改 ~31 处** | 4 种标题字号 |
| P0-7 | 根容器统一 `space-y-3` | **C 改 ~12 处** | 3 种间距 |
| P0-8 | 全局快捷键（`/` `g` `n` `?` `Esc`）+ 输入框避让 | **D 新增 1 Provider** | 仅图表内有快捷键 |
| P0-9 | Sidebar `md` 以下改抽屉；OrderDesk 反转主 / 操作区 | **B 改 2 组件** | 小屏挤压、操作区被挤 |

### P1

| # | 动作 | 成本 | 解决 |
|---|------|------|------|
| P1-1 | 硬编码状态色迁移到 token（emerald/amber/red 200+ 处） | **C 批量改** | 语义污染（渐进，按页做） |
| P1-2 | 11px(521) / 裸 px 全量归 6 档阶梯 | **C 批量改** | 字号失控 |
| P1-3 | 表格数字全 `.num` + 右对齐 | **B 改共享 th/td** | 数字跳动 |
| P1-4 | `Ctrl+K` 命令面板 | **D 新增组件** | 跳页效率 |
| P1-5 | 悬浮预览迷你 K 线（复用 Sparkline） | **D 新增组件** | 表格 ↔ 详情往返 |
| P1-6 | 数字告警克制闪烁 + `prefers-reduced-motion` | **A 改 index.css + 卡片** | 强调不滥用 |
| P1-7 | 盈亏常驻 Badge（Topbar）+ 持仓抽屉 | **B 改 Topbar** | 实时盈亏不可见 |

### P2

| # | 动作 | 成本 | 解决 |
|---|------|------|------|
| P2-1 | 可拖拽面板宽度 + localStorage | **D 新增 hook** | 布局个性化 |
| P2-2 | 表格多选 + 批量操作 | **B 改表格组件** | 批量效率 |
| P2-3 | 右键菜单（加自选 / 预警 / 下单） | **D 新增组件** | 快捷操作 |
| P2-4 | 独立窗口路由 `?detach=chart` | **C 改路由** | 多显示器 |
| P2-5 | MetricCard 加 `emphasis` 变体（更大数字） | **B 改 UI 组件** | KPI 主次 |

---

## 附：改动成本速查

| 层级 | 定义 | 例子 |
|------|------|------|
| **A 改 config** | 1 文件，全局生效 | 颜色 token、字号阶梯、maxWidth |
| **B 改共享组件** | ui/index.tsx / App.tsx / Sidebar.tsx / KLineChart.tsx | 标题规范、滚动结构、导航 |
| **C 改单页** | 某 page 的 class | 栅格调整、间距、批量色值 |
| **D 需重构** | 逻辑 + 结构改动 | 快捷键 Provider、命令面板、持仓抽屉 |

---

*— Designer 产出，视觉 / 配色 / 排版 / 栅格 / 交互维度；信息架构合并需与 product 的 `AQP-frontend-ia-review.md` 对齐。*
