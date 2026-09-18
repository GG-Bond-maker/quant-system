/**
 * 选股结果概览卡（6 张），数据来自后端 /screener 的 stats 块。
 *
 * 视觉对齐 ETF 中心 OverviewCards（模板标准）：大卡 px-4 py-3.5、text-2xl 主值、
 * 右侧迷你图（环形 / 面积折线 / 柱状，内联 SVG 不引 ECharts）、hint ⓘ 口径提示。
 * 「较昨日」对比行由后端用前一交易日榜单同口径计算，前端不做任何编造；
 * prev 为 null 时该行显示「较昨日 —」占位。
 */

export interface StatsDay {
  /** 榜单截断后的条目数（= top_k 上限，非股票池总数） */
  total: number;
  /** 股票池总数：universe 过滤 ST/停牌 + 板块筛选后、top_k 截断前的标的数 */
  pool_size: number;
  win_rate: number | null;
  avg_pct: number | null;
  avg_score: number | null;
  strong_signal: number;
  industry_count: number;
  /** 最大行业占比 %（行业集中度，环形图语义） */
  top_industry_ratio: number | null;
}

export interface StatsBlock {
  today: StatsDay;
  prev: StatsDay | null;
  prev_date: string | null;
}

/* ---------------- 迷你图（对齐 ETF OverviewCards） ---------------- */

/** 环形进度图：segments 为 [值, 颜色]，按总和归一化 */
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
        const el = (
          <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color}
            strokeWidth={thickness} strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} />
        );
        offset += len;
        return el;
      })}
    </svg>
  );
}

/** 面积折线迷你图：带渐变填充 */
function MiniLine({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 84, h = 36;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * (w - 4) + 2).toFixed(1)},${
    (h - 3 - ((v - lo) / span) * (h - 8)).toFixed(1)}`);
  const gid = `sc-${color.replace('#', '')}`;
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

/** 柱状迷你图（强信号占比用）：上下柱，基线居中 */
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

/* ---------------- 卡片壳（对齐 ETF OverviewCards） ---------------- */
function Card({ label, value, unit, tone, compare, chart, hint }: {
  label: string; value: React.ReactNode; unit?: string; tone?: string;
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

/** 「较昨日 X」：正值红 / 负值绿 / 持平灰；整数计数项传 digits=0 */
function Compare({ delta, unit = '', digits = 2, flatText = '较昨日持平' }: {
  delta: number | null; unit?: string; digits?: number; flatText?: string;
}) {
  if (delta == null || !Number.isFinite(delta)) return <span>较昨日 <span className="text-ink-muted">—</span></span>;
  if (Math.abs(delta) < 1e-9) return <span>{flatText}</span>;
  const prefix = delta > 0 ? '+' : '';
  return (
    <span>
      较昨日{' '}
      <span className={`font-medium ${delta > 0 ? 't-up' : 't-down'}`}>
        {prefix}{delta.toFixed(digits)}{unit}
      </span>
    </span>
  );
}

/* ---------------- 主组件 ---------------- */
export default function StatsCards({ stats }: { stats: StatsBlock | null }) {
  const t = stats?.today;
  const p = stats?.prev ?? null;
  if (!t) {
    return (
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[96px] animate-pulse rounded-lg border border-hair bg-white" />
        ))}
      </div>
    );
  }

  // 「股票数量」与「较昨日」对比基于 pool_size（截断前的股票池数），
  // 而非 total（= top_k 截断后的榜单条目数，恒为 top_k 上限）。
  const dPool = p ? t.pool_size - p.pool_size : null;
  const dWin = p && t.win_rate != null && p.win_rate != null ? t.win_rate - p.win_rate : null;
  const dPct = p && t.avg_pct != null && p.avg_pct != null ? t.avg_pct - p.avg_pct : null;
  const dScore = p && t.avg_score != null && p.avg_score != null ? t.avg_score - p.avg_score : null;
  const dInd = p ? t.industry_count - p.industry_count : null;

  // 强信号占比：分母用 pool_size（更真实的池子规模）。
  const highRatio = t.pool_size ? (t.strong_signal / t.pool_size) * 100 : 0;

  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
      {/* 股票数量（当前股票池，top_k 截断前的标的数） */}
      <Card label="股票数量" value={t.pool_size.toLocaleString('zh-CN')} unit="只"
        hint="当日有预测快照、进入 Alpha 榜前（top_k 截断前）的标的数；已剔除 ST/停牌，按当前板块筛选"
        compare={<Compare delta={dPool} unit="" digits={0} />}
        chart={<Donut segments={[[t.total, '#2563EB'], [Math.max(t.pool_size - t.total, 0), '#E2E8F0']]} />} />

      {/* 今日胜率 */}
      <Card label="今日胜率"
        value={t.win_rate != null ? t.win_rate.toFixed(2) : '—'} unit="%"
        tone={t.win_rate != null && t.win_rate >= 50 ? 't-up' : 't-down'}
        hint="榜单内标的当日实际收盘上涨的比例（A股口径：红涨绿跌）"
        compare={<Compare delta={dWin} unit="%" />}
        chart={<Donut segments={[
          [t.win_rate ?? 0, '#DC2626'],
          [100 - (t.win_rate ?? 0), '#16A34A'],
        ]} />} />

      {/* 平均涨跌幅 */}
      <Card label="平均涨跌幅"
        value={t.avg_pct != null ? `${t.avg_pct >= 0 ? '+' : ''}${t.avg_pct.toFixed(2)}` : '—'} unit="%"
        tone={t.avg_pct == null ? undefined : t.avg_pct >= 0 ? 't-up' : 't-down'}
        compare={<Compare delta={dPct} unit="%" />}
        chart={<MiniLine values={[p?.avg_pct ?? t.avg_pct ?? 0, t.avg_pct ?? 0]} color="#DC2626" />} />

      {/* 平均 Score */}
      <Card label="平均 Score"
        value={t.avg_score != null ? t.avg_score.toFixed(4) : '—'}
        hint="alpha_basic_v1 模型预测的未来收益（小数口径，×100 为百分比预期）"
        compare={<Compare delta={dScore} digits={4} />}
        chart={<MiniLine values={[p?.avg_score ?? t.avg_score ?? 0, t.avg_score ?? 0]} color="#2563EB" />} />

      {/* 强信号数量 */}
      <Card label="强信号数量"
        value={<>{t.strong_signal}<span className="text-base font-normal text-ink-muted"> / {t.pool_size}</span></>}
        hint="strong（模型预测强度最强）的标的数，反映模型预测强度，不代表投资风险；分母为股票池总数（pool_size）"
        compare={<span>占比 <span className="num font-medium t-up">{highRatio.toFixed(0)}%</span></span>}
        chart={<MiniBar values={[highRatio]} color="#F87171" />} />

      {/* 覆盖行业 */}
      <Card label="覆盖行业"
        value={t.industry_count.toLocaleString('zh-CN')} unit="个"
        hint={`环形图为最大行业的集中度（${t.top_industry_ratio != null ? `${t.top_industry_ratio.toFixed(0)}%` : '—'}）`}
        compare={<Compare delta={dInd} digits={0} />}
        chart={<Donut segments={t.top_industry_ratio != null
          ? [[t.top_industry_ratio, '#10B981'], [100 - t.top_industry_ratio, '#E2E8F0']]
          : []} />} />
    </div>
  );
}
