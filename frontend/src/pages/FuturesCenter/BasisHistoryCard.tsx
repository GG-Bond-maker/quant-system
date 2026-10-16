/**
 * 期货中心 · 历史基差走势与分位数雷达卡片 (BasisHistoryCard)。
 *
 * 评估基差是否处于历史极端值（寻找期现套利与极佳套保对冲窗口）：
 * 1. 呈现过去 180 天现货指数 vs 期货主力走势；
 * 2. 呈现基差时序走势与 0 轴升贴水分界线；
 * 3. 核算当前基差所处的历史分位数 (0% ~ 100%) 与极值状态判定。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { futuresApi } from '@/api/futures';
import type { BasisHistoryResult } from '@/types/futures';
import { fmtNum } from '@/utils/format';

interface Props {
  symbol?: string;
}

export default function BasisHistoryCard({ symbol = 'IF' }: Props) {
  const [data, setData] = useState<BasisHistoryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const theme = useTheme();

  useEffect(() => {
    let unmounted = false;
    setLoading(true);
    futuresApi.basisHistory(symbol, 180)
      .then((res) => {
        if (!unmounted) setData(res);
      })
      .catch((err) => {
        console.error('Failed to load basis history', err);
      })
      .finally(() => {
        if (!unmounted) setLoading(false);
      });

    return () => {
      unmounted = true;
    };
  }, [symbol]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.series?.length) return null;
    const p = chartPalette();
    const dates = data.series.map((s) => s.date);
    const bases = data.series.map((s) => s.basis);
    const spots = data.series.map((s) => s.spot_price);
    const futures = data.series.map((s) => s.futures_price);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      legend: {
        data: ['现货价格', '期货价格', '基差 (现货-期货)'],
        top: 0,
        textStyle: { color: p.INK2, fontSize: 10 },
      },
      grid: { left: 45, right: 45, top: 32, bottom: 20 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 9 },
      },
      yAxis: [
        {
          type: 'value',
          name: '价格',
          scale: true,
          axisLine: { show: false },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
          axisLabel: { color: p.INKM, fontSize: 9 },
        },
        {
          type: 'value',
          name: '基差 (点)',
          axisLine: { show: false },
          splitLine: { show: false },
          axisLabel: { color: p.INKM, fontSize: 9 },
        },
      ],
      series: [
        {
          name: '现货价格',
          type: 'line',
          data: spots,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: p.INKM, type: 'dashed' },
        },
        {
          name: '期货价格',
          type: 'line',
          data: futures,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
        },
        {
          name: '基差 (现货-期货)',
          type: 'bar',
          yAxisIndex: 1,
          data: bases,
          barWidth: 4,
          itemStyle: {
            color: (params: any) => ((params.value ?? 0) >= 0 ? withAlpha(p.UP, 0.7) : withAlpha(p.DOWN, 0.7)),
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(option);

  const stats = data?.percentile_stats;

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface p-3 shadow-sm">
      <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">
            {data?.name ?? symbol} · 历史基差分位数雷达
          </span>
          {stats && (
            <span
              className={`rounded px-1.5 py-0.2 text-2xs font-semibold ${
                stats.percentile >= 80
                  ? 'bg-up-bg text-up border border-up/30'
                  : stats.percentile <= 20
                  ? 'bg-down-bg text-down border border-down/30'
                  : 'bg-surface-sunken text-ink-secondary border border-hair'
              }`}
            >
              历史分位: {stats.percentile}% (
              {stats.regime === 'extreme_backwardation'
                ? '极度贴水'
                : stats.regime === 'moderate_backwardation'
                ? '温和贴水'
                : stats.regime === 'extreme_contango'
                ? '极度升水'
                : stats.regime === 'moderate_contango'
                ? '温和升水'
                : '中性区间'}
              )
            </span>
          )}
        </div>
        <span className="text-2xs text-ink-muted">近 180 交易日分布</span>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-2 text-2xs">
        <div className="rounded bg-surface-alt p-1.5 border border-hair">
          <span className="text-ink-muted block">当前最新基差</span>
          <span className="font-bold text-xs text-brand-600 num">{data ? fmtNum(data.current_basis) : '--'}</span>
        </div>
        <div className="rounded bg-surface-alt p-1.5 border border-hair">
          <span className="text-ink-muted block">历史均值</span>
          <span className="font-semibold text-ink num">{stats ? fmtNum(stats.historical_mean) : '--'}</span>
        </div>
        <div className="rounded bg-surface-alt p-1.5 border border-hair">
          <span className="text-ink-muted block">区间最低基差</span>
          <span className="font-semibold text-ink-secondary num">{stats ? fmtNum(stats.historical_min) : '--'}</span>
        </div>
        <div className="rounded bg-surface-alt p-1.5 border border-hair">
          <span className="text-ink-muted block">区间最高基差</span>
          <span className="font-semibold text-ink-secondary num">{stats ? fmtNum(stats.historical_max) : '--'}</span>
        </div>
      </div>

      <div className="relative w-full h-[200px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/60 text-xs text-ink-muted z-10">
            正在拉取历史基差时序与分位数...
          </div>
        )}
        <div ref={chartRef} className="w-full h-full" />
      </div>
    </div>
  );
}
