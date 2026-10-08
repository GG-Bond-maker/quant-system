/**
 * 多期连乘平滑累积超额收益时序归因瀑布与堆叠面积图（CumulativeAttributionChart）。
 * 对标 FactSet PA / Carino 连乘复合归因标准体系。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import type { AttributionTimeseriesPoint } from '@/api/production';

export interface CumulativeAttributionChartProps {
  timeseries: AttributionTimeseriesPoint[];
}

export default function CumulativeAttributionChart({ timeseries }: CumulativeAttributionChartProps) {
  const theme = useTheme();

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!timeseries || timeseries.length === 0) return null;
    const p = chartPalette();

    const xDates = timeseries.map((pt) => pt.date);
    const excessData = timeseries.map((pt) => Number((pt.excess_cum_ret * 100).toFixed(3)));
    const allocData = timeseries.map((pt) => Number((pt.cum_allocation * 100).toFixed(3)));
    const selectData = timeseries.map((pt) => Number((pt.cum_selection * 100).toFixed(3)));
    const interData = timeseries.map((pt) => Number((pt.cum_interaction * 100).toFixed(3)));

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: unknown) => {
          const items = params as Array<{ seriesName: string; value: number; marker: string }>;
          if (!items?.length) return '';
          const idx = (params as Array<{ dataIndex: number }>)[0]?.dataIndex ?? 0;
          const pt = timeseries[idx];
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px 4px">
              <b>交易日:</b> ${pt?.date ?? ''}<br/>
              <b>累计超额:</b> <span style="font-weight:bold;color:${(pt?.excess_cum_ret ?? 0) >= 0 ? p.UP : p.DOWN}">
                ${((pt?.excess_cum_ret ?? 0) * 100).toFixed(2)}%
              </span><br/>
              <b>配置效应:</b> ${((pt?.cum_allocation ?? 0) * 100).toFixed(2)}%<br/>
              <b>选股效应:</b> ${((pt?.cum_selection ?? 0) * 100).toFixed(2)}%<br/>
              <b>交互效应:</b> ${((pt?.cum_interaction ?? 0) * 100).toFixed(2)}%<br/>
              <span style="font-size:10px;color:${p.INKM}">组合累计: ${((pt?.portfolio_cum_ret ?? 0) * 100).toFixed(2)}% | 基准累计: ${((pt?.benchmark_cum_ret ?? 0) * 100).toFixed(2)}%</span>
            </div>
          `;
        },
      },
      legend: {
        data: ['累计超额收益', '配置效应 (Allocation)', '选股效应 (Selection)', '交互效应 (Interaction)'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 48, right: 32, top: 20, bottom: 32 },
      xAxis: {
        type: 'category',
        data: xDates,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        name: '收益贡献 (%)',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: {
          fontSize: 9,
          color: p.INKM,
          formatter: (v: number) => `${v.toFixed(1)}%`,
        },
      },
      series: [
        {
          name: '累计超额收益',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: excessData,
          lineStyle: { width: 2.2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          z: 5,
        },
        {
          name: '选股效应 (Selection)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: selectData,
          lineStyle: { width: 1.5, color: p.UP },
          itemStyle: { color: p.UP },
          areaStyle: { opacity: 0.12, color: p.UP },
        },
        {
          name: '配置效应 (Allocation)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: allocData,
          lineStyle: { width: 1.5, color: p.ALT },
          itemStyle: { color: p.ALT },
          areaStyle: { opacity: 0.12, color: p.ALT },
        },
        {
          name: '交互效应 (Interaction)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: interData,
          lineStyle: { width: 1.2, color: p.WARN, type: 'dashed' },
          itemStyle: { color: p.WARN },
        },
      ],
    } as echarts.EChartsOption;
  }, [timeseries, theme]);

  const chartRef = useChart(option);

  if (!timeseries || timeseries.length === 0) {
    return (
      <div className="py-8 text-center text-xs text-ink-muted">
        正在计算时序多期连乘平滑曲线…
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-2xs text-ink-muted">
        <span>Menchero / Carino 连乘平滑累计时序走势（精确闭合零残差）</span>
        <span>共 {timeseries.length} 个交易日追踪</span>
      </div>
      <div ref={chartRef} className="h-56 w-full" />
    </div>
  );
}
