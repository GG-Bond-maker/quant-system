/**
 * 组合回测工作台（Portfolio Backtest Workbench）
 * 对标 Bloomberg PORT / Barra Aegis / WorldQuant BRAIN / JoinQuant
 *
 * 布局：
 *   左侧 (xl:col-span-3)：资产配置、权重输入、回测区间、资金、权重方案、调仓频率、基准选择
 *   右侧 (xl:col-span-9)：
 *     - 折叠式凸优化求解器 (PortfolioOptimizerPanel - initialCollapsed)
 *     - 复权口径与基准退化披露 Banner
 *     - 3-Tab 体系：
 *       Tab 1: 业绩与超额概览 (Overview & Alpha)
 *         - 10 核心机构 KPI 矩阵 (CAGR, Benchmark CAGR, Excess CAGR, IR, TE, Active Win Rate, Max DD, Active Max DD, Sharpe, Current DD)
 *         - 组合净值 vs 基准净值走势图 (NetValueChart)
 *         - 累计超额收益时序走势 (ActiveReturnChart)
 *         - 双分栏：水下回撤曲线 (DrawdownChart) + 年度收益分布柱状图 (AnnualReturnsChart)
 *       Tab 2: 风险预算与压力测试 (Risk & Stress)
 *         - 欧拉齐次风险分解 (%RC 剪刀差分析 & MRC / %RC 资产偏离度审计表)
 *         - 历史黑天鹅极端情景压力测试看板 (2015 股灾, 2016 熔断, 2018 贸易战, 2020 疫情, 2024 微盘股流动性危机)
 *         - 传统风险与极端非对称风险指标综合矩阵 (Volatility, Beta, Alpha, Sortino, Omega, CVaR 95%, CVaR 99%, Tail Risk Ratio)
 *         - 蒙特卡洛随机漫步预测置信扇形图 (Monte Carlo Bootstrap Fan Chart: 5%, 25%, 50%, 75%, 95% 分位数未来 1 年路径，含 Summary KPI)
 *       Tab 3: 调仓流水与摩擦损耗 (Trades & Friction)
 *         - 调仓摩擦损耗归因看板 (年化换手率, 摩擦拖累 Drag bps, 佣金/印花税/滑点)
 *         - 单期调仓换手率与交易额时序柱状图 (TurnoverChart)
 *         - 资产持仓变动与权重漂移堆叠图 (HoldingsDriftChart)
 *         - 逐期调仓执行审计日志表格 (RebalanceAuditTable)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';

import { ApiError } from '@/api/client';
import { portfolioApi } from '@/api/portfolio';
import ResearchDisclaimer from '@/components/ResearchDisclaimer';
import { hasMinimumRole } from '@/components/RequireAuth';
import { EmptyState, LoadingState, PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useAuthStore } from '@/stores/useAuthStore';
import type {
  ActiveReturnPoint,
  AssetSearchItem,
  MonteCarloFanPoint,
  MonteCarloSummary,
  PortfolioAsset,
  PortfolioBacktestResult,
  PortfolioWeighting,
  RebalanceFreq,
  RiskBudgetingItem,
  StressTestScenario,
  TurnoverRecord,
} from '@/types/portfolio';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';
import PortfolioOptimizerPanel from './PortfolioOptimizerPanel';

/* ==================== 常量 ==================== */
const REBALANCE_OPTIONS: Array<{ key: RebalanceFreq; label: string }> = [
  { key: 'M', label: '月度' },
  { key: 'Q', label: '季度' },
  { key: 'Y', label: '年度' },
  { key: 'none', label: '不调仓' },
];

const BENCHMARK_OPTIONS = [
  { key: '000300', label: '沪深300' },
  { key: '000905', label: '中证500' },
  { key: '000852', label: '中证1000' },
];

/** 权重方案：与后端 run_portfolio_backtest 的 weighting 参数一一对应（真实生效） */
const STRATEGY_OPTIONS: Array<{ key: PortfolioWeighting; label: string }> = [
  { key: 'user', label: '给定权重（自定义/等权）' },
  { key: 'hrp', label: '分层风险平价 (HRP 聚类)' },
  { key: 'risk_parity', label: '风险平价' },
  { key: 'max_div', label: '最大分散度' },
  { key: 'inverse_vol', label: '波动率倒数加权' },
];

const DEFAULT_ASSETS: PortfolioAsset[] = [
  { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.3 },
  { code: '600036', name: '招商银行', type: 'stock', weight: 0.4 },
  { code: '300750', name: '宁德时代', type: 'stock', weight: 0.3 },
];

type PortfolioTabKey = 'overview' | 'risk' | 'trades';

/* ==================== 复权口径披露 ==================== */
function portfolioBasisLabel(result: PortfolioBacktestResult): string {
  const pb = result.price_basis;
  if (!pb) return '复权口径未知';
  if (pb.basis === 'qfq') return 'QFQ（前复权）';
  if (pb.basis === 'raw') return '不复权（备用源降级）';
  if (pb.basis === 'unknown') return '复权口径未知';
  const n = pb.raw_fallback_symbols?.length ?? 0;
  return `QFQ + 不复权混用（${n} 个标的回退）`;
}

function portfolioBasisWarn(result: PortfolioBacktestResult): boolean {
  return !result.price_basis || result.price_basis.basis !== 'qfq';
}

/* ==================== 子组件 ==================== */

function KpiCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-hair bg-surface px-3 py-2.5">
      <div className="text-2xs text-ink-secondary">{label}</div>
      <div className={`num mt-0.5 text-base font-semibold ${tone ?? 'text-ink'}`}>{value}</div>
      {sub && <div className="mt-0.5 text-3xs text-ink-muted truncate">{sub}</div>}
    </div>
  );
}

