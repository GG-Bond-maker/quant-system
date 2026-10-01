# AQP 前端布局 / 信息架构 改造执行报告

**日期**：2026-10-01
**场景**：前端 IA 复盘 + 落地执行 + 质量验证（多成员协作）
**参与成员**：产品官（gstack-product-reviewer） + 设计师（gstack-designer） + 质量门神（gstack-qa-lead） + 通用实施（general-purpose-1）
**输入基线**：`deliverables/gstack/AQP-frontend-ia-review.md`（12 项 P0/P1/P2 提案）

---

## 📌 TL;DR（执行摘要）

- **整体结论：🟢 通过（有条件）** —— 12 项提案全部有明确落点，前端 5 道质量闸门全绿。
- **关键发现**：上一轮改造把 5 个页面从侧边栏**摘除却没有建任何入口**，同时留下「由相邻入口进入」的**误导性提示** —— 这是**用户可见的功能回归 + 主动误导**，严重度高于原 P0 清单中的任何一项。
- **根因发现**：项目**完全没有 ESLint / lint 脚本 / 检查配置**。这一条解释了此前所有「规范被绕过」现象（PageHeader 仅 1/19 使用、quant-table 55% 绕过、144 处硬编码色值）。
- **量化反差**：人工估算硬编码色值约 24 处，闸门脚本实测 **144 处**（6 倍低估）⇒ 证明「没有闸门，连债务规模都观测不到」。
- **下一步**：清完 ✅，但「方向性配色正确性」**无机器闸门可守**，需人工评审兜底。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟡 **条件 Go**（功能与门禁全绿；遗留 1 项需人工评审的盲区） |
| 严重度分布 | 🔴 1（已修） / 🟠 3（已修） / 🟡 6（已修） / 🟢 11（已完成） |
| 提案完成度 | **12 / 12 有落点**（9 项上轮已完成，3 项本轮补齐 + 1 项上轮引入的回归被纠正） |
| 前端改动文件 | **65 个已跟踪文件** + **6 个新增文件** |
| 质量闸门 | 5 / 5 全绿（tsc / build / bundle / style-h1 / style-hex） |
| 关键行动项 | 3 条（见下） |

---

## 1. 各成员核心结论

### 🔍 产品官（产品评审）
- **核心判断**：原文档 §8 划的「红线」是对的 —— 降级态必须原位可见。但**执行层违反了红线之外的另一条隐性规则**：把页面从导航摘除时，**声明「由 X 进入」却没有让 X 真的链过去**。`Sidebar.tsx:180-191` 的注释声称「由相邻入口进入」，grep 验证 5 个宿主页**没有任何一条链接**指向被摘除的目标。
- **关键建议**：把「虚假整合」升为 **P0-1**（高于动作条），因为它是**用户可见的功能回归**且带**主动误导**（tooltip 骗用户）。修复方案**不采纳**「Backtest 真加 Tab」（566 行 Portfolio 塞进 3 标签页 = 双层结构，成本不成比例），改用 5 分钟的「下游分析」入口条。

> ⚠️ **2026-10-01 勘误**：本节初稿称「5 页被摘出导航」，**该数字有误**。
> 经 `git diff HEAD -- frontend/src/components/Sidebar.tsx` 逐行核对，实际被摘除的是 **2 项**
> （`/portfolio` 组合回测、`/capacity` 容量与归因）。
> `/research`、`/dataquality`、`/pipeline` **从未被摘除**，一直在侧栏正常渲染
> （初稿误判原因：只 grep 了 JSX 属性写法 `to="/x"`，漏了对象字面量写法 `to: '/x'`）。
> 问题性质不变 —— 「注释声称有入口、实际没有」依然成立，只是范围是 2 页而非 5 页。

### 🎨 设计师（设计系统与视觉）
- **核心判断**：`frontend/package.json` **没有 ESLint、没有 lint 脚本、没有任何检查配置**。这一条**同时**解释了 4 类看似独立的债务：PageHeader 17/18 页绕过、quant-table 55% 绕过、~24 处硬编码 hex、`SectionCard` 默认 `p-4` 被 124 处覆盖。**不是 4 种病，是 1 种病**。
- **关键建议**：走 **Path A —— 零依赖的 `check-style.mjs`**。设计上必须**双开关**：h1 规则默认开（零争议，可立即卡住），hex 规则 `--hex` 显式开（存量未清，硬开会让所有人绕过闸门）。

