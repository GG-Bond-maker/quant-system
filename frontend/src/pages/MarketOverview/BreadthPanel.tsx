/**
 * 市场涨跌分布面板：红绿方块密度网格（按涨跌幅区间）+ 市场概况迷你网格 + 红绿盘环形图。
 * 悬浮方块显示该区间家数；A 股惯例红=涨 绿=跌 灰=平。
 */
import { useMemo, useRef, useEffect } from 'react';
import * as echarts from '@/lib/echarts';
import type { HeatBlock } from '@/types/stock';
import { fmtNum } from '@/utils/format';

const BUCKET_META: Array<{ key: string; label: string; color: string }> = [
  { key: 'b1', label: '≤-7%', color: '#16A34A' },
  { key: 'b2', label: '-7~-5%', color: '#22C55E' },
  { key: 'b3', label: '-5~-3%', color: '#4ADE80' },
  { key: 'b4', label: '-3~0%', color: '#86EFAC' },
  { key: 'b5', label: '平', color: '#CBD5E1' },
  { key: 'b6', label: '0~3%', color: '#FCA5A5' },
  { key: 'b7', label: '3~5%', color: '#F87171' },
  { key: 'b8', label: '5~7%', color: '#EF4444' },
  { key: 'b9', label: '≥7%', color: '#DC2626' },
];

/** 用区间家数按比例铺 N×M 方块网格（一眼看出多空密度） */
function BlockGrid({ heat }: { heat: HeatBlock }) {
  const cells = useMemo(() => {
    const buckets = heat.buckets;
    if (!buckets) return [];
    const total = Object.values(buckets).reduce((a, b) => a + b, 0);
    if (!total) return [];
    const metaWithCount = BUCKET_META.map((m) => ({ ...m, n: buckets[m.key] ?? 0 }));
    const out: Array<{ color: string; label: string }> = [];
    const TARGET = 240; // 网格块总数（16 列 × 15 行）
    metaWithCount.forEach((m) => {
      const n = Math.round((m.n / total) * TARGET);
      for (let i = 0; i < n; i += 1) out.push({ color: m.color, label: m.label });
    });
    // 按颜色乱序穿插（参考稿的混合密度观感），稳定随机用 index 乘散列
    out.sort((a, b) =>
      ((a.label.charCodeAt(1) * 31 + a.color.charCodeAt(1)) % 97) -
      ((b.label.charCodeAt(1) * 31 + b.color.charCodeAt(1)) % 97));
    return out.slice(0, TARGET);
  }, [heat]);

  return (
    <div className="grid flex-1 grid-cols-16 gap-[3px]" style={{ gridTemplateColumns: 'repeat(16, minmax(0, 1fr))' }}>
      {cells.map((c, i) => (
        <div key={i} title={`${c.label}（${c.color} 区间样本）`}
          className="aspect-square rounded-[2px]" style={{ backgroundColor: c.color, opacity: 0.92 }} />
      ))}
    </div>
  );
}

function Donut({ heat }: { heat: HeatBlock }) {
  const ref = useRef<HTMLDivElement>(null);
  const option = useMemo<echarts.EChartsOption>(() => {
    const up = heat.up ?? 0, down = heat.down ?? 0, flat = heat.flat ?? 0;
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie', radius: ['52%', '78%'], center: ['50%', '50%'],
        label: { show: false }, silent: false,
        data: [
          { name: '红盘(涨)', value: up, itemStyle: { color: '#DC2626' } },
          { name: '绿盘(跌)', value: down, itemStyle: { color: '#16A34A' } },
          { name: '平盘', value: flat, itemStyle: { color: '#CBD5E1' } },
        ],
      }],
    };
  }, [heat]);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    return () => chart.dispose();
  }, [option]);
  return <div ref={ref} style={{ width: 130, height: 130 }} />;
}

export default function BreadthPanel({ heat, loading }: {
  heat?: HeatBlock; loading: boolean;
}) {
  const ok = heat?.status === 'ok';
  // ⚠️ up / down 均为可选字段：不得用 `?? 0` 兜底 —— 家数未知时会被算成 0，
  //    渲染成「红盘 0% / 绿盘 0%」，等于把"未知"说成 0%（判据沿用本组件 :124 的
  //    `n != null && n > 0 ? fmtNum(n, 0) : '—'` 先例）。未知 ⇒ null，渲染为 '—' 且不染色。
  const up = heat?.up ?? null;
  const down = heat?.down ?? null;
  const total = (up ?? 0) + (down ?? 0) + (heat?.flat ?? 0) || 1;
  const upPct = up != null ? Math.round((up / total) * 100) : null;
  const downPct = down != null ? Math.round((down / total) * 100) : null;

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">市场涨跌分布</h2>
        {heat?.source === 'local' && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted">本地样本</span>
        )}
      </div>
      <div className="min-w-0 flex-1 p-4">
        {ok ? (
          <div className="flex flex-wrap items-stretch gap-4">
            {/* 左：标题行 + 方块网格 */}
            <div className="flex min-w-[260px] flex-1 flex-col">
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium text-up">红盘 {fmtNum(heat?.up, 0)}</span>
                <span className="text-ink-muted">/ 绿盘 {fmtNum(heat?.down, 0)}</span>
              </div>
              <BlockGrid heat={heat} />
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-muted">
                {BUCKET_META.map((m) => (
                  <span key={m.key} className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: m.color }} />
                    {m.label}
                  </span>
                ))}
              </div>
            </div>
            {/* 右：市场概况迷你网格 + 环形图 */}
            <div className="flex w-44 shrink-0 flex-col items-center justify-between border-l border-hair pl-4">
              <div className="w-full">
                <div className="mb-1.5 text-xs font-medium text-ink">市场概况</div>
                <div className="grid grid-cols-3 gap-[3px]">
                  {[heat?.limit_up, heat?.up, heat?.flat, heat?.down, heat?.limit_down, 0].map((n, i) => (
                    <div key={i} className={`flex aspect-square items-center justify-center rounded-[3px] text-2xs font-medium ${
                      i === 0 ? 'bg-red-600 text-white' : i === 1 ? 'bg-red-100 text-up'
                        : i === 2 ? 'bg-slate-100 text-ink-muted' : i === 3 ? 'bg-green-100 text-down'
                          : i === 4 ? 'bg-green-600 text-white' : 'bg-slate-50'}`}>
                      {n != null && n > 0 ? fmtNum(n, 0) : '—'}
                    </div>
                  ))}
                </div>
                <div className="mt-2 text-center text-2xs leading-relaxed text-ink-muted">
                  <span className={upPct == null ? 'text-ink-muted' : 'text-up'}>红盘 {upPct == null ? '—' : `${upPct}%`}</span>
                  {' / '}
                  <span className={downPct == null ? 'text-ink-muted' : 'text-down'}>绿盘 {downPct == null ? '—' : `${downPct}%`}</span>
                </div>
              </div>
              <Donut heat={heat} />
            </div>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-2" style={{ minHeight: 200 }}>
            <div className="h-3 w-1/2 rounded bg-slate-100" />
            <div className="h-40 w-full rounded bg-slate-100" />
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-xs text-ink-muted">
            {heat?.reason ?? '涨跌分布暂不可用'}
          </div>
        )}
        {heat?.note && <p className="mt-2 text-2xs text-ink-muted">※ {heat.note}</p>}
      </div>
    </div>
  );
}
