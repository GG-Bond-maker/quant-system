/**
 * 期货跨期套利价差与滚动 Z-Score 监控卡片 (CalendarSpreadCard)。
 * 
 * 适用于股指与商品期货统计套利：
 * 1. 监测近月合约与远季月合约价差走势 (Spread = Near - Far)；
 * 2. 测算 20 日滚动均值、波动率与标准分 Z-Score；
 * 3. 触发超买/超卖均值回归统计套利交易信号。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { SectionCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import type { CalendarSpreadResult } from '@/types/futures';
import { fmtNum } from '@/utils/format';

interface Props {
  initialSymbol?: string;
}

const SYMBOL_OPTIONS = [
  { prefix: 'IF', name: '沪深300股指' },
  { prefix: 'IC', name: '中证500股指' },
  { prefix: 'IM', name: '中证1000股指' },
  { prefix: 'IH', name: '上证50股指' },
  { prefix: 'AU', name: '沪金主力' },
  { prefix: 'AG', name: '沪银主力' },
  { prefix: 'RB', name: '螺纹钢主力' },
];

export default function CalendarSpreadCard({ initialSymbol = 'IF' }: Props) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [data, setData] = useState<CalendarSpreadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const theme = useTheme();

  useEffect(() => {
    if (initialSymbol) {
      setSymbol(initialSymbol);
    }
  }, [initialSymbol]);

  useEffect(() => {
    let unmounted = false;
    setLoading(true);
    futuresApi.calendarSpread(symbol, 20)
      .then((res) => {
        if (!unmounted) {
          setData(res);
        }
      })
      .catch((err) => {
        console.error('Failed to load calendar spread', err);
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
    const spreads = data.series.map((s) => s.spread);
    const zScores = data.series.map((s) => s.z_score ?? 0);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      legend: {
        data: ['跨期价差 (Near - Far)', '滚动 Z-Score'],
        top: 0,
        textStyle: { color: p.INK2, fontSize: 11 },
      },
      grid: {
        left: 45,
        right: 45,
        top: 36,
        bottom: 24,
      },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      yAxis: [
        {
          type: 'value',
          name: '价差',
          axisLine: { show: false },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
          axisLabel: { color: p.INKM, fontSize: 10 },
        },
        {
          type: 'value',
          name: 'Z-Score',
          axisLine: { show: false },
          splitLine: { show: false },
          axisLabel: { color: p.INKM, fontSize: 10 },
        },
      ],
      series: [
        {
          name: '跨期价差 (Near - Far)',
          type: 'line',
          data: spreads,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
        },
        {
          name: '滚动 Z-Score',
          type: 'line',
          yAxisIndex: 1,
          data: zScores,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: p.WARN, type: 'dotted' },
          itemStyle: { color: p.WARN },
          markLine: {
            silent: true,
            data: [
              { yAxis: 2, lineStyle: { color: p.DOWN, type: 'dashed' } },
              { yAxis: -2, lineStyle: { color: p.UP, type: 'dashed' } },
            ],
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(option);

  return (
    <SectionCard
      title="跨期套利价差分析 (Calendar Spread Arbitrage)"
      action={
        <div className="flex items-center gap-2">
          <label className="text-2xs text-ink-muted">品种切换：</label>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink"
          >
            {SYMBOL_OPTIONS.map((opt) => (
              <option key={opt.prefix} value={opt.prefix}>
                {opt.prefix} · {opt.name}
              </option>
            ))}
          </select>
        </div>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-2xs text-ink-muted">合约标的组合</div>
          <div className="text-xs font-semibold text-ink mt-0.5">
            {data?.near_contract ?? '近月'} vs {data?.far_contract ?? '远季月'}
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-2xs text-ink-muted">当前价差 (Spread)</div>
          <div className="text-xs font-bold text-brand-600 num mt-0.5">
            {data ? fmtNum(data.current_spread) : '--'}
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-2xs text-ink-muted">20日波动均值 / 标准差</div>
          <div className="text-xs font-medium text-ink num mt-0.5">
            {data ? `${fmtNum(data.mean_spread)} / ±${fmtNum(data.std_spread)}` : '--'}
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2.5">
          <div className="text-2xs text-ink-muted">统计 Z-Score / 套利信号</div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-xs font-bold num text-ink">{data?.current_z_score ?? 0}</span>
            <span
              className={`rounded px-1.5 py-0.2 text-2xs font-semibold ${
                data?.arbitrage_signal === 'short_near_long_far'
                  ? 'bg-down-bg text-down'
                  : data?.arbitrage_signal === 'long_near_short_far'
                  ? 'bg-up-bg text-up'
                  : 'bg-surface-sunken text-ink-muted'
              }`}
            >
              {data?.arbitrage_signal === 'short_near_long_far'
                ? '空近多远'
                : data?.arbitrage_signal === 'long_near_short_far'
                ? '多近空远'
                : '区间中性'}
            </span>
          </div>
        </div>
      </div>

      <div className="relative w-full h-[220px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/60 text-xs text-ink-muted z-10">
            正在计算跨期价差与协整分位数...
          </div>
        )}
        <div ref={chartRef} className="w-full h-full" />
      </div>
    </SectionCard>
  );
}