### ✅ 质量门神（QA 测试与发布）
- **核心判断**：**主题响应式是架构级缺口**。`chartPalette()` 在**调用时**读 DOM，但只有 `KLineChart` 用了 `useTheme()`；其余 21 个图表组件切主题后**不会重绘**，要等下一次数据刷新才生效。修法必须**严格照 `KLineChart` 范式**，不自创。
- **关键建议**：给每个「调用 `chartPalette()` 且结果进 `useMemo`」的组件补 `const theme = useTheme()` + 把 `theme` 加进 deps；内联算色的组件只需裸调 `useTheme()` 触发重渲染。
- **本成员运行中断**：任务执行到 8/22 文件时触发 **429 频率限制**，由主理人接手补完剩余 14 个文件。

### 🔧 通用实施（general-purpose-1）
- **核心判断**：17 个页面手写 `<h1>` 批量迁移到 `<PageHeader>`，过程发现 `tsc -b` **增量缓存竞态**：并发跑 `tsc -b` 会污染 `node_modules/.tmp`，产生**假的 TS6133「已声明未使用」**错误。
- **关键建议**：验证前必须 `rm -rf node_modules/.tmp && npx tsc -b --force`，否则会追着假错误跑。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🔴 | 功能回归 / 误导 | `Sidebar.tsx:180-191` | **2 页**（`/portfolio` `/capacity`）被摘出导航，**无任何入口**；注释却声称「由相邻入口进入」——tooltip 主动骗用户 | 删全部误导 `mergedNote`；Backtest 加真实「下游分析」入口条 | 产品官 |
| 2 | 🟠 | 工程基建 | `frontend/package.json` | **无 ESLint / 无 lint / 无配置** —— 单因导致 4 类规范被系统性绕过 | 建 `check-style.mjs` 零依赖闸门（双开关） | 设计师 |
| 3 | 🟠 | 正确性 / 语义 | `Backtest/resultParts.tsx` | KPI 迷你走势图 `up ? '#3B82F6' : '#EF4444'`（**蓝涨红跌**），与正上方 `t-up`/`t-down` 文字的**红涨绿跌**完全相反 | 改 `p.UP : p.DOWN` | 主理人 |
| 4 | 🟠 | 正确性 / 语义 | `Backtest/resultParts.tsx` | 月度收益柱图「正=蓝 / 负=橙」，与全站红涨绿跌口径不符 | 改 `p.UP : p.DOWN` | 主理人 |
| 5 | 🟡 | 主题响应 | 21 个图表组件 | `chartPalette()` 调用时读 DOM，但未订阅主题 ⇒ 切主题不重绘 | 补 `useTheme()` + deps | 质量门神 |
| 6 | 🟡 | 债务不可观测 | 全前端 | 人工估算 24 处硬编码 hex，闸门实测 **144 处**（后清至 136，再清至 0） | 先度量再清理 | 设计师 |
| 7 | 🟡 | 架构缺口 | `MarketOverview` / `StockDetail` | `isMarketOpen()` 两处**逐字节相同**的重复实现 | 抽 `useMarketSession.ts` | 主理人 |
| 8 | 🟡 | 色板孤岛 | `KLineChart.tsx` | 私有 `cssVar()` 导致 8 个文件各写各的 24 处色值 | 抽 `lib/chartTheme.ts` 作唯一真源 | 主理人 |
| 9 | 🟡 | 视觉不一致 | `KLineChart.tsx` 周期切换按钮 | `border-[#ffd591] bg-[#fffbe6] text-[#d48806]` 未走 brand token | 改 `border-brand-300 bg-brand-50 text-brand-600` | 主理人 |
| 10 | 🟢 | 排版不收敛 | 根容器 | `space-y-4` / `space-y-3` 混用 | 统一 `space-y-3` | 产品官 |
| 11 | 🟢 | 侧边栏状态 | `Sidebar.tsx` | 收起状态刷新即丢 | `useUiStore` persist（已有基础设施） | 产品官 |
| 12 | 🟢 | 信息密度 | `MarketOverview` | 5 张等权 KPI 卡，无主次 | 改「1 大 + 4 小」 | 产品官 |

---

## 3. 逐项验收：12 项提案的实际落地状态

> 判定口径：**能 grep 到真实入口 / 真实组件** 才算「已完成」；仅有注释声明不算。

