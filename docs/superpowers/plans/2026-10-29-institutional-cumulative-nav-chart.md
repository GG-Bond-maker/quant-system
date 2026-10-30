# 机构级累计净值基准对比图表（含跑赢/跑输差分阴影与摩擦披露）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现量化机构标准的累计净值基准对比图表（ECharts），支持策略与基准双曲线、跑赢/跑输双色差分动态着色阴影、1.0 水平参考基准线、图例动态计算展示年化复合收益率、以及样本属性（全样本/样本外 OOS）与双边摩擦成本（bps）的规范化披露，并在策略回测（`/backtest`）与投资组合回测（`/portfolio`）中落地。

**Architecture:** 
1. 在 `src/lib/chartTheme.ts` 集中注入机构级图表主题色与透明度 token（符合 `check-style.mjs` 白名单规范，支持亮/暗主题）。
2. 构建可复用的 `InstitutionalNavChart` 组件，通过双重 Stacking 差分算法计算 `min(strategy, benchmark)`、`max(0, strategy - benchmark)`（跑赢）与 `max(0, benchmark - strategy)`（跑输），并绘制 1.0 水平基准线与动态年化图例。
3. 升级 `Backtest/resultParts.tsx` 的 `NavChart`，支持「机构对比 (跑赢/跑输)」、「基础策略与基准」和「超额 Alpha」模式平滑切换，并感知 Walk-Forward 样本外属性与双边成本（35bp 等）。
4. 升级 `Portfolio/index.tsx` 的 `NetValueChart`，使组合回测享受同等机构级可视化呈现。

**Tech Stack:** React 18, TypeScript, ECharts 5.5.1, Tailwind CSS, Vitest.

**Spec:** 用户上传的图表原型《累计净值（样本外 2019–2025，含双边成本 35bp）》及 AQP 前端量化设计规范。

## Global Constraints

- 严守 `scripts/check-style.mjs` 闸门：禁止在 `src/pages` 或 `src/components` 内硬编码 hex 色值；所有图表色彩必须来自 `src/lib/chartTheme.ts`。
- 严禁手写 `<h1>`，遵循项目标题规范。
- 保证 `npm run type-check` 和 `npm run test:raw` 100% 通过，不破坏既有 417 项单元测试。

## Review Focus

1. 差分堆叠在两线交叉（Cross-over）时的平滑性：确保在策略与基准交汇点处数值为 0，无缝过渡，不发生闪烁或异常锯齿。
2. 年化收益计算边界：当净值序列点数过少、时间跨度过短或数据有 null/0 时，安全降级，不抛出 `NaN%` 或除零异常。
3. 双边摩擦成本（bps）计算：根据输入的 commission_rate 与 slippage_bps 准确折算双边总成本（基点 bps）。
4. 亮色/暗色主题适配：`useTheme()` 变化时，图表色彩与背景自适应重绘。
5. 现有测试兼容性：保证 `BacktestUpgrades.test.tsx` 中已有的交互与文本断言持续通过。

---

### Task 1: 扩展图表主题色与量化年化辅助函数

**Files:**
- Modify: `frontend/src/lib/chartTheme.ts`
- Test: `frontend/src/hooks/useTheme.test.ts` 或对应工具测试

- [ ] 在 `frontend/src/lib/chartTheme.ts` 中增补机构级对比配色方案与透明度（`STRATEGY_LINE`, `BENCHMARK_LINE`, `OUTPERFORM_FILL`, `UNDERPERFORM_FILL`, `BASELINE`）。
- [ ] 导出安全的年化收益率计算与年化标签辅助函数 `calcAnnualizedReturn(navSeries)`。
- [ ] 运行 `npm run style:check` 验证白名单合规。

### Task 2: 实现机构级差分净值图核心组件 `InstitutionalNavChart`

**Files:**
- Create: `frontend/src/components/charts/InstitutionalNavChart.tsx`
- Test: `frontend/src/components/charts/InstitutionalNavChart.test.tsx`

- [ ] 编写测试用例 `InstitutionalNavChart.test.tsx`，覆盖：
  - 跑赢与跑输数据序列的堆叠计算与正确渲染
  - 动态标题生成（含样本属性与双边成本 bps）
  - 1.0 水平线与图例年化收益率文本展示
- [ ] 实现 `InstitutionalNavChart.tsx`：
  - 接收 `navPoints: Array<{ date: string; strategy: number; benchmark: number | null }>`、`title?: string`、`sampleLabel?: string`、`costBps?: number`、`signals?: StrategySignal[]` 等属性。
  - 使用双 Stacking 算法生成跑赢/跑输阴影，并绘制主折线与参考线。
  - 支持 DataZoom 与多维度 Tooltip。
- [ ] 运行测试验证 `InstitutionalNavChart.test.tsx` 全部通过。

### Task 3: 升级策略回测主净值图 `NavChart` 与 Walk-Forward 样本外联动

**Files:**
- Modify: `frontend/src/pages/Backtest/resultParts.tsx:98-345`
- Modify: `frontend/src/pages/Backtest/index.tsx`
- Test: `frontend/src/pages/Backtest/BacktestUpgrades.test.tsx`

- [ ] 在 `NavChart` 中引入 `viewMode` 三态：`'spread'`（机构对比，含跑赢/跑输差分阴影）、`'nav'`（策略与基准折线）、`'alpha'`（超额 Alpha 曲线）。
- [ ] 传递当前回测上下文（佣金与滑点折算的成本 bps，以及 Walk-Forward 样本外标记）。
- [ ] 确保 `BacktestUpgrades.test.tsx` 现有测试完全通过，并增补对 `'机构对比 (跑赢/跑输)'` 视图的测试。
- [ ] 运行 `npm run test:raw` 验证回测模块相关测试。

### Task 4: 升级投资组合回测主净值图 `NetValueChart`

**Files:**
- Modify: `frontend/src/pages/Portfolio/index.tsx:126-180`
- Test: `frontend/src/pages/Portfolio/EfficientFrontierCard.test.tsx`

- [ ] 将 `Portfolio/index.tsx` 的 `NetValueChart` 升级为具备跑赢/跑输区间差分填充与年化指标图例。
- [ ] 动态计算多资产组合年化收益与基准年化收益，并在图例中高亮呈现。
- [ ] 验证投资组合回测界面渲染正常。

### Task 5: 验证风格闸门、类型安全与完整测试套件

**Files:**
- Test All

- [ ] 运行 `npm run style:check`（确认无 hex 硬编码违规与 h1 违规）。
- [ ] 运行 `npm run type-check`（确认 TypeScript 0 错误）。
- [ ] 运行 `npm run test:raw`（确认所有单元测试通过）。
- [ ] 运行 `npm run nav:check`（确认路由完整性）。
