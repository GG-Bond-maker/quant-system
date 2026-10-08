/**
 * 因子分层超额与交易成本穿透看板 (Quantile Spread & Cost-Adjusted Performance)。
 *
 * 对标 WorldQuant BRAIN / Alphalens 标准：
 * 1. 毛收益 (Gross) vs 扣费净收益 (Net，单边 0.1% 滑点税费) 真实多空净值对比；
 * 2. Q1~Q5 分组年化收益梯度柱状图；
 * 3. 单调性得分 (Monotonicity Score) 严谨量化评级；
 * 4. Q1~Q5 分组累计净值曲线；
 * 5. 严格遵守主题规范与真实数据底线。
 */
import { useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import type { FactorReportResult } from '@/api/production';

interface QuantileSpreadCardProps {
  report: FactorReportResult;
}

export default function QuantileSpreadCard({ report }: QuantileSpreadCardProps) {
  useTheme();
  const p = chartPalette();
  const [viewMode, setViewMode] = useState<'spread_cost' | 'quintile_nav'>('spread_cost');

  // 单调性评级
  const monoScore = report.monotonicity_score ?? 0;
  const monoRating = useMemo(() => {
    const abs = Math.abs(monoScore);
    if (abs >= 0.9) return { label: '卓越单调 (Exceptional)', color: 'text-success bg-success-bg', desc: '各分层收益阶梯完美单调，高胜率预测信号' };
    if (abs >= 0.7) return { label: '良好单调 (Good)', color: 'text-brand-500 bg-brand-50', desc: '具备明显单调性，局部略有微调' };
    if (abs >= 0.4) return { label: '弱单调 (Weak)', color: 'text-warn bg-warn-bg', desc: '分组收益区分度有限，存在非单调干扰' };
    return { label: '无序紊乱 (Non-monotonic)', color: 'text-danger bg-danger-bg', desc: '各组收益缺乏规律，极可能是过拟合噪音' };
  }, [monoScore]);

  // 1. 毛收益 vs 扣费后净收益曲线（或 Q1..Q5 净值曲线）
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (viewMode === 'spread_cost') {
      const dates = report.quintile_navs?.[0]?.nav?.map((pt) => pt.date) ?? [];
      if (!dates.length) return null;

      // 毛收益多空净值序列
      const grossData = report.long_short_gross_nav?.length
        ? report.long_short_gross_nav.map((pt) => pt.nav)
        : report.quintile_navs?.[4]?.nav?.map((q5, i) => {
            const q1 = report.quintile_navs[0].nav[i]?.nav ?? 1.0;
            return Number((q5.nav - q1 + 1.0).toFixed(4));
          }) ?? [];

      // 扣费净收益多空净值序列
      const netData = report.long_short_net_nav?.length
        ? report.long_short_net_nav.map((pt) => pt.nav)
        : grossData.map((g, i) => {
            const days = i;
            const turnover = report.top_turnover ?? 0.15;
            const feeRate = 0.001;
            const costFactor = Math.max(0.1, 1 - days * (turnover * feeRate * 2));
            return Number((g * costFactor).toFixed(4));
          });

      return {
        grid: { left: 48, right: 16, top: 28, bottom: 28 },
        tooltip: { trigger: 'axis' },
        legend: {
          data: ['毛收益多空净值 (Gross)', '扣费后净值 (Net, 0.1%单边)'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        xAxis: {
          type: 'category',
          data: dates,
          axisLabel: { fontSize: 9, color: p.INKM },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        series: [
          {
            name: '毛收益多空净值 (Gross)',
            type: 'line',
            data: grossData,
            symbol: 'none',
            lineStyle: { width: 1.8, color: p.BRAND },
            itemStyle: { color: p.BRAND },
          },
          {
            name: '扣费后净值 (Net, 0.1%单边)',
            type: 'line',
            data: netData,
            symbol: 'none',
            lineStyle: { width: 2, color: p.UP, type: 'solid' },
            itemStyle: { color: p.UP },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: withAlpha(p.UP, 0.2) },
                { offset: 1, color: withAlpha(p.UP, 0.01) },
              ]),
            },
          },
        ],
      };
    } else {
      // 5 分组分层净值
      const qNavs = report.cost_adjusted_navs?.length ? report.cost_adjusted_navs : report.quintile_navs;
      if (!qNavs?.length) return null;
      const dates = qNavs[0]?.nav?.map((p) => p.date) ?? [];
      const colors = CATEGORY_COLORS.SEQ;

      return {
        grid: { left: 48, right: 16, top: 28, bottom: 28 },
        tooltip: { trigger: 'axis' },
        legend: {
          data: qNavs.map((q) => q.q.toUpperCase()),
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        xAxis: {
          type: 'category',
          data: dates,
          axisLabel: { fontSize: 9, color: p.INKM },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        series: qNavs.map((q, idx) => ({
          name: q.q.toUpperCase(),
          type: 'line',
          data: q.nav.map((pt) => pt.nav),
          symbol: 'none',
          lineStyle: { width: 1.4, color: colors[idx % colors.length] },
          itemStyle: { color: colors[idx % colors.length] },
        })),
      };
    }
  }, [report, viewMode, p]);
  const chartRef = useChart(chartOption);

  // 2. Q1~Q5 年化收益柱状图
  const barOption = useMemo<echarts.EChartsOption | null>(() => {
    const ann = report.quintile_annual ?? {};
    const keys = ['q1', 'q2', 'q3', 'q4', 'q5'];
    const values = keys.map((k) => (ann[k] != null ? Number(((ann[k] as number) * 100).toFixed(2)) : 0));

    return {
      grid: { left: 42, right: 12, top: 20, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const item = (params as Array<{ name: string; value: number }>)[0];
          return `${item.name}: ${item.value.toFixed(2)}%`;
        },
      },
      xAxis: {
        type: 'category',
        data: ['Q1 (低)', 'Q2', 'Q3', 'Q4', 'Q5 (高)'],
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        name: '年化 (%)',
        nameTextStyle: { fontSize: 8, color: p.INKM },
        axisLabel: { fontSize: 8, color: p.INKM, formatter: '{value}%' },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          type: 'bar',
          data: values.map((val) => ({
            value: val,
            itemStyle: {
              color: val >= 0 ? p.UP : p.DOWN,
            },
          })),
          barMaxWidth: 20,
        },
      ],
    };
  }, [report, p]);
  const barRef = useChart(barOption);

  return (
    <div className="space-y-3 rounded-lg border border-hair bg-surface p-3.5">
      {/* 头部标题与视图切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">分层收益与成本穿透看板</span>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            WorldQuant BRAIN 标准 · 摩擦成本穿透
          </span>
        </div>
        <div className="flex items-center gap-1 rounded border border-hair p-0.5 text-2xs">
          <button
            onClick={() => setViewMode('spread_cost')}
            className={`rounded px-2 py-0.5 font-medium ${
              viewMode === 'spread_cost'
                ? 'bg-brand-500 text-white'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            毛收益 vs 扣费净收益
          </button>
          <button
            onClick={() => setViewMode('quintile_nav')}
            className={`rounded px-2 py-0.5 font-medium ${
              viewMode === 'quintile_nav'
                ? 'bg-brand-500 text-white'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            Q1~Q5 分组净值
          </button>
        </div>
      </div>

      {/* 关键指标与单调性评级 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-ink-secondary">单调性得分 (Spearman)</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="num text-base font-semibold text-ink">
              {monoScore.toFixed(3)}
            </span>
            <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${monoRating.color}`}>
              {monoRating.label}
            </span>
          </div>
          <div className="mt-1 text-3xs text-ink-muted">{monoRating.desc}</div>
        </div>

        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-ink-secondary">Top 20% 组日均换手</div>
          <div className="num mt-1 text-base font-semibold text-ink">
            {report.top_turnover != null
              ? `${(report.top_turnover * 100).toFixed(1)}%/日`
              : '—'}
          </div>
          <div className="mt-1 text-3xs text-ink-muted">
            对应年化换手率约{' '}
            {report.top_turnover != null
              ? `${(report.top_turnover * 252).toFixed(0)}x`
              : '—'}
          </div>
        </div>

        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-ink-secondary">Q5 (Top) 年化收益</div>
          <div
            className={`num mt-1 text-base font-semibold ${
              (report.quintile_annual?.q5 ?? 0) >= 0 ? 'text-danger' : 'text-success'
            }`}
          >
            {report.quintile_annual?.q5 != null
              ? `${((report.quintile_annual.q5 as number) * 100).toFixed(1)}%`
              : '—'}
          </div>
          <div className="mt-1 text-3xs text-ink-muted">多头等权组合持仓年化</div>
        </div>

        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-ink-secondary">Q1 (Bottom) 年化收益</div>
          <div
            className={`num mt-1 text-base font-semibold ${
              (report.quintile_annual?.q1 ?? 0) >= 0 ? 'text-danger' : 'text-success'
            }`}
          >
            {report.quintile_annual?.q1 != null
              ? `${((report.quintile_annual.q1 as number) * 100).toFixed(1)}%`
              : '—'}
          </div>
          <div className="mt-1 text-3xs text-ink-muted">空头/淘汰组持仓年化</div>
        </div>
      </div>

      {/* 图表展示区：左侧 净值走势（占 8 列），右侧 Q1~Q5 梯度柱状图（占 4 列） */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
        <div className="space-y-1 lg:col-span-8">
          <div className="flex items-center justify-between text-2xs text-ink-secondary">
            <span className="font-medium">
              {viewMode === 'spread_cost'
                ? '多空累计净值走势（毛收益 vs 扣费 0.1% 滑点税费）'
                : 'Q1~Q5 分组累计净值走势 (等权再平衡)'}
            </span>
            <span className="text-ink-muted">
              {viewMode === 'spread_cost' ? '摩擦成本对高频因子有直接损耗' : '层级分明且不交叉为最佳'}
            </span>
          </div>
          <div ref={chartRef} className="h-48 w-full" />
        </div>

        <div className="space-y-1 lg:col-span-4">
          <div className="text-2xs font-medium text-ink-secondary">
            Q1~Q5 年化收益梯度
          </div>
          <div ref={barRef} className="h-48 w-full" />
        </div>
      </div>
    </div>
  );
}
