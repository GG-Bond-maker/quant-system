/**
 * 选股筛选器（机构级多维因子漏斗与策略预设模板）：
 * 1. 核心控制：快照日期 / Top-K / 预测强度 / 最小 Score；
 * 2. 多维因子漏斗：换手活跃度 (Turnover) / 成交额门槛 (Amount) / 涨跌动量区间 (Pct)；
 * 3. 经典量化策略预设：一键套用经典策略模板（强信号精选、动量主升浪、核心流动性、均衡基准）；
 * 4. 杜绝造假：所有漏斗条件与底层截面数据实时联动。
 */
import { useState } from 'react';

export interface ScreenerFilters {
  day: string;
  topK: number;
  minScore: string;
  /** 预测强度筛选键：与后端 signal_strength 取值 weak/neutral/strong 对齐 */
  signal: string;
  /** 多维因子漏斗拓展字段 */
  minTurnover?: string;
  minAmountYi?: string;
  minPct?: string;
  maxPct?: string;
}

const SIGNAL_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'weak', label: '弱信号' },
  { key: 'neutral', label: '中性' },
  { key: 'strong', label: '强信号' },
] as const;

const TOPK_OPTIONS = [10, 20, 50, 100] as const;

const inputCls =
  'w-full rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-brand-300';

export const DEFAULT_FILTERS: ScreenerFilters = {
  day: '',
  topK: 20,
  minScore: '',
  signal: 'all',
  minTurnover: '',
  minAmountYi: '',
  minPct: '',
  maxPct: '',
};

export const STRATEGY_PRESETS = [
  {
    key: 'high_signal',
    name: '强信号精选',
    desc: 'Score≥0.005 · 强信号 · 换手≥1.5%',
    patch: { minScore: '0.005', signal: 'strong', minTurnover: '1.5', minAmountYi: '', minPct: '', maxPct: '' },
  },
  {
    key: 'momentum',
    name: '动量主升浪',
    desc: '涨跌≥0% · 换手≥2.0% · Score≥0.002',
    patch: { minScore: '0.002', signal: 'all', minTurnover: '2.0', minPct: '0', maxPct: '', minAmountYi: '' },
  },
  {
    key: 'core_liq',
    name: '核心流动性',
    desc: '成交额≥3亿 · Top 20 优选',
    patch: { minScore: '', signal: 'all', topK: 20, minAmountYi: '3.0', minTurnover: '', minPct: '', maxPct: '' },
  },
  {
    key: 'balanced',
    name: '均衡基准',
    desc: 'Top 50 · 全市场标准覆盖',
    patch: { minScore: '', signal: 'all', topK: 50, minTurnover: '', minAmountYi: '', minPct: '', maxPct: '' },
  },
] as const;

