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

const inputCls = 'w-full rounded border border-hair bg-white px-2 py-1 text-xs text-ink outline-none focus:border-brand-300';

export default function FilterPanel({ options, value, onChange, onReset }: {
  options: { boards: string[]; types: string[]; indexes: string[]; managers: string[] } | null | undefined;
  value: EtfFilters;
  onChange: (v: EtfFilters) => void;
  onReset: () => void;
}) {
  const set = <K extends keyof EtfFilters>(k: K, v: EtfFilters[K]) =>
    onChange({ ...value, [k]: v });

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">ETF筛选器</h3>
        <button onClick={onReset} className="text-2xs text-brand-600 hover:underline">重置</button>
      </div>
      <div className="space-y-2.5 p-3">
        {/* 投资类型 */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">投资类型</div>
          <div className="flex flex-wrap gap-1">
            {TYPES.map((t) => (
              <button key={t.key} onClick={() => set('etype', t.key)}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  value.etype === t.key
                    ? 'bg-brand-500 text-white'
                    : 'bg-slate-100 text-ink-secondary hover:bg-slate-200'}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* 跟踪指数 */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">跟踪指数</span>
          <select value={value.index} onChange={(e) => set('index', e.target.value)}
            className={`${inputCls} mt-1`}>
            <option value="">全部指数</option>
            {(options?.indexes ?? []).map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>

        {/* 管理公司 */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">管理公司</span>
          <select value={value.manager} onChange={(e) => set('manager', e.target.value)}
            className={`${inputCls} mt-1`}>
            <option value="">全部公司</option>
            {(options?.managers ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        {/* 规模区间 */}
        <div>
          <span className="text-2xs text-ink-secondary">规模（亿元）</span>
          <div className="mt-1 flex items-center gap-1.5">
            <input type="number" min={0} placeholder="0" value={value.min_size}
              onChange={(e) => set('min_size', e.target.value)} className={inputCls} />
            <span className="text-xs text-ink-muted">—</span>
            <input type="number" min={0} placeholder="10000+" value={value.max_size}
              onChange={(e) => set('max_size', e.target.value)} className={inputCls} />
          </div>
        </div>

        {/* 成立日期 */}
        <div>
          <span className="text-2xs text-ink-secondary">成立日期</span>
          <div className="mt-1 flex items-center gap-1.5">
            <input type="date" value={value.inception_from}
              onChange={(e) => set('inception_from', e.target.value)} className={inputCls} />
            <span className="text-xs text-ink-muted">—</span>
            <input type="date" value={value.inception_to}
              onChange={(e) => set('inception_to', e.target.value)} className={inputCls} />
          </div>
          <p className="mt-1 text-2xs text-ink-muted">
            仅中/美国 ETF 提供成立日期，日韩目录暂无该字段
          </p>
        </div>

        <div className="space-y-1.5 pt-1">
          <button onClick={() => onChange(value)}
            className="w-full rounded bg-brand-500 py-1.5 text-xs text-white hover:bg-brand-600">
            筛选
          </button>
          <button onClick={onReset}
            className="w-full rounded border border-hair bg-white py-1.5 text-xs text-ink-secondary hover:bg-slate-50">
            重置条件
          </button>
        </div>

        {!options && <PanelEmpty text="筛选项加载中" minH="min-h-[48px]" />}
      </div>
    </div>
  );
}
