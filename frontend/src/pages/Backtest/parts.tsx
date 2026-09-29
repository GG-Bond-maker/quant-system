/**
 * 策略回测子组件（对照设计稿拆分）：
 * ParamBar（顶部参数配置） / ParamsCard（左侧策略参数详情） / SkeletonBlock。
 */
import { useState } from 'react';

/** 运行配置（与 StrategyBacktestRequest 字段一致；费率以 % 展示） */
export interface ParamForm {
  strategyName: string;
  start: string;
  end: string;
  initCash: number;
  commissionPct: number;   // %，如 0.03
  shortMa: number;
  longMa: number;
  trailingStop: number;    // %
  symbolsText: string;     // 逗号/换行分隔
  /** P2-16：参数寻优（目标=夏普；最优参数自动应用） */
  optimize: boolean;
  optShortMa: string;      // 候选值列表，逗号分隔，如 "5,10,20"
  optLongMa: string;
  optTrailing: string;
  /** §4.4 Sprint4：寻优方法与 walk-forward 折外验证 */
  optMethod?: 'grid' | 'ga' | 'optuna';
  walkForward?: boolean;
  wfFolds?: number;
}

/** 逗号/分号分隔的数字列表解析（寻优候选值输入） */
export function parseNumList(s: string): number[] {
  return s.split(/[\s,，;；]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n));
}

/**
 * 默认运行配置。
 *
 * 历史问题（审计 F5）：start/end 曾写死为 '2024-08-28' ~ '2026-08-28' 的固定
 * 窗口——数据一更新就会与本地数据上限错位（超出时后端直接报错），
 * 且审计出具时 end 是未来日期，"回测区间覆盖到今天之后"具有误导性。
 * 现在改为模块加载时按当前日期动态推算起点；结束日期仍需以本地数据
 * 实际上限为准，由 Backtest 页面挂载时用 /datacenter/datasets 校准。
 */
const TODAY = new Date().toISOString().slice(0, 10);
const TWO_YEARS_AGO = new Date(Date.now() - 2 * 365.25 * 86400_000)
  .toISOString().slice(0, 10);

export const DEFAULT_FORM: ParamForm = {
  strategyName: '趋势跟踪策略 v1.0',
  start: TWO_YEARS_AGO,
  end: TODAY,
  initCash: 1_000_000,
  commissionPct: 0.03,
  shortMa: 5,
  longMa: 20,
  trailingStop: 3,
  symbolsText: '000001.SZ, 300750.SZ',
  optimize: false,
  optShortMa: '5,10',
  optLongMa: '20,40',
  optTrailing: '',
};

/** 表单校验：返回错误消息（null=通过）。与后端规则一致。 */
export function validateForm(f: ParamForm): string | null {
  if (!f.strategyName.trim()) return '请填写策略名称';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.start) || !/^\d{4}-\d{2}-\d{2}$/.test(f.end))
    return '回测时间区间格式应为 YYYY-MM-DD';
  if (f.start >= f.end) return '开始时间必须早于结束时间';
  if (!(f.initCash > 0)) return '初始资金必须大于 0';
  if (f.commissionPct / 100 < 0.0001 || f.commissionPct / 100 > 0.01)
    return '佣金费率需在 0.01% ~ 1% 之间';
  if (!Number.isInteger(f.shortMa) || !Number.isInteger(f.longMa)
      || f.shortMa < 2 || f.longMa < 5) return '均线周期需为整数（短≥2，长≥5）';
  if (f.shortMa >= f.longMa) return `短均线周期（${f.shortMa}）必须小于长均线周期（${f.longMa}）`;
  if (f.trailingStop <= 0 || f.trailingStop > 50) return '移动止损百分比需在 0 ~ 50% 之间';
  const syms = f.symbolsText.split(/[\s,，;；]+/).filter(Boolean);
  if (!syms.length) return '请至少填写一个参与股票/ETF';
  if (syms.length > 10) return '参与标的最多 10 只';
  // P2-16：寻优校验（候选值必须为数字且至少一个参数有候选）
  if (f.optimize) {
    const lists = [f.optShortMa, f.optLongMa, f.optTrailing];
    if (lists.every((s) => !s.trim())) return '开启寻优需至少为一个参数填写候选值';
    const parsed: number[][] = [];
    for (const s of lists) {
      if (!s.trim()) continue;
      const raw = s.split(/[\s,，;；]+/).filter(Boolean);
      if (raw.some((v) => !Number.isFinite(Number(v)))) return '寻优候选值需为数字（逗号分隔）';
      parsed.push(raw.map(Number));
    }
    // I-4：条数与笛卡尔积上限必须在前端拦下。后端 grid_search 的组合数守卫
    // （`param_search.py` 的 `max_trials: int = 500`）是在**物化之后**才执行，
    // 单进程部署下 1e6+ 组合会先把 API 进程打死，故此处按同一上限前置校验。
    const MAX_CANDIDATES = 30;
    const MAX_TRIALS = 500;
    for (const v of parsed) {
      if (v.length > MAX_CANDIDATES)
        return `单个参数的候选值最多 ${MAX_CANDIDATES} 个（当前 ${v.length} 个）`;
    }
    const total = parsed.reduce((acc, v) => acc * v.length, 1);
    if (total > MAX_TRIALS)
      return `寻优组合数 ${total} 超过上限 ${MAX_TRIALS}，请缩小参数空间`;
  }
  return null;
}