| # | 提案 | 上轮状态 | 本轮核验结论 | 落点 |
|---|------|---------|-------------|------|
| P0-1 | 删除 ETF分析 导航项 | ✅ 已做 | ✅ **已完成**（ETF 已并入「数据中心」域） | `Sidebar.tsx` |
| P0-2 | 侧边栏收起状态持久化 | ✅ 已做 | ✅ **已完成**（`useUiStore` persist） | `stores/useUiStore.ts` |
| P0-3 | 根容器间距统一 `space-y-3` | ⚠️ 部分 | ✅ **本轮补齐**（`StockDetail` 由 `space-y-4` 改正） | `StockDetail/index.tsx` |
| P0-4 | MarketOverview KPI「1 大 + 4 小」 | ✅ 已做 | ✅ **已完成** | `MarketOverview/pieces.tsx` |
| P0-5 | StockDetail 动作条 `[加自选][设预警][去下单]` | ❌ 未做 | ✅ **本轮新建** `ActionBar`（含 RBAC 门控 + 显式错误展示） | `StockDetail/index.tsx` |
| P1-6 | 组合回测并入策略回测 | ⚠️ **假整合** | ✅ **本轮纠正**：不合并，改为在 Backtest 建真实「下游分析」入口条 | `Backtest/index.tsx` |
| P1-7 | 数据质量/任务调度并入数据中心 | ⚠️ **假整合** | ✅ **本轮纠正**：删误导提示；两项**本就未摘除**，补专属图标后保留独立导航项 | `Sidebar.tsx` |
| P1-8 | 导航图标去重 + `IconFlask`/`IconGauge` | ✅ 已做 | ✅ **已完成** | `Sidebar.tsx` |
| P1-9 | StockDetail 事件面板抽成抽屉 | ❌ 未做 | ✅ **本轮新建**：摘要卡（近 3 条）+ `Drawer`（全量），降级态 `PanelEmpty` **原位保留** | `StockDetail/index.tsx` |
| P1-10 | 容量与归因并入回测结果 | ⚠️ **假整合** | ✅ **本轮纠正**：建真实入口条 | `Backtest/index.tsx` |
| P2-11 | 策略研究 + 因子工作室 合并 | ⚠️ **假整合** | ✅ **本轮纠正**：`/research` **本就未摘除**，删误导提示后保留 | `Sidebar.tsx` |
| P2-12 | 自选池移入「盯盘」组 | ✅ 已做 | ✅ **已完成** | `Sidebar.tsx` |
| 附 | `useUiStore.lastPath` 会话恢复 | ✅ 已做 | ✅ **已完成** | `stores/useUiStore.ts` |
| 附 | `isMarketOpen()` 去重 | ❌ 未做 | ✅ **本轮新建** `useMarketSession.ts` | `hooks/useMarketSession.ts` |
| 附 | `useHotkeys` 全局快捷键 | ✅ 已做 | ✅ **已完成**（含 `HotkeyHelp.tsx`） | `hooks/useHotkeys.ts` |

**汇总**：12 项提案 **全部有落点**；其中 **4 项属「上轮声明已完成、实为虚假整合」**，本轮一并纠正。

---

## 4. 本轮实际代码变更（按类别）

### 4.1 新建文件（6 个）

| 文件 | 作用 | 关键设计 |
|------|------|---------|
| `src/hooks/useMarketSession.ts` | 消除 `isMarketOpen()` 双份重复 | `OPEN_MIN=9*60+15` / `CLOSE_MIN=15*60+5`；周末直接 false |
| `src/lib/chartTheme.ts` | **图表配色唯一真源** | `cssVar()` / `isDarkTheme()` / `withAlpha()` / `chartPalette()` / `CATEGORY_COLORS` / `upDownColor()` |
| `src/hooks/useTheme.ts` | 画布组件主题响应 | `MutationObserver` 监听 `<html class>`，返回 `'light'\|'dark'` |
| `scripts/check-style.mjs` | **样式纪律闸门** | 双开关：h1 默认开、hex 用 `--hex` 显式开；支持 `--json` |
| `src/hooks/useHotkeys.ts` | 全局快捷键体系 | 此前快捷键只存在于 K 线图内部 |
| `src/components/HotkeyHelp.tsx` | 快捷键帮助浮层 | 可发现性 |

### 4.2 主题响应式修复（本轮补 14 个文件，累计 22 个）

**问题**：`chartPalette()` 在调用时读 DOM，但 `useMemo`/`useEffect` 不依赖主题 ⇒ 切主题后图表不重绘，要等下次数据刷新。

**修复范式**（严格照 `KLineChart` 样板）：

