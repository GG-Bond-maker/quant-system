/**
 * 热门板块排行榜与申万行业 RRG 相对强弱轮动图（Relative Rotation Graph）。
 * 支持列表排行与四象限轮动视图双向切换。
 * 点击行或散点跳转选股中心对应板块。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { ViewToggle } from '@/components/ui';
import type { SectorsBlock } from '@/types/stock';
import { fmtPct, pctClass } from '@/utils/format';

export default function HotSectorsPanel({
  sectors,
  loading,
}: {
  sectors?: SectorsBlock;
  loading: boolean;
}) {
  const navigate = useNavigate();
  const [view, setView] = useState<'list' | 'rrg'>('list');
  const ok = sectors?.status === 'ok';
  const items = sectors?.items ?? [];
  const maxAbs = Math.max(...items.map((s) => Math.abs(s.pct)), 0.01);

  const theme = useTheme();

  // RRG 相对强弱轮动散点图 option 算力缓存
  const rrgOption = useMemo<echarts.EChartsOption | null>(() => {
    if (view !== 'rrg' || !items.length) return null;
    const p = chartPalette();

    const data = items.map((s) => {
      const x = s.rs_ratio ?? 100;
      const y = s.rs_momentum ?? 100;
      let color = p.INFO;
      if (s.quadrant === 'leading') color = p.UP;
      else if (s.quadrant === 'weakening') color = p.WARN;
      else if (s.quadrant === 'lagging') color = p.DOWN;
      else if (s.quadrant === 'improving') color = p.INFO;

      return {
        name: s.name,
        value: [x, y, s.pct, s.quadrant_cn, s.name],
        itemStyle: { color },
      };
    });

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const val = params.value as any[];
          return `
            <div style="font-size:12px;padding:2px;">
              <b>${val[4]}</b> (${val[3] || '轮动中'})<br/>
              板块涨跌: <b>${val[2] != null ? val[2] + '%' : '—'}</b><br/>
              相对强弱 (RS-Ratio): <b>${val[0]}</b><br/>
              相对动量 (RS-Momentum): <b>${val[1]}</b>
            </div>
          `;
        },
      },
      grid: { left: 16, right: 28, top: 24, bottom: 28, containLabel: true },
      xAxis: {
        type: 'value',
        name: 'RS-Ratio (相对强弱)',
        nameLocation: 'middle',
        nameGap: 18,
        nameTextStyle: { fontSize: 10, color: p.INKM },
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'value',
        name: 'RS-Momentum (相对动量)',
        nameTextStyle: { fontSize: 10, color: p.INKM },
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          type: 'scatter',
          data,
          symbolSize: 12,
          label: {
            show: true,
            formatter: '{@[4]}',
            position: 'top',
            fontSize: 10,
            color: p.INK2,
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: p.HAIR2, type: 'dashed' },
            data: [{ xAxis: 100 }, { yAxis: 100 }],
          },
        },
      ],
    };
  }, [view, items, theme]);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const chartCallbackRef = useChart(rrgOption);
  const setBox = useCallback(
    (el: HTMLDivElement | null) => {
      boxRef.current = el;
      chartCallbackRef(el);
    },
    [chartCallbackRef]
  );

  useEffect(() => {
    if (view !== 'rrg' || !boxRef.current) return;
    const inst = echarts.getInstanceByDom(boxRef.current);
    if (!inst) return;
    const onClick = (params: any) => {
      const name = params.data?.name;
      if (name) {
        navigate(`/screener?board=${encodeURIComponent(name)}`);
      }
    };
    inst.on('click', onClick);
    return () => {
      inst.off('click', onClick);
    };
  }, [view, navigate, rrgOption]);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">热门板块</h2>
          <span className="text-2xs text-ink-muted">
            {view === 'list' ? '按涨跌幅排行' : '申万行业 RRG 轮动'}
          </span>
        </div>
        <ViewToggle
          value={view}
          onChange={setView}
          options={[
            { key: 'list', label: '列表' },
            { key: 'rrg', label: 'RRG轮动' },
          ]}
        />
      </div>

      <div className="min-w-0 flex-1 overflow-x-auto p-0">
        {ok && items.length ? (
          view === 'list' ? (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-hair text-2xs text-ink-muted">
                  <th className="px-4 py-2 text-left font-normal">名称</th>
                  <th className="py-2 text-left font-normal">板块涨幅</th>
                  <th className="py-2 text-right font-normal">涨跌幅</th>
                  <th className="py-2 text-right font-normal">样本数</th>
                  <th className="px-4 py-2 text-right font-normal">领涨股</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr
                    key={s.name}
                    title={`${s.name} · 板块均涨跌幅 ${fmtPct(s.pct)}`}
                    className="cursor-pointer border-b border-hair/50 hover:bg-surface-alt"
                    onClick={() => navigate(`/screener?board=${encodeURIComponent(s.name)}`)}
                  >
                    <td className="max-w-[7rem] truncate px-4 py-2 font-medium text-ink">
                      {s.name}
                    </td>
                    <td className="py-2 pr-3">
                      <div className="relative h-2.5 w-28 overflow-hidden rounded-sm bg-surface-sunken">
                        <div
                          className={`absolute top-0 h-full ${s.pct >= 0 ? 'bg-up left-1/2' : 'bg-down right-1/2'}`}
                          style={{
                            width: `${s.pct === 0 ? 0 : Math.max((Math.abs(s.pct) / maxAbs) * 50, 2.5)}%`,
                          }}
                        />
                        <div className="absolute left-1/2 top-0 h-full w-px bg-hair2" />
                      </div>
                    </td>
                    <td className={`num py-2 text-right font-medium ${pctClass(s.pct)}`}>
                      {fmtPct(s.pct)}
                    </td>
                    <td className="num py-2 text-right text-ink-muted">{s.count ?? '—'}</td>
                    <td
                      className="max-w-[8rem] truncate px-4 py-2 text-right text-ink-secondary"
                      title={s.leader ?? ''}
                    >
                      {s.leader ?? '—'}
                      {s.leader_pct != null && (
                        <span className={`num ml-1 text-2xs ${pctClass(s.leader_pct)}`}>
                          {fmtPct(s.leader_pct)}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex h-full flex-col p-3">
              <div className="mb-1 flex items-center justify-between text-2xs text-ink-muted">
                <span>点击散点可直接联动下钻到选股中心</span>
                <span className="flex items-center gap-2">
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-green-600" />
                    领涨
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-sky-600" />
                    改善
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-yellow-500" />
                    衰退
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-purple-600" />
                    落后
                  </span>
                </span>
              </div>
              <div ref={setBox} className="min-h-[260px] w-full flex-1" />
            </div>
          )
        ) : loading ? (
          <div className="animate-pulse space-y-2.5 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-4 rounded bg-surface-sunken"
                style={{ width: `${92 - i * 7}%` }}
              />
            ))}
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
            {sectors?.reason ?? '板块数据暂不可用'}
          </div>
        )}
      </div>
      {sectors?.note && (
        <div className="border-t border-hair px-4 py-1.5 text-2xs text-ink-muted">
          ※ {sectors.note}
        </div>
      )}
    </div>
  );
}
