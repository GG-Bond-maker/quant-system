/**
 * 资金流向面板：行业板块 主力/散户 净流入双向堆叠柱状图 + 右侧净流入榜单。
 * 板块结构缺失时降级为大盘主力/北向汇总数字。
 */
import { useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import type { MarketOverviewData } from '@/types/stock';

function fmtFlow(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(0)}亿`;
}

export default function MoneyFlowPanel({ data, loading }: {
  data: MarketOverviewData | null; loading: boolean;
}) {
  const navigate = useNavigate();
  const theme = useTheme();
  const money = data?.money_flow;
  const ok = money?.status === 'ok';
  const flows = money?.sector_flows ?? [];
  const ref = useRef<HTMLDivElement>(null);

  const option = useMemo(() => {
    if (!flows.length) return null;
    const p = chartPalette();
    const names = flows.map((f) => f.name);
    return ({
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps: unknown) => {
          const arr = ps as Array<{ axisValue: string; seriesName: string; value: number; marker: string }>;
          return `<b>${arr[0]?.axisValue}</b><br/>` + arr
            .map((pt) => `${pt.marker}${pt.seriesName}: ${pt.value > 0 ? '+' : ''}${pt.value?.toFixed(1)} 亿`).join('<br/>');
        },
      },
      legend: { data: ['主力', '散户'], top: 0, textStyle: { fontSize: 10, color: p.INK2 }, itemWidth: 10, itemHeight: 8 },
      grid: { left: 8, right: 8, top: 22, bottom: 2, containLabel: true },
      xAxis: { type: 'category', data: names, axisLabel: { fontSize: 9, color: p.INKM, interval: 0, rotate: 30 }, axisTick: { show: false } },
      yAxis: {
        type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v}亿` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '主力', type: 'bar', stack: 'total', barMaxWidth: 22,
          data: flows.map((f) => f.main_yi),
          // 主力：涨跌语义色（正=红流入 / 负=绿流出）
          itemStyle: { color: (pt: { value: number }) => (pt.value >= 0 ? p.UP : p.DOWN), borderRadius: [2, 2, 0, 0] },
        },
        {
          name: '散户', type: 'bar', stack: 'total', barMaxWidth: 22,
          data: flows.map((f) => f.retail_yi),
          // 散户：同一涨跌语义的**浅色档**（与主力区分，方向一致，不换色相）
          itemStyle: { color: (pt: { value: number }) => (pt.value >= 0 ? withAlpha(p.UP, 0.5) : withAlpha(p.DOWN, 0.5)), borderRadius: [0, 0, 2, 2] },
        },
      ],
    }) as echarts.EChartsOption;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [flows, theme]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); };
  }, [option]);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">资金流向</h2>
        <span className="text-2xs text-ink-muted">全市场 · 行业口径</span>
      </div>
      <div className="min-w-0 flex-1 p-4">
        {ok && flows.length ? (
          <div className="flex flex-wrap items-stretch gap-4">
            <div className="min-w-[280px] flex-[3]">
              <div ref={ref} style={{ height: 230 }} className="w-full" />
            </div>
            <div className="min-w-[190px] flex-1 border-l border-hair pl-4">
              <table className="w-full text-xs">
                <thead><tr className="text-2xs text-ink-muted">
                  <th className="pb-1 text-left font-normal">名称</th>
                  <th className="pb-1 text-right font-normal">金额</th>
                </tr></thead>
                <tbody>
                  {flows.map((f) => (
                    <tr key={f.name} className="cursor-pointer border-t border-hair/60 hover:bg-surface-alt"
                      title={`主力 ${fmtFlow(f.main_yi)} · 散户 ${fmtFlow(f.retail_yi)}`}
                      onClick={() => navigate(`/screener?board=${encodeURIComponent(f.name)}`)}>
                      <td className="max-w-[5rem] truncate py-1 text-ink-secondary">{f.name}</td>
                      <td className={`num py-1 text-right font-medium ${f.total_yi >= 0 ? 't-up' : 't-down'}`}>
                        {fmtFlow(f.total_yi)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : ok ? (
          <div className="flex h-full flex-col justify-center gap-2.5" style={{ minHeight: 200 }}>
            {[
              { label: '主力净流入', val: money?.main_net_today },
              { label: '北向净流入', val: money?.north_net_today },
              { label: '两市成交额', val: data?.heat?.total_amount_yi, amount: true },
            ].map((r) => (
              <div key={r.label} className="flex items-center justify-between rounded-md bg-surface-alt px-3 py-2.5">
                <span className="text-xs text-ink-secondary">{r.label}</span>
                <span className={`num text-base font-semibold ${r.val == null ? 'text-ink-muted' : (r.amount ? 'text-ink' : r.val >= 0 ? 't-up' : 't-down')}`}>
                  {r.amount ? `${r.val?.toFixed(0) ?? '—'} 亿` : fmtFlow(r.val)}
                </span>
              </div>
            ))}
            <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
              ※ 行业板块资金结构实时源暂不可用（东财接口离线），仅展示大盘汇总口径；恢复后自动渲染双向柱状图
              {/* 成交额同属本地降级路径时必须标出口径日，否则会被当成"今日"读数 */}
              {data?.heat?.source === 'local' && data.heat.data_date
                ? `（成交额为 ${data.heat.data_date} 口径${data.heat.latest_date && data.heat.latest_date !== data.heat.data_date ? '，已跳过覆盖不足日' : ''}）`
                : ''}
            </p>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-2" style={{ minHeight: 200 }}>
            <div className="h-40 w-full rounded bg-surface-sunken" />
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
            {money?.reason ?? '资金数据暂不可用'}
          </div>
        )}
      </div>
    </div>
  );
}
