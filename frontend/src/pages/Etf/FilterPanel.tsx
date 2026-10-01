/**
 * ETF 筛选器：投资类型 / 跟踪指数 / 管理公司 / 规模区间 / 成立日期区间。
 *
 * 跟踪指数与管理公司下拉项来自后端 options（由当前目录聚合，含中/美/日/韩）。
 * 规模与成立日期为可选条件，留空即不限制。
 */
import { PanelEmpty } from '@/components/ui';

export interface EtfFilters {
  etype: string;
  index: string;
  manager: string;
  min_size: string;
  max_size: string;
  inception_from: string;
  inception_to: string;
  q: string;
}

const TYPES = [
  { key: 'all', label: '全部' },
  { key: '股票型', label: '股票型' },
  { key: '债券型', label: '债券型' },
  { key: '商品型', label: '商品型' },
  { key: '货币型', label: '货币型' },
  { key: '跨境型', label: '跨境型' },
];

// py-1.5 → py-1：本面板有 4 个输入框 + 2 个日期框，每个省 4px 共省 ~24px 纵向
const inputCls = 'w-full rounded border border-hair bg-surface px-1.5 py-1 text-xs text-ink outline-none focus:border-brand-300';
// 行标签：与输入框同排的紧凑标签（2-xs + 固定宽度对齐），替代"标签独占一行"
const labelCls = 'w-14 shrink-0 text-2xs text-ink-secondary';

export default function FilterPanel({ options, value, onChange, onReset }: {
  options: { boards: string[]; types: string[]; indexes: string[]; managers: string[] } | null | undefined;
  value: EtfFilters;
  onChange: (v: EtfFilters) => void;
  onReset: () => void;
}) {
  const set = <K extends keyof EtfFilters>(k: K, v: EtfFilters[K]) =>
    onChange({ ...value, [k]: v });

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-2.5 py-1.5">
        <h3 className="text-xs font-semibold text-ink">ETF筛选器</h3>
        <button onClick={onReset} className="text-2xs text-brand-600 hover:underline">重置</button>
      </div>
      {/* space-y-2.5 → space-y-2：整体收紧一档，配合下面的"标签左置"进一步降高 */}
      <div className="space-y-2 p-2.5">
        {/* 投资类型 */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">投资类型</div>
          <div className="flex flex-wrap gap-1">
            {TYPES.map((t) => (
              <button key={t.key} onClick={() => set('etype', t.key)}
                className={`rounded px-1.5 py-0.5 text-2xs transition-colors ${
                  value.etype === t.key
                    ? 'bg-brand-500 text-white'
                    : 'bg-surface-sunken text-ink-secondary hover:bg-surface-sunken'}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* 跟踪指数 / 管理公司：标签左置同排，省掉两行标签高度（~32px） */}
        <label className="flex items-center gap-1.5">
          <span className={labelCls}>跟踪指数</span>
          <select value={value.index} onChange={(e) => set('index', e.target.value)}
            className={inputCls}>
            <option value="">全部指数</option>
            {(options?.indexes ?? []).map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>

        <label className="flex items-center gap-1.5">
          <span className={labelCls}>管理公司</span>
          <select value={value.manager} onChange={(e) => set('manager', e.target.value)}
            className={inputCls}>
            <option value="">全部公司</option>
            {(options?.managers ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        {/* 规模区间：标签左置，两个输入框各占半宽 */}
        <div className="flex items-center gap-1.5">
          <span className={labelCls}>规模(亿)</span>
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <input type="number" min={0} placeholder="不限" value={value.min_size}
              onChange={(e) => set('min_size', e.target.value)} className={inputCls} />
            <span className="text-2xs text-ink-muted">—</span>
            <input type="number" min={0} placeholder="不限" value={value.max_size}
              onChange={(e) => set('max_size', e.target.value)} className={inputCls} />
          </div>
        </div>

        {/* 成立日期：同上，标签左置 */}
        <div className="flex items-center gap-1.5">
          <span className={labelCls}>成立日期</span>
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <input type="date" value={value.inception_from}
              onChange={(e) => set('inception_from', e.target.value)} className={inputCls} />
            <span className="text-2xs text-ink-muted">—</span>
            <input type="date" value={value.inception_to}
              onChange={(e) => set('inception_to', e.target.value)} className={inputCls} />
          </div>
        </div>
        <p className="text-2xs leading-tight text-ink-muted">
          仅中/美国 ETF 提供成立日期，日韩目录暂无该字段
        </p>

        <div className="flex gap-1.5 pt-0.5">
          <button onClick={() => onChange(value)}
            className="flex-1 rounded bg-brand-500 py-1.5 text-xs text-white hover:bg-brand-600">
            筛选
          </button>
          <button onClick={onReset}
            className="flex-1 rounded border border-hair bg-surface py-1.5 text-xs text-ink-secondary hover:bg-surface-alt">
            重置条件
          </button>
        </div>

        {!options && <PanelEmpty text="筛选项加载中" minH="min-h-[40px]" />}
      </div>
    </div>
  );
}
