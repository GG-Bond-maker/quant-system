/**
 * 期货中心 · 期限结构与基差升贴水雷达卡片 (Term Structure & Basis Radar Card)。
 *
 * 理论支持：
 * - 跟踪近月、下月、当季、隔季合约价差结构（Term Structure）；
 * - 自动判别市场结构：Contango (正向/远月升水) vs Backwardation (反向/远月贴水)；
 * - 实时呈现股指/商品期货年化基差率（对冲吃贴水成本与套保基差风险）。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { futuresApi } from '@/api/futures';
import type { TermStructureResult } from '@/types/futures';
import { fmtNum, fmtPct } from '@/utils/format';

interface Props {
  initialSymbol?: string;
}

const SYMBOL_OPTIONS = [
  { key: 'IF', label: '沪深300 (IF)' },
  { key: 'IC', label: '中证500 (IC)' },
  { key: 'IM', label: '中证1000 (IM)' },
  { key: 'IH', label: '上证50 (IH)' },
  { key: 'AU', label: '沪金主力 (AU)' },
  { key: 'RB', label: '螺纹钢 (RB)' },
  { key: 'CU', label: '沪铜主力 (CU)' },
  { key: 'T', label: '10年国债 (T)' },
];

export default function TermStructureCard({ initialSymbol = 'IF' }: Props) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [data, setData] = useState<TermStructureResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  useEffect(() => {
    if (initialSymbol) {
      setSymbol(initialSymbol);
    }
  }, [initialSymbol]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    futuresApi.termStructure(symbol)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '获取期限结构失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.contracts?.length) return null;
    const p = chartPalette();
    const contracts = data.contracts;
    const xData = contracts.map((c) => `${c.label}\n(${c.contract_code})`);
    const prices = contracts.map((c) => c.price);
    const annRates = contracts.map((c) => c.annualized_basis_rate);
    const spot = data.spot_price;

    return {
      grid: { left: 56, right: 56, top: 28, bottom: 36 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{
            seriesName: string;
            name: string;
            value: number;
          }>;
          if (!items?.length) return '';
          const idx = contracts.findIndex((c) => `${c.label}\n(${c.contract_code})` === items[0].name);
          const c = contracts[idx] ?? contracts[0];
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>${c.label} (${c.contract_code})</b><br/>
              期货价格: <b class="num">${fmtNum(c.price)}</b><br/>
              现货参考价: <b class="num">${fmtNum(c.spot_price)}</b><br/>
              基差 (现货-期货): <b class="num">${fmtNum(c.basis)} 点</b><br/>
              年化升贴水率: <b class="num" style="color:${c.annualized_basis_rate >= 0 ? p.UP : p.DOWN}">${fmtPct(c.annualized_basis_rate)}</b><br/>
              距到期日: <b class="num">${c.days_to_expiry} 天</b>
            </div>
          `;
        },
      },
      legend: {
        data: ['合约价格', '年化升贴水率 (%)'],
        top: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      xAxis: {
        type: 'category',
        data: xData,
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: [
        {
          type: 'value',
          name: '价格',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        },
        {
          type: 'value',
          name: '年化基差率 (%)',
          axisLabel: { fontSize: 9, color: p.INKM, formatter: '{value}%' },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '合约价格',
          type: 'line',
          yAxisIndex: 0,
          data: prices,
          smooth: true,
          symbolSize: 8,
          itemStyle: { color: p.BRAND },
          lineStyle: { width: 2.5 },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              {
                yAxis: spot,
                lineStyle: { color: p.INKM, type: 'dashed' },
                label: { formatter: `现货: ${spot}`, position: 'end', fontSize: 9 },
              },
            ],
          },
        },
        {
          name: '年化升贴水率 (%)',
          type: 'bar',
          yAxisIndex: 1,
          data: annRates,
          barWidth: 18,
          itemStyle: {
            color: (params: any) => ((params.value ?? 0) >= 0 ? withAlpha(p.UP, 0.6) : withAlpha(p.DOWN, 0.6)),
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(option);

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface p-3.5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">期限结构与基差雷达</span>
          {data?.structure_type && (
            <span
              className={`rounded px-2 py-0.5 text-2xs font-medium border ${
                data.structure_type.includes('Backwardation')
                  ? 'border-brand-300 bg-brand-50 text-brand-700'
                  : 'border-warn/30 bg-warn-bg text-warn'
              }`}
            >
              {data.structure_type}
            </span>
          )}
        </div>

        {/* 品种切换器 */}
        <div className="flex items-center gap-1">
          {SYMBOL_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setSymbol(opt.key)}
              className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                symbol === opt.key
                  ? 'bg-brand-600 text-white font-medium'
                  : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* 提示与状态 */}
      <div className="mt-2 flex items-center justify-between text-2xs text-ink-muted">
        <span>标的现货价格: <strong className="num text-ink">{data?.spot_price ?? '—'}</strong></span>
        <span>柱形：年化升贴水率 (正为贴水/负为升水) · 折线：合约价格走势</span>
      </div>

      {loading && (
        <div className="flex h-56 items-center justify-center text-xs text-ink-muted animate-pulse">
          正在计算期限结构曲线与基差率...
        </div>
      )}

      {error && (
        <div className="flex h-56 items-center justify-center text-xs text-warn">
          {error}
        </div>
      )}

      {!loading && !error && (
        <div ref={chartRef} className="h-56 w-full" data-testid="term-structure-chart" />
      )}
    </div>
  );
}
