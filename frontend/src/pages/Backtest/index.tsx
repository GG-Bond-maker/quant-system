/**
 * 策略回测：趋势跟踪 / Top-K 模型 / 信号分析 三 Tab。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import ResearchDisclaimer from '@/components/ResearchDisclaimer';
import { datacenterApi } from '@/api/datacenter';
import { exportApi } from '@/api/export';
import {
  strategyBacktestApi, type StrategyBacktestResult,
} from '@/api/strategyBacktest';
import {
  DEFAULT_FORM, ParamBar, ParamsCard, SkeletonBlock,
  formToRequest, validateForm, type ParamForm,
} from './parts';
import {
  KpiCards, MonthlyChart, NavChart, RiskPanel, TradesPanel,
} from './resultParts';
import TopKPanel from './TopKPanel';
import SignalAnalysisPanel from './SignalAnalysisPanel';

type TabKey = 'ma' | 'topk' | 'signal';

/**
 * 复权口径标签：**只显示后端披露的事实**，不在前端替后端担保（审计 B7b F6）。
 *
 * 修复前此处硬编码 "QFQ"，而 `_load_strategy_bars` 在缺 QFQ 分区时会静默回退
 * **不复权**日线 ⇒ 前端把"不纯的口径"展示成纯 QFQ（比不显示更危险）。
 * `price_basis` 缺失（旧缓存 payload / 升级期间的 Redis 命中）时如实显示"口径未知"。
 */
function basisLabel(result: StrategyBacktestResult): string {
  const pb = result.price_basis;
  if (!pb) return '复权口径未知';
  if (pb.basis === 'qfq') return 'QFQ';
  if (pb.basis === 'raw') return '不复权（缺 QFQ 分区）';
  const n = pb.raw_fallback_symbols?.length ?? 0;
  return `QFQ + 不复权混用（${n} 个标的回退）`;
}

/** 基准口径标签：构造基准必须显式标注（审计 P0-4 下半条） */
function benchmarkLabel(result: StrategyBacktestResult): string | null {
  return result.benchmark_basis?.synthetic ? '基准不可得（构造基准）' : null;
}

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'ma', label: '趋势跟踪' },
  { key: 'topk', label: 'Top-K 模型' },
  { key: 'signal', label: '信号分析' },
];

/**
 * I-10：Tab 切换会卸载本组件。结果与表单在模块级保留，
 * 重挂载时直接复用既有结果，不再自动重跑整次回测（120s / 600s 请求）。
 */
let cachedResult: StrategyBacktestResult | null = null;
let cachedForm: ParamForm | null = null;

