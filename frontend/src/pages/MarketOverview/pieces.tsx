/**
 * 市场概览共享小件：骨架屏卡片 / 迷你走势线。
 * 骨架屏替代"数据暂不可用"占位（设计稿要求 loading 态为 Skeleton）。
 */

/** 卡片骨架（加载中占位，与卡片同高） */
export function SkeletonCard({ lines = 3, className = '' }: {
  lines?: number; className?: string;
}) {
  return (
    <div className={`animate-pulse rounded-lg border border-hair bg-white p-4 ${className}`}>
      <div className="h-3 w-24 rounded bg-slate-100" />
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="mt-3 h-3 rounded bg-slate-100"
          style={{ width: `${88 - i * 18}%` }} />
      ))}
    </div>
  );
}

/** 区块加载中（卡片头 + 脉冲骨架体） */
export function SkeletonPanel({ title, height = 220 }: { title: string; height?: number }) {
  return (
    <div className="flex flex-col rounded-lg border border-hair bg-white">
      <div className="border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
      </div>
      <div className="flex-1 p-4">
        <div className="animate-pulse space-y-2.5" style={{ minHeight: height }}>
          <div className="h-3 w-3/4 rounded bg-slate-100" />
          <div className="h-3 w-full rounded bg-slate-100" />
          <div className="h-3 w-5/6 rounded bg-slate-100" />
          <div className="h-3 w-2/3 rounded bg-slate-100" />
        </div>
      </div>
    </div>
  );
}

/** 迷你走势线（KPI 卡右侧，红涨绿跌跟随 sign） */
export function MiniSpark({ data, up, width = 64, height = 30 }: {
  data: number[]; up: boolean; width?: number; height?: number;
}) {
  const pts = data.filter((v) => Number.isFinite(v));
  if (pts.length < 3) return <div style={{ width, height }} />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const step = width / (pts.length - 1);
  const y = (v: number) => height - 3 - ((v - min) / range) * (height - 6);
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const color = up ? '#EF4444' : '#22C55E'; // A 股惯例：红涨绿跌
  const area = `${d} L${width},${height} L0,${height} Z`;
  const gid = `sg-${up ? 'u' : 'd'}-${pts.length}-${Math.round(min)}`;
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
