/**
 * 选股筛选器（对齐 ETF 中心 FilterPanel 模板）：
 * Card 壳（头部 border-b + 重置）、pills 式预测强度筛选、日期 / Top-K / 最小 Score。
 *
 * 板块筛选不在本面板 —— 已上移到页面上方的下划线 Tab 行（与 ETF 中心板块 Tab 一致）。
 * 策略项当前仅 alpha_basic_v1 一个生产模型，禁用展示。
 */

export interface ScreenerFilters {
  day: string;
  topK: number;
  minScore: string;
  /** 预测强度筛选键：与后端 signal_strength 取值 weak/neutral/strong 对齐 */
  signal: string;
}

const SIGNAL_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'weak', label: '弱信号' },
  { key: 'neutral', label: '中性' },
  { key: 'strong', label: '强信号' },
] as const;

const TOPK_OPTIONS = [10, 20, 50, 100] as const;

const inputCls = 'w-full rounded border border-hair bg-white px-2 py-1 text-xs text-ink outline-none focus:border-brand-300';

export const DEFAULT_FILTERS: ScreenerFilters = { day: '', topK: 20, minScore: '', signal: 'all' };

export default function FilterPanel({ value, onChange, onApply, onReset, loading }: {
  value: ScreenerFilters;
  onChange: (v: ScreenerFilters) => void;
  onApply: () => void;
  onReset: () => void;
  loading?: boolean;
}) {
  const set = <K extends keyof ScreenerFilters>(k: K, v: ScreenerFilters[K]) =>
    onChange({ ...value, [k]: v });

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">筛选条件</h3>
        <button onClick={onReset} className="text-2xs text-brand-600 hover:underline">重置</button>
      </div>
      <div className="space-y-2.5 p-3">
        {/* 预测强度（后端字段 signal_strength，取值 weak/neutral/strong，不表示投资风险） */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">预测强度</div>
          <div className="flex flex-wrap gap-1">
            {SIGNAL_OPTIONS.map((r) => (
              <button key={r.key} onClick={() => set('signal', r.key)}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  value.signal === r.key
                    ? 'bg-brand-500 text-white'
                    : 'bg-slate-100 text-ink-secondary hover:bg-slate-200'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
            该指标反映模型预测强度，不代表风险高低。
          </p>
        </div>

        {/* Top-K */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">榜单容量 Top-K</div>
          <div className="flex flex-wrap gap-1">
            {TOPK_OPTIONS.map((k) => (
              <button key={k} onClick={() => set('topK', k)}
                className={`num rounded px-2 py-0.5 text-2xs transition-colors ${
                  value.topK === k
                    ? 'bg-brand-500 text-white'
                    : 'bg-slate-100 text-ink-secondary hover:bg-slate-200'}`}>
                {k}
              </button>
            ))}
          </div>
        </div>

        {/* 日期 */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">快照日期</span>
          <input type="date" value={value.day}
            onChange={(e) => set('day', e.target.value)} className={`${inputCls} mt-1`} />
          <p className="mt-1 text-2xs text-ink-muted">留空取最新交易日预测快照</p>
        </label>

        {/* 最小 Score */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">最小 Score</span>
          <input type="number" step="0.0001" min="0" placeholder="不限" value={value.minScore}
            onChange={(e) => set('minScore', e.target.value)} className={`${inputCls} mt-1`} />
        </label>

        {/* 策略（仅一个生产模型，展示用） */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">策略</span>
          <select disabled value="alpha_basic_v1"
            className={`${inputCls} mt-1 bg-slate-50 text-ink-muted`}>
            <option>alpha_basic_v1</option>
          </select>
          <p className="mt-1 text-2xs text-ink-muted">当前唯一生产模型，不可切换</p>
        </label>

        <div className="space-y-1.5 pt-1">
          <button onClick={onApply} disabled={loading}
            className="w-full rounded bg-brand-500 py-1.5 text-xs text-white hover:bg-brand-600 disabled:opacity-50">
            {loading ? '加载中…' : '应用筛选'}
          </button>
          <button onClick={onReset}
            className="w-full rounded border border-hair bg-white py-1.5 text-xs text-ink-secondary hover:bg-slate-50">
            重置条件
          </button>
        </div>
      </div>
    </div>
  );
}
