/**
 * 市场概览共享小件：骨架屏卡片 / 迷你走势线。
 * 骨架屏替代"数据暂不可用"占位（设计稿要求 loading 态为 Skeleton）。
 */
import { useId, useMemo } from 'react';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

/** 卡片骨架（加载中占位，与卡片同高） */
export function SkeletonCard({ lines = 3, className = '' }: {
  lines?: number; className?: string;
}) {
  return (
    <div className={`animate-pulse rounded-lg border border-hair bg-surface p-4 ${className}`}>
      <div className="h-3 w-24 rounded bg-surface-sunken" />
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="mt-3 h-3 rounded bg-surface-sunken"
          style={{ width: `${88 - i * 18}%` }} />
      ))}
    </div>
  );
}

/** 区块加载中（卡片头 + 脉冲骨架体） */
export function SkeletonPanel({ title, height = 220 }: { title: string; height?: number }) {
  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface">
      <div className="border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
      </div>
      <div className="flex-1 p-4">
        <div className="animate-pulse space-y-2.5" style={{ minHeight: height }}>
          <div className="h-3 w-3/4 rounded bg-surface-sunken" />
          <div className="h-3 w-full rounded bg-surface-sunken" />
          <div className="h-3 w-5/6 rounded bg-surface-sunken" />
          <div className="h-3 w-2/3 rounded bg-surface-sunken" />
        </div>
      </div>
    </div>
  );
}

/** 迷你走势线（KPI 卡右侧，红涨绿跌跟随 sign；方向未知时用中性色，**不得替未知表态**） */
export function MiniSpark({ data, up, width = 64, height = 30 }: {
  /** 可空：`undefined` = 方向未知 ⇒ 中性色。**不要**在调用处用 `?? false` 兜底，
   *  那会把「未知」静默染成「跌」（绿），正是本项目反复清理的「替未知表态」模式。 */
  data: number[]; up: boolean | undefined; width?: number; height?: number;
}) {
  /**
   * 全局唯一的渐变 id 后缀。`useId()` 返回值含 `:`，直接拼进 CSS `url(#...)` 会失效，
   * 故先清除。⚠️ 此 hook 必须位于下方 `pts.length < 3` 的提前 return **之前** ——
   * 否则会构成条件调用 hook（该分支一出现/消失即改变 hook 数量），React 会直接崩。
   */
  const uid = useId().replace(/:/g, '');
  const theme = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算（chartPalette 现读 DOM）
  const pal = useMemo(() => chartPalette(), [theme]);
  const pts = data.filter((v) => Number.isFinite(v));
  if (pts.length < 3) return <div style={{ width, height }} />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const step = width / (pts.length - 1);
  const y = (v: number) => height - 3 - ((v - min) / range) * (height - 6);
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  // A 股惯例：红涨绿跌；未知 = 中性色（不得染成涨或跌）
  const color = up == null ? pal.FLAT : up ? pal.UP : pal.DOWN;
  const area = `${d} L${width},${height} L0,${height} Z`;
  const gid = `sg-${uid}`;
  return (
    <svg width={width} height={height} className="shrink-0">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.22} />
          <stop offset="100%" stopColor={color} stopOpacity={0.02} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