export default function FilterPanel({
  value,
  onChange,
  onApply,
  onReset,
  loading,
}: {
  value: ScreenerFilters;
  onChange: (v: ScreenerFilters) => void;
  onApply: () => void;
  onReset: () => void;
  loading?: boolean;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const set = <K extends keyof ScreenerFilters>(k: K, v: ScreenerFilters[K]) =>
    onChange({ ...value, [k]: v });

  const applyPreset = (patch: Partial<ScreenerFilters>) => {
    onChange({ ...value, ...patch });
  };

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-3 py-2">
        <h3 className="text-xs font-semibold text-ink">多维因子漏斗筛选</h3>
        <span
          title="多维条件将作为交集因子漏斗，严格按真实量价与模型指标执行筛选"
          className="inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-hair2 text-[9px] text-ink-muted"
        >
          ⓘ
        </span>
      </div>

      <div className="space-y-2.5 p-3">
        {/* 经典策略模板快捷预设 */}
        <div>
          <div className="mb-1 text-2xs font-medium text-ink-secondary">量化策略模板预设</div>
          <div className="grid grid-cols-2 gap-1.5">
            {STRATEGY_PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p.patch)}
                title={p.desc}
                className="rounded border border-hair/80 bg-surface-alt/40 p-1.5 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/30"
              >
                <div className="text-2xs font-semibold text-ink">{p.name}</div>
                <div className="truncate text-[10px] text-ink-muted">{p.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 预测强度 */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">预测强度</div>
          <div className="flex flex-wrap gap-1">
            {SIGNAL_OPTIONS.map((r) => (
              <button
                key={r.key}
                type="button"
                onClick={() => set('signal', r.key)}
                className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                  value.signal === r.key
                    ? 'bg-brand-500 text-white'
                    : 'bg-surface-sunken text-ink-secondary hover:bg-surface-sunken'
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        {/* Top-K */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">榜单容量 Top-K</div>
          <div className="flex flex-wrap gap-1">
            {TOPK_OPTIONS.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => set('topK', k)}
                className={`num rounded px-2 py-0.5 text-2xs transition-colors ${
                  value.topK === k
                    ? 'bg-brand-500 text-white'
                    : 'bg-surface-sunken text-ink-secondary hover:bg-surface-sunken'
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>

        {/* 日期 */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">快照日期</span>
          <input
            type="date"
            value={value.day}
            onChange={(e) => set('day', e.target.value)}
            className={`${inputCls} mt-1`}
          />
          <p className="mt-1 text-2xs text-ink-muted">留空取最新交易日预测快照</p>
        </label>

        {/* 最小 Score */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">最小 Score 门槛</span>
          <input
            type="number"
            step="0.0001"
            min="0"
            placeholder="例如 0.005（留空不限）"
            value={value.minScore}
            onChange={(e) => set('minScore', e.target.value)}
            className={`${inputCls} mt-1`}
          />
        </label>

        {/* 高级多维因子漏斗折叠区 */}
        <div className="border-t border-hair/70 pt-2">
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex w-full items-center justify-between text-2xs text-brand-600 hover:text-brand-700"
          >
            <span>多维量价与流动性漏斗</span>
            <span>{showAdvanced ? '收起 ▲' : '展开 ▼'}</span>
          </button>

          {showAdvanced && (
            <div className="mt-2 space-y-2 rounded bg-surface-alt/30 p-2 text-2xs">
              <label className="block">
                <span className="text-ink-secondary">最低换手率 (%)</span>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  placeholder="例如 1.5（留空不限）"
                  value={value.minTurnover ?? ''}
                  onChange={(e) => set('minTurnover', e.target.value)}
                  className={`${inputCls} mt-0.5`}
                />
              </label>

              <label className="block">
                <span className="text-ink-secondary">最低成交额 (亿元)</span>
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  placeholder="例如 2.0（留空不限）"
                  value={value.minAmountYi ?? ''}
                  onChange={(e) => set('minAmountYi', e.target.value)}
                  className={`${inputCls} mt-0.5`}
                />
              </label>

              <div className="grid grid-cols-2 gap-1.5">
                <label className="block">
                  <span className="text-ink-secondary">涨跌幅下限 (%)</span>
                  <input
                    type="number"
                    step="0.5"
                    placeholder="例如 0"
                    value={value.minPct ?? ''}
                    onChange={(e) => set('minPct', e.target.value)}
                    className={`${inputCls} mt-0.5`}
                  />
                </label>
                <label className="block">
                  <span className="text-ink-secondary">涨跌幅上限 (%)</span>
                  <input
                    type="number"
                    step="0.5"
                    placeholder="例如 10"
                    value={value.maxPct ?? ''}
                    onChange={(e) => set('maxPct', e.target.value)}
                    className={`${inputCls} mt-0.5`}
                  />
                </label>
              </div>
            </div>
          )}
        </div>

        {/* 策略模型展示 */}
        <label className="block">
          <span className="text-2xs text-ink-secondary">基础模型</span>
          <select
            disabled
            value="alpha_basic_v1"
            className={`${inputCls} mt-1 bg-surface-alt text-ink-muted`}
          >
            <option>alpha_basic_v1 (LightGBM 5D)</option>
          </select>
        </label>

        <div className="space-y-1.5 pt-1">
          <button
            type="button"
            onClick={onApply}
            disabled={loading}
            className="w-full rounded bg-brand-500 py-1.5 text-xs text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {loading ? '加载中…' : '应用筛选'}
          </button>
          <button
            type="button"
            onClick={onReset}
            className="w-full rounded border border-hair bg-surface py-1.5 text-xs text-ink-secondary hover:bg-surface-alt"
          >
            重置条件
          </button>
        </div>
      </div>
    </div>
  );
}
