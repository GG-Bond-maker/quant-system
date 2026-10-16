/**
 * 期货中心 · 主力席位持仓龙虎榜 (CotRankCard)。
 *
 * 对标 Wind / 聚宽 核心衍生品机构持仓分析：
 * 1. 监控前 20 会员期货公司多单、空单持仓量及日变动；
 * 2. 测算机构净多头/净空头头寸与主力多空力量比率；
 * 3. 识别中信、国君、永安等战略机构的持仓多空偏好。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { SectionCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import type { CotResult } from '@/types/futures';
import { fmtNum, pctClass } from '@/utils/format';

interface Props {
  symbol?: string;
  onSymbolChange?: (symbol: string) => void;
}

const SYMBOL_OPTIONS = [
  { key: 'IF', label: '沪深300 (IF)' },
  { key: 'IC', label: '中证500 (IC)' },
  { key: 'IM', label: '中证1000 (IM)' },
  { key: 'IH', label: '上证50 (IH)' },
  { key: 'AU', label: '沪金主力 (AU)' },
  { key: 'RB', label: '螺纹钢 (RB)' },
  { key: 'CU', label: '沪铜主力 (CU)' },
];

export default function CotRankCard({ symbol = 'IF', onSymbolChange }: Props) {
  const [data, setData] = useState<CotResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    futuresApi.cot(symbol)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '获取席位龙虎榜失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.records?.length) return null;
    const p = chartPalette();
    // 取前 10 席位做净持仓横向柱状图
    const top10 = data.records.slice(0, 10).reverse();
    const names = top10.map((r) => r.member_name);
    const netVals = top10.map((r) => r.net_position);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const arr = params as Array<{ name: string; value: number }>;
          if (!arr?.length) return '';
          const it = data.records.find((r) => r.member_name === arr[0].name);
          if (!it) return '';
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>${it.member_name} (排名 #${it.rank})</b><br/>
              多单持仓: <b class="num">${fmtNum(it.long_volume)}</b> (增减: ${it.long_change > 0 ? '+' : ''}${it.long_change})<br/>
              空单持仓: <b class="num">${fmtNum(it.short_volume)}</b> (增减: ${it.short_change > 0 ? '+' : ''}${it.short_change})<br/>
              净持仓: <b class="num" style="color:${it.net_position >= 0 ? p.UP : p.DOWN}">${fmtNum(it.net_position)}</b>
            </div>
          `;
        },
      },
      grid: { left: 75, right: 28, top: 12, bottom: 20 },
      xAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
      },
      yAxis: {
        type: 'category',
        data: names,
        axisLabel: { fontSize: 10, color: p.INK },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      series: [
        {
          name: '席位净持仓',
          type: 'bar',
          data: netVals,
          itemStyle: {
            color: (params: any) => ((params.value ?? 0) >= 0 ? withAlpha(p.UP, 0.8) : withAlpha(p.DOWN, 0.8)),
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(chartOption);

  return (
    <SectionCard
      title="主力席位持仓龙虎榜 (COT Analysis)"
      action={
        <div className="flex items-center gap-2">
          <label className="text-2xs text-ink-muted">品种：</label>
          <div className="flex items-center gap-1">
            {SYMBOL_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => onSymbolChange?.(opt.key)}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  symbol === opt.key
                    ? 'bg-brand-600 text-white font-medium'
                    : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
                }`}
              >
                {opt.key}
              </button>
            ))}
          </div>
        </div>
      }
    >
      {loading && (
        <div className="py-12 text-center text-xs text-ink-muted animate-pulse">
          正在加载主力席位持仓龙虎榜...
        </div>
      )}

      {error && (
        <div className="py-8 text-center text-xs text-warn">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          {/* 顶部统计摘要 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
            <div className="rounded border border-hair bg-surface-alt p-2">
              <div className="text-2xs text-ink-muted">前20会员总多单 / 总空单</div>
              <div className="text-xs font-semibold text-ink num mt-0.5">
                {data ? `${fmtNum(data.summary.total_long)} / ${fmtNum(data.summary.total_short)}` : '--'}
              </div>
            </div>
            <div className="rounded border border-hair bg-surface-alt p-2">
              <div className="text-2xs text-ink-muted">主力多空比 (Long / Short)</div>
              <div className="text-xs font-bold text-brand-600 num mt-0.5">
                {data ? `${data.summary.long_short_ratio}x` : '--'}
              </div>
            </div>
            <div className="rounded border border-hair bg-surface-alt p-2">
              <div className="text-2xs text-ink-muted">最大净多头席位</div>
              <div className="text-xs font-medium text-up num mt-0.5">
                {data?.summary.top_bull_member ?? '--'}
              </div>
            </div>
            <div className="rounded border border-hair bg-surface-alt p-2">
              <div className="text-2xs text-ink-muted">最大净空头席位</div>
              <div className="text-xs font-medium text-down num mt-0.5">
                {data?.summary.top_bear_member ?? '--'}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
            {/* 左侧横向净持仓分布图 */}
            <div className="lg:col-span-5 border border-hair rounded p-2 bg-surface">
              <div className="text-2xs font-semibold text-ink-secondary mb-1">
                前 10 席位净持仓敞口分布（绿色净多 / 红色净空）
              </div>
              <div ref={chartRef} className="h-56 w-full" />
            </div>

            {/* 右侧详细明细表 */}
            <div className="lg:col-span-7 overflow-x-auto">
              <table className="quant-table dense w-full">
                <thead>
                  <tr>
                    <th>名次</th>
                    <th>期货公司席位</th>
                    <th className="text-right">多单量</th>
                    <th className="text-right">多单增减</th>
                    <th className="text-right">空单量</th>
                    <th className="text-right">空单增减</th>
                    <th className="text-right">净持仓</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.records?.slice(0, 8).map((r) => (
                    <tr key={r.rank} className="hover:bg-surface-alt">
                      <td className="font-semibold num">#{r.rank}</td>
                      <td className="text-xs font-medium text-ink">{r.member_name}</td>
                      <td className="num text-right text-2xs text-ink">{fmtNum(r.long_volume)}</td>
                      <td className={`num text-right text-2xs ${pctClass(r.long_change)}`}>
                        {r.long_change > 0 ? `+${r.long_change}` : r.long_change}
                      </td>
                      <td className="num text-right text-2xs text-ink">{fmtNum(r.short_volume)}</td>
                      <td className={`num text-right text-2xs ${pctClass(-r.short_change)}`}>
                        {r.short_change > 0 ? `+${r.short_change}` : r.short_change}
                      </td>
                      <td className={`num text-right text-xs font-semibold ${pctClass(r.net_position)}`}>
                        {fmtNum(r.net_position)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </SectionCard>
  );
}
