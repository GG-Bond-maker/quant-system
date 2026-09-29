/**
 * 策略研究工作台（四象限）。
 *
 * 全部数据来自 /api/v1/research/* 真实计算：
 *   左上 因子研发中心：features 截面 Rank IC / 相关矩阵 / 分层净值
 *   右上 MLOps 建模中心：model_registry 实验 / Purged CV fold / 模型 gain 重要性
 *   左下 极致组合与风控：LW+RMT 协方差优化器 + 风格暴露前后对比
 *   右下 策略引擎进阶：日频执行冲击模拟（真实 VWAP 口径）/ 历史重演压力测试
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { researchApi } from '@/api/research';
import { SectionCard, LoadingState } from '@/components/ui';
import { ERR } from '@/types/api';
import {
  CorrHeatmap, CvGantt, ExposureChart, IcirPanel, ImpactChart,
  ImportancePanel, QuantileChart, StressTable,
} from './parts';

const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';
const switchCls = 'relative h-4 w-7 rounded-full transition-colors';

function Switch({ on, onChange, label }: {
  on: boolean; onChange: (v: boolean) => void; label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between text-2xs text-ink-secondary">
      {label}
      <button type="button" role="switch" aria-checked={on}
              onClick={() => onChange(!on)}
              className={`${switchCls} ${on ? 'bg-brand-500' : 'bg-slate-300'}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${on ? 'left-3.5' : 'left-0.5'}`} />
      </button>
    </label>
  );
}

const DEFAULT_ASSETS = [
  { code: '000001.SZ', weight: 0.4 },
  { code: '300750.SZ', weight: 0.3 },
  { code: '000002.SZ', weight: 0.3 },
];
const DEFAULT_FACTORS = ['ret_5', 'vol_20', 'rsi_14', 'skew_ret_20', 'ma_gap_20', 'v_rank_20'];

type ResearchSection = 'factor' | 'ml' | 'portfolio' | 'execution';
type SectionStatus = Record<ResearchSection, { loading: boolean; error: string | null }>;

const INITIAL_SECTION_STATUS: SectionStatus = {
  factor: { loading: false, error: null },
  ml: { loading: false, error: null },
  portfolio: { loading: false, error: null },
  execution: { loading: false, error: null },
};

function formatSectionError(error: unknown): string {
  if (
    error instanceof ApiError &&
    (error.code === ERR.RATE_LIMITED || error.code === ERR.PIPELINE_BUSY)
  ) {
    return '计算资源繁忙，请稍后重试';
  }
  return error instanceof ApiError ? error.message : '研究服务暂不可用，请稍后重试';
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === 'CanceledError' || error.name === 'AbortError');
}

/**
 * 研究接口的后端计算槽只有两个。所有昂贵请求进入此页面级队列，避免首次渲染
 * 和用户重试相互争抢而触发 40103；已取消的排队任务不会启动。
 */
class ComputeQueue {
  private active: number = 0;
  private readonly pending: Array<() => void> = [];

  enqueue<T>(work: () => Promise<T>, signal: AbortSignal): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        if (signal.aborted) {
          reject(new DOMException('请求已取消', 'AbortError'));
          this.drain();
          return;
        }
        this.active += 1;
        void work().then(resolve, reject).finally(() => {
          this.active -= 1;
          this.drain();
        });
      };
      const onAbort = () => {
        const index = this.pending.indexOf(start);
        if (index >= 0) {
          this.pending.splice(index, 1);
          reject(new DOMException('请求已取消', 'AbortError'));
        }
      };
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) {
        reject(new DOMException('请求已取消', 'AbortError'));
      } else if (this.active < 2) {
        start();
      } else {
        this.pending.push(start);
      }
    });
  }

  private drain(): void {
    while (this.active < 2 && this.pending.length > 0) {
      this.pending.shift()?.();
    }
  }
}

