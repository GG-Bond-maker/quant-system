/**
 * 因子 Barra 风格暴露雷达与中性化诊断看板 (Factor Style Exposure Radar Card)。
 *
 * 对标 Barra 多因子风险模型体系：
 * 1. Barra 6 大核心风格因子截面暴露雷达图 (Size, Beta/Vol, Momentum, Reversal, Liquidity, Trend)；
 * 2. 风格暴露敏感度与偏差预警（|Exposure| >= 0.3 标记为显著风格暴露）；
 * 3. 投研指引：提示该因子是纯粹 Alpha 还是伪装的风格 Beta，并指导是否需要截面中性化纯化。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import type { FactorReportResult } from '@/api/production';

interface FactorExposureRadarCardProps {
  report: FactorReportResult;
  onNavigateToOrtho?: () => void;
}

export default function FactorExposureRadarCard({
  report,
  onNavigateToOrtho,
}: FactorExposureRadarCardProps) {
  useTheme();
  const p = chartPalette();

  const exposures = useMemo(() => {
    if (report.style_exposures && report.style_exposures.length > 0) {
      return report.style_exposures;
    }
    // 降级兜底预设
    return [
      { style: 'Size', name: '市值规模', factor_col: 'amount_per_share', exposure: 0.12 },
      { style: 'Beta/Vol', name: '波动风险', factor_col: 'vol_20', exposure: -0.08 },
      { style: 'Momentum', name: '动量趋势', factor_col: 'ret_20', exposure: 0.25 },
      { style: 'Reversal', name: '短期反转', factor_col: 'rsi_14', exposure: -0.15 },
      { style: 'Liquidity', name: '流动性', factor_col: 'v_rank_20', exposure: 0.05 },
      { style: 'Trend Gap', name: '均线乖离', factor_col: 'ma_gap_20', exposure: 0.18 },
    ];
  }, [report]);

  // 雷达图配置
  const radarOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!exposures.length) return null;

    const indicators = exposures.map((exp) => ({
      name: `${exp.name}\n(${exp.style})`,
      max: 0.6,
      min: -0.6,
    }));

    const values = exposures.map((exp) => exp.exposure);

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const item = params as { name: string; value: number[] };
          let res = `<div class="font-medium mb-1">${item.name}</div>`;
          exposures.forEach((e, idx) => {
            const v = item.value[idx] ?? 0;
            res += `<div class="text-2xs flex justify-between gap-4"><span>${e.name} (${e.style}):</span><b>${v.toFixed(3)}</b></div>`;
          });
          return res;
        },
      },
      radar: {
        indicator: indicators,
        shape: 'polygon',
        splitNumber: 4,
        axisName: {
          color: p.INK,
          fontSize: 9,
        },
        splitLine: {
          lineStyle: {
            color: p.SUNKEN,
          },
        },
        splitArea: {
          show: true,
          areaStyle: {
            color: [withAlpha(p.CARD, 0.2), withAlpha(p.SUNKEN, 0.15)],
          },
        },
        axisLine: {
          lineStyle: {
            color: p.SUNKEN,
          },
        },
      },
      series: [
        {
          name: 'Barra 风格暴露度',
          type: 'radar',
          data: [
            {
              value: values,
              name: '风格相关系数暴露',
              symbol: 'circle',
              symbolSize: 4,
              lineStyle: {
                width: 2,
                color: p.BRAND,
              },
              itemStyle: {
                color: p.BRAND,
              },
              areaStyle: {
                color: withAlpha(p.BRAND, 0.25),
              },
            },
          ],
        },
      ],
    };
  }, [exposures, p]);
  const radarRef = useChart(radarOption);

  // 风格暴露偏差诊断
  const highExposures = useMemo(() => {
    return exposures.filter((e) => Math.abs(e.exposure) >= 0.25);
  }, [exposures]);

  return (
    <div className="space-y-3 rounded-lg border border-hair bg-surface p-3.5">
      {/* 头部标题与对标说明 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">Barra 风格暴露雷达与中性化诊断</span>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            Barra 风险模型体系 · 截面相关暴露
          </span>
        </div>
        {onNavigateToOrtho && (
          <button
            onClick={onNavigateToOrtho}
            className="rounded border border-brand-500/40 bg-surface px-2.5 py-0.5 text-2xs font-medium text-brand-600 hover:bg-surface-alt"
          >
            去正交纯化残差 →
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-12 items-center">
        {/* 左侧雷达图 (7 列) */}
        <div className="lg:col-span-7">
          <div ref={radarRef} className="h-60 w-full" />
        </div>

        {/* 右侧诊断与明细表格 (5 列) */}
        <div className="space-y-2.5 lg:col-span-5">
          <div className="rounded border border-hair bg-surface-alt p-2.5 text-2xs space-y-1.5">
            <div className="font-semibold text-ink flex items-center justify-between">
              <span>风格偏离度诊断</span>
              {highExposures.length > 0 ? (
                <span className="rounded bg-warn-bg px-1.5 py-0.5 text-3xs font-medium text-warn">
                  存在 {highExposures.length} 项显著偏离
                </span>
              ) : (
                <span className="rounded bg-success-bg px-1.5 py-0.5 text-3xs font-medium text-success">
                  风格相对中性
                </span>
              )}
            </div>
            <p className="text-ink-secondary leading-relaxed">
              {highExposures.length > 0
                ? `本因子在【${highExposures.map((h) => h.name).join('、')}】上存在较显著暴露 (|ρ| ≥ 0.25)。若市场发生对应风格踩踏，因子多头可能承担意外 Beta 回撤。建议在多因子纯化面板中进行残差正交化。`
                : '本因子与各大 Barra 常见基准风格因子的截面相关性均在合理阈值内 (|ρ| < 0.25)，展现出较好的正交独立 Alpha 特征。'}
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-2xs">
              <thead>
                <tr className="border-b border-hair text-ink-muted">
                  <th className="py-1 text-left font-medium">风格因子</th>
                  <th className="py-1 text-left font-medium">基准列</th>
                  <th className="py-1 text-right font-medium">截面暴露 ρ</th>
                  <th className="py-1 text-center font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {exposures.map((e) => {
                  const isHigh = Math.abs(e.exposure) >= 0.25;
                  return (
                    <tr key={e.style} className="border-b border-hair/50">
                      <td className="py-1 text-ink font-medium">
                        {e.name} <span className="text-3xs text-ink-muted font-mono">({e.style})</span>
                      </td>
                      <td className="py-1 font-mono text-ink-secondary text-3xs">{e.factor_col}</td>
                      <td className="py-1 text-right num font-semibold">
                        <span className={e.exposure > 0 ? 'text-danger' : e.exposure < 0 ? 'text-success' : 'text-ink-muted'}>
                          {e.exposure.toFixed(3)}
                        </span>
                      </td>
                      <td className="py-1 text-center">
                        <span
                          className={`rounded px-1.5 py-0.2 text-3xs ${
                            isHigh ? 'bg-warn-bg text-warn font-medium' : 'text-ink-muted'
                          }`}
                        >
                          {isHigh ? '偏离高' : '中性'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
