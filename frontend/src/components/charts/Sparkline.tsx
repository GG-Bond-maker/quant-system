/**
 * KPI 卡片的历史趋势迷你图（内联 SVG，不引 ECharts，与卡片其余部分同为纯展示）。
 *
 * ## 绘制判据
 * `series?.enough && series?.comparable` —— 这是本组件采信后端序列的**唯一语义开关**，
 * 与后端 `kpi_series.MetricSeries` 的语义严格一致。任一不满足即渲染
 * 「暂无历史序列」占位，并把 `note` / `basis`（口径）挂到 `title` 上，保证可追溯。
 * 在此之上，前端再用 `MIN_DRAW_POINTS`（≥6，与后端 `MIN_POINTS` 同源）独立兜底，
 * 不依赖后端 `enough` 的诚实性。
 *
 * ## 禁止清单（红线 —— 改动本组件前请逐条对照）
 * 1. **不画"只有 2 个点"的线**：两点折线在视觉上像趋势，实际不含趋势信息；
 *    `prev == null` 时更容易传 `[x, x]` 画出一条贴底平线，看着"有历史且在横走"。
 *    `enough`（≥6 点）已从源头挡掉这种情况；即便如此，前端仍按 `MIN_DRAW_POINTS`
 *    （=6，与后端 `MIN_POINTS` 同源）独立兜底，不依赖后端 `enough` 的诚实性。
 * 2. **不补 0、不插值、不用当前值重复补齐、不合成任何兜底曲线**：数据不足时
 *    只显示占位文案。图形"空着"是可接受的，"画出来但是假的"是红线。
 * 3. **不绘制 `dropped` 里的日期**：被剔除的日子（非交易日 / 分区缺失 /
 *    `pool_size < top_k`）在后端已明确不构成独立观测点，前端不得把它补回曲线上；
 *    `dropped` 的数量在 `title` 里如实披露。
 * 4. 全等序列（真实平线）**居中**绘制：若照搬"归一化到底部"的算法会把一条
 *    真实的水平序列画成贴底直线，容易被读成"接近 0"，故居中。
 */
import { useId, useMemo } from 'react';
import type { MetricSeries } from '@/types/kpi';

/** 不可绘制时的占位文案（与后端 `enough=false` 的语义对应） */
const EMPTY_TEXT = '暂无历史序列';

/**
 * 可绘制的最小真实观测点数。
 * 与后端 `backend/app/data/kpi_series.py` 的 `MIN_POINTS = 6` 同源 —— 语义契约是
 * "≥6 个观测点才算有趋势"。前端**不**完全信任后端 `enough`：即便后端在只有
 * 2~5 个点时误给出 `enough=true`，此处也独立守住下限，绝不画出 2 点折线。
 * ⚠️ 后端若调整 `MIN_POINTS`，此处必须同步修改。
 */
const MIN_DRAW_POINTS = 6;

export default function Sparkline({ series, color, width = 84, height = 36, label }: {
  /** 后端返回的指标序列；null / undefined 表示还没取到 */
  series?: MetricSeries | null;
  /** 线条与渐变填充色（沿用卡片既有配色，不改视觉） */
  color: string;
  width?: number;
  height?: number;
  /** 指标名（用于 `aria-label` 与 `title` 的可读前缀） */
  label?: string;
}) {
  const drawable = Boolean(series?.enough && series?.comparable);

  /**
   * 全局唯一的渐变 id 后缀。`useId()` 的返回值含 `:`，直接拼进 CSS `url(#...)`
   * 会失效，故先清除。hook 必须位于任何提前 return 之前，保证调用顺序稳定。
   */
  const uid = useId().replace(/:/g, '');

  /** 只取真实观测点：丢弃 null/非有限值，并按日期升序（后端已升序，此处防御性重排） */
  const pts = useMemo(() => {
    if (!drawable || !series) return [];
    return [...series.points]
      .filter((p) => p.value != null && Number.isFinite(p.value))
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((p) => ({ date: p.date, value: p.value }));
  }, [drawable, series]);

  /** 悬停说明：口径（basis）+ 窗口 + 被剔除的日期数量，全部如实披露 */
  const title = useMemo(() => {
    const name = label ?? series?.key ?? '历史序列';
    if (!series) return `${name}：${EMPTY_TEXT}（后端未返回序列）`;
    const gaps = series.dropped?.length
      ? `；另有 ${series.dropped.length} 天因数据不足被剔除（未绘制）`
      : '';
    if (pts.length < MIN_DRAW_POINTS) {
      return `${name}：${EMPTY_TEXT}——${series.note ?? series.basis ?? '历史点数不足'}${gaps}`;
    }
    const first = pts[0].date;
    const last = pts[pts.length - 1].date;
    return `${name}：${pts.length} 个观测点（${first} ~ ${last}）；口径：${series.basis}${gaps}`;
  }, [series, pts, label]);

  // 真实观测点少于 MIN_DRAW_POINTS(=6，与后端 MIN_POINTS 同源) ⇒ 不足以构成趋势，
  // 一律走占位，绝不退化成"单点"或"2 点折线"
  if (pts.length < MIN_DRAW_POINTS) {
    return (
      <span title={title} aria-label={title}
        className="inline-flex shrink-0 items-center justify-center rounded border border-dashed border-slate-200 bg-slate-50/60 px-1 text-center text-2xs leading-tight text-ink-muted"
        style={{ width, height }}>
        {EMPTY_TEXT}
      </span>
    );
  }

  const h = height;
  const lo = Math.min(...pts.map((p) => p.value));
  const hi = Math.max(...pts.map((p) => p.value));
  const flat = hi - lo < 1e-12;
  // 全等序列居中（禁止清单第 4 条）；否则按极值归一化到 3px 内边距内
  const y = (v: number) => (flat ? h / 2 : h - 3 - ((v - lo) / (hi - lo)) * (h - 8));
  const xy = pts.map((p, i) => {
    const x = (i / (pts.length - 1)) * (width - 4) + 2;
    return `${x.toFixed(1)},${y(p.value).toFixed(1)}`;
  });
  const gid = `spark-${uid}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}
      className="shrink-0" role="img" aria-label={title}>
      <title>{title}</title>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={`${xy.join(' ')} ${width - 2},${h - 2} 2,${h - 2}`} fill={`url(#${gid})`} />
      <polyline points={xy.join(' ')} fill="none" stroke={color} strokeWidth="1.8"
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