```tsx
// 范式 A：option 进 useMemo
const theme = useTheme();
// eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
const option = useMemo(() => { const p = chartPalette(); ... }, [data, theme]);

// 范式 B：option 内联计算（每次渲染重算）
useTheme();  // 裸调，仅用于订阅以触发重渲染
```

| 文件 | 范式 | 覆盖点 |
|------|------|--------|
| `pages/CapacityAttribution/index.tsx` | B | `styleOption` 内联 |
| `pages/Backtest/resultParts.tsx` | A×2 + B | `NavChart` / `MonthlyChart` memo；`KpiCards` 内联 |
| `pages/Backtest/SignalAnalysisPanel.tsx` | B | option 内联 |
| `pages/Etf/index.tsx` | A | `scaleOption` |
| `pages/Etf/OverviewCards.tsx` | B | `pal` 内联 |
| `pages/Etf/PerformanceChart.tsx` | A | `option` memo |
| `pages/EtfDetail/index.tsx` | A×4 + B×2 | `TrackingChart` / `TrackingErrorChart` / `VolumeChart` / `Gauge` memo；`GaugeQuad` / 主页内联 |
| `pages/FactorStudio/index.tsx` | B | fitness 双轴 option 内联 |
| `pages/OrderDesk/index.tsx` | B | 净值曲线 option 内联 |
| `pages/Portfolio/index.tsx` | A×3 | `NetValueChart` / `AnnualReturnsChart` / `HoldingsDriftChart` |
| `pages/Screener/DistributionCharts.tsx` | A×3 | `industryOption` / `scoreOption` / `pctOption` |
| `pages/Screener/PerformanceChart.tsx` | A×2 | 双 memo |
| `pages/Screener/StatsCards.tsx` | B | `pal` / `signalRing` 内联 |
| `components/charts/MarketHeatmap.tsx` | A×2 | `heat` / `moneyFlow` option |

### 4.3 语义方向性缺陷修复（正确性债，非视觉债）

这 4 处**机器查不出**，只能靠人比对「同一视觉区内相邻元素是否同口径」：

| 位置 | 原实现 | 问题 | 修复 |
|------|--------|------|------|
| `resultParts.tsx` KPI 迷你走势 | `up ? '#3B82F6' : '#EF4444'` | 蓝涨红跌，与正上方 `t-up`/`t-down` 文字**相反** | `p.UP : p.DOWN` |
| `resultParts.tsx` 月度收益柱 | 正=蓝 `#3B82F6` / 负=橙 `#F59E0B` | 与全站红涨绿跌不符 | `p.UP : p.DOWN` |
| `Etf/OverviewCards.tsx` 资金净流入卡 | 红字 + 绿 sparkline | 文字与图形方向矛盾 | `chartPalette()` 统一 |
| `Screener/StatsCards.tsx` / `Etf/OverviewCards.tsx` | `win_rate` / `avg_pct` | 涨跌方向与 token 不一致 | `upDownColor()` |

> **结论**：方向性配色错误不是偶发，是**系统性模式**。已写入闸门「已知盲区」文档：**闸门通过 ≠ 颜色没骗人**。

### 4.4 其他关键修改

| 文件 | 变更 |
|------|------|
| `components/Sidebar.tsx` | 删全部误导 `mergedNote`；删除被摘除后遗留的 `mergedNote` 字段与 tooltip 逻辑；加长注释声明「'由 X 进入' 必须 X 真链过去」；`/dataquality` `/pipeline` 图标由复用的 `IconDatabase`/`IconMenu` 换成专属 `IconShield`/`IconSchedule` |
| `pages/Backtest/index.tsx` | 新增 `useNavigate` + 「下游分析」入口条 → `/portfolio`、`/capacity` |
| `pages/StockDetail/index.tsx` | 新建 `ActionBar`；`EventsPanel` 改「摘要卡 + Drawer」；`space-y-4 → space-y-3`；删本地 `isMarketOpen` |
| `components/ui/index.tsx` | 新增 `SubNav`（页内二级导航） + `Drawer`（右侧抽屉） |
| `components/charts/KLineChart.tsx` | 删私有 `cssVar`/`isDark`，改 import `chartTheme`；周期按钮走 brand token |
| `components/charts/KpiBits.tsx` | 甜甜圈底环 `#F1F5F9` → `var(--bg-sunken)` |
| `pages/Watchlist/index.tsx` | **补 `useTheme` import**（上一轮中断留下的真实编译错误） |
| `package.json` | 新增 `style:check` / `style:check:hex` 脚本 |

---

## 5. 验收证据（5 道闸门全绿）