function MaCrossTab() {
  const [form, setForm] = useState<ParamForm>(cachedForm ?? DEFAULT_FORM);
  const [result, setResult] = useState<StrategyBacktestResult | null>(cachedResult);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async (f: ParamForm) => {
    const invalid = validateForm(f);
    if (invalid) { setError(invalid); return; }
    // 新一轮请求前取消在途请求；旧响应不得覆盖新结果
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    cachedForm = f;
    setRunning(true); setError(null);
    try {
      const r = await strategyBacktestApi.run(formToRequest(f), ctrl.signal);
      if (ctrl.signal.aborted) return;
      cachedResult = r;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(e instanceof ApiError ? e.message : '回测服务暂不可用');
    } finally {
      if (abortRef.current === ctrl) setRunning(false);
    }
  }, []);

  // 卸载（切 Tab / 离开页面）时取消 120s/600s 在途回测请求
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let f = cachedForm ?? DEFAULT_FORM;
      try {
        const ds = await datacenterApi.datasets();
        const bar = ds.items.find((d) => d.dataset === 'daily_bar_qfq')
          ?? ds.items.find((d) => d.dataset === 'daily_bar');
        if (bar?.end && f.end > bar.end) f = { ...f, end: bar.end };
        if (bar?.start && f.start < bar.start) f = { ...f, start: bar.start };
      } catch { /* 后端兜底 */ }
      if (cancelled) return;
      cachedForm = f;
      setForm(f);
      // 已有上次结果（切 Tab 重挂载）⇒ 不自动重跑；仅在首次进入时触发
      if (!cachedResult) void run(f);
    })();
    return () => { cancelled = true; };
  }, [run]);

  const loading = running && !result;
  const kpi = result?.kpi ?? {
    annual_strategy: null, annual_benchmark: null, sharpe: null, max_drawdown: null,
  };
  const [exporting, setExporting] = useState(false);
  const doExport = async () => {
    const invalid = validateForm(form);
    if (invalid) { setError(invalid); return; }
    setExporting(true);
    try {
      await exportApi.strategyBacktest(formToRequest(form));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '导出失败');
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {result && (
          <span className="text-2xs text-ink-muted">
            {result.from_cache ? '缓存' : '实时'} · {basisLabel(result)} · T+1 开盘撮合
            {benchmarkLabel(result) && ` · ${benchmarkLabel(result)}`}
          </span>
        )}
        <button onClick={() => void doExport()} disabled={exporting || !result}
          className="rounded-md border border-hair bg-white px-2.5 py-1 text-2xs text-ink-secondary
            hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
          {exporting ? '导出中…' : '导出 Excel'}
        </button>
      </div>
      <ParamBar form={form} onChange={setForm} onRun={() => void run(form)}
        running={running} error={error} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="min-w-0 xl:col-span-3">
          <ParamsCard form={form} onChange={setForm} running={running} />
        </div>
        <div className="flex min-w-0 flex-col gap-3 xl:col-span-6">
          <div>
            <div className="mb-2 text-sm font-semibold text-ink">策略表现概览</div>
            <KpiCards kpi={kpi} nav={result?.nav_curve ?? []} loading={loading}
              benchmarkSynthetic={result?.benchmark_basis?.synthetic} />
          </div>
          {/* P2-16/§4.4：寻优结果（grid/ga/optuna 摘要 或 walk-forward 折表） */}
          {result?.optimization && result.optimization.method === 'walk_forward' ? (
            <div className="rounded-lg border border-hair bg-white p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">Walk-Forward 折外验证</span>
                <span className="text-2xs text-ink-muted">
                  {result.optimization.n_folds} 折 · IS 寻优 {result.optimization.search_method} ·
                  {' '}折外均值夏普 <span className="num font-medium">{result.optimization.mean_oos_sharpe}</span> ·
                  {/* ⚠️ overfit_ratio 可为 null（下方已显示 '—'）：不得用 `(x ?? 0) > 1.5` 兜底，
                      否则 null 落进 text-up(红) —— 把"无数据"说成"正常偏红"。null ⇒ 中性。 */}
                  过拟合比 <span className={`num font-medium ${
                    result.optimization.overfit_ratio == null ? 'text-ink-muted'
                      : result.optimization.overfit_ratio > 1.5 ? 'text-red-600' : 'text-up'}`}>
                    {result.optimization.overfit_ratio ?? '—'}</span>
                </span>
              </div>
              <table className="w-full text-2xs">
                <thead>
                  <tr className="border-b border-hair text-left text-ink-muted">
                    <th className="py-1 pr-3 font-normal">折</th>
                    <th className="py-1 pr-3 font-normal">IS 窗口（寻优）</th>
                    <th className="py-1 pr-3 font-normal">OOS 窗口（折外）</th>
                    <th className="py-1 pr-3 font-normal">最优参数</th>
                    <th className="py-1 text-right font-normal">IS 夏普</th>
                    <th className="py-1 text-right font-normal">OOS 夏普</th>
                  </tr>
                </thead>
                <tbody className="num text-ink-secondary">
                  {result.optimization.folds?.map((f) => (
                    <tr key={f.fold} className="border-b border-hair last:border-0">
                      <td className="py-1 pr-3">{f.fold}</td>
                      <td className="py-1 pr-3">{f.is_window[0]} ~ {f.is_window[1]}</td>
                      <td className="py-1 pr-3">{f.oos_window[0]} ~ {f.oos_window[1]}</td>
                      <td className="py-1 pr-3 font-mono">
                        {Object.entries(f.best_params).map(([k, v]) => `${k}=${v}`).join('，')}
                      </td>
                      <td className="py-1 text-right">{f.is_sharpe}</td>
                      <td className={`py-1 text-right ${f.oos_sharpe >= f.is_sharpe ? 'text-up' : 'text-down'}`}>
                        {f.oos_sharpe}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1.5 text-2xs text-ink-muted">{result.optimization.note}</p>
            </div>
          ) : result?.optimization && result.optimization.best_params && (
            <div className="rounded-lg border border-hair bg-white p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-ink">参数寻优结果</span>
                <span className="text-2xs text-ink-muted">
                  {result.optimization.method} · {result.optimization.n_trials ?? '—'} 组试验 · 最优参数已应用
                </span>
              </div>
              <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-2xs">
                <span className="text-ink-secondary">
                  最优组合：
                  {Object.entries(result.optimization.best_params)
                    .map(([k, v]) => `${k}=${v}`).join('，')}
                </span>
                <span className="text-ink-secondary">
                  最优夏普 <span className="num font-medium text-up">{result.optimization.best_sharpe}</span>
                </span>
                <span className="text-ink-secondary">
                  Deflated Sharpe{' '}
                  <span className="num font-medium text-ink">
                    {result.optimization.deflated_sharpe ?? '—'}
                  </span>
                  （多重试验惩罚后，低于最优夏普属正常）
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-2xs">
                  <thead>
                    <tr className="border-b border-hair text-left text-ink-muted">
                      <th className="py-1 pr-3 font-normal">#</th>
                      <th className="py-1 pr-3 font-normal">参数组合</th>
                      <th className="py-1 text-right font-normal">夏普</th>
                    </tr>
                  </thead>
                  <tbody className="num text-ink-secondary">
                    {result.optimization.top5?.map((t, i) => (
                      <tr key={i} className="border-b border-hair last:border-0">
                        <td className="py-1 pr-3">{i + 1}</td>
                        <td className="py-1 pr-3">
                          {Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join('，')}
                        </td>
                        <td className={`py-1 text-right ${i === 0 ? 'font-semibold text-up' : ''}`}>
                          {t.objective}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {running && !result ? <SkeletonBlock height={340} />
            : <NavChart nav={result?.nav_curve ?? []} signals={result?.signals ?? []} loading={false} />}
          {running && !result ? <SkeletonBlock height={210} />
            : <MonthlyChart monthly={result?.monthly_returns ?? []} loading={false} />}
        </div>
        <div className="min-w-0 xl:col-span-3">
          <div className="flex h-full flex-col rounded-lg border border-hair bg-white p-4">
            <div className="mb-3 text-sm font-semibold text-ink">交易明细 & 详细指标</div>
            <TradesPanel trades={result?.trades ?? []} loading={loading} />
            <RiskPanel risk={result?.risk ?? null} loading={loading} />
            {/* I-11：流动性口径（含冲击成本是否计入）必须披露，不得静默丢弃 */}
            <p className="mt-3 border-t border-hair pt-2 text-2xs leading-relaxed text-ink-muted">
              流动性/摩擦口径：{result?.liquidity
                ? `${result.liquidity.note}（冲击成本${result.liquidity.impact_cost_included ? '已' : '未'}计入）`
                : result ? '后端未披露' : '运行回测后显示'}
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

export default function Backtest() {
  const [tab, setTab] = useState<TabKey>('ma');

  return (
    <div className="flex min-h-full flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold text-ink">策略回测</h1>
        <div className="flex gap-1 rounded-lg border border-hair bg-slate-50 p-0.5">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`rounded-md px-3 py-1 text-2xs font-medium transition-colors ${
                tab === t.key ? 'bg-white text-brand-600 shadow-sm' : 'text-ink-secondary hover:text-ink'
              }`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'ma' && <MaCrossTab />}
      {tab === 'topk' && <TopKPanel />}
      {tab === 'signal' && <SignalAnalysisPanel />}
      <ResearchDisclaimer kind="backtest" className="text-center" />
    </div>
  );
}