export default function ResearchPage() {
  const [err, setErr] = useState<string | null>(null);
  const [sectionStatus, setSectionStatus] = useState<SectionStatus>(INITIAL_SECTION_STATUS);
  const mountedRef = useRef<boolean>(false);
  const computeQueueRef = useRef<ComputeQueue>(new ComputeQueue());
  const interactiveControllersRef = useRef<Set<AbortController>>(new Set());

  /* ---- 左上：因子研发 ---- */
  const [overview, setOverview] = useState<Awaited<ReturnType<typeof researchApi.overview>> | null>(null);
  const [factors, setFactors] = useState<string[]>(DEFAULT_FACTORS);
  const [availableFactors, setAvailableFactors] = useState<string[]>(DEFAULT_FACTORS);
  const [horizon, setHorizon] = useState(5);
  const [neutralize, setNeutralize] = useState(false);
  const [icirRows, setIcirRows] = useState<Awaited<ReturnType<typeof researchApi.factorIcir>>['rows']>([]);
  const [icirSel, setIcirSel] = useState<string | null>(null);
  const [corr, setCorr] = useState<Awaited<ReturnType<typeof researchApi.factorCorr>> | null>(null);
  const [quantile, setQuantile] = useState<Awaited<ReturnType<typeof researchApi.factorQuantile>> | null>(null);
  const [quantileFactor, setQuantileFactor] = useState('ret_5');

  /* ---- 右上：MLOps ---- */
  const [experiments, setExperiments] = useState<Awaited<ReturnType<typeof researchApi.experiments>>>([]);
  const [labYearly, setLabYearly] = useState<Awaited<ReturnType<typeof researchApi.labYearly>>>([]);
  /** 分年稳定性面板独立错误态：后端实测 ~19s，失败只降级本块，不点亮整页错误条。 */
  const [labYearlyError, setLabYearlyError] = useState<string | null>(null);
  const [cvParams, setCvParams] = useState({ n_splits: 4, purge_window: 5, embargo_window: 2 });
  const [cv, setCv] = useState<Awaited<ReturnType<typeof researchApi.cvFolds>> | null>(null);
  const [importance, setImportance] = useState<Awaited<ReturnType<typeof researchApi.featureImportance>> | null>(null);
  const [impSel, setImpSel] = useState<string | null>(null);

  /* ---- 左下 / 右下 共用资产 ---- */
  const [assets, setAssets] = useState(DEFAULT_ASSETS);
  const [optMethod, setOptMethod] = useState<'risk_parity' | 'max_div' | 'mvo' | 'inverse_vol'>('risk_parity');
  const [weightCap, setWeightCap] = useState(0.4);
  const [turnoverPenalty, setTurnoverPenalty] = useState(true);
  const [optimize, setOptimize] = useState<Awaited<ReturnType<typeof researchApi.optimize>> | null>(null);
  const [optRunning, setOptRunning] = useState(false);

  const [impactParams, setImpactParams] = useState({
    symbol: '000001.SZ', algo: 'vwap' as 'market' | 'vwap' | 'twap',
    order_amount: 20_000_000, participation_cap: 0.05, side: 'buy' as 'buy' | 'sell',
  });
  const [impact, setImpact] = useState<Awaited<ReturnType<typeof researchApi.impactSim>> | null>(null);
  const [stress, setStress] = useState<Awaited<ReturnType<typeof researchApi.stressTest>> | null>(null);

  const updateSectionStatus = useCallback((section: ResearchSection, patch: Partial<SectionStatus[ResearchSection]>) => {
    if (!mountedRef.current) return;
    setSectionStatus((current) => ({
      ...current,
      [section]: { ...current[section], ...patch },
    }));
  }, []);

  /** 轻量元数据先到先显示；不占后端计算槽。 */
  const loadLightweightData = useCallback(async (signal: AbortSignal) => {
    const tasks: Array<Promise<void>> = [
      researchApi.overview({ signal }).then((value) => {
        if (mountedRef.current && !signal.aborted) setOverview(value);
      }),
      researchApi.experiments({ signal }).then((value) => {
        if (mountedRef.current && !signal.aborted) setExperiments(value);
      }),
      researchApi.featureImportance(10, { signal }).then((value) => {
        if (!mountedRef.current || signal.aborted) return;
        setImportance(value);
        setImpSel((previous) => previous ?? value.items[0]?.feature ?? null);
      }),
    ].map((task) => task.catch((error: unknown) => {
      if (!signal.aborted && !isAbortError(error) && mountedRef.current) {
        setErr(formatSectionError(error));
      }
    }));
    // 分年稳定性单独处理：失败只降级本面板（内联错误），不误点亮整页错误条。
    const labTask = researchApi.labYearly({ signal }).then((value) => {
      if (mountedRef.current && !signal.aborted) {
        setLabYearly(value);
        setLabYearlyError(null);
      }
    }).catch((error: unknown) => {
      if (!signal.aborted && !isAbortError(error) && mountedRef.current) {
        setLabYearlyError(formatSectionError(error));
      }
    });
    await Promise.all([...tasks, labTask]);
  }, []);

  /** 每个区块独立失败与重试；昂贵请求总是通过并发为 2 的队列执行。 */
  const loadSection = useCallback(async (section: ResearchSection, signal: AbortSignal) => {
    updateSectionStatus(section, { loading: true, error: null });
    const queued = <T,>(work: () => Promise<T>): Promise<T> => computeQueueRef.current.enqueue(work, signal);
    try {
      if (section === 'factor') {
        const [icir, corrData, quantileData] = await Promise.all([
          queued(() => researchApi.factorIcir({ factors: DEFAULT_FACTORS, horizon: 5, neutralize_size: false }, { signal })),
          queued(() => researchApi.factorCorr({ factors: DEFAULT_FACTORS }, { signal })),
          queued(() => researchApi.factorQuantile({ factor: 'ret_5', horizon: 5 }, { signal })),
        ]);
        if (!mountedRef.current || signal.aborted) return;
        setIcirRows(icir.rows);
        setAvailableFactors(icir.available_factors);
        setIcirSel((previous) => previous ?? icir.rows[0]?.factor ?? null);
        setCorr(corrData);
        setQuantile(quantileData);
      } else if (section === 'ml') {
        const cvData = await queued(() => researchApi.cvFolds({ n_splits: 4, purge_window: 5, embargo_window: 2 }, { signal }));
        if (mountedRef.current && !signal.aborted) setCv(cvData);
      } else if (section === 'portfolio') {
        const optimized = await queued(() => researchApi.optimize({
          assets: DEFAULT_ASSETS, method: 'risk_parity', weight_cap: 0.4,
          turnover_penalty: 3.0, cov_window: 120,
        }, { signal }));
        if (mountedRef.current && !signal.aborted) setOptimize(optimized);
      } else {
        const [impactData, stressData] = await Promise.all([
          queued(() => researchApi.impactSim({
            symbol: '000001.SZ', algo: 'vwap', order_amount: 20_000_000,
            participation_cap: 0.05, side: 'buy', split_days: 5, lookback_days: 20,
          }, { signal })),
          queued(() => researchApi.stressTest({ assets: DEFAULT_ASSETS }, { signal })),
        ]);
        if (!mountedRef.current || signal.aborted) return;
        setImpact(impactData);
        setStress(stressData);
      }
    } catch (error) {
      if (!signal.aborted && !isAbortError(error)) {
        updateSectionStatus(section, { error: formatSectionError(error) });
      }
    } finally {
      if (!signal.aborted) updateSectionStatus(section, { loading: false });
    }
  }, [updateSectionStatus]);

  const retrySection = useCallback((section: ResearchSection) => {
    const controller = new AbortController();
    void loadSection(section, controller.signal);
  }, [loadSection]);

  /** 用户触发的重算也进入同一队列，并在组件卸载时一并取消。 */
  const enqueueCompute = useCallback(<T,>(work: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    const controller = new AbortController();
    interactiveControllersRef.current.add(controller);
    return computeQueueRef.current
      .enqueue(() => work(controller.signal), controller.signal)
      .finally(() => interactiveControllersRef.current.delete(controller));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    mountedRef.current = true;
    void loadLightweightData(controller.signal);
    (['factor', 'ml', 'portfolio', 'execution'] as ResearchSection[])
      .forEach((section) => { void loadSection(section, controller.signal); });
    return () => {
      mountedRef.current = false;
      controller.abort();
      interactiveControllersRef.current.forEach((interactiveController) => interactiveController.abort());
      interactiveControllersRef.current.clear();
    };
  }, [loadLightweightData, loadSection]);

  /* 局部联动：因子选择 / 口径变化 -> 重算左上 */
  // I-8：重算批次的代际守卫。ComputeQueue 只限并发 2、不保证顺序，
  // 快速连点（勾选因子/中性化/CV/优化器）时旧批次后到会覆盖新批次，
  // 导致 chips 与图表不同源 ⇒ 只有最新一批可写状态。
  const factorSeqRef = useRef(0);
  const cvSeqRef = useRef(0);
  const optSeqRef = useRef(0);
  const rerunFactorPanels = async (
    fs = factors, h = horizon, nz = neutralize,
  ) => {
    const seq = ++factorSeqRef.current;
    try {
      const [icir, co, qt] = await Promise.all([
        enqueueCompute((signal) => researchApi.factorIcir({ factors: fs, horizon: h, neutralize_size: nz }, { signal })),
        enqueueCompute((signal) => researchApi.factorCorr({ factors: fs }, { signal })),
        enqueueCompute((signal) => researchApi.factorQuantile({ factor: quantileFactor, horizon: h }, { signal })),
      ]);
      if (!mountedRef.current || seq !== factorSeqRef.current) return;
      setIcirRows(icir.rows);
      setCorr(co);
      setQuantile(qt);
    } catch (error) {
      if (!isAbortError(error) && seq === factorSeqRef.current) setErr(formatSectionError(error));
    }
  };

  const toggleFactor = (f: string) => {
    if (factors.includes(f)) {
      if (factors.length <= 2) return;                 // 相关矩阵至少 2 因子
      const next = factors.filter((x) => x !== f);
      setFactors(next); void rerunFactorPanels(next);
    } else if (factors.length < 8) {
      const next = [...factors, f];
      setFactors(next); void rerunFactorPanels(next);
    }
  };

  const rerunCv = async (p = cvParams) => {
    const seq = ++cvSeqRef.current;
    try {
      const value = await enqueueCompute((signal) => researchApi.cvFolds(p, { signal }));
      if (mountedRef.current && seq === cvSeqRef.current) setCv(value);
    } catch (error) {
      if (!isAbortError(error) && seq === cvSeqRef.current) setErr(formatSectionError(error));
    }
  };

  const runOptimizer = async (method = optMethod, cap = weightCap, pen = turnoverPenalty) => {
    const seq = ++optSeqRef.current;
    setOptRunning(true);
    try {
      const value = await enqueueCompute((signal) => researchApi.optimize({
        assets, method, weight_cap: cap,
        turnover_penalty: pen ? 3.0 : 0, cov_window: 120,
      }, { signal }));
      if (mountedRef.current && seq === optSeqRef.current) setOptimize(value);
    } catch (error) {
      if (!isAbortError(error) && seq === optSeqRef.current) setErr(formatSectionError(error));
    } finally {
      if (mountedRef.current && seq === optSeqRef.current) setOptRunning(false);
    }
  };

  const rerunImpact = async (p = impactParams) => {
    try {
      const value = await enqueueCompute((signal) => researchApi.impactSim({ ...p, split_days: 5, lookback_days: 20 }, { signal }));
      if (mountedRef.current) setImpact(value);
    } catch (error) {
      if (!isAbortError(error)) setErr(formatSectionError(error));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-ink">策略研究 · 核心因子与策略引擎</h1>
        {err && <span className="text-2xs text-red-600">{err}</span>}
      </div>

      {/* 顶栏：真实统计 */}
      <div className="grid grid-cols-4 gap-2">
        <div className="rounded-lg border border-hair bg-white px-3 py-2">
          <div className="text-2xs text-ink-secondary">因子库总数</div>
          <div className="num text-base font-semibold">{overview?.factor_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">{overview?.factor_universe ?? ''}</div>
        </div>
        <div className="rounded-lg border border-hair bg-white px-3 py-2">
          <div className="text-2xs text-ink-secondary">模型版本数</div>
          <div className="num text-base font-semibold">{overview?.model_version_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">model_registry 已登记</div>
        </div>
        <div className="rounded-lg border border-hair bg-white px-3 py-2">
          <div className="text-2xs text-ink-secondary">策略容量估算</div>
          <div className="num text-base font-semibold">
            {overview?.capacity_estimate_yi != null ? `${overview.capacity_estimate_yi} 亿` : '—'}</div>
          <div className="text-2xs text-ink-muted">{overview?.capacity_formula ?? ''}</div>
        </div>
        <div className="rounded-lg border border-hair bg-white px-3 py-2">
          <div className="text-2xs text-ink-secondary">因子表达式库</div>
          <div className="num text-base font-semibold">{overview?.expression_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">可批量挖掘的算子表达式</div>
        </div>
      </div>

      {/* 四象限 */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {/* ============ 左上：因子研发中心 ============ */}
        <SectionCard title="因子研发中心（因子挖掘与 IC 分析）"
          action={sectionStatus.factor.error ? <button onClick={() => retrySection('factor')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
          bodyClassName="p-3 space-y-3">
          {sectionStatus.factor.error && <p role="alert" className="rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">{sectionStatus.factor.error}</p>}
          {sectionStatus.factor.loading && <p className="text-2xs text-ink-muted">因子计算排队或执行中…</p>}
          <div className="flex flex-wrap gap-1">
            {availableFactors.map((f) => (
              <button key={f} onClick={() => toggleFactor(f)}
                      className={`rounded px-1.5 py-0.5 font-mono text-2xs border ${
                        factors.includes(f)
                          ? 'border-brand-400 bg-brand-50 text-brand-700'
                          : 'border-hair text-ink-secondary hover:bg-slate-50'}`}>
                {f}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 text-2xs">
            <label className="text-ink-secondary">Hold-out 口径
              <select value={horizon} className={`${inputCls} mt-0.5 w-20`}
                      onChange={(e) => {
                        const h = Number(e.target.value); setHorizon(h);
                        void rerunFactorPanels(factors, h, neutralize);
                      }}>
                {[1, 5, 20].map((h) => <option key={h} value={h}>{h}D</option>)}
              </select>
            </label>
            <div className="flex-1" />
            <Switch on={neutralize} label="市值中性化"
                    onChange={(v) => { setNeutralize(v); void rerunFactorPanels(factors, horizon, v); }} />
          </div>
          <IcirPanel rows={icirRows} selected={icirSel} onPick={setIcirSel} />
          <div className="grid grid-cols-2 gap-3 border-t border-hair pt-2">
            <div>
              <div className="mb-1 text-2xs font-medium text-ink-secondary">因子正交化热力图</div>
              {corr && <CorrHeatmap factors={corr.factors} matrix={corr.matrix} highPairs={corr.high_corr_pairs} />}
            </div>
            <div>
              <div className="mb-1 text-2xs font-medium text-ink-secondary">因子分层收益图</div>
              <select value={quantileFactor} className={`${inputCls} mb-1`}
                      onChange={(e) => {
                        setQuantileFactor(e.target.value);
                        void enqueueCompute((signal) => researchApi
                          .factorQuantile({ factor: e.target.value, horizon }, { signal }))
                          .then((value) => { if (mountedRef.current) setQuantile(value); })
                          .catch((error: unknown) => {
                            if (!isAbortError(error)) setErr(formatSectionError(error));
                          });
                      }}>
                {availableFactors.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              {quantile && <QuantileChart curves={quantile.curves} labels={quantile.labels} />}
            </div>
          </div>
        </SectionCard>

        {/* ============ 右上：MLOps 建模中心 ============ */}
        <SectionCard title="MLOps 建模中心（实验追踪与可解释性）"
          action={sectionStatus.ml.error ? <button onClick={() => retrySection('ml')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
          bodyClassName="p-3 space-y-3">
          {sectionStatus.ml.error && <p role="alert" className="rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">{sectionStatus.ml.error}</p>}
          {sectionStatus.ml.loading && <p className="text-2xs text-ink-muted">CV 计算排队或执行中…</p>}
          <div>
            <div className="mb-1 text-2xs font-medium text-ink-secondary">实验追踪模板（model_registry 真实产物）</div>
            <div className="max-h-28 overflow-auto rounded border border-hair">
              <table className="w-full text-2xs">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-ink-secondary">
                    <th className="text-left font-medium">版本</th>
                    <th className="font-medium">状态</th>
                    <th className="font-medium">LR</th>
                    <th className="font-medium">Valid RankIC</th>
                    <th className="font-medium">Test RankIC</th>
                  </tr>
                </thead>
                <tbody>
                  {experiments.map((e) => (
                    <tr key={e.version} className="border-t border-hair">
                      <td className="py-0.5 font-mono">{e.version}</td>
                      <td className={`text-center ${e.is_production ? 'font-semibold text-brand-600' : 'text-ink-secondary'}`}>
                        {e.status}</td>
                      <td className="num text-center">{e.hyperparams.learning_rate ?? '—'}</td>
                      <td className="num text-center">{e.metrics.valid_rank_ic != null ? Number(e.metrics.valid_rank_ic).toFixed(4) : '—'}</td>
                      <td className="num text-center">{e.metrics.test_rank_ic != null ? Number(e.metrics.test_rank_ic).toFixed(4) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {/* 分年稳定性热力（§4.5）：生产模型预测 × 次日真实收益逐年聚合 */}
          <div>
            <div className="mb-1 text-2xs font-medium text-ink-secondary">
              分年稳定性（生产模型预测 × 次日收益；色深 = RankIC 高低）
            </div>
            {labYearlyError ? (
              <p role="alert" className="rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">
                分年稳定性加载失败：{labYearlyError}
              </p>
            ) : labYearly.length ? (
              <div className="flex flex-wrap gap-1.5">
                {labYearly.map((y) => {
                  const intensity = Math.min(1, Math.max(0, y.rank_ic * 10));
                  const bg = y.rank_ic >= 0
                    ? `rgba(37,99,235,${0.12 + intensity * 0.55})`
                    : `rgba(220,38,38,${0.12 + Math.min(1, -y.rank_ic * 10) * 0.55})`;
                  return (
                    <div key={y.year}
                         className="rounded px-2 py-1 text-2xs text-white"
                         style={{ background: bg }}
                         title={`${y.year}：RankIC ${y.rank_ic.toFixed(4)} · ICIR ${y.icir ?? '—'} · 命中率 ${(y.hit_rate * 100).toFixed(1)}% · ${y.n_days} 个 IC 日`}>
                      <div className="num font-semibold">{y.year}</div>
                      <div className="num">{y.rank_ic.toFixed(3)}</div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-2xs text-ink-muted">暂无足够预测样本（每一年需 ≥500 对齐样本）</div>
            )}
          </div>
          <div>
            <div className="mb-1 flex items-center gap-2 text-2xs font-medium text-ink-secondary">
              Purged CV Fold 可视化
              <span className="font-normal text-ink-muted">隔离带 = {cvParams.purge_window}d purge + {cvParams.embargo_window}d embargo</span>
            </div>
            {cv && <CvGantt folds={cv.folds} totalDays={cv.total_days}
                            dateStart={cv.date_start} dateEnd={cv.date_end} />}
            <div className="mt-1 flex gap-2 text-2xs">
              <label>n_splits
                <select value={cvParams.n_splits} className={`${inputCls} ml-1 w-14`}
                        onChange={(e) => {
                          const p = { ...cvParams, n_splits: Number(e.target.value) };
                          setCvParams(p); void rerunCv(p);
                        }}>
                  {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label>purge
                <select value={cvParams.purge_window} className={`${inputCls} ml-1 w-14`}
                        onChange={(e) => {
                          const p = { ...cvParams, purge_window: Number(e.target.value) };
                          setCvParams(p); void rerunCv(p);
                        }}>
                  {[5, 10, 20].map((n) => <option key={n} value={n}>{n}d</option>)}
                </select>
              </label>
              <label>embargo
                <select value={cvParams.embargo_window} className={`${inputCls} ml-1 w-14`}
                        onChange={(e) => {
                          const p = { ...cvParams, embargo_window: Number(e.target.value) };
                          setCvParams(p); void rerunCv(p);
                        }}>
                  {[2, 5, 10].map((n) => <option key={n} value={n}>{n}d</option>)}
                </select>
              </label>
            </div>
          </div>
          <div className="border-t border-hair pt-2">
            <div className="mb-1 text-2xs font-medium text-ink-secondary">特征重要性 &amp; SHAP 效应（gain 真实值）</div>
            {importance
              ? <ImportancePanel items={importance.items} curves={importance.effect_curves}
                                 selected={impSel} onPick={setImpSel} />
              : <LoadingState text="加载模型…" />}
          </div>
        </SectionCard>

        {/* ============ 左下：极致组合与风控 ============ */}
        <SectionCard title="极致组合与风控（风险分解与优化）"
          action={sectionStatus.portfolio.error ? <button onClick={() => retrySection('portfolio')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
          bodyClassName="p-3 space-y-3">
          {sectionStatus.portfolio.error && <p role="alert" className="rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">{sectionStatus.portfolio.error}</p>}
          {sectionStatus.portfolio.loading && <p className="text-2xs text-ink-muted">组合优化排队或执行中…</p>}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-2xs font-medium text-ink-secondary">GICS 风格暴露分解（截面 zscore 加权）</div>
              {optimize && <ExposureChart before={optimize.exposure_before} after={optimize.exposure_after} />}
              <p className="text-2xs text-ink-muted">
                口径：{optimize ? Object.entries(optimize.style_factor_map).map(([k, v]) => `${k}=${v}`).join(' · ') : ''}
              </p>
            </div>
            <div className="space-y-2">
              <div className="text-2xs font-medium text-ink-secondary">组合优化控制台</div>
              <div className="text-2xs text-ink-secondary">资产（本地 hfq 库）</div>
              {assets.map((a, i) => (
                <div key={a.code} className="flex items-center gap-1 text-2xs">
                  <span className="w-20 font-mono">{a.code}</span>
                  <input type="number" step={0.05} min={0} max={1} value={a.weight}
                         className="w-16 rounded border border-hair px-1 py-0.5"
                         onChange={(e) => {
                           const next = assets.map((x, j) => j === i
                             ? { ...x, weight: Number(e.target.value) } : x);
                           setAssets(next);
                         }} />
                  <button className="text-ink-muted hover:text-red-600"
                          onClick={() => assets.length > 2 && setAssets(assets.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <label className="block text-2xs text-ink-secondary">优化目标
                <select value={optMethod} className={`${inputCls} mt-0.5`}
                        onChange={(e) => {
                          const m = e.target.value as typeof optMethod;
                          setOptMethod(m); void runOptimizer(m, weightCap, turnoverPenalty);
                        }}>
                  <option value="risk_parity">Risk Parity（风险平摊）</option>
                  <option value="max_div">Max Diversification（最大分散度）</option>
                  <option value="mvo">MVO（μ 不可用 ⇒ 实为最小方差）</option>
                  <option value="inverse_vol">Inverse Volatility（波动率倒数）</option>
                </select>
              </label>
              <label className="block text-2xs text-ink-secondary">
                单股上限 {(weightCap * 100).toFixed(0)}%
                <input type="range" min={0.1} max={1} step={0.05} value={weightCap}
                       className="w-full"
                       onMouseUp={() => void runOptimizer()}
                       onTouchEnd={() => void runOptimizer()}
                       onChange={(e) => setWeightCap(Number(e.target.value))} />
              </label>
              <Switch on={turnoverPenalty} label="换手率惩罚（L2）"
                      onChange={(v) => { setTurnoverPenalty(v); void runOptimizer(optMethod, weightCap, v); }} />
              <button disabled={optRunning}
                      onClick={() => void runOptimizer()}
                      className="w-full rounded-md bg-brand-500 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
                {optRunning ? '优化中…' : '组合优化（QP 求解）'}
              </button>
            </div>
          </div>
          {optimize && (
            <div className="border-t border-hair pt-2 text-2xs">
              <span className="text-ink-secondary">优化后权重：</span>
              {Object.entries(optimize.weights).map(([s, w]) => (
                <span key={s} className="mr-2 font-mono">{s.replace(/\.\w+$/, '')} {(w * 100).toFixed(1)}%</span>
              ))}
              <span className="text-ink-muted">
                （协方差：Ledoit-Wolf 收缩 + RMT 去噪，{optimize.n_obs} 个真实收益观测；
                权重合计 {((optimize.weight_cap_info?.invested_ratio ?? 1) * 100).toFixed(1)}%）
              </span>
              {/* 审计 P1-13：上限不可行/未生效必须显式告警——否则用户把"部分现金"或
                  "上限根本没生效"的解读成正常结果（修复前无任何字段可查）。 */}
              {optimize.weight_cap_info
                && (!optimize.weight_cap_info.feasible || !optimize.weight_cap_info.cap_enforced) && (
                <p role="alert" className="mt-1 rounded bg-amber-50 px-2 py-1 text-amber-700">
                  {optimize.weight_cap_info.note}
                </p>
              )}
              {/* 审计 B2-11：μ≡0 ⇒ λ 无影响，"均值-方差"只是标签（修复前无任何提示） */}
              {optimize.expected_returns && 'basis' in optimize.expected_returns
                && optimize.expected_returns.basis === 'unavailable' && (
                <p role="alert" className="mt-1 rounded bg-amber-50 px-2 py-1 text-amber-700">
                  {optimize.expected_returns.note}
                </p>
              )}
            </div>
          )}
        </SectionCard>

        {/* ============ 右下：策略引擎进阶 ============ */}
        <SectionCard title="策略引擎进阶（执行成本与压力测试）"
          action={sectionStatus.execution.error ? <button onClick={() => retrySection('execution')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
          bodyClassName="p-3 space-y-3">
          {sectionStatus.execution.error && <p role="alert" className="rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">{sectionStatus.execution.error}</p>}
          {sectionStatus.execution.loading && <p className="text-2xs text-ink-muted">执行与压力测试排队或执行中…</p>}
          <div>
            <div className="mb-1 flex flex-wrap items-center gap-2 text-2xs text-ink-secondary">
              执行冲击模拟（<span className="text-amber-600">日频口径：真实日 VWAP + sqrt 冲击</span>）
              <input value={impactParams.symbol} className={`${inputCls} w-24`}
                     onChange={(e) => setImpactParams({ ...impactParams, symbol: e.target.value })} />
              <select value={impactParams.algo} className={`${inputCls} w-28`}
                      onChange={(e) => {
                        const p = { ...impactParams, algo: e.target.value as typeof impactParams.algo };
                        setImpactParams(p); void rerunImpact(p);
                      }}>
                <option value="market">Market 一次性</option>
                <option value="vwap">VWAP 分批</option>
                <option value="twap">TWAP 分批</option>
              </select>
              <select value={impactParams.side} className={`${inputCls} w-16`}
                      onChange={(e) => {
                        const p = { ...impactParams, side: e.target.value as typeof impactParams.side };
                        setImpactParams(p); void rerunImpact(p);
                      }}>
                <option value="buy">BUY</option>
                <option value="sell">SELL</option>
              </select>
              <label>订单
                <input type="number" step={5_000_000} min={100_000}
                       value={impactParams.order_amount}
                       className={`${inputCls} ml-1 w-28`}
                       onChange={(e) => setImpactParams({ ...impactParams, order_amount: Number(e.target.value) })}
                       onBlur={() => void rerunImpact()} />
              </label>
            </div>
            {impact && <ImpactChart priceSeries={impact.price_series} fills={impact.fills}
                                    side={impact.side} />}
            {impact && (
              <p className="text-2xs text-ink-muted">
                订单 {impact.order_amount.toLocaleString()} 元 · 均冲击 <span className="num font-semibold text-brand-600">{impact.avg_impact_bps} bps</span>
                {' '}· 总成本 {impact.total_impact_cost.toLocaleString()} 元
                {impact.unfilled > 0 && <span className="text-red-600"> · 未成交 {impact.unfilled.toLocaleString()} 元（参与率 {impactParams.participation_cap * 100}% 上限）</span>}
              </p>
            )}
            {/* I-12：后端已按 close 修正 VWAP 坏点并返回 data_warnings，必须如实告知 */}
            {impact?.data_warnings?.length ? (
              <p role="alert" className="mt-1 rounded bg-amber-50 px-2 py-1 text-2xs text-amber-700">
                数据质量提示：{impact.data_warnings.join('；')}
              </p>
            ) : null}
          </div>
          <div className="border-t border-hair pt-2">
            <div className="mb-1 text-2xs font-medium text-ink-secondary">
              压力测试情景归因（真实历史最深回撤窗口 · 历史重演法）
            </div>
            {stress ? <StressTable scenarios={stress.scenarios} />
              : <LoadingState text="压力计算中…" />}
            <p className="mt-1 text-2xs text-ink-muted">
              {stress?.note ?? '窗口由真实市场数据自动识别（滚动收益最深的互不重叠区间）'}
            </p>
          </div>
        </SectionCard>
      </div>

      <p className="text-center text-2xs text-ink-muted">
        本页全部指标来自本地真实数据（features 截面 / model_registry / hfq 行情）实时计算 · 无任何模拟值 · 仅用于研究参考
      </p>
    </div>
  );
}
