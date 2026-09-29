/**
 * KPI 卡片共用零件（选股中心 `StatsCards` 与 ETF 中心 `OverviewCards` 共用）。
 *
 * ## 为什么要抽出来
 * `Donut` / `Card` / `Compare` 此前在 `pages/Screener/StatsCards.tsx` 与
 * `pages/Etf/OverviewCards.tsx` 里**各写了一份、实现逐行雷同**（仅 `MiniLine`
 * 的渐变 id 前缀不同）。两份实现意味着"改一处忘一处"，且让「卡片视觉标准」
 * 失去单一事实源。此处合并为一份，**视觉零改动**。
 *
 * ## 关于已删除的 `MiniLine` / `MiniBar`
 * 那两个组件（两点折线 / 单柱）**已随本次改造删除**，不在此处保留：
 * 全项目仅上述两个文件使用它们，且用途均已被 `Sparkline`（真实序列）与
 * 「真实构成环」取代。把没有消费方的组件"合并"过来只会留下死代码。
 */
import type { ReactNode } from 'react';

/** 环形进度图（内联 SVG）：`segments` 为 [值, 颜色]，按总和归一化后绘制。 */
export function Donut({ segments, size = 52, thickness = 8 }: {
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

/**
 * KPI 卡壳：左列（标签 + 主值 + 对比行）、右列（图形）。
 *
 * `value` 取 `ReactNode`（不是 `string`）：选股中心的「强信号数量」需要塞
 * `<>{n}<span>/ {pool}</span></>` 这类复合结构，窄化成 `string` 会逼调用方先拼字符串。
 */
export function KpiCard({ label, value, unit, tone, compare, chart, hint }: {
  label: string; value: ReactNode; unit?: string; tone?: string;
  compare?: ReactNode; chart?: ReactNode; hint?: string;
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

/**
 * 「较昨日 X」对比行。
 *
 * 合并了此前两份实现的全部语义（ETF 侧的 `comparable`/`note`/`format` 与选股侧的 `flatText`）：
 * - `comparable === false`：口径不可比（如数据源切换导致的数量变化）⇒ 只显示「—」+ 说明，
 *   **不显示差值数字**，避免把口径变更读成市场变化；
 * - `delta == null` / 非有限值（含 `NaN`）⇒ 显示「—」，**不得**显示"持平"；
 * - `|delta| < 1e-9` ⇒ 真正的零变化，显示"持平"。
 */
export function KpiCompare({ delta, unit = '', digits = 2, format, comparable = true, note, flatText = '较昨日持平' }: {
  delta: number | null; unit?: string; digits?: number;
  /** 自定义格式化（如整数计数用 digits=0 也能覆盖） */
  format?: (v: number) => string;
  /** 口径是否可比（false 时只显示「—」+ 不可比说明） */
  comparable?: boolean;
  /** 不可比原因（挂在 title 上） */
  note?: string;
  /** 零变化的文案（默认「较昨日持平」） */
  flatText?: string;
}) {
  if (!comparable) {
    return (
      <span title={note ?? ''}>
        较昨日 <span className="text-ink-muted">—</span>
        <span className="ml-1 text-ink-muted">（口径不同，不可比）</span>
      </span>
    );
  }
  if (delta == null || !Number.isFinite(delta)) {
    return <span>较昨日 <span className="text-ink-muted">—</span></span>;
  }
  if (Math.abs(delta) < 1e-9) return <span>{flatText}</span>;
  return (
    <span>
      较昨日{' '}
      <span className={`font-medium ${delta > 0 ? 't-up' : 't-down'}`}>
        {delta > 0 ? '+' : ''}{format ? format(delta) : delta.toFixed(digits)}{unit}
      </span>
    </span>
  );
}
