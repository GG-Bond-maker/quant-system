/**
 * AQP UI 组件库（P2-5 视觉重构）：量化终端风格通用小组件。
 * 所有组件纯展示，无业务逻辑，可在任意页面复用。
 */
import type { ReactNode } from 'react';
import { fmtPct } from '@/utils/format';

// ==================== MetricCard ====================
export function MetricCard({ label, value, sub, tone, compact }: {
  label: string; value: string; sub?: string; tone?: string; compact?: boolean;
}) {
  return (
    <div className={`rounded-lg border border-hair bg-white ${compact ? 'px-2 py-1.5' : 'px-4 py-3'}`}>
      <div className="truncate text-2xs text-ink-secondary">{label}</div>
      <div className={`num truncate leading-tight ${compact ? 'mt-0 text-xs font-semibold' : 'mt-0.5 text-lg font-semibold'} ${tone ?? 'text-ink'}`}>
        {value}
      </div>
      {sub && <div className="num mt-0.5 truncate text-2xs text-ink-muted">{sub}</div>}
    </div>
  );
}

// ==================== SectionCard ====================
export function SectionCard({ title, action, children, className, bodyClassName }: {
  title: string; action?: ReactNode; children: ReactNode;
  className?: string; bodyClassName?: string;
}) {
  return (
    <div className={`flex flex-col rounded-lg border border-hair bg-white ${className ?? ''}`}>
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {action}
      </div>
      <div className={`flex-1 p-4 ${bodyClassName ?? ''}`}>{children}</div>
    </div>
  );
}