/** 寻优参数网格（P2-16）：仅在开启且至少一个参数有候选值时返回 */
function buildOptimizeParams(f: ParamForm): Record<string, number[]> | undefined {
  if (!f.optimize) return undefined;
  const grid: Record<string, number[]> = {};
  const shortList = parseNumList(f.optShortMa);
  const longList = parseNumList(f.optLongMa);
  const trailList = parseNumList(f.optTrailing);
  if (shortList.length) grid.short_ma = shortList;
  if (longList.length) grid.long_ma = longList;
  if (trailList.length) grid.trailing_stop_pct = trailList;
  return Object.keys(grid).length ? grid : undefined;
}

export function formToRequest(f: ParamForm) {
  return {
    strategy_name: f.strategyName.trim(),
    start: f.start, end: f.end,
    init_cash: f.initCash,
    commission_rate: f.commissionPct / 100,
    short_ma: f.shortMa, long_ma: f.longMa,
    trailing_stop_pct: f.trailingStop,
    symbols: f.symbolsText.split(/[\s,，;；]+/).filter(Boolean),
    optimize_params: buildOptimizeParams(f),
    optimize_method: (f.optMethod ?? 'grid') as 'grid' | 'ga' | 'optuna',
    walk_forward: f.walkForward ?? false,
    wf_folds: f.wfFolds ?? 3,
  };
}