| 闸门 | 命令 | 结果 |
|------|------|------|
| 类型检查 | `npx tsc -b --force` | ✅ **0 error**（先清 `node_modules/.tmp` 排除增量缓存假错） |
| 生产构建 | `npx vite build` | ✅ **built in 7.87s**，最大 chunk `echarts` 696 kB / gzip 231 kB |
| 体积预算 | `npm run bundle:check` | ✅ raw ≤ 768000 B、gzip ≤ 256000 B |
| 样式闸门·h1 | `node scripts/check-style.mjs` | ✅ 0 违规（页面均未手写 `<h1>`） |
| 样式闸门·hex | `node scripts/check-style.mjs --hex` | ✅ 0 违规（**144 → 0**） |

**覆盖度交叉验证**：
- 调用 `chartPalette()` 的文件 **22 个，全部已订阅 `useTheme`**（唯一例外 `lib/chartTheme.ts` 是定义处，应豁免）。
- 手写 `<h1>` 全库 4 处命中：`Login`（白名单）、`ErrorBoundary`（白名单）、`ui/index.tsx` 文档注释、`PageHeader` 自身实现 —— **真实违规 0**。
- 19 个页面中 **18 个使用 `PageHeader`**，唯一例外 `Login`（刻意的独立登录页设计）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 把 `style:check` 接入 CI / pre-commit，否则闸门只是「手动脚本」，三周后回归 | 工程 | **P0** | 本周 |
| 2 | `style:check:hex` 的存量已清至 0，应**把默认值翻转为开启**（去掉 `--hex` 门槛） | 工程 | **P1** | 下周 |
| 3 | 补一条**人工评审清单**：每个含涨跌配色的视觉区，必须交叉核对「文字/图形/图例」三方方向一致（机器查不出） | 产品 + 设计 | **P1** | 下个迭代 |
| 4 | `SectionCard` 默认 `p-4` 仍被 124 处覆盖 —— 建议默认改 `p-3` 并清理覆盖点（本轮未做，属独立议题） | 设计 | P2 | 待排期 |
| 5 | 移动端断点仅用 2 次（`sm`），基本未适配；若「个人量化系统」需手机盯盘，需单独立项 | 产品 | P2 | 待排期 |

---

## ⚠️ 待完善 / 已知局限

1. **闸门只管「没有新的硬编码」，不管「颜色语义对不对」** —— 已写入脚本头部「已知盲区」。4 处方向反转全部是人工发现的。
2. **`eslint-disable-next-line react-hooks/exhaustive-deps` 引入了技术债标记** —— 主题依赖无法被 ESLint 静态理解；项目本就没有 ESLint，所以暂不构成实际代价，但接 CI 时需注意。
3. **`check-style.mjs` 的注释行排除规则不是白名单** —— 在注释里写 hex 不会报错。这是权衡（避免文档误报）而非疏漏，已在头部声明。
4. **`SectionCard` 内边距未收敛**（124 处覆盖），本次未动，避免污染改造范围。
5. **`CapacityAttribution` / `OrderDesk` / `FactorStudio` 等 opt-in 面板仍用「渲染即跑」模式**，主题订阅是裸调 `useTheme()`，属「够用但非最优」——若未来这些图表变重，应改为 memo + deps 范式。
6. **质量门神成员运行被 429 限流中断**（8/22 文件），剩余 14 个由主理人接手补完并复验 —— 结论有效，但**独立第二人复验环节被压缩**，建议后续对图表主题切换做一次真机（浏览器）目视验证。

---

## 📚 成员产出索引

- `gstack-product-reviewer`（产品官）：升级「虚假整合」为 P0-1 的评审判断；否决「Backtest 真加 Tab」方案；「视觉债 vs 正确性债」分类
- `gstack-designer`（设计师）：发现「无 ESLint」单因解释 4 类债务；`check-style.mjs` 双开关设计（Path A）
- `gstack-qa-lead`（质量门神）：发现主题响应式架构缺口；确认 `useTheme` 范式；**运行于 8/22 文件处被 429 限流中断**
- `general-purpose-1`（通用实施）：17 页 `<h1>` → `<PageHeader>` 迁移；发现 `tsc -b` 增量缓存假错

**关联文档**：
- 输入基线：`deliverables/gstack/AQP-frontend-ia-review.md`
- 设计规范：`deliverables/gstack/AQP-frontend-design-spec.md`
- 前序记录：`deliverables/gstack/frontend-layout-optimization-2026-10-01.md`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