// ==================== 状态组件 ====================
export function LoadingState({ text: _text = '加载中…' }: { text?: string }) {
  return (
    <div className="flex items-center justify-center py-16 text-sm text-ink-muted">
      <svg className="mr-2 h-4 w-4 animate-spin text-brand-500" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      {_text}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white py-12">
      <p className="text-sm font-medium text-ink-secondary">{title}</p>
      {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function ErrorState({ onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-hair bg-white py-10">
      <p className="text-sm font-medium text-ink-secondary">数据暂时不可用</p>
      <p className="mt-1 text-xs text-ink-muted">数据源暂时没有响应</p>
      {onRetry && (
        <button onClick={onRetry}
          className="mt-3 rounded-md bg-brand-500 px-3 py-1 text-xs text-white hover:bg-brand-600">
          重试
        </button>
      )}
    </div>
  );
}

export function DegradedBadge({ reason }: { reason?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
      <div className="text-sm font-medium text-ink-muted">数据暂不可用</div>
      {reason && <div className="mt-1 text-2xs text-ink-muted/70">数据源暂时离线</div>}
    </div>
  );
}

// ==================== ScoreBadge ====================
export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 0.7 ? 'bg-up-bg text-up' : score >= 0.4 ? 'bg-amber-50 text-amber-700' : 'bg-slate-50 text-ink-secondary';
  return (
    <span className={`num inline-block rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>
      {score.toFixed(4)}
    </span>
  );
}

// ==================== ProbabilityBar ====================
/** value=null 表示后端无法计算（如模型 metrics 缺失），如实显示不可用，不造假值 */
export function ProbabilityBar({ value }: { value: number | null }) {
  if (value == null || Number.isNaN(value)) {
    return (
      <div className="flex items-center gap-1.5">
        <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full w-full rounded-full bg-slate-200" />
        </div>
        <span className="num text-xs text-ink-muted">—</span>
      </div>
    );
  }
  const pct = Math.round(value * 100);
  const color = value >= 0.6 ? 'bg-up' : value >= 0.4 ? 'bg-amber-400' : 'bg-down';
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs text-ink-secondary">{pct}%</span>
    </div>
  );
}

// ==================== FactorBar（因子贡献横向条） ====================
export function FactorBar({ name, value, maxAbs }: {
  name: string; value: number; maxAbs: number;
}) {
  const width = Math.max(4, (Math.abs(value) / (maxAbs || 1)) * 100);
  const pos = value >= 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0 truncate text-ink-secondary" title={name}>{name}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-slate-100">
        <div className={`h-full rounded-sm ${pos ? 'bg-red-400' : 'bg-green-400'}`}
             style={{ width: `${width}%` }} />
      </div>
      <span className={`num w-14 shrink-0 text-right ${pos ? 't-up' : 't-down'}`}>
        {value >= 0 ? '+' : ''}{(value * 100).toFixed(2)}%
      </span>
    </div>
  );
}

// ==================== PctCell（涨跌单元格） ====================
export function PctCell({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  if (value == null || !Number.isFinite(value)) return <span className="t-flat">—</span>;
  const cls = value > 1e-9 ? 't-up' : value < -1e-9 ? 't-down' : 't-flat';
  return <span className={`num ${cls}`}>{fmtPct(value, digits)}</span>;
}

// ==================== RatioBar（横向进度条） ====================
/**
 * 与 FactorBar 几何完全一致的横向进度条（label + bar + value），
 * 但语义是"占比进度"而非"双向贡献"：fill 为 0~100 的绝对填充比例。
 * 用于资金流向（主力净流入强度 / 北向持股比例 / 换手率）等归一化指标。
 */
export function RatioBar({ label, fill, value, tone = 'bg-brand-500', hint }: {
  label: string; fill: number; value: string; tone?: string; hint?: string;
}) {
  const width = Math.min(100, Math.max(0, fill));
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 truncate text-ink-secondary" title={hint ?? label}>{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-slate-100">
        <div className={`h-full rounded-sm transition-all ${tone}`} style={{ width: `${width}%` }} />
      </div>
      <span className="num w-24 shrink-0 text-right text-ink" title={hint}>{value}</span>
    </div>
  );
}

// ==================== SplitBar（内外盘分段条） ====================
/** 把一个 100% 整体拆成两段（如外盘 / 内盘），带两侧数值标注。 */
export function SplitBar({ label, left, right, leftLabel, rightLabel,
  leftTone = 'bg-red-400', rightTone = 'bg-green-400' }: {
  label: string;
  /** 左侧占比 %（右侧自动为 100-left） */
  left: number | null;
  right: number | null;
  leftLabel: string;
  rightLabel: string;
  leftTone?: string;
  rightTone?: string;
}) {
  const l = left == null || !Number.isFinite(left) ? 50 : Math.min(100, Math.max(0, left));
  const r = right == null || !Number.isFinite(right) ? 100 - l : Math.min(100, Math.max(0, right));
  return (
    <div className="text-xs">
      <div className="flex items-center gap-2">
        <span className="w-20 shrink-0 truncate text-ink-secondary">{label}</span>
        <div className="flex h-2 flex-1 overflow-hidden rounded-sm bg-slate-100">
          <div className={`h-full ${leftTone}`} style={{ width: `${l}%` }} />
          <div className={`h-full ${rightTone}`} style={{ width: `${r}%` }} />
        </div>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="w-20 shrink-0" />
        <div className="num flex flex-1 justify-between text-2xs">
          <span className="t-up">{leftLabel}</span>
          <span className="t-down">{rightLabel}</span>
        </div>
      </div>
    </div>
  );
}

// ==================== PanelEmpty（分块降级占位） ====================
/** 面板数据块不可用时的统一占位（"暂无数据"）。 */
export function PanelEmpty({ text = '暂无数据', minH = 'min-h-[72px]' }: {
  text?: string; minH?: string;
}) {
  return (
    <div className={`flex items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50/60 ${minH}`}>
      <span className="text-xs text-ink-muted">{text}</span>
    </div>
  );
}

// ==================== StatRow（标签 / 值 行） ====================
export function StatRow({ label, value, tone }: {
  label: string; value: ReactNode; tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="shrink-0 text-ink-secondary">{label}</span>
      <span className={`num truncate text-right ${tone ?? 'text-ink'}`}>{value}</span>
    </div>
  );
}

// ==================== ViewToggle（视图切换分段控件） ====================
/**
 * 紧凑分段控件，放在 SectionCard 的 action 位，
 * 用于在同一组件内切换展示形态（如 进度条 / 纯数值）。
 */
export function ViewToggle<T extends string>({ value, onChange, options, title }: {
  value: T;
  onChange: (v: T) => void;
  options: ReadonlyArray<{ key: T; label: string }>;
  title?: string;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md bg-slate-100 p-0.5" title={title}>
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)}
          className={`rounded px-2 py-0.5 text-2xs transition-colors ${
            value === o.key
              ? 'bg-white font-medium text-brand-600 shadow-sm'
              : 'text-ink-muted hover:text-ink-secondary'}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ==================== SortHeader（可排序表头） ====================
/**
 * 表头排序按钮：首次点击降序 → 升序 → 取消（回到默认顺序）。
 * 上下箭头用 CSS 边框三角形绘制，不引入图标依赖，与 quant-table 紧凑风格一致。
 */
export function SortHeader({ label, sortKey, sort, onSort, align = 'left' }: {
  label: string;
  sortKey: string;
  /** 当前排序状态；null 表示未启用排序（保持服务端默认顺序） */
  sort: { key: string; dir: 'asc' | 'desc' } | null;
  onSort: (key: string) => void;
  align?: 'left' | 'right' | 'center';
}) {
  const active = sort?.key === sortKey;
  const dir = active ? sort.dir : null;
  return (
    <button onClick={() => onSort(sortKey)}
      title={`按${label}排序（降序 → 升序 → 取消）`}
      className={`group inline-flex items-center gap-1 transition-colors ${
        active ? 'text-brand-600' : 'hover:text-ink'}`}>
      <span className={align === 'right' ? 'order-2' : ''}>{label}</span>
      <span className="flex flex-col leading-none">
        <span className={`h-0 w-0 border-x-[3px] border-b-[3px] border-x-transparent ${
          dir === 'asc' ? 'border-b-brand-600' : 'border-b-slate-300 group-hover:border-b-slate-400'}`} />
        <span className={`mt-0.5 h-0 w-0 border-x-[3px] border-t-[3px] border-x-transparent ${
          dir === 'desc' ? 'border-t-brand-600' : 'border-t-slate-300 group-hover:border-t-slate-400'}`} />
      </span>
    </button>
  );
}

// ==================== Pager（分页条） ====================
/**
 * 列表底部分页条（‹ 1 / N ›）。
 *
 * 只有一页时不渲染，避免出现"1 / 1"的无效控件。
 * 页面只负责传当前页与总页数，翻页后的取数由调用方（通常是服务端分页）自行触发。
 */
export function Pager({ page, totalPages, onChange }: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-center gap-1 border-t border-hair py-2">
      <button onClick={() => onChange(Math.max(1, page - 1))} disabled={page <= 1}
        className="rounded border border-hair px-2 py-0.5 text-xs disabled:opacity-40">‹</button>
      <span className="num px-2 text-xs text-ink-secondary">{page} / {totalPages}</span>
      <button onClick={() => onChange(Math.min(totalPages, page + 1))} disabled={page >= totalPages}
        className="rounded border border-hair px-2 py-0.5 text-xs disabled:opacity-40">›</button>
    </div>
  );
}

// ==================== Modal（详情弹窗） ====================
/** 轻量模态框：Esc / 点击遮罩关闭，body 滚动锁定。 */
export function Modal({ title, sub, open, onClose, children, footer }: {
  title: string; sub?: string; open: boolean; onClose: () => void;
  children: ReactNode; footer?: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className="relative z-10 flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-hair bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-hair px-4 py-3">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold leading-snug text-ink">{title}</h3>
            {sub && <div className="num mt-0.5 text-2xs text-ink-muted">{sub}</div>}
          </div>
          <button onClick={onClose} aria-label="关闭"
            className="-mr-1 -mt-1 rounded p-1 text-ink-muted hover:bg-slate-100 hover:text-ink">
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs leading-relaxed text-ink-secondary">
          {children}
        </div>
        {footer && <div className="border-t border-hair px-4 py-2.5">{footer}</div>}
      </div>
    </div>
  );
}