/** 顶部参数配置条 */
export function ParamBar({ form, onChange, onRun, running, error }: {
  form: ParamForm; onChange: (f: ParamForm) => void;
  onRun: () => void; running: boolean; error: string | null;
}) {
  const inputCls = 'rounded-md border border-hair bg-white px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300 disabled:opacity-60';
  return (
    <div className="rounded-lg border border-hair bg-white px-4 py-3">
      <div className="mb-2.5 text-sm font-semibold text-ink">策略参数配置</div>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-2xs text-ink-secondary">策略名称</span>
          <input value={form.strategyName} disabled={running}
            onChange={(e) => onChange({ ...form, strategyName: e.target.value })}
            className={`${inputCls} w-48`} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs text-ink-secondary">回测时间区间</span>
          <span className="flex items-center gap-1.5">
            <input type="date" value={form.start} disabled={running}
              onChange={(e) => onChange({ ...form, start: e.target.value })} className={inputCls} />
            <span className="text-xs text-ink-muted">至</span>
            <input type="date" value={form.end} disabled={running}
              onChange={(e) => onChange({ ...form, end: e.target.value })} className={inputCls} />
          </span>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs text-ink-secondary">初始资金</span>
          <span className="flex items-center gap-1.5">
            <input type="number" min={1000} step={100000} value={form.initCash} disabled={running}
              onChange={(e) => onChange({ ...form, initCash: Number(e.target.value) })}
              className={`${inputCls} w-32 text-right`} />
            <span className="text-xs text-ink-muted">元</span>
          </span>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-2xs text-ink-secondary">佣金费率</span>
          <span className="flex items-center gap-1">
            <input type="number" min={0.01} max={1} step={0.01} value={form.commissionPct} disabled={running}
              onChange={(e) => onChange({ ...form, commissionPct: Number(e.target.value) })}
              className={`${inputCls} w-20 text-right`} />
            <span className="text-xs text-ink-muted">%</span>
          </span>
        </label>
        <button onClick={onRun} disabled={running}
          className="ml-auto flex items-center gap-1.5 rounded-md bg-brand-500 px-5 py-2 text-xs font-medium text-white
            transition-colors hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60">
          {running ? (
            <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
          {running ? '回测运行中…' : '运行回测'}
        </button>
      </div>
      {error && (
        <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-600">{error}</div>
      )}
    </div>
  );
}

/** 左侧策略参数详情卡 */
export function ParamsCard({ form, onChange, running }: {
  form: ParamForm; onChange: (f: ParamForm) => void; running: boolean;
}) {
  const [showSymbolInput, setShowSymbolInput] = useState(false);
  const inputCls = 'w-full rounded-md border border-hair bg-white px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300 disabled:opacity-60';
  const syms = form.symbolsText.split(/[\s,，;；]+/).filter(Boolean);
  return (
    <div className="flex h-full flex-col rounded-lg border border-hair bg-white p-4">
      <div className="mb-3 text-sm font-semibold text-ink">策略参数详情</div>
      <div className="space-y-3.5">
        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs text-ink-secondary">策略类型</span>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted">趋势</span>
          </div>
          <select className={inputCls} disabled value="ma_cross">
            <option value="ma_cross">趋势跟踪策略（MA 交叉）</option>
          </select>
        </div>
        {[
          { label: '短均线周期', key: 'shortMa' as const, min: 2, max: 60, step: 1 },
          { label: '长均线周期', key: 'longMa' as const, min: 5, max: 250, step: 1 },
          { label: '移动止损百分比', key: 'trailingStop' as const, min: 0.5, max: 50, step: 0.5 },
        ].map((f) => (
          <label key={f.key} className="block">
            <span className="mb-1 block text-xs text-ink-secondary">{f.label}</span>
            <input type="number" min={f.min} max={f.max} step={f.step} disabled={running}
              value={form[f.key]}
              onChange={(e) => onChange({ ...form, [f.key]: Number(e.target.value) })}
              className={`${inputCls} text-left`} />
          </label>
        ))}
        <div>
          <span className="mb-1 block text-xs text-ink-secondary">
            参与股票/ETF
            <span className="ml-1 text-2xs text-ink-muted">（≤10 只，逗号分隔）</span>
          </span>
          {showSymbolInput ? (
            <textarea autoFocus rows={3} disabled={running} value={form.symbolsText}
              onBlur={() => setShowSymbolInput(false)}
              onChange={(e) => onChange({ ...form, symbolsText: e.target.value })}
              className={inputCls} placeholder="如 000001.SZ, 300750.SZ" />
          ) : (
            <button onClick={() => setShowSymbolInput(true)} disabled={running}
              className="w-full rounded-md border border-dashed border-slate-300 bg-slate-50 px-2.5 py-2 text-left text-xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-60">
              {syms.length ? `${syms.join('、')}` : '点击填写参与标的…'}
            </button>
          )}
          {syms.length > 0 && !showSymbolInput && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {syms.map((s) => (
                <span key={s} className="num rounded bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600">{s}</span>
              ))}
            </div>
          )}
        </div>

        {/* 参数寻优（§4.4：grid/ga/optuna(TPE) 三法 + walk-forward 折外验证） */}
        <div className="rounded-md border border-hair bg-slate-50 p-2.5">
          <label className="flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={form.optimize} disabled={running}
              onChange={(e) => onChange({ ...form, optimize: e.target.checked })}
              className="h-3.5 w-3.5 accent-brand-500" />
            <span className="text-xs text-ink-secondary">参数寻优</span>
          </label>
          {form.optimize && (
            <div className="mt-2 space-y-2">
              <div className="flex items-center gap-3">
                <label className="text-2xs text-ink-muted">
                  方法
                  <select value={form.optMethod ?? 'grid'} disabled={running}
                    onChange={(e) => onChange({ ...form, optMethod: e.target.value as 'grid' | 'ga' | 'optuna' })}
                    className={`${inputCls} ml-1 w-20`}>
                    <option value="grid">网格</option>
                    <option value="ga">遗传</option>
                    <option value="optuna">TPE</option>
                  </select>
                </label>
                <label className="flex items-center gap-1 text-2xs text-ink-muted">
                  <input type="checkbox" checked={form.walkForward ?? false} disabled={running}
                    onChange={(e) => onChange({ ...form, walkForward: e.target.checked })}
                    className="h-3 w-3 accent-brand-500" />
                  walk-forward 折外验证
                </label>
                {(form.walkForward ?? false) && (
                  <label className="text-2xs text-ink-muted">
                    折数
                    <input type="number" min={2} max={8} value={form.wfFolds ?? 3} disabled={running}
                      onChange={(e) => onChange({ ...form, wfFolds: Number(e.target.value) })}
                      className={`${inputCls} num ml-1 w-12`} />
                  </label>
                )}
              </div>
              {[
                { label: '短均线候选', key: 'optShortMa' as const, ph: '如 5,10,20' },
                { label: '长均线候选', key: 'optLongMa' as const, ph: '如 20,40,60' },
                { label: '止损候选 %', key: 'optTrailing' as const, ph: '如 2,3,5（留空则不寻优此项）' },
              ].map((f) => (
                <label key={f.key} className="block">
                  <span className="mb-0.5 block text-2xs text-ink-muted">{f.label}</span>
                  <input value={form[f.key]} disabled={running} placeholder={f.ph}
                    onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                    className={`${inputCls} num text-left`} />
                </label>
              ))}
              <p className="text-[10px] leading-relaxed text-ink-muted">
                TPE = Optuna 贝叶斯采样（试验数固定 30，适合大候选空间）；结果含
                Deflated Sharpe（多重试验惩罚）。walk-forward：每折在段内 IS 寻优、
                下一折 OOS 折外验证，overfit_ratio&gt;1.5 提示参数过拟合；寻优报告落
                models/exp/backtest_opt/。
              </p>
            </div>
          )}
        </div>
      </div>

      {/* 策略规则说明（引擎实际执行逻辑） */}
      <div className="mt-auto border-t border-hair pt-3">
        <div className="mb-1.5 text-xs font-semibold text-ink">策略规则</div>
        <ul className="space-y-1.5 text-2xs leading-relaxed text-ink-muted">
          <li>· 短均线上穿长均线（金叉）→ 次日开盘买入</li>
          <li>· 短均线下穿长均线（死叉）或回撤触及移动止损线 → 次日开盘卖出</li>
          <li>· 持仓标的等权分配资金，按 100 股整手撮合</li>
          <li>· 买入扣佣金；卖出另计印花税（0.05%）</li>
          <li>· 行情口径：**优先**本地前复权（QFQ）日线；缺 QFQ 分区的标的会回退
            不复权日线，**实际口径以结果页右上角标注为准**</li>
        </ul>
      </div>
    </div>
  );
}

/** 结果区骨架（运行中占位） */
export function SkeletonBlock({ height = 200 }: { height?: number }) {
  return (
    <div className="animate-pulse rounded-lg border border-hair bg-white p-4" style={{ minHeight: height }}>
      <div className="mb-3 h-3 w-28 rounded bg-slate-100" />
      <div className="h-3 w-3/4 rounded bg-slate-100" />
      <div className="mt-2 h-3 w-1/2 rounded bg-slate-100" />
    </div>
  );
}