function NetValueChart({ data }: { data: PortfolioBacktestResult['nav_curve'] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : '—'),
      },
      legend: {
        data: ['组合净值', '基准净值'],
        bottom: 0,
        textStyle: { fontSize: 11, color: p.INK2 },
      },
      grid: { left: 16, right: 16, top: 20, bottom: 32, containLabel: true },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date),
        axisLabel: { fontSize: 10, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: { color: p.INKM, formatter: (v: number) => v.toFixed(2) },
      },
      dataZoom: [
        { type: 'inside' },
        { type: 'slider', bottom: 0, height: 14, borderColor: p.HAIR },
      ],
      series: [
        {
          name: '组合净值',
          type: 'line',
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.8, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          data: data.map((d) => d.nav),
        },
        {
          name: '基准净值',
          type: 'line',
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.4, type: 'dashed', color: p.INK2 },
          itemStyle: { color: p.INK2 },
          data: data.map((d) => d.benchmark),
        },
      ],
    };
  }, [data, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-72 w-full" />;
}

function ActiveReturnChart({ data }: { data?: ActiveReturnPoint[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.length) return null;
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? fmtPct(v * 100) : '—'),
      },
      grid: { left: 16, right: 16, top: 20, bottom: 28, containLabel: true },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date),
        axisLabel: { fontSize: 10, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: {
          color: p.INKM,
          formatter: (v: number) => `${(v * 100).toFixed(1)}%`,
        },
      },
      series: [
        {
          name: '累计超额收益',
          type: 'line',
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.6, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          areaStyle: { color: withAlpha(p.BRAND, 0.12) },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [{ yAxis: 0, lineStyle: { color: p.INKM, type: 'dashed' } }],
          },
          data: data.map((d) => d.excess_return),
        },
      ],
    };
  }, [data, theme]);
  const ref = useChart(option);
  if (!option) return null;
  return <div ref={ref} className="h-56 w-full" />;
}

function DrawdownChart({ data }: { data: PortfolioBacktestResult['drawdown_curve'] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.length) return null;
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) =>
          typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : '—',
      },
      grid: { left: 16, right: 16, top: 16, bottom: 28, containLabel: true },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date),
        axisLabel: { fontSize: 10, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        max: 0,
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: {
          color: p.INKM,
          formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
      },
      series: [
        {
          name: '回撤',
          type: 'line',
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.2, color: p.DOWN },
          areaStyle: { color: p.DOWN, opacity: 0.12 },
          data: data.map((d) => d.drawdown),
        },
      ],
    };
  }, [data, theme]);
  const ref = useChart(option);
  if (!option) {
    return (
      <div className="flex h-56 items-center justify-center text-xs text-ink-muted">
        后端未返回回撤序列
      </div>
    );
  }
  return <div ref={ref} className="h-56 w-full" />;
}

function AnnualReturnsChart({ data }: { data: PortfolioBacktestResult['annual_returns'] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (pt: unknown) => {
          const ps = pt as Array<{ axisValue: string; data: number }>;
          return `${ps[0]?.axisValue ?? ''}<br/>组合 ${fmtPct((ps[0]?.data ?? 0) * 100)}`;
        },
      },
      grid: { left: 16, right: 16, top: 16, bottom: 24, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: p.INKM, formatter: (v: number) => fmtPct(v * 100) },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'category',
        data: data.map((d) => `${d.year}年`),
        axisLabel: { fontSize: 10, color: p.INKM },
      },
      series: [
        {
          type: 'bar' as const,
          data: data.map((d) => ({
            value: d.portfolio,
            itemStyle: { color: d.portfolio >= 0 ? p.UP : p.DOWN },
          })),
          label: {
            show: true,
            position: 'right',
            fontSize: 9,
            color: p.INK2,
            formatter: (pt: echarts.DefaultLabelFormatterCallbackParams) =>
              fmtPct(Number(pt.value) * 100),
          },
        },
      ],
    };
  }, [data, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-56 w-full" />;
}

function HoldingsDriftChart({
  data,
  codes,
}: {
  data: PortfolioBacktestResult['holdings_drift'];
  codes: string[];
}) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const pal = chartPalette();
    const dates = data.map((d) => d.date);
    const series = codes.map((code) => ({
      name: code,
      type: 'line' as const,
      stack: 'total',
      areaStyle: {},
      showSymbol: false,
      data: data.map((d) => (d.weights[code] ?? 0) * 100),
    }));
    return {
      tooltip: { trigger: 'axis' },
      legend: {
        data: codes,
        bottom: 0,
        textStyle: { fontSize: 10, color: pal.INK2 },
        type: 'scroll',
      },
      grid: { left: 16, right: 16, top: 16, bottom: 40, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: pal.INKM },
      },
      yAxis: {
        type: 'value',
        max: 100,
        axisLabel: { color: pal.INKM, formatter: '{value}%' },
        splitLine: { lineStyle: { color: pal.SUNKEN } },
      },
      series,
    };
  }, [data, codes, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-64 w-full" />;
}

function RiskBudgetingChart({ data }: { data: RiskBudgetingItem[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.length) return null;
    const p = chartPalette();
    const codes = data.map((d) => d.code);
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        valueFormatter: (v: unknown) =>
          typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—',
      },
      legend: {
        data: ['名义资产权重', '风险贡献占比 (%RC)'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 16, right: 16, top: 16, bottom: 36, containLabel: true },
      xAxis: {
        type: 'category',
        data: codes,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: {
          color: p.INKM,
          formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
      },
      series: [
        {
          name: '名义资产权重',
          type: 'bar',
          data: data.map((d) => d.weight),
          itemStyle: { color: p.BRAND },
          barGap: '10%',
        },
        {
          name: '风险贡献占比 (%RC)',
          type: 'bar',
          data: data.map((d) =>
            d.rc_pct != null ? d.rc_pct : (d.pct_risk_contribution ?? 0) / 100,
          ),
          itemStyle: { color: p.WARN },
        },
      ],
    };
  }, [data, theme]);

  const ref = useChart(option);
  if (!option) return null;
  return <div ref={ref} className="h-60 w-full" />;
}

