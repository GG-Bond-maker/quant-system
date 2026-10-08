/**
 * 行业主动暴露偏离度与 Brinson 超额收益归因透视卡片（SectorActiveTiltCard）。
 * 对标 FactSet PA / Barra 行业主动暴露与收益归因穿透体系。
 */
import { useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import type { AttributionResult } from '@/api/production';

export interface SectorActiveTiltCardProps {
  attr: AttributionResult;
}

export default function SectorActiveTiltCard({ attr }: SectorActiveTiltCardProps) {
  const theme = useTheme();
  const [modelType, setModelType] = useState<'multi_period' | 'single_period'>('multi_period');

  const activeSectors = useMemo(() => {
    if (modelType === 'multi_period' && attr.multi_period_brinson?.sectors?.length) {
      return attr.multi_period_brinson.sectors;
    }
    return attr.brinson?.sectors ?? [];
  }, [attr, modelType]);

  const summary = useMemo(() => {
    if (modelType === 'multi_period' && attr.multi_period_brinson?.summary) {
      return attr.multi_period_brinson.summary;
    }
    return attr.brinson?.summary;
  }, [attr, modelType]);

  // ECharts 双轴水平柱状图：主动权重偏离 (Active Weight) vs 行业合计超额贡献 (Total Return)
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!activeSectors || activeSectors.length === 0) return null;
    const p = chartPalette();
    // 取绝对收益前 10 个行业
    const displaySectors = [...activeSectors].slice(0, 10).reverse();
    const categories = displaySectors.map((s) => s.industry);
    const activeWeights = displaySectors.map((s) =>
      Number(((s.active_weight ?? (s.portfolio_weight - s.benchmark_weight)) * 100).toFixed(2)),
    );
    const totalExcess = displaySectors.map((s) => Number((s.total * 100).toFixed(2)));

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
      },
      legend: {
        data: ['主动超配/低配 (%)', '行业超额贡献 (%)'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 70, right: 28, top: 20, bottom: 32 },
      xAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      series: [
        {
          name: '主动超配/低配 (%)',
          type: 'bar',
          data: activeWeights,
          barWidth: 8,
          itemStyle: {
            color: (params: { data: number }) => (params.data >= 0 ? p.BRAND : p.INKM),
          },
        },
        {
          name: '行业超额贡献 (%)',
          type: 'bar',
          data: totalExcess,
          barWidth: 8,
          itemStyle: {
            color: (params: { data: number }) => (params.data >= 0 ? p.UP : p.DOWN),
          },
        },
      ],
    } as echarts.EChartsOption;
  }, [activeSectors, theme]);

  const chartRef = useChart(chartOption);

  const fmtPct = (v: number | null | undefined, digits = 2) =>
    v == null ? '—' : `${(v * 100).toFixed(digits)}%`;

  return (
    <div className="space-y-3">
      {/* 模式切换与口径说明 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2 text-2xs">
        <div className="flex items-center gap-2">
          <span className="font-medium text-ink">Brinson 行业归因透视</span>
          <span className="rounded bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-700">
            {modelType === 'multi_period' ? 'Carino / Menchero 连乘平滑模型 (零残差)' : '经典单期 Brinson-Fachler'}
          </span>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => setModelType('multi_period')}
            className={`rounded px-2 py-0.5 text-2xs ${
              modelType === 'multi_period'
                ? 'bg-brand-50 font-medium text-brand-700'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            多期连乘平滑 (标准)
          </button>
          <button
            onClick={() => setModelType('single_period')}
            className={`rounded px-2 py-0.5 text-2xs ${
              modelType === 'single_period'
                ? 'bg-brand-50 font-medium text-brand-700'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            单期近似
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 左侧：行业归因数据表格 */}
        <div className="overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-hair text-ink-secondary">
                <th className="py-1 text-left font-medium">行业</th>
                <th className="py-1 text-center font-medium">组合权重</th>
                <th className="py-1 text-center font-medium">基准权重</th>
                <th className="py-1 text-center font-medium">主动偏离</th>
                <th className="py-1 text-right font-medium">配置收益</th>
                <th className="py-1 text-right font-medium">选股收益</th>
                <th className="py-1 text-right font-medium">合计超额</th>
              </tr>
            </thead>
            <tbody>
              {activeSectors.map((s) => {
                const activeW = s.active_weight ?? (s.portfolio_weight - s.benchmark_weight);
                return (
                  <tr key={s.industry} className="border-b border-hair hover:bg-surface-alt">
                    <td className="py-1 font-medium">{s.industry}</td>
                    <td className="num py-1 text-center">{fmtPct(s.portfolio_weight, 1)}</td>
                    <td className="num py-1 text-center">{fmtPct(s.benchmark_weight, 1)}</td>
                    <td
                      className={`num py-1 text-center font-medium ${
                        activeW >= 0 ? 'text-brand-700' : 'text-ink-secondary'
                      }`}
                    >
                      {activeW >= 0 ? '+' : ''}{fmtPct(activeW, 1)}
                    </td>
                    <td
                      className={`num py-1 text-right ${
                        s.allocation >= 0 ? 'text-danger' : 'text-success'
                      }`}
                    >
                      {fmtPct(s.allocation)}
                    </td>
                    <td
                      className={`num py-1 text-right ${
                        s.selection >= 0 ? 'text-danger' : 'text-success'
                      }`}
                    >
                      {fmtPct(s.selection)}
                    </td>
                    <td
                      className={`num py-1 text-right font-semibold ${
                        s.total >= 0 ? 'text-danger' : 'text-success'
                      }`}
                    >
                      {fmtPct(s.total)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            {summary && (
              <tfoot>
                <tr className="border-t-2 border-hair bg-surface-alt font-semibold">
                  <td className="py-1">合计小计</td>
                  <td className="num py-1 text-center">—</td>
                  <td className="num py-1 text-center">—</td>
                  <td className="num py-1 text-center">—</td>
                  <td
                    className={`num py-1 text-right ${
                      summary.allocation >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {fmtPct(summary.allocation)}
                  </td>
                  <td
                    className={`num py-1 text-right ${
                      summary.selection >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {fmtPct(summary.selection)}
                  </td>
                  <td
                    className={`num py-1 text-right ${
                      summary.excess_return >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {fmtPct(summary.excess_return)}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>

          {summary && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-muted">
              <span>交互效应: <span className="num font-medium text-ink-secondary">{fmtPct(summary.interaction)}</span></span>
              <span>
                连乘残差 Alpha:{' '}
                <span
                  className={`num font-semibold ${
                    Math.abs(summary.residual_alpha) < 1e-4 ? 'text-success' : 'text-warn'
                  }`}
                >
                  {fmtPct(summary.residual_alpha, 4)}
                </span>
                {Math.abs(summary.residual_alpha) < 1e-4 && (
                  <span className="ml-1 text-success">（零残差精确闭合）</span>
                )}
              </span>
            </div>
          )}
        </div>

        {/* 右侧：主动偏离权重 vs 超额贡献双柱图 */}
        <div>
          <div className="mb-1 text-2xs font-medium text-ink-secondary">
            重点行业主动暴露 (Active Tilts) 与超额收益贡献对比
          </div>
          <div ref={chartRef} className="h-56 w-full" />
        </div>
      </div>
    </div>
  );
}
