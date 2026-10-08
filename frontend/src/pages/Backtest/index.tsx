/**
 * 策略回测：趋势跟踪 / Top-K 模型 / 信号分析 三 Tab。
 *
 * ⚠️ 本页**只负责"单个策略"的回测**。曾经在页头放了一条「下游分析」按钮条，
 * 用来跳转 `/portfolio`（组合回测）与 `/capacity`（容量与归因）——那是侧栏入口
 * 被降级后的**临时修复**。2026-10-01 两者已升为一组独立一级导航（侧栏「组合分析」组），
 * 该按钮条**已彻底删除**：一级功能不应寄生在另一个页面的页头，否则既重复又误导层级。
 * 🔴 若日后再次出现"X 页里指向 Y 页的快捷入口"，先问"Y 是不是本该自己占一个导航项"。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import ResearchDisclaimer from '@/components/ResearchDisclaimer';
import { PageHeader } from '@/components/ui';
import { datacenterApi } from '@/api/datacenter';
import { exportApi } from '@/api/export';
import {
  strategyBacktestApi, type OptimizationProgress, type StrategyBacktestResult,
} from '@/api/strategyBacktest';
import {
  DEFAULT_FORM, ParamBar, ParamsCard, SkeletonBlock,
  formToRequest, validateForm, type ParamForm,
} from './parts';
import {
  KpiCards, MonthlyChart, MonteCarloPanel, NavChart, RiskPanel, TradesPanel,
  UnderwaterChart,
} from './resultParts';
import type { MonteCarloResult } from '@/api/strategyBacktest';

import TopKPanel from './TopKPanel';
import SignalAnalysisPanel from './SignalAnalysisPanel';
import OptimizeProgressPanel from './OptimizeProgressPanel';
import ParamHeatmapModal from './ParamHeatmapModal';
import AssetAttributionCard from './AssetAttributionCard';

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
  /**
   * 本次在途请求的**精确请求体**（P8-c）。
   *
   * ⚠️ 必须存快照，不能在轮询/取消时用 `formToRequest(form)` 现算：寻优进行中
   * 用户仍可能改动未禁用的输入（如策略名称），现算会得到**不同的请求体**，
   * 而后端用请求签名定位目标 ⇒ 轮询会查到 0 进度、取消会打在空签名上。
   */
  const inFlightReqRef = useRef<ReturnType<typeof formToRequest> | null>(null);
  const [optProgress, setOptProgress] = useState<OptimizationProgress | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [mcResult, setMcResult] = useState<MonteCarloResult | null>(null);
  const [mcLoading, setMcLoading] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [heatmapOpen, setHeatmapOpen] = useState(false);
  const [exportUniverseTip, setExportUniverseTip] = useState<string | null>(null);

  const handleExportUniverse = useCallback(() => {
    const symbols = form.symbolsText.split(/[\s,，;；]+/).filter(Boolean);
    if (!symbols.length) return;
    try {
      localStorage.setItem('aqp:optimizer_imported_universe', JSON.stringify(symbols));
      setExportUniverseTip(`已成功导出 ${symbols.length} 只标的至组合优化工作台！可在「投资组合」中一键加载。`);
      setTimeout(() => setExportUniverseTip(null), 4000);
    } catch {
      setExportUniverseTip('导出失败，请重试');
    }
  }, [form.symbolsText]);

  const handleRunMonteCarlo = useCallback(async () => {
    if (!result?.nav_curve || result.nav_curve.length < 10) return;
    setMcLoading(true);
    try {
      const returns: number[] = [];
      for (let i = 1; i < result.nav_curve.length; i++) {
        const prev = result.nav_curve[i - 1].strategy;
        const curr = result.nav_curve[i].strategy;
        if (prev > 0) returns.push((curr - prev) / prev);
      }
      const mc = await strategyBacktestApi.monteCarlo({ returns });
      setMcResult(mc);
    } catch {
      // 错误由蒙特卡洛组件内展示或静默
    } finally {
      setMcLoading(false);
    }
  }, [result]);


  const run = useCallback(async (f: ParamForm) => {
    const invalid = validateForm(f);
    if (invalid) { setError(invalid); return; }
    // 新一轮请求前取消在途请求；旧响应不得覆盖新结果
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    cachedForm = f;
    const req = formToRequest(f);
    inFlightReqRef.current = req;
    setRunning(true); setError(null);
    // 新一轮开始 ⇒ 清掉上一轮的进度与"取消中"状态，避免陈旧进度误导
    setOptProgress(null); setCancelling(false);
    try {
      // 开启参数寻优时计算量大，自动走异步作业队列并轮询持久化任务，解耦 600s 同步长连接以杜绝网关 504 超时；
      // 普通非寻优回测则直接同步请求以保持亚秒级极速响应。
      const r = req.optimize_params
        ? await strategyBacktestApi.runAsyncWithPolling(req, { signal: ctrl.signal })
        : await strategyBacktestApi.run(req, ctrl.signal);
      if (ctrl.signal.aborted) return;
      cachedResult = r;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(e instanceof ApiError ? e.message : (e instanceof Error ? e.message : '回测服务暂不可用'));
    } finally {
      if (abortRef.current === ctrl) setRunning(false);
    }
  }, []);

  // 卸载（切 Tab / 离开页面）时取消 120s/600s 在途回测请求
  useEffect(() => () => abortRef.current?.abort(), []);

  /**
   * 寻优进度轮询（P8-c）。只在"寻优 + 运行中"时启动。
   *
   * ⚠️ 三个必须守住的点：
   *  1. **卸载/停止即清定时器**：`running` 变化或组件卸载都要 clearInterval。
   *     本仓有过真实事故 —— FactorStudio 里 setInterval 未清导致轮询泄漏
   *     （`pages/FactorStudio/index.tsx`，审计 C.4）。
   *  2. **轮询失败不打断主请求**：查进度失败只把进度置空，绝不 setError，
   *     否则一个辅助接口的抖动会把"回测还在跑"显示成失败。
   *  3. **用请求体快照**（见 inFlightReqRef 注释）。
   */
  useEffect(() => {
    if (!running || !inFlightReqRef.current?.optimize_params) return;
    const req = inFlightReqRef.current;
    let stopped = false;
    const tick = async () => {
      try {
        const p = await strategyBacktestApi.optStatus(req);
        if (!stopped) setOptProgress(p);
      } catch {
        if (!stopped) setOptProgress(null);   // 辅助接口失败不影响主流程
      }
    };
    void tick();                              // 立刻出一次，避免首屏空等 2s
    const id = setInterval(() => void tick(), 2000);
    return () => { stopped = true; clearInterval(id); };
  }, [running]);

  /**
   * 取消寻优（P8-c）。只对 **grid** 生效：后端取消钩子仅接在网格循环上，
   * ga / optuna 路径忽略它（不引入半成品语义），故这里也不显示按钮。
   */
  const cancelOpt = useCallback(async () => {
    const req = inFlightReqRef.current;
    if (!req?.optimize_params) return;
    setCancelling(true);
    try {
      await strategyBacktestApi.cancel(req);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '取消失败');
      setCancelling(false);
    }
    // 成功时不复位 cancelling：后端要等**当前参数组合跑完**才停，
    // 期间保持"正在停止…"提示，由 run 的 finally（running→false）复位。
  }, []);

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
        <button
          type="button"
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          className="rounded-md border border-hair bg-surface px-2.5 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 transition-colors"
        >
          {sidebarCollapsed ? '展开参数侧栏 ⇥' : '折叠参数侧栏 ⇤'}
        </button>
        <button
          type="button"
          onClick={handleExportUniverse}
          className="rounded-md border border-brand-500/30 bg-brand-50/50 px-2.5 py-1 text-2xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
        >
          🎯 导出标的至组合优化
        </button>
        <button onClick={() => void doExport()} disabled={exporting || !result}
          className="rounded-md border border-hair bg-surface px-2.5 py-1 text-2xs text-ink-secondary
            hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
          {exporting ? '导出中…' : '导出 Excel'}
        </button>
      </div>

      {exportUniverseTip && (
        <div className="rounded-lg bg-success-bg border border-hair px-4 py-2 text-2xs text-success transition-all">
          {exportUniverseTip}
        </div>
      )}

      <ParamBar form={form} onChange={setForm} onRun={() => void run(form)}
        running={running} error={error} />
      {/* 寻优进行中才显示进度/取消面板（P8-c）。run 的 finally 会复位 running。 */}
      {running && inFlightReqRef.current?.optimize_params && (
        <OptimizeProgressPanel
          progress={optProgress}
          cancelling={cancelling}
          onCancel={() => void cancelOpt()}
          // 取消只接在网格寻优上（ga/optuna 是各自独立的循环，未接入）⇒
          // 非 grid 不显示按钮，而不是给一个按不动的按钮。
          canCancel={(inFlightReqRef.current?.optimize_method ?? 'grid') === 'grid'}
        />
      )}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        {!sidebarCollapsed && (
          <div className="min-w-0 xl:col-span-3">
            <ParamsCard form={form} onChange={setForm} running={running} />
          </div>
        )}
        <div className={`flex min-w-0 flex-col gap-3 ${sidebarCollapsed ? 'xl:col-span-9' : 'xl:col-span-6'}`}>
          <div>
            <div className="mb-2 text-sm font-semibold text-ink">策略表现概览</div>
            <KpiCards kpi={kpi} nav={result?.nav_curve ?? []} loading={loading}
              benchmarkSynthetic={result?.benchmark_basis?.synthetic} />
          </div>
          {/* P2-16/§4.4：寻优结果（grid/ga/optuna 摘要 或 walk-forward 折表） */}
          {result?.optimization && result.optimization.method === 'walk_forward' ? (
            <div className="rounded-lg border border-hair bg-surface p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">Walk-Forward 折外验证</span>
                <span className="text-2xs text-ink-muted">
                  {result.optimization.n_folds} 折 · IS 寻优 {result.optimization.search_method} ·
                  {' '}折外均值夏普 <span className="num font-medium">{result.optimization.mean_oos_sharpe}</span> ·
                  {/* ⚠️ overfit_ratio 可为 null（下方已显示 '—'）：不得用 `(x ?? 0) > 1.5` 兜底，
                      否则 null 落进 t-up(红) —— 把"无数据"说成"正常偏红"。null ⇒ 中性。 */}
                  过拟合比 <span className={`num font-medium ${
                    result.optimization.overfit_ratio == null ? 'text-ink-muted'
                      : result.optimization.overfit_ratio > 1.5 ? 'text-danger' : 't-up'}`}>
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
                      <td className={`py-1 text-right ${f.oos_sharpe >= f.is_sharpe ? 't-up' : 't-down'}`}>
                        {f.oos_sharpe}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1.5 text-2xs text-ink-muted">{result.optimization.note}</p>
            </div>
          ) : result?.optimization?.cancelled ? (
            /* P8-c 取消：后端**刻意不返回**半成品的"最优参数"（best_params 为空、
               n_trials=0），因为一个没跑完的网格给不出可信的最优解。
               这里如实说明状态与如何续跑，而不是显示一个空白或假的最优参数。
               ⚠️ 必须放在 best_params 分支**之前**：取消时 best_params 存在但为空对象，
               老分支的 `result.optimization.best_params &&` 对 {} 为真 ⇒ 会渲染一个
               空表头的结果卡（比不显示更糟）。 */
            <div className="rounded-lg border border-hair bg-surface p-3">
              <div className="mb-1.5 text-sm font-semibold text-ink">参数寻优已取消</div>
              <p className="text-2xs text-ink-secondary">
                {result.optimization.reason ?? '寻优已被取消。'}
              </p>
              <p className="mt-1.5 text-2xs text-ink-muted">
                已完成的参数组合<span className="text-ink">已保存进度</span> —— 用相同参数重跑即可从断点继续
                （续跑时会显示「跳过 N 组」）。取消不返回「最优参数」：
                一个没跑完的网格给不出可信的最优解，故此处留空而不是给你一个半成品。
              </p>
            </div>
          ) : result?.optimization && result.optimization.best_params && (
            <div className="rounded-lg border border-hair bg-surface p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-ink">参数寻优结果</span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setHeatmapOpen(true)}
                    className="rounded border border-brand-500/30 bg-brand-50/50 px-2 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
                  >
                    🗺️ 2D 参数敏感性热力图
                  </button>
                  <span className="text-2xs text-ink-muted">
                    {result.optimization.method} · {result.optimization.n_trials ?? '—'} 组试验 · 最优参数已应用
                    {/* P8-c 续跑披露：>0 表示本次跳过了已完成的组合（断点续跑），
                        不能让用户把"只算了 20 组"误读成"网格只有 20 组"。 */}
                    {(result.optimization.n_skipped ?? 0) > 0 && (
                      <> · 断点续跑跳过 {result.optimization.n_skipped} 组</>
                    )}
                  </span>
                </div>
              </div>
              <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-2xs">
                <span className="text-ink-secondary">
                  最优组合：
                  {Object.entries(result.optimization.best_params)
                    .map(([k, v]) => `${k}=${v}`).join('，')}
                </span>
                <span className="text-ink-secondary">
                  最优夏普 <span className="num font-medium t-up">{result.optimization.best_sharpe}</span>
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
                        <td className={`py-1 text-right ${i === 0 ? 'font-semibold t-up' : ''}`}>
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
          {running && !result ? <SkeletonBlock height={180} />
            : <UnderwaterChart underwater={result?.underwater} maxDrawdown={result?.kpi?.max_drawdown} maxDrawdownDuration={result?.risk?.max_drawdown_duration} loading={false} />}
          {running && !result ? <SkeletonBlock height={210} />
            : <MonthlyChart monthly={result?.monthly_returns ?? []} loading={false} />}
          {result?.asset_pnl && Object.keys(result.asset_pnl).length > 0 && (
            <AssetAttributionCard
              assetPnl={result.asset_pnl}
              loading={loading}
              onExportUniverse={(syms) => {
                setExportUniverseTip(`已成功导出 ${syms.length} 只标的至组合优化工作台！可在「投资组合」中一键加载。`);
                setTimeout(() => setExportUniverseTip(null), 4000);
              }}
            />
          )}
        </div>
        <div className="min-w-0 xl:col-span-3">
          <div className="flex h-full flex-col rounded-lg border border-hair bg-surface p-4">
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
      <MonteCarloPanel
        mc={mcResult}
        loading={mcLoading}
        hasReturns={Boolean(result?.nav_curve && result.nav_curve.length > 10)}
        onRun={() => void handleRunMonteCarlo()}
      />
      <ParamHeatmapModal
        isOpen={heatmapOpen}
        onClose={() => setHeatmapOpen(false)}
        trials={result?.optimization?.trials}
        bestParams={result?.optimization?.best_params}
        method={result?.optimization?.method}
      />
    </>
  );
}

export default function Backtest() {
  const [tab, setTab] = useState<TabKey>('ma');

  return (
    <div className="flex min-h-full flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PageHeader title="策略回测" />
        <div className="flex gap-1 rounded-lg border border-hair bg-surface-alt p-0.5">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`rounded-md px-3 py-1 text-2xs font-medium transition-colors ${
                tab === t.key ? 'bg-surface text-brand-600 shadow-sm' : 'text-ink-secondary hover:text-ink'
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