function StressTestingCards({ scenarios }: { scenarios?: StressTestScenario[] }) {
  if (!scenarios?.length) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
      {scenarios.map((sc) => {
        const inRange = sc.status === 'in_range';
        return (
          <div
            key={sc.scenario}
            className={`rounded-lg border p-3 text-xs ${
              inRange ? 'border-hair bg-surface' : 'border-hair/60 bg-surface-alt opacity-75'
            }`}
          >
            <div className="flex items-center justify-between gap-1">
              <span className="font-semibold text-ink truncate">{sc.name}</span>
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-3xs font-medium ${
                  inRange ? 'bg-brand-50 text-brand-700' : 'bg-surface-sunken text-ink-muted'
                }`}
              >
                {inRange ? '覆盖区间' : '未覆盖时段'}
              </span>
            </div>
            <div className="mt-1 text-3xs text-ink-muted">{sc.period}</div>

            {inRange ? (
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-2xs border-t border-hair pt-2">
                <div>
                  <div className="text-ink-secondary">组合区间收益</div>
                  <div
                    className={`num font-semibold ${
                      sc.portfolio_return != null ? pctClass(sc.portfolio_return) : 'text-ink'
                    }`}
                  >
                    {sc.portfolio_return != null ? fmtPct(sc.portfolio_return * 100) : '—'}
                  </div>
                </div>
                <div>
                  <div className="text-ink-secondary">基准区间收益</div>
                  <div
                    className={`num font-semibold ${
                      sc.benchmark_return != null ? pctClass(sc.benchmark_return) : 'text-ink'
                    }`}
                  >
                    {sc.benchmark_return != null ? fmtPct(sc.benchmark_return * 100) : '—'}
                  </div>
                </div>
                <div>
                  <div className="text-ink-secondary">组合最大回撤</div>
                  <div className="num font-semibold text-danger">
                    {sc.portfolio_max_dd != null ? fmtPct(sc.portfolio_max_dd * 100) : '—'}
                  </div>
                </div>
                <div>
                  <div className="text-ink-secondary">区间超额表现</div>
                  <div
                    className={`num font-semibold ${
                      sc.excess_return != null ? pctClass(sc.excess_return) : 'text-ink'
                    }`}
                  >
                    {sc.excess_return != null
                      ? `${sc.excess_return >= 0 ? '+' : ''}${fmtPct(sc.excess_return * 100)}`
                      : '—'}
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-3 text-3xs text-ink-muted text-center py-2 bg-surface-sunken/40 rounded">
                当前回测时间跨度未覆盖此黑天鹅时段
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MonteCarloFanChart({
  data,
  summary,
}: {
  data?: MonteCarloFanPoint[];
  summary?: MonteCarloSummary;
}) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.length) return null;
    const p = chartPalette();
    const days = data.map((d) => `T+${d.day}`);
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—'),
      },
      legend: {
        data: ['95% 乐观上限', '75% 上分位', '50% 中位期望', '25% 下分位', '5% 悲观底线'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 16, right: 16, top: 20, bottom: 36, containLabel: true },
      xAxis: {
        type: 'category',
        data: days,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: { color: p.INKM, formatter: (v: number) => v.toFixed(2) },
      },
      series: [
        {
          name: '95% 乐观上限',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 1.2, type: 'dashed', color: p.UP },
          itemStyle: { color: p.UP },
          data: data.map((d) => d.q95),
        },
        {
          name: '75% 上分位',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 1.2, type: 'dashed', color: p.BRAND },
          itemStyle: { color: p.BRAND },
          data: data.map((d) => d.q75),
        },
        {
          name: '50% 中位期望',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          data: data.map((d) => d.q50),
        },
        {
          name: '25% 下分位',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 1.2, type: 'dashed', color: p.WARN },
          itemStyle: { color: p.WARN },
          data: data.map((d) => d.q25),
        },
        {
          name: '5% 悲观底线',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 1.2, type: 'dashed', color: p.DOWN },
          itemStyle: { color: p.DOWN },
          data: data.map((d) => d.q05),
        },
      ],
    };
  }, [data, theme]);

  const ref = useChart(option);
  if (!option) return null;

  return (
    <div className="space-y-3">
      <div ref={ref} className="h-64 w-full" />
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-2 border-t border-hair">
          <div className="rounded-md border border-hair bg-surface-alt px-2.5 py-2 text-center">
            <div className="text-3xs text-ink-secondary">未来 1 年正收益概率</div>
            <div className="num font-semibold text-xs text-brand-600 mt-0.5">
              {fmtPct(summary.prob_positive * 100)}
            </div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt px-2.5 py-2 text-center">
            <div className="text-3xs text-ink-secondary">模拟预期年化收益</div>
            <div className={`num font-semibold text-xs mt-0.5 ${pctClass(summary.expected_return)}`}>
              {fmtPct(summary.expected_return * 100)}
            </div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt px-2.5 py-2 text-center">
            <div className="text-3xs text-ink-secondary">95% 在险价值 (VaR)</div>
            <div className="num font-semibold text-xs text-danger mt-0.5">
              {fmtPct(summary.var_95 * 100)}
            </div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt px-2.5 py-2 text-center">
            <div className="text-3xs text-ink-secondary">95% 条件在险价值 (CVaR)</div>
            <div className="num font-semibold text-xs text-danger mt-0.5">
              {fmtPct(summary.cvar_95 * 100)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TurnoverChart({ data }: { data?: TurnoverRecord[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.length) return null;
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ name: string; seriesName: string; value: number }>;
          const r = data.find((d) => d.date === items[0]?.name);
          if (!r) return '';
          return `
            <div style="font-size:11px;">
              <b>调仓日期: ${r.date}</b><br/>
              单期换手率: <b class="num">${fmtPct(r.turnover_pct * 100)}</b><br/>
              交易金额: <b class="num">¥${fmtNum(r.trade_volume)}</b><br/>
              佣金: <b class="num">¥${r.commission.toFixed(2)}</b> | 
              印花税: <b class="num">¥${r.stamp_duty.toFixed(2)}</b> | 
              滑点损耗: <b class="num">¥${r.slippage.toFixed(2)}</b>
            </div>
          `;
        },
      },
      legend: {
        data: ['单期换手率', '调仓交易额'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 16, right: 36, top: 20, bottom: 32, containLabel: true },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date),
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: [
        {
          type: 'value',
          name: '换手率',
          nameTextStyle: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: { color: p.INKM, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        },
        {
          type: 'value',
          name: '交易额 (元)',
          nameTextStyle: { fontSize: 9, color: p.INKM },
          splitLine: { show: false },
          axisLabel: { color: p.INKM, formatter: (v: number) => fmtNum(v) },
        },
      ],
      series: [
        {
          name: '单期换手率',
          type: 'bar',
          data: data.map((d) => d.turnover_pct),
          itemStyle: { color: p.BRAND },
        },
        {
          name: '调仓交易额',
          type: 'line',
          yAxisIndex: 1,
          showSymbol: false,
          smooth: true,
          data: data.map((d) => d.trade_volume),
          lineStyle: { width: 1.5, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
      ],
    };
  }, [data, theme]);

  const ref = useChart(option);
  if (!option) return null;
  return <div ref={ref} className="h-64 w-full" />;
}

function RebalanceAuditTable({
  logs,
}: {
  logs?: Array<{
    date: string;
    weighting: string;
    fallback: boolean;
    weights: Record<string, number>;
  }>;
}) {
  if (!logs?.length) {
    return (
      <div className="py-6 text-center text-xs text-ink-muted">
        给定权重策略下无需每期动态重新求解目标权重
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-2xs">
        <thead>
          <tr className="border-b border-hair text-ink-secondary">
            <th className="py-1 text-left font-medium">调仓日</th>
            <th className="py-1 text-left font-medium">优化方案</th>
            <th className="py-1 text-left font-medium">状态</th>
            <th className="py-1 text-left font-medium">求解权重结果</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.date} className="border-b border-hair">
              <td className="py-1 font-mono text-ink">{log.date}</td>
              <td className="py-1 font-medium">{log.weighting}</td>
              <td className="py-1">
                {log.fallback ? (
                  <span className="rounded bg-warn-bg px-1 py-0.5 text-warn">
                    历史不足 · 回退等权
                  </span>
                ) : (
                  <span className="rounded bg-success-bg px-1 py-0.5 text-success">求解成功</span>
                )}
              </td>
              <td className="py-1 font-mono text-ink-muted">
                {Object.entries(log.weights)
                  .map(([c, w]) => `${c}: ${(w * 100).toFixed(1)}%`)
                  .join(' | ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ==================== 主页面组件 ==================== */

export default function PortfolioBacktest() {
  // 投研联动闭环：从因子工作室 / 因子库导入的资产
  const [importedFactorInfo, setImportedFactorInfo] = useState<{
    source: string;
    expr: string;
  } | null>(null);

  const [activeTab, setActiveTab] = useState<PortfolioTabKey>('overview');

  const [assets, setAssets] = useState<PortfolioAsset[]>(() => {
    try {
      const studioImportRaw = sessionStorage.getItem('aqp:portfolio:import_assets');
      if (studioImportRaw) {
        const parsed = JSON.parse(studioImportRaw) as {
          source?: string;
          expr?: string;
          assets?: PortfolioAsset[];
        };
        if (Array.isArray(parsed.assets) && parsed.assets.length) {
          return parsed.assets;
        }
      }
    } catch {
      /* 忽略脏数据 */
    }

    try {
      const raw = localStorage.getItem('AQP_PORTFOLIO_IMPORT');
      if (raw) {
        const parsed = JSON.parse(raw) as PortfolioAsset[];
        if (Array.isArray(parsed) && parsed.length) return parsed;
      }
    } catch {
      /* 忽略脏数据 */
    }
    return DEFAULT_ASSETS;
  });

  useEffect(() => {
    try {
      const studioImportRaw = sessionStorage.getItem('aqp:portfolio:import_assets');
      if (studioImportRaw) {
        const parsed = JSON.parse(studioImportRaw) as { source?: string; expr?: string };
        if (parsed.source || parsed.expr) {
          setImportedFactorInfo({
            source: parsed.source || '因子研究室',
            expr: parsed.expr || '',
          });
        }
      }
    } catch {
      /* 忽略 */
    }
    localStorage.removeItem('AQP_PORTFOLIO_IMPORT');
  }, []);

  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<AssetSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [startDate, setStartDate] = useState(dayjs().subtract(2, 'year').format('YYYY-MM-DD'));
  const [endDate, setEndDate] = useState(dayjs().format('YYYY-MM-DD'));
  const [initialCash, setInitialCash] = useState(1_000_000);
  const [rebalance, setRebalance] = useState<RebalanceFreq>('M');
  const [benchmark, setBenchmark] = useState('000300');
  const [strategy, setStrategy] = useState<PortfolioWeighting>(STRATEGY_OPTIONS[0].key);
  const [result, setResult] = useState<PortfolioBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const btTask = useAbortableTask();
  const role = useAuthStore((s) => s.user?.role);
  const canRun = hasMinimumRole(role, 'researcher');

  const totalWeight = useMemo(() => assets.reduce((s, a) => s + a.weight, 0), [assets]);
  const weightOk = Math.abs(totalWeight - 1) < 0.001;
  const dateOk = dayjs(endDate).isAfter(dayjs(startDate));
  const benchDegenerate =
    result != null &&
    result.nav_curve.length > 0 &&
    (!Number.isFinite(result.nav_curve[0].benchmark) || result.nav_curve[0].benchmark === 0);

  const ddNow = result?.drawdown_curve?.length
    ? result.drawdown_curve[result.drawdown_curve.length - 1].drawdown
    : null;
  const ddNowText = ddNow != null && Number.isFinite(ddNow) ? fmtPct(ddNow * 100) : '—';
  const ddNowTone = ddNow != null && ddNow < 0 ? 't-down' : undefined;

  const searchSeqRef = useRef(0);
  const [searchError, setSearchError] = useState<string | null>(null);
  useEffect(() => {
    if (!query.trim()) {
      searchSeqRef.current += 1;
      setSearchResults([]);
      setSearchError(null);
      setSearching(false);
      return;
    }
    const seq = ++searchSeqRef.current;
    setSearching(true);
    setSearchError(null);
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      portfolioApi
        .search(query.trim(), ctrl.signal)
        .then((r) => {
          if (seq !== searchSeqRef.current) return;
          setSearchResults(r);
        })
        .catch((e) => {
          if (seq !== searchSeqRef.current) return;
          setSearchResults([]);
          setSearchError(e instanceof ApiError ? e.message : '搜索失败');
        })
        .finally(() => {
          if (seq === searchSeqRef.current) setSearching(false);
        });
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query]);

  const addAsset = (item: AssetSearchItem) => {
    if (assets.some((a) => a.code === item.code && a.type === item.type)) return;
    const weight = assets.length === 0 ? 1 : 0;
    setAssets((cur) => [...cur, { code: item.code, name: item.name, type: item.type, weight }]);
    setQuery('');
    setSearchResults([]);
  };

  const removeAsset = (idx: number) => {
    setAssets((cur) => cur.filter((_, i) => i !== idx));
  };

  const updateWeight = (idx: number, w: number) => {
    setAssets((cur) =>
      cur.map((a, i) => (i === idx ? { ...a, weight: Math.max(0, Math.min(1, w)) } : a)),
    );
  };

  const equalize = () => {
    if (assets.length === 0) return;
    const w = 1 / assets.length;
    setAssets((cur) => cur.map((a) => ({ ...a, weight: Number(w.toFixed(4)) })));
  };

  const run = useCallback(async () => {
    if (!weightOk || !dateOk) return;
    const ctrl = btTask.begin();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await portfolioApi.backtest(
        {
          assets: assets.map((a) => ({ code: a.code, type: a.type, weight: a.weight })),
          start_date: startDate,
          end_date: endDate,
          rebalance,
          benchmark,
          initial_cash: initialCash,
          weighting: strategy,
          cov_window: 60,
        },
        { signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      setResult(res);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(e instanceof ApiError ? e.message : '回测失败');
    } finally {
      if (btTask.finish(ctrl)) setLoading(false);
    }
  }, [
    assets,
    benchmark,
    dateOk,
    endDate,
    initialCash,
    rebalance,
    startDate,
    strategy,
    weightOk,
    btTask,
  ]);

  return (
    <div className="space-y-3">
      <PageHeader title="组合回测" sub={<ResearchDisclaimer kind="backtest" />} />

      {importedFactorInfo && (
        <div className="flex items-center justify-between rounded-lg border border-brand-500/30 bg-brand-50/20 px-3.5 py-2.5 text-xs">
          <div className="flex items-center gap-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-500 text-3xs font-bold text-white">
              🎯
            </span>
            <div>
              <span className="font-semibold text-ink">投研联动闭环：</span>
              <span className="text-ink-secondary">
                已自动从【{importedFactorInfo.source}】导入由 Alpha 因子筛选的多头资产池（{assets.length} 只标的，已均衡分配权重）
              </span>
              {importedFactorInfo.expr && (
                <span className="ml-2 rounded border border-hair bg-surface px-1.5 py-0.5 font-mono text-3xs text-brand-600">
                  {importedFactorInfo.expr}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => {
              sessionStorage.removeItem('aqp:portfolio:import_assets');
              setImportedFactorInfo(null);
            }}
            className="shrink-0 rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary hover:bg-surface-alt"
          >
            关闭提示
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        {/* 左侧：参数配置 */}
        <div className="xl:col-span-3 space-y-3">
          <SectionCard title="组合配置 & 回测参数">
            <div className="space-y-3">
              {/* 资产搜索 */}
              <div>
                <label className="text-2xs font-medium text-ink-secondary">组合资产</label>
                <div className="relative mt-1">
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="搜索代码 / 名称"
                    className="w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs outline-none focus:border-brand-300"
                  />
                  {searching && <span className="absolute right-2 top-1.5 text-2xs text-ink-muted">…</span>}
                  {searchResults.length > 0 && (
                    <div className="absolute z-10 mt-0.5 max-h-40 w-full overflow-auto rounded-md border border-hair bg-surface shadow-sm">
                      {searchResults.map((item) => (
                        <button
                          key={`${item.type}-${item.code}`}
                          onClick={() => addAsset(item)}
                          className="flex w-full items-center justify-between px-2.5 py-1.5 text-left text-xs hover:bg-surface-alt"
                        >
                          <span className="truncate">
                            {item.name}（{item.code}）
                          </span>
                          <span className="shrink-0 rounded bg-surface-sunken px-1 py-0.5 text-2xs text-ink-muted">
                            {item.type === 'etf' ? 'ETF' : '股票'}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                  {searchError && (
                    <div className="absolute z-10 mt-0.5 w-full rounded-md border border-warn/30 bg-warn-bg px-2.5 py-1.5 text-2xs text-warn">
                      搜索失败：{searchError}
                    </div>
                  )}
                </div>

                {/* 资产列表 */}
                <div className="mt-2 space-y-1.5">
                  {assets.map((a, idx) => (
                    <div
                      key={`${a.type}-${a.code}`}
                      className="flex items-center gap-2 rounded-md border border-hair bg-surface-alt px-2 py-1.5"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-ink">{a.name || a.code}</div>
                        <div className="text-2xs text-ink-muted">
                          {a.code} · {a.type === 'etf' ? 'ETF' : '股票'}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <input
                          type="number"
                          min={0}
                          max={1}
                          step={0.01}
                          value={a.weight}
                          onChange={(e) => updateWeight(idx, Number(e.target.value))}
                          className="w-14 rounded border border-hair px-1 py-0.5 text-right text-xs outline-none focus:border-brand-300"
                        />
                        <span className="text-2xs text-ink-muted">%</span>
                      </div>
                      <button
                        onClick={() => removeAsset(idx)}
                        className="rounded p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>

                <div className="mt-2 flex items-center justify-between">
                  <button onClick={equalize} className="text-xs text-brand-600 hover:underline">
                    一键均分权重
                  </button>
                  <span className={`num text-2xs ${weightOk ? 'text-ink-secondary' : 't-up'}`}>
                    当前权重和 {fmtPct(totalWeight * 100)} {weightOk ? '' : '（需等于 100%）'}
                  </span>
                </div>
              </div>

              <hr className="border-hair" />

              {/* 时间范围 */}
              <div className="grid grid-cols-2 gap-2">
                <label className="text-2xs text-ink-secondary">
                  开始日期
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                  />
                </label>
                <label className="text-2xs text-ink-secondary">
                  结束日期
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                  />
                </label>
              </div>
              {!dateOk && <p className="text-2xs t-up">结束日期必须晚于开始日期</p>}

              {/* 初始资金 */}
              <label className="text-2xs text-ink-secondary">
                初始资金
                <input
                  type="number"
                  min={10000}
                  step={10000}
                  value={initialCash}
                  onChange={(e) => setInitialCash(Number(e.target.value))}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                />
              </label>

              {/* 策略类型 */}
              <label className="text-2xs text-ink-secondary">
                策略方案
                <select
                  value={strategy}
                  onChange={(e) => setStrategy(e.target.value as PortfolioWeighting)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                >
                  {STRATEGY_OPTIONS.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>

              {/* 调仓频率 */}
              <label className="text-2xs text-ink-secondary">
                调仓频率
                <select
                  value={rebalance}
                  onChange={(e) => setRebalance(e.target.value as RebalanceFreq)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                >
                  {REBALANCE_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              {/* 基准 */}
              <label className="text-2xs text-ink-secondary">
                基准指数
                <select
                  value={benchmark}
                  onChange={(e) => setBenchmark(e.target.value)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300"
                >
                  {BENCHMARK_OPTIONS.map((b) => (
                    <option key={b.key} value={b.key}>
                      {b.label}
                    </option>
                  ))}
                </select>
              </label>

              <button
                onClick={() => void run()}
                disabled={!canRun || loading || !weightOk || !dateOk}
                className="mt-2 w-full rounded-md bg-brand-500 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
              >
                {loading ? '回测中…' : '开始回测'}
              </button>
              {!canRun && (
                <p className="mt-1 text-2xs text-ink-muted">
                  组合回测需要 researcher 及以上角色。
                </p>
              )}
            </div>
          </SectionCard>
        </div>

        {/* 右侧：主体分析工作台 (Tab 体系) */}
        <div className="xl:col-span-9 space-y-3">
          {/* 折叠式投资组合凸优化求解器 */}
          <PortfolioOptimizerPanel
            currentAssets={assets}
            onApplyWeights={(newAssets) => setAssets(newAssets)}
            initialCollapsed={true}
          />

          {error && (
            <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
              {error}
            </div>
          )}
          {result && (
            <div className="rounded-md border border-brand-100 bg-brand-50 px-3 py-2 text-xs text-brand-700">
              回测完成。复权口径：{portfolioBasisLabel(result)}。
            </div>
          )}
          {result && portfolioBasisWarn(result) && (
            <div
              role="alert"
              className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn"
            >
              {result.price_basis?.note ??
                '复权口径未知：部分标的未经平台数据源加载，结果口径无法确认。'}
            </div>
          )}
          {result && benchDegenerate && (
            <div
              role="alert"
              className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn"
            >
              基准序列不可用（首值为 0）⇒ 基准净值线不显示，且 Beta / Alpha 的 1.00 / 0.00
              是退化值、不可用于解释收益。
            </div>
          )}

          {!result && !loading && !error && (
            <EmptyState title="配置资产后点击「开始回测」" hint="权重和需等于 100%" />
          )}
          {loading && <LoadingState text="正在计算组合回测…" />}

          {result && (
            <div className="space-y-3">
              {/* 3-Tab 导航切换栏 */}
              <div className="flex border-b border-hair">
                <button
                  type="button"
                  onClick={() => setActiveTab('overview')}
                  className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors ${
                    activeTab === 'overview'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  业绩与超额概览 (Overview & Alpha)
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('risk')}
                  className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors ${
                    activeTab === 'risk'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  风险预算与压力测试 (Risk & Stress)
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('trades')}
                  className={`px-4 py-2 text-xs font-semibold border-b-2 transition-colors ${
                    activeTab === 'trades'
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-ink-secondary hover:text-ink'
                  }`}
                >
                  调仓流水与摩擦损耗 (Trades & Friction)
                </button>
              </div>

              {/* TAB 1: 业绩与超额概览 */}
              {activeTab === 'overview' && (
                <div className="space-y-3">
                  {/* 核心机构 KPI 矩阵 */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
                    <KpiCard
                      label="组合年化 (CAGR)"
                      value={
                        result.metrics.cagr != null && Number.isFinite(result.metrics.cagr)
                          ? fmtPct(result.metrics.cagr * 100)
                          : '—'
                      }
                      tone={result.metrics.cagr != null ? pctClass(result.metrics.cagr) : undefined}
                      sub={`总收益 ${fmtPct(result.metrics.total_return * 100)}`}
                    />
                    <KpiCard
                      label="基准年化 (Benchmark)"
                      value={
                        result.metrics.bm_cagr != null && Number.isFinite(result.metrics.bm_cagr)
                          ? fmtPct(result.metrics.bm_cagr * 100)
                          : '—'
                      }
                      tone={result.metrics.bm_cagr != null ? pctClass(result.metrics.bm_cagr) : undefined}
                      sub={BENCHMARK_OPTIONS.find((b) => b.key === result.benchmark)?.label}
                    />
                    <KpiCard
                      label="年化超额 (Excess CAGR)"
                      value={
                        result.metrics.excess_cagr != null && Number.isFinite(result.metrics.excess_cagr)
                          ? `${result.metrics.excess_cagr >= 0 ? '+' : ''}${fmtPct(result.metrics.excess_cagr * 100)}`
                          : '—'
                      }
                      tone={
                        result.metrics.excess_cagr != null ? pctClass(result.metrics.excess_cagr) : undefined
                      }
                      sub={`累计超额 ${fmtPct((result.metrics.excess_total_return ?? 0) * 100)}`}
                    />
                    <KpiCard
                      label="信息比率 (IR)"
                      value={
                        result.metrics.information_ratio != null
                          ? result.metrics.information_ratio.toFixed(2)
                          : '—'
                      }
                      tone={
                        result.metrics.information_ratio != null &&
                        result.metrics.information_ratio >= 0.5
                          ? 't-up'
                          : undefined
                      }
                      sub="超额风险调整收益"
                    />
                    <KpiCard
                      label="跟踪误差 (TE)"
                      value={
                        result.metrics.tracking_error != null
                          ? fmtPct(result.metrics.tracking_error * 100)
                          : '—'
                      }
                      sub="主动风险波动"
                    />
                    <KpiCard
                      label="超额胜率 (Win Rate)"
                      value={
                        result.metrics.win_rate != null
                          ? fmtPct(result.metrics.win_rate * 100)
                          : '—'
                      }
                      tone={
                        result.metrics.win_rate != null && result.metrics.win_rate >= 0.5
                          ? 't-up'
                          : undefined
                      }
                      sub="日频跑赢基准占比"
                    />
                    <KpiCard
                      label="组合最大回撤"
                      value={
                        result.metrics.max_drawdown != null && Number.isFinite(result.metrics.max_drawdown)
                          ? fmtPct(result.metrics.max_drawdown * 100)
                          : '—'
                      }
                      tone="t-down"
                      sub="全区间最深跌幅"
                    />
                    <KpiCard
                      label="超额最大回撤"
                      value={
                        result.metrics.active_max_drawdown != null
                          ? fmtPct(result.metrics.active_max_drawdown * 100)
                          : '—'
                      }
                      tone="t-down"
                      sub="相对基准走势最深回撤"
                    />
                    <KpiCard
                      label="夏普比率 (Sharpe)"
                      value={fmtNum(result.metrics.sharpe)}
                      sub={`Rf=${fmtPct(result.metrics.risk_free * 100)}`}
                    />
                    <KpiCard
                      label="当前回撤"
                      value={ddNowText}
                      tone={ddNowTone}
                      sub="决策时点距离峰值"
                    />
                  </div>

                  {/* 净值走势曲线 */}
                  <SectionCard title="净值时序走势（组合 vs 基准）" bodyClassName="p-2">
                    <NetValueChart data={result.nav_curve} />
                  </SectionCard>

                  {/* 累计超额收益时序走势 */}
                  {result.active_return_curve && result.active_return_curve.length > 0 && (
                    <SectionCard title="累计超额收益走势 (Active Alpha Curve)" bodyClassName="p-2">
                      <ActiveReturnChart data={result.active_return_curve} />
                    </SectionCard>
                  )}

                  {/* 双分栏：水下回撤曲线 + 年度收益分布 */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <SectionCard title="回撤曲线（水下曲线）" bodyClassName="p-2">
                      <DrawdownChart data={result.drawdown_curve} />
                    </SectionCard>
                    <SectionCard title="年度收益分布" bodyClassName="p-2">
                      <AnnualReturnsChart data={result.annual_returns} />
                    </SectionCard>
                  </div>
                </div>
              )}

              {/* TAB 2: 风险预算与压力测试 */}
              {activeTab === 'risk' && (
                <div className="space-y-3">
                  {/* 历史黑天鹅极端情景压力测试看板 */}
                  <SectionCard
                    title="历史黑天鹅极端情景压力测试 (Stress Testing)"
                    bodyClassName="p-3"
                  >
                    <div className="mb-2.5 text-2xs text-ink-muted">
                      对比组合在 A 股历史重大危机事件中的承压与回撤防御能力
                    </div>
                    <StressTestingCards scenarios={result.stress_tests} />
                  </SectionCard>

                  {/* 传统与非对称风险指标矩阵 */}
                  <SectionCard title="综合风险度量与非对称尾部特征" bodyClassName="p-3">
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">年化波动率</div>
                        <div className="num font-semibold text-sm mt-0.5 text-ink">
                          {fmtPct(result.metrics.volatility * 100)}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">贝塔系数 (Beta)</div>
                        <div className="num font-semibold text-sm mt-0.5 text-ink">
                          {benchDegenerate ? '—' : fmtNum(result.metrics.beta)}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">阿尔法 (Alpha 年化)</div>
                        <div
                          className={`num font-semibold text-sm mt-0.5 ${
                            !benchDegenerate && result.metrics.alpha != null
                              ? result.metrics.alpha >= 0
                                ? 't-up'
                                : 't-down'
                              : 'text-ink'
                          }`}
                        >
                          {benchDegenerate ? '—' : fmtNum(result.metrics.alpha)}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">卡玛比率 (Calmar)</div>
                        <div className="num font-semibold text-sm mt-0.5 text-ink">
                          {result.metrics.calmar != null ? result.metrics.calmar.toFixed(2) : '—'}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">索提诺比率 (Sortino)</div>
                        <div className="num font-semibold text-sm mt-0.5 text-brand-600">
                          {result.metrics.sortino != null ? result.metrics.sortino.toFixed(2) : '—'}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">Omega 胜负比</div>
                        <div className="num font-semibold text-sm mt-0.5 text-brand-600">
                          {result.metrics.omega != null ? result.metrics.omega.toFixed(2) : '—'}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">95% 条件在险 (CVaR)</div>
                        <div className="num font-semibold text-sm mt-0.5 text-danger">
                          {result.metrics.cvar_95 != null
                            ? fmtPct(result.metrics.cvar_95 * 100)
                            : '—'}
                        </div>
                      </div>
                      <div className="rounded-md border border-hair bg-surface-alt p-2.5 text-center">
                        <div className="text-3xs text-ink-secondary">尾部风险比率 (Tail Risk Ratio)</div>
                        <div className="num font-semibold text-sm mt-0.5 text-ink">
                          {result.metrics.tail_risk_ratio != null
                            ? result.metrics.tail_risk_ratio.toFixed(2)
                            : '—'}
                        </div>
                      </div>
                    </div>
                  </SectionCard>

                  {/* 欧拉齐次风险分解 */}
                  {result.risk_budgeting && result.risk_budgeting.length > 0 && (
                    <SectionCard
                      title="风险预算与欧拉齐次风险分解 (%RC 剪刀差分析)"
                      bodyClassName="p-3 space-y-3"
                    >
                      <div className="text-2xs text-ink-muted">
                        名义资产投资权重 vs 实际协方差风险贡献占比
                      </div>
                      <RiskBudgetingChart data={result.risk_budgeting} />
                      <div className="overflow-x-auto">
                        <table className="w-full text-2xs">
                          <thead>
                            <tr className="border-b border-hair text-ink-secondary">
                              <th className="py-1 text-left font-medium">标的代码</th>
                              <th className="py-1 text-right font-medium">名义权重</th>
                              <th className="py-1 text-right font-medium">边际风险 (MRC)</th>
                              <th className="py-1 text-right font-medium">风险贡献 (%RC)</th>
                              <th className="py-1 text-right font-medium">风险偏离度 (剪刀差)</th>
                            </tr>
                          </thead>
                          <tbody>
                            {result.risk_budgeting.map((rb) => {
                              const rcVal =
                                rb.rc_pct != null
                                  ? rb.rc_pct
                                  : (rb.pct_risk_contribution ?? 0) / 100;
                              const diff = rcVal - rb.weight;
                              return (
                                <tr key={rb.code} className="border-b border-hair">
                                  <td className="py-1 font-mono font-medium">{rb.code}</td>
                                  <td className="num py-1 text-right">{fmtPct(rb.weight * 100)}</td>
                                  <td className="num py-1 text-right">{rb.mrc ? rb.mrc.toFixed(4) : '—'}</td>
                                  <td className="num py-1 text-right font-semibold text-warn">
                                    {fmtPct(rcVal * 100)}
                                  </td>
                                  <td
                                    className={`num py-1 text-right font-medium ${
                                      Math.abs(diff) < 0.02
                                        ? 'text-ink-muted'
                                        : diff > 0
                                        ? 'text-danger'
                                        : 'text-success'
                                    }`}
                                  >
                                    {diff > 0 ? '+' : ''}
                                    {fmtPct(diff * 100)}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      <div className="text-3xs text-ink-muted">
                        欧拉齐次分解定理：Σ(w_i · MRC_i) = σ_p。若名义权重与 %RC 存在显著剪刀差，表明低波动或高相关资产正隐蔽集中风险。
                      </div>
                    </SectionCard>
                  )}

                  {/* 蒙特卡洛置信扇形图 */}
                  {result.monte_carlo && result.monte_carlo.fan && result.monte_carlo.fan.length > 0 && (
                    <SectionCard
                      title="蒙特卡洛随机漫步预测扇形图 (Monte Carlo Bootstrap Fan Chart)"
                      bodyClassName="p-3"
                    >
                      <div className="mb-2.5 text-2xs text-ink-muted">
                        基于历史日收益率随机 Bootstrap 采样 300 条财富路径，预测未来 252 交易日收益置信区间
                      </div>
                      <MonteCarloFanChart
                        data={result.monte_carlo.fan}
                        summary={result.monte_carlo.summary}
                      />
                    </SectionCard>
                  )}
                </div>
              )}

              {/* TAB 3: 调仓流水与摩擦损耗 */}
              {activeTab === 'trades' && (
                <div className="space-y-3">
                  {/* 摩擦损耗归因指标卡 */}
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                    <KpiCard
                      label="年化换手率"
                      value={
                        result.metrics.annualized_turnover != null
                          ? fmtPct(result.metrics.annualized_turnover * 100)
                          : '—'
                      }
                      sub="单边调仓换手率年化"
                    />
                    <KpiCard
                      label="摩擦年化拖累 (Drag)"
                      value={
                        result.metrics.friction_drag_bps != null
                          ? `${result.metrics.friction_drag_bps.toFixed(1)} bps`
                          : '—'
                      }
                      tone="t-down"
                      sub="年化交易损耗基点"
                    />
                    <KpiCard
                      label="累计佣金"
                      value={`¥${fmtNum(result.friction_costs?.commission ?? 0)}`}
                      sub="万2.5双边 / 最低5元"
                    />
                    <KpiCard
                      label="累计印花税"
                      value={`¥${fmtNum(result.friction_costs?.stamp_duty ?? 0)}`}
                      sub="万5单边 (仅股票)"
                    />
                    <KpiCard
                      label="累计滑点成本"
                      value={`¥${fmtNum(result.friction_costs?.slippage ?? 0)}`}
                      sub="5 bps 滑点预估"
                    />
                  </div>

                  {/* 单期调仓换手率柱状图 */}
                  {result.turnover_series && result.turnover_series.length > 0 && (
                    <SectionCard
                      title="单期调仓换手率与交易额时序分布"
                      bodyClassName="p-2"
                    >
                      <div className="px-2 pt-1 pb-2 text-2xs text-ink-muted">
                        展现各调仓执行日的换手强度与双边成交量
                      </div>
                      <TurnoverChart data={result.turnover_series} />
                    </SectionCard>
                  )}

                  {/* 资产持仓变动图 */}
                  <SectionCard
                    title="持仓权重漂移时序堆叠图"
                    bodyClassName="p-2"
                  >
                    <div className="px-2 pt-1 pb-2 text-2xs text-ink-muted">
                      反映再平衡前后各标的市值占总资产权重的动态漂移过程
                    </div>
                    <HoldingsDriftChart
                      data={result.holdings_drift}
                      codes={result.assets.map((a) => a.code)}
                    />
                  </SectionCard>

                  {/* 逐期调仓执行审计日志 */}
                  <SectionCard
                    title="调仓决策审计日志 (Rebalance Decision Audit)"
                    bodyClassName="p-3"
                  >
                    <div className="mb-2 text-2xs text-ink-muted">
                      记录历次再平衡时目标权重计算决策与回退事件
                    </div>
                    <RebalanceAuditTable logs={result.rebalance_log} />
                  </SectionCard>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
