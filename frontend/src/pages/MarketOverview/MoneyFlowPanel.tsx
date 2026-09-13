/**
 * 资金流向面板：行业板块 主力/散户 净流入双向堆叠柱状图 + 右侧净流入榜单（带迷你走势）。
 * 板块结构缺失时降级为大盘主力/北向汇总数字。
 */
import { useMemo, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import type { MarketOverviewData } from '@/types/stock';
import { MiniSpark } from './pieces';

function fmtFlow(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(0)}亿`;
}

export default function MoneyFlowPanel({ data, loading }: {
  data: MarketOverviewData | null; loading: boolean;
}) {
  const navigate = useNavigate();
  const money = data?.money_flow;
  const ok = money?.status === 'ok';
  const flows = money?.sector_flows ?? [];
  const ref = useRef<HTMLDivElement>(null);

  const option = useMemo(() => {
    if (!flows.length) return null;
    const names = flows.map((f) => f.name);
    return ({
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps: unknown) => {
          const arr = ps as Array<{ axisValue: string; seriesName: string; value: number; marker: string }>;
          return `<b>${arr[0]?.axisValue}</b><br/>` + arr
            .map((p) => `${p.marker}${p.seriesName}: ${p.value > 0 ? '+' : ''}${p.value?.toFixed(1)} 亿`).join('<br/>');
        },
      },
      legend: { data: ['主力', '散户'], top: 0, textStyle: { fontSize: 10 }, itemWidth: 10, itemHeight: 8 },
      grid: { left: 8, right: 8, top: 22, bottom: 2, containLabel: true },
      xAxis: { type: 'category', data: names, axisLabel: { fontSize: 9, interval: 0, rotate: 30 }, axisTick: { show: false } },
      yAxis: {
        type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => `${v}亿` },
        splitLine: { lineStyle: { color: '#F1F5F9' } },
      },
      series: [
        {
          name: '主力', type: 'bar', stack: 'total', barMaxWidth: 22,
          data: flows.map((f) => f.main_yi),
          itemStyle: { color: (p: { value: number }) => (p.value >= 0 ? '#DC2626' : '#16A34A'), borderRadius: [2, 2, 0, 0] },
        },
        {
          name: '散户', type: 'bar', stack: 'total', barMaxWidth: 22,
          data: flows.map((f) => f.retail_yi),
          itemStyle: { color: (p: { value: number }) => (p.value >= 0 ? '#FCA5A5' : '#86EFAC'), borderRadius: [0, 0, 2, 2] },
        },
      ],
    }) as echarts.EChartsOption;
  }, [flows]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); };
  }, [option]);

  // 同 KpiCards：data 为 daily/rt 浅合并，单块先到时 indices 可能为 undefined
  const indices = data?.indices?.status === 'ok' ? data.indices.items ?? [] : [];
  const spark = indices[0]?.sparkline ?? [];

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
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
                  <th className="pb-1 text-right font-normal">走势</th>
                </tr></thead>
                <tbody>
                  {flows.map((f) => (
                    <tr key={f.name} className="cursor-pointer border-t border-hair/60 hover:bg-slate-50"
                      title={`主力 ${fmtFlow(f.main_yi)} · 散户 ${fmtFlow(f.retail_yi)}`}
                      onClick={() => navigate(`/screener?board=${encodeURIComponent(f.name)}`)}>
                      <td className="max-w-[5rem] truncate py-1 text-ink-secondary">{f.name}</td>
                      <td className={`num py-1 text-right font-medium ${f.total_yi >= 0 ? 't-up' : 't-down'}`}>
                        {fmtFlow(f.total_yi)}
                      </td>
                      <td className="py-1 text-right">
                        <MiniSpark data={spark.slice(-20)} up={f.total_yi >= 0} width={44} height={18} />
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
              <div key={r.label} className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2.5">
                <span className="text-xs text-ink-secondary">{r.label}</span>
                <span className={`num text-base font-semibold ${r.val == null ? 'text-ink-muted' : (r.amount ? 'text-ink' : r.val >= 0 ? 't-up' : 't-down')}`}>
                  {r.amount ? `${r.val?.toFixed(0) ?? '—'} 亿` : fmtFlow(r.val)}
                </span>
              </div>
            ))}
            <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
              ※ 行业板块资金结构实时源暂不可用（东财接口离线），仅展示大盘汇总口径；恢复后自动渲染双向柱状图
            </p>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-2" style={{ minHeight: 200 }}>
            <div className="h-40 w-full rounded bg-slate-100" />
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
