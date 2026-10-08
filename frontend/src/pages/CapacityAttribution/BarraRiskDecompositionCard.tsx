/**
 * Barra 风格多因子归因与跟踪误差主动风险结构分解卡片（BarraRiskDecompositionCard）。
 * 对标 Barra Aegis / MSCI MAC 机构级多因子风险结构分解标准。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import type { AttributionResult } from '@/api/production';

export interface BarraRiskDecompositionCardProps {
  style: AttributionResult['style'];
}

export default function BarraRiskDecompositionCard({ style }: BarraRiskDecompositionCardProps) {
  const theme = useTheme();

  const factorEntries = useMemo(() => {
    if (!style.factor_stats) {
      return Object.entries(style.contributions).map(([k, contrib]) => ({
        name: k,
        beta: style.betas[k] ?? 0,
        std_error: 0,
        t_stat: 0,
        p_value: 1,
        significance: 'ns',
        annualized_contrib: contrib,
      }));
    }
    return Object.entries(style.factor_stats).map(([k, s]) => ({
      name: k,
      ...s,
    }));
  }, [style]);

  // ECharts 水平柱状图呈现各风格因子年化贡献
  const barOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!factorEntries.length) return null;
    const p = chartPalette();
    const categories = factorEntries.map((f) => f.name);
    const dataVals = factorEntries.map((f) => f.annualized_contrib);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const item = factorEntries[items[0].dataIndex];
          if (!item) return '';
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px 4px">
              <b>风格因子:</b> ${item.name}<br/>
              <b>因子暴露 β:</b> ${item.beta.toFixed(3)} (t=${item.t_stat.toFixed(2)}, p=${item.p_value.toFixed(3)})<br/>
              <b>显著性:</b> ${item.significance}<br/>
              <b>年化收益贡献:</b> <span style="font-weight:bold;color:${item.annualized_contrib >= 0 ? p.UP : p.DOWN}">
                ${(item.annualized_contrib * 100).toFixed(2)}%
              </span>
            </div>
          `;
        },
      },
      grid: { left: 60, right: 30, top: 12, bottom: 20 },
      xAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      series: [
        {
          type: 'bar',
          data: dataVals,
          barWidth: 12,
          itemStyle: {
            color: (pParams: { data: number }) =>
              pParams.data >= 0 ? p.UP : p.DOWN,
          },
          label: {
            show: true,
            position: 'right',
            formatter: (pParams: { data: number }) => `${(pParams.data * 100).toFixed(2)}%`,
            fontSize: 9,
          },
        },
      ],
    } as echarts.EChartsOption;
  }, [factorEntries, theme]);

  const barRef = useChart(barOption);

  const riskDecomp = style.risk_decomposition;

  return (
    <div className="space-y-3">
      {/* 头部：Barra 风险结构分解（主动跟踪误差方差分解） */}
      {riskDecomp && (
        <div className="rounded-lg border border-hair bg-surface p-3 text-2xs">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-semibold text-ink">
              Barra 主动跟踪误差风险结构分解（TE = {riskDecomp.tracking_error_pct.toFixed(2)}% 年化）
            </span>
            <span className="text-ink-muted">样本期: {style.n_days} 个有效观测</span>
          </div>

          <div className="space-y-1.5">
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface-alt">
              <div
                style={{ width: `${riskDecomp.factor_risk_pct}%` }}
                className="bg-brand-500 transition-all duration-300"
                title={`因子系统性风险: ${riskDecomp.factor_risk_pct}%`}
              />
              <div
                style={{ width: `${riskDecomp.specific_risk_pct}%` }}
                className="bg-accent transition-all duration-300"
                title={`特异残差风险: ${riskDecomp.specific_risk_pct}%`}
              />
            </div>
            <div className="flex justify-between text-2xs">
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-brand-500" />
                <span className="text-ink-secondary">
                  因子风险占比: <b className="num text-ink">{riskDecomp.factor_risk_pct}%</b>
                  <span className="ml-1 text-ink-muted">({riskDecomp.annual_factor_vol_pct}% 年化波动)</span>
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full bg-accent" />
                <span className="text-ink-secondary">
                  特异残差风险占比: <b className="num text-ink">{riskDecomp.specific_risk_pct}%</b>
                  <span className="ml-1 text-ink-muted">({riskDecomp.annual_specific_vol_pct}% 年化波动)</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 统计指标与显著性检验表 */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div className="overflow-x-auto">
          <div className="mb-1 text-2xs font-medium text-ink-secondary">
            风格因子暴露与统计显著性检验表（OLS 回归，R² = {style.r_squared != null ? `${(style.r_squared * 100).toFixed(1)}%` : '—'}）
          </div>
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-hair text-ink-secondary">
                <th className="py-1 text-left font-medium">因子名称</th>
                <th className="py-1 text-center font-medium">暴露度 (β)</th>
                <th className="py-1 text-right font-medium">标准误 (SE)</th>
                <th className="py-1 text-right font-medium">t 统计量</th>
                <th className="py-1 text-center font-medium">显著性</th>
                <th className="py-1 text-right font-medium">年化收益贡献</th>
              </tr>
            </thead>
            <tbody>
              {factorEntries.map((f) => (
                <tr key={f.name} className="border-b border-hair hover:bg-surface-alt">
                  <td className="py-1 font-medium">{f.name}</td>
                  <td className="num py-1 text-center font-mono">{f.beta.toFixed(3)}</td>
                  <td className="num py-1 text-right font-mono text-ink-muted">{f.std_error ? f.std_error.toFixed(3) : '—'}</td>
                  <td className={`num py-1 text-right font-mono ${Math.abs(f.t_stat) >= 1.96 ? 'font-semibold text-brand-700' : 'text-ink-muted'}`}>
                    {f.t_stat ? f.t_stat.toFixed(2) : '—'}
                  </td>
                  <td className="py-1 text-center">
                    <span className={`inline-block rounded px-1 text-2xs ${
                      f.significance === '***' ? 'bg-danger-bg text-danger font-bold' :
                      f.significance === '**' ? 'bg-warn/20 text-warn font-semibold' :
                      f.significance === '*' ? 'bg-brand-50 text-brand-700' : 'text-ink-muted'
                    }`}>
                      {f.significance}
                    </span>
                  </td>
                  <td className={`num py-1 text-right font-mono font-medium ${f.annualized_contrib >= 0 ? 'text-danger' : 'text-success'}`}>
                    {(f.annualized_contrib * 100).toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-hair bg-surface-alt font-medium">
                <td className="py-1">残差 Alpha</td>
                <td className="num py-1 text-center" colSpan={2}>
                  {style.alpha_annualized != null ? `${(style.alpha_annualized * 100).toFixed(2)}% 年化` : '—'}
                </td>
                <td className="num py-1 text-right font-mono">
                  {style.alpha_t_stat != null ? `t=${style.alpha_t_stat.toFixed(2)}` : '—'}
                </td>
                <td className="py-1 text-center font-semibold text-brand-700">
                  {style.alpha_significance || '—'}
                </td>
                <td className="num py-1 text-right font-mono text-ink-muted">—</td>
              </tr>
            </tfoot>
          </table>
          <div className="mt-1 text-2xs text-ink-muted">
            注：*** p&lt;0.001, ** p&lt;0.01, * p&lt;0.05, ns 不显著 (|t|&lt;1.96)。
          </div>
        </div>

        <div>
          <div className="mb-1 text-2xs font-medium text-ink-secondary">
            风格因子对超额年化收益的贡献对比
          </div>
          <div ref={barRef} className="h-44 w-full" />
        </div>
      </div>
    </div>
  );
}
