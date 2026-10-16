/**
 * 期货中心 · 产业链与跨品种比价套利卡片 (InterCommoditySpreadCard)。
 *
 * 适用于产业链套利与宏观对冲配对：
 * 1. 监测贵金属金银比 (AU/AG)、黑色螺矿比 (RB/I)、盘面炼钢毛利、股指大小盘轮动 (IM/IH)；
 * 2. 统计检验：Ornstein-Uhlenbeck 均值回归半衰期估算 (Half-Life) 与滚动 Z-Score；
 * 3. 产生配对交易套利信号。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { SectionCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import type { InterCommoditySpreadResult } from '@/types/futures';
import { fmtNum } from '@/utils/format';

const PAIR_OPTIONS = [
  { key: 'AU_AG', label: '沪金 / 沪银 (金银比)' },
  { key: 'RB_I', label: '螺纹钢 / 铁矿石 (螺矿比)' },
  { key: 'IM_IH', label: '中证1000 / 上证50 (成长/价值比)' },
  { key: 'STEEL_PROFIT', label: '黑色盘面虚拟炼钢毛利 (元/吨)' },
];

export default function InterCommoditySpreadCard() {
  const [pairKey, setPairKey] = useState('AU_AG');
  const [data, setData] = useState<InterCommoditySpreadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const theme = useTheme();

  useEffect(() => {
    let unmounted = false;
    setLoading(true);
    futuresApi.interCommodity(pairKey)
      .then((res) => {
        if (!unmounted) setData(res);
      })
      .catch((err) => {
        console.error('Failed to load inter-commodity spread', err);
      })
      .finally(() => {
        if (!unmounted) setLoading(false);
      });

    return () => {
      unmounted = true;
    };
  }, [pairKey]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.series?.length) return null;
    const p = chartPalette();
    const dates = data.series.map((s) => s.date);
    const spreads = data.series.map((s) => s.spread_value);
    const zScores = data.series.map((s) => s.z_score ?? 0);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      legend: {
        data: [`${data.name} 走势`, '滚动 Z-Score'],
        top: 0,
        textStyle: { color: p.INK2, fontSize: 11 },
      },
      grid: { left: 45, right: 45, top: 32, bottom: 24 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      yAxis: [
        {
          type: 'value',
          name: data.type === 'ratio' ? '比率' : '价差',
          scale: true,
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
          name: `${data.name} 走势`,
          type: 'line',
          data: spreads,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
        },
        {
          name: '滚动 Z-Score',
          type: 'line',
          yAxisIndex: 1,
          data: zScores,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: p.WARN, type: 'dotted' },
          markLine: {
            silent: true,
            data: [
              { yAxis: 1.8, lineStyle: { color: p.DOWN, type: 'dashed' } },
              { yAxis: -1.8, lineStyle: { color: p.UP, type: 'dashed' } },
            ],
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(option);

  return (
    <SectionCard
      title="产业链与跨品种比价套利 (Inter-Commodity Spreads)"
      action={
        <div className="flex items-center gap-2">
          <label className="text-2xs text-ink-muted">套利策略配对：</label>
          <select
            value={pairKey}
            onChange={(e) => setPairKey(e.target.value)}
            className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink"
          >
            {PAIR_OPTIONS.map((opt) => (
              <option key={opt.key} value={opt.key}>{opt.label}</option>
            ))}
          </select>
        </div>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
        <div className="rounded border border-hair bg-surface-alt p-2">
          <div className="text-2xs text-ink-muted">当前比价 / 价差点位</div>
          <div className="text-xs font-bold text-brand-600 num mt-0.5">
            {data ? fmtNum(data.current_value) : '--'}
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2">
          <div className="text-2xs text-ink-muted">20日波动均值 / 标准差</div>
          <div className="text-xs font-medium text-ink num mt-0.5">
            {data ? `${fmtNum(data.mean_value)} / ±${fmtNum(data.std_value)}` : '--'}
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2">
          <div className="text-2xs text-ink-muted">均值回归半衰期 (Half-Life)</div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-xs font-bold num text-ink">
              {data?.half_life_stats?.half_life_days ? `${data.half_life_stats.half_life_days} 天` : '非平稳'}
            </span>
            <span className="text-2xs text-ink-muted">
              ({data?.half_life_stats?.is_stationary ? '具有统计套利价值' : '发散漂移风险'})
            </span>
          </div>
        </div>
        <div className="rounded border border-hair bg-surface-alt p-2">
          <div className="text-2xs text-ink-muted">统计 Z-Score / 套利信号</div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-xs font-bold num text-ink">{data?.current_z_score ?? 0}</span>
            <span
              className={`rounded px-1.5 py-0.2 text-2xs font-semibold ${
                data?.arbitrage_signal === 'short_asset1_long_asset2'
                  ? 'bg-down-bg text-down'
                  : data?.arbitrage_signal === 'long_asset1_short_asset2'
                  ? 'bg-up-bg text-up'
                  : 'bg-surface-sunken text-ink-muted'
              }`}
            >
              {data?.arbitrage_signal === 'short_asset1_long_asset2'
                ? `空${data.asset1_name} / 多${data.asset2_name}`
                : data?.arbitrage_signal === 'long_asset1_short_asset2'
                ? `多${data.asset1_name} / 空${data.asset2_name}`
                : '区间中性观望'}
            </span>
          </div>
        </div>
      </div>

      <div className="relative w-full h-[220px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/60 text-xs text-ink-muted z-10">
            正在计算产业链跨品种比价与半衰期...
          </div>
        )}
        <div ref={chartRef} className="w-full h-full" />
      </div>
    </SectionCard>
  );
}
