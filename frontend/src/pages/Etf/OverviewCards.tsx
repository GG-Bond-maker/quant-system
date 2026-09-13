/**
 * ETF 市场概览 5 张卡：ETF数量 / 总规模 / 今日平均涨跌幅 / 资金净流入 / 成交额。
 *
 * 「较昨日」对比来自后端 Redis 存档快照；首日运行 prev 为 null 时显示「较昨日 —」占位，
 * 不编造对比数值。图表为内联 SVG（环形 / 面积折线 / 柱状），不引 ECharts。
 */
import type { EtfOverviewDay } from '@/types/etf';

/** 环形进度图（内联 SVG） */
function Donut({ segments, size = 52, thickness = 8 }: {
  segments: Array<[number, string]>; size?: number; thickness?: number;
}) {
  const total = segments.reduce((s, [v]) => s + Math.max(v, 0), 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0 -rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#F1F5F9" strokeWidth={thickness} />
      {total > 0 && segments.map(([v, color], i) => {
        const len = (Math.max(v, 0) / total) * c;
        const el = <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color}
          strokeWidth={thickness} strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} />;
        offset += len;
        return el;
      })}
    </svg>
  );
}

/** 面积折线迷你图：带渐变填充，视觉上更接近参考图 */
function MiniLine({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 84, h = 36;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * (w - 4) + 2).toFixed(1)},${
    (h - 3 - ((v - lo) / span) * (h - 8)).toFixed(1)}`);
  const gid = `g-${color.replace('#', '')}`;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="shrink-0">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={`${pts.join(' ')} ${w - 2},${h - 2} 2,${h - 2}`} fill={`url(#${gid})`} />
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.8"
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/** 柱状迷你图（资金净流入用） */
function MiniBar({ values, color }: { values: number[]; color: string }) {
  if (!values.length) return null;
  const w = 84, h = 36, gap = 3;
  const bw = Math.max(4, (w - gap * (values.length - 1) - 2) / values.length);
  const max = Math.max(...values.map(Math.abs)) || 1;
  const baseline = h / 2;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="shrink-0">
      <line x1="1" y1={baseline} x2={w - 1} y2={baseline} stroke="#E2E8F0" strokeWidth="1" />
      {values.map((v, i) => {
        const bh = Math.max(2, (Math.abs(v) / max) * (h / 2 - 3));
        return <rect key={i} x={(i * (bw + gap) + 2).toFixed(1)} width={bw.toFixed(1)}
          y={v >= 0 ? baseline - bh : baseline} height={bh.toFixed(1)} rx="1"
          fill={v >= 0 ? color : '#F87171'} opacity={v >= 0 ? 0.9 : 0.8} />;
      })}
    </svg>
  );
}

function Card({ label, value, unit, tone, compare, chart, hint }: {
  label: string; value: string; unit?: string; tone?: string;
  compare?: React.ReactNode; chart?: React.ReactNode; hint?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-hair bg-white px-4 py-3.5">
      <div className="min-w-0">
        <div className="truncate text-2xs text-ink-secondary" title={hint}>
          {label}{hint && <span className="ml-1 cursor-help text-ink-muted">ⓘ</span>}
        </div>
        <div className="mt-1 flex items-baseline gap-1">
          <span className={`num text-2xl font-semibold leading-none ${tone ?? 'text-ink'}`}>{value}</span>
          {unit && <span className="text-xs text-ink-secondary">{unit}</span>}
        </div>
        <div className="num mt-1.5 h-4 truncate text-2xs text-ink-muted">{compare ?? ''}</div>
      </div>
      {chart && <div className="shrink-0">{chart}</div>}
    </div>
  );
}

function Compare({ delta, unit = '', digits = 2, format }: {
  delta: number | null; unit?: string; digits?: number; format?: (v: number) => string;
}) {
  if (delta == null || !Number.isFinite(delta)) return <span>较昨日 <span className="text-ink-muted">—</span></span>;
  if (Math.abs(delta) < 1e-9) return <span>较昨日持平</span>;
  return (
    <span>
      较昨日 <span className={`font-medium ${delta > 0 ? 't-up' : 't-down'}`}>
        {delta > 0 ? '+' : ''}{format ? format(delta) : delta.toFixed(digits)}{unit}
      </span>
    </span>
  );
}

export default function OverviewCards({ data }: { data: { today: EtfOverviewDay; prev: EtfOverviewDay | null } | null }) {
  if (!data?.today) {
    return (
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-[96px] animate-pulse rounded-lg border border-hair bg-white" />
        ))}
      </div>
    );
  }
  const t = data.today, p = data.prev;
  const dCount = p ? t.etf_count - p.etf_count : null;
  const dSize = p ? t.total_size_yi - p.total_size_yi : null;
  const dPct = p && t.avg_pct != null && p.avg_pct != null ? t.avg_pct - p.avg_pct : null;
  const dFlow = p ? t.net_inflow_yi - p.net_inflow_yi : null;
  const dAmt = p ? t.amount_yi - p.amount_yi : null;
  /** 总规模以「万亿」呈现（参考图口径），对比差值仍以「亿」计 */
  const sizeWan = (t.total_size_yi / 10000).toFixed(2);

  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-5">
      <Card label="ETF 数量" value={t.etf_count.toLocaleString('zh-CN')} unit="只"
        hint={t.overseas
          ? `仅境内有行情的中国 ETF；另有美股 ${t.overseas.us_count} 只、日韩目录 ${t.overseas.jp_count + t.overseas.kr_count} 只（无行情，未计入）`
          : undefined}
        compare={<Compare delta={dCount} unit=" 只" digits={0} />}
        chart={<Donut segments={[[1, '#2563EB'], [1.4, '#E2E8F0']]} />} />

      <Card label="总规模" value={sizeWan} unit="万亿"
        hint={t.overseas?.note}
        compare={<Compare delta={dSize} unit=" 亿" />}
        chart={<Donut segments={[[1, '#F59E0B'], [1.2, '#FDE68A']]} />} />

      <Card label="今日平均涨跌幅"
        value={`${t.avg_pct != null && t.avg_pct >= 0 ? '+' : ''}${(t.avg_pct ?? 0).toFixed(2)}`} unit="%"
        tone={t.avg_pct == null ? undefined : t.avg_pct >= 0 ? 't-up' : 't-down'}
        compare={<Compare delta={dPct} unit="%" />}
        chart={<MiniLine values={[p?.avg_pct ?? t.avg_pct ?? 0, t.avg_pct ?? 0]} color="#DC2626" />} />

      <Card label="资金净流入"
        value={`${t.net_inflow_yi >= 0 ? '+' : ''}${t.net_inflow_yi.toFixed(2)}`} unit="亿"
        tone={t.net_inflow_yi >= 0 ? 't-up' : 't-down'}
        compare={<Compare delta={dFlow} unit=" 亿" />}
        chart={<MiniBar values={[p?.net_inflow_yi ?? t.net_inflow_yi, t.net_inflow_yi]} color="#16A34A" />} />

      <Card label="成交额" value={t.amount_yi.toFixed(2)} unit="亿"
        compare={<Compare delta={dAmt} unit=" 亿" />}
        chart={<MiniLine values={[p?.amount_yi ?? t.amount_yi, t.amount_yi]} color="#7C3AED" />} />
    </div>
  );
}
