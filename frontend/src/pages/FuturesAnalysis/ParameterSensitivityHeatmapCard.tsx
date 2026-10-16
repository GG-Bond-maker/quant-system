/**
 * 双均线参数敏感性网格寻优二维热力图卡片 (Parameter Sensitivity Heatmap)。
 *
 * 包含：
 * 1. 快线 (Fast MA) 与 慢线 (Slow MA) 参数组合网格扫描；
 * 2. 夏普比率色阶热力图与最大回撤/收益率浮窗诊断；
 * 3. 最优参数智能推荐与一键应用回测。
 */
import { useMemo } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { SectionCard } from '@/components/ui';
import type { ParameterSensitivityResult, CostStressMatrix } from '@/types/futures';
import { fmtPct } from '@/utils/format';

interface Props {
  data: ParameterSensitivityResult | null;
  costStressMatrix?: CostStressMatrix | null;
  loading?: boolean;
  onApplyOptimal?: (fast: number, slow: number) => void;
}

export function ParameterSensitivityHeatmapCard({
  data,
  costStressMatrix,
  loading = false,
  onApplyOptimal,
}: Props) {
  const theme = useTheme();

  // 提取唯一的快线与慢线坐标
  const { fastList, slowList, matrixData, optimal } = useMemo(() => {
    if (!data?.grid?.length) {
      return { fastList: [], slowList: [], matrixData: [], optimal: null };
    }

    const fasts = Array.from(new Set(data.grid.map((g) => g.fast))).sort((a, b) => a - b);
    const slows = Array.from(new Set(data.grid.map((g) => g.slow))).sort((a, b) => a - b);

    // ECharts heatmap 数据结构: [xIndex, yIndex, value, totalRet, maxDd]
    const matrix = data.grid.map((item) => {
      const xIdx = fasts.indexOf(item.fast);
      const yIdx = slows.indexOf(item.slow);
      return [xIdx, yIdx, item.sharpe, item.total_return_pct, item.max_drawdown_pct];
    });

    return {
      fastList: fasts,
      slowList: slows,
      matrixData: matrix,
      optimal: data.optimal_params,
    };
  }, [data]);

  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!matrixData.length) return null;
    const p = chartPalette();

    const sharpeValues = matrixData.map((d) => d[2] as number);
    const minSharpe = Math.min(...sharpeValues, 0);
    const maxSharpe = Math.max(...sharpeValues, 1.5);

    return {
      grid: { left: 60, right: 90, top: 24, bottom: 44 },
      tooltip: {
        position: 'top',
        backgroundColor: p.TIP_BG,
        borderColor: p.TIP_BORDER,
        textStyle: { color: p.TIP_TEXT },
        formatter: (params: any) => {
          const d = params.data;
          const f = fastList[d[0]];
          const s = slowList[d[1]];
          const sh = d[2];
          const ret = d[3];
          const dd = d[4];
          return `<div style="font-size:11px; font-weight:600; margin-bottom:4px;">快线 MA${f} / 慢线 MA${s}</div>
            <div style="font-size:11px; display:flex; justify-content:space-between; gap:12px;">
              <span>夏普比率:</span>
              <span style="font-family:monospace; font-weight:bold; color:${p.SUCCESS};">${sh}</span>
            </div>
            <div style="font-size:11px; display:flex; justify-content:space-between; gap:12px;">
              <span>累计收益率:</span>
              <span style="font-family:monospace; font-weight:bold;">${ret}%</span>
            </div>
            <div style="font-size:11px; display:flex; justify-content:space-between; gap:12px;">
              <span>最大回撤:</span>
              <span style="font-family:monospace; font-weight:bold; color:${p.DANGER};">${dd}%</span>
            </div>`;
        },
      },
      xAxis: {
        type: 'category',
        name: '快线周期 (Fast)',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: p.INK2, fontSize: 11 },
        data: fastList.map((f) => `MA${f}`),
        splitArea: { show: true },
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      yAxis: {
        type: 'category',
        name: '慢线周期 (Slow)',
        nameLocation: 'middle',
        nameGap: 36,
        nameTextStyle: { color: p.INK2, fontSize: 11 },
        data: slowList.map((s) => `MA${s}`),
        splitArea: { show: true },
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      visualMap: {
        min: minSharpe,
        max: maxSharpe,
        calculable: true,
        orient: 'vertical',
        right: 8,
        top: 'center',
        textStyle: { color: p.INKM, fontSize: 10 },
        inRange: {
          color: [p.INFO, p.BRAND, p.SUCCESS, p.WARN, p.DANGER],
        },
      },
      series: [
        {
          name: '参数敏感性',
          type: 'heatmap',
          data: matrixData,
          label: {
            show: true,
            color: p.ON_SOLID,
            fontSize: 10,
            formatter: (p: any) => p.data[2].toFixed(2),
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowColor: 'rgba(0, 0, 0, 0.5)',
            },
          },
        },
      ],
    };
  }, [fastList, slowList, matrixData, theme]);

  const chartRef = useChart(chartOption);

  if (loading) {
    return (
      <SectionCard title="参数敏感性二维寻优热力图">
        <div className="flex h-64 items-center justify-center text-sm text-text-muted">
          正在计算快慢均线全域参数敏感性热力图...
        </div>
      </SectionCard>
    );
  }

  if (!data || matrixData.length === 0) {
    return (
      <SectionCard title="参数敏感性二维寻优热力图">
        <div className="flex h-32 items-center justify-center text-sm text-text-muted">
          暂无参数寻优网格数据
        </div>
      </SectionCard>
    );
  }

  const stressMatrix = costStressMatrix || data?.cost_stress_matrix;

  return (
    <div className="space-y-4">
      <SectionCard
        title={`${data.name} 快慢均线参数敏感性网格寻优热力图`}
        action={
          <span className="text-2xs text-text-muted">
            全域网格共遍历 {data.total_combinations} 组均线组合，色阶深浅代表超额夏普比率表现
          </span>
        }
      >
        <div className="space-y-4">
          {/* 最优参数看板 */}
          {optimal && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/10 p-3.5 text-xs">
              <div className="flex flex-wrap items-center gap-4">
                <span className="font-semibold text-primary">👑 最优均线参数推荐:</span>
                <span className="font-mono text-sm font-bold text-text">
                  快线: MA{optimal.fast} / 慢线: MA{optimal.slow}
                </span>
                <span className="text-text-muted">最优夏普比率:</span>
                <span className="font-mono text-sm font-bold text-emerald-500">{optimal.sharpe}</span>
                <span className="text-text-muted">累计收益率:</span>
                <span className="font-mono font-semibold text-emerald-500">
                  {fmtPct(optimal.total_return_pct)}
                </span>
                <span className="text-text-muted">最大回撤:</span>
                <span className="font-mono font-semibold text-rose-500">
                  {fmtPct(optimal.max_drawdown_pct)}
                </span>
              </div>

              {onApplyOptimal && (
                <button
                  onClick={() => onApplyOptimal(optimal.fast, optimal.slow)}
                  className="rounded-md bg-primary px-3 py-1.5 font-medium text-white shadow-sm hover:bg-primary-hover active:opacity-90"
                >
                  ⚡ 一键应用该参数并回测
                </button>
              )}
            </div>
          )}

          {/* 二维热力图 */}
          <div className="rounded-lg border border-border bg-card p-2">
            <div ref={chartRef} style={{ width: '100%', height: '360px' }} />
          </div>
        </div>
      </SectionCard>

      {/* 稳健性分析：手续费与滑点成本压力测试矩阵 */}
      {stressMatrix && stressMatrix.grid?.length > 0 && (
        <SectionCard
          title="摩擦成本压力测试矩阵 (Friction Cost Stress Matrix: 4×4)"
          action={
            <span className="text-2xs text-text-muted">
              对标机构风控：检验不同佣金倍数与滑点冲击下的策略夏普比率与盈亏临界点
            </span>
          }
        >
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2.5">
              <div className="text-xs text-text-muted">
                期货具有高杠杆与日内平今高惩罚性手续费（如股指平今 3.45‱ 为平昨 15 倍）。本矩阵严格审计策略在摩擦加倍下的鲁棒性。
              </div>
              {stressMatrix.breakeven_slippage_ticks !== null ? (
                <div className="rounded bg-amber-500/10 border border-amber-500/30 px-2.5 py-1 text-2xs text-amber-500 font-mono font-medium">
                  ⚖️ 盈亏平衡临界滑点: <strong>{stressMatrix.breakeven_slippage_ticks} 跳</strong> (超过该滑点期望收益转负)
                </div>
              ) : (
                <div className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 text-2xs text-emerald-500 font-mono font-medium">
                  ✅ 强抗摩擦鲁棒性: 在测试滑点范围内均保持正向期望收益
                </div>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="bg-surface-raised font-medium text-text-muted">
                  <tr>
                    <th className="px-3 py-2.5">费率倍数 \ 滑点设定</th>
                    {(stressMatrix.tested_slippages || [0, 1, 2, 5]).map((slip) => (
                      <th key={slip} className="px-3 py-2.5 text-center font-mono">
                        {slip === 0 ? '0 跳 (零滑点)' : `${slip} 跳 (${slip === 1 ? '实盘标准' : slip === 2 ? '摩擦翻倍' : '极端枯竭'})`}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {(stressMatrix.tested_fee_multipliers || [0.5, 1, 2, 5]).map((feeMult) => (
                    <tr key={feeMult} className="hover:bg-surface-raised/40">
                      <td className="px-3 py-2.5 font-medium text-text font-mono">
                        {feeMult}x 佣金
                        <span className="text-2xs text-text-muted ml-1.5 font-sans">
                          {feeMult === 0.5 ? '(VIP优惠)' : feeMult === 1.0 ? '(交易所标准)' : feeMult === 2.0 ? '(加倍压力测试)' : '(极端摩擦)'}
                        </span>
                      </td>
                      {(stressMatrix.tested_slippages || [0, 1, 2, 5]).map((slip) => {
                        const cell = stressMatrix.grid.find(
                          (c) => Math.abs(c.fee_multiplier - feeMult) < 0.001 && Math.abs(c.slippage_ticks - slip) < 0.001
                        );
                        if (!cell) {
                          return <td key={slip} className="px-3 py-2.5 text-center text-text-muted">-</td>;
                        }
                        const isProfit = cell.total_return_pct > 0;
                        return (
                          <td key={slip} className="px-3 py-2.5 text-center">
                            <div className={`inline-block rounded px-2.5 py-1.5 text-2xs ${isProfit ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-500 border border-rose-500/20'}`}>
                              <div className="font-mono font-bold">
                                夏普: {cell.sharpe.toFixed(2)}
                              </div>
                              <div className="font-mono text-[10px] opacity-90 mt-0.5">
                                收益: {fmtPct(cell.total_return_pct)} | 回撤: {fmtPct(cell.max_drawdown_pct)}
                              </div>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
