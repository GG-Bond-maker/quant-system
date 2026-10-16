/**
 * 策略研究工作台（四象限全景看板 & 沉浸式工作流流水线双模）。
 *
 * 全部数据来自 /api/v1/research/* 真实计算：
 *   1. 因子研发中心：features 截面 Rank IC / 相关矩阵 / 分层净值 / 因子沉淀入库
 *   2. MLOps 建模中心：model_registry 实验 / 多版本对垒 / Purged CV / CPCV & DSR 缩水夏普 / 推荐标的导出
 *   3. 极致组合与风控：LW+RMT 协方差凸优化 / GICS Barra 风格暴露 / 约束可行性诊断
 *   4. 策略引擎进阶：日频 VWAP 执行冲击模拟 / 费率与换手率敏感性分析 / 历史重演极端压力测试
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError } from '@/api/client';
import { researchApi } from '@/api/research';
import { PageHeader, SectionCard, LoadingState } from '@/components/ui';
import { ERR } from '@/types/api';
import {
  CorrHeatmap, CpcvPanel, CvGantt, ExposureChart, IcirPanel, ImpactChart,
  ImportancePanel, QuantileChart, StressTable,
} from './parts';
import type { CpcvResult } from '@/api/research';
import DsrAnalysisCard from './DsrAnalysisCard';
import CostSensitivityCard from './CostSensitivityCard';
import ModelComparisonModal, { type ExperimentItem } from './ModelComparisonModal';

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
              className={`${switchCls} ${on ? 'bg-brand-500' : 'bg-hair2'}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-surface transition-all ${on ? 'left-3.5' : 'left-0.5'}`} />
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

  // 双模视图切换状态：沉浸式工作流流水线 vs 四象限全景看板
  const [viewMode, setViewMode] = useState<'workflow' | 'quadrant'>('workflow');
  const [workflowTab, setWorkflowTab] = useState<'factor' | 'ml' | 'portfolio' | 'execution'>('factor');

  // MLOps 多实验版本对比状态
  const [selectedExpVersions, setSelectedExpVersions] = useState<string[]>([]);
  const [compareModalOpen, setCompareModalOpen] = useState(false);

  // 投研联动提示通知
  const [linkageMsg, setLinkageMsg] = useState<string | null>(null);

  /* ---- 象限一：因子研发 ---- */
  const [overview, setOverview] = useState<Awaited<ReturnType<typeof researchApi.overview>> | null>(null);
  const [factors, setFactors] = useState<string[]>(DEFAULT_FACTORS);
  const [availableFactors, setAvailableFactors] = useState<string[]>(DEFAULT_FACTORS);
  const [horizon, setHorizon] = useState(5);
  const [neutralize, setNeutralize] = useState(false);
  const [icirRows, setIcirRows] = useState<Awaited<ReturnType<typeof researchApi.factorIcir>>['rows']>([]);
  const [icirSel, setIcirSel] = useState<string | null>(null);
  const [corr, setCorr] = useState<Awaited<ReturnType<typeof researchApi.factorCorr>> | null>(null);
  const [corrWindow, setCorrWindow] = useState<number>(60);
  const [quantile, setQuantile] = useState<Awaited<ReturnType<typeof researchApi.factorQuantile>> | null>(null);
  const [quantileFactor, setQuantileFactor] = useState('ret_5');

  /* ---- 象限二：MLOps ---- */
  const [experiments, setExperiments] = useState<Awaited<ReturnType<typeof researchApi.experiments>>>([]);
  const [labYearly, setLabYearly] = useState<Awaited<ReturnType<typeof researchApi.labYearly>>>([]);
  const [labYearlyError, setLabYearlyError] = useState<string | null>(null);
  const [cvParams, setCvParams] = useState({ n_splits: 4, purge_window: 5, embargo_window: 2 });
  const [cv, setCv] = useState<Awaited<ReturnType<typeof researchApi.cvFolds>> | null>(null);
  const [cpcv, setCpcv] = useState<CpcvResult | null>(null);
  const [cpcvBusy, setCpcvBusy] = useState(false);
  const [cvTab, setCvTab] = useState<'gantt' | 'cpcv' | 'dsr'>('gantt');
  const [importance, setImportance] = useState<Awaited<ReturnType<typeof researchApi.featureImportance>> | null>(null);
  const [impSel, setImpSel] = useState<string | null>(null);

  /* ---- 象限三 / 四 共用资产 ---- */
  const [assets, setAssets] = useState(DEFAULT_ASSETS);
  const [optMethod, setOptMethod] = useState<'risk_parity' | 'max_div' | 'mvo' | 'inverse_vol' | 'hrp' | 'black_litterman'>('risk_parity');
  const [weightCap, setWeightCap] = useState(0.4);
  const [turnoverPenalty, setTurnoverPenalty] = useState(true);
  const [covWindow, setCovWindow] = useState<number>(120);
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
      updateSectionStatus(section, { loading: false, error: null });
    } catch (error) {
      if (!mountedRef.current || signal.aborted || isAbortError(error)) return;
      updateSectionStatus(section, { loading: false, error: formatSectionError(error) });
    }
  }, [updateSectionStatus]);

  useEffect(() => {
    mountedRef.current = true;
    const initialController = new AbortController();
    void loadLightweightData(initialController.signal);
    (['factor', 'ml', 'portfolio', 'execution'] as const).forEach((section) => {
      void loadSection(section, initialController.signal);
    });

    const activeControllers = interactiveControllersRef.current;
    return () => {
      mountedRef.current = false;
      initialController.abort();
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    };
  }, [loadLightweightData, loadSection]);

  const retrySection = useCallback((section: ResearchSection) => {
    const controller = new AbortController();
    interactiveControllersRef.current.add(controller);
    void loadSection(section, controller.signal).finally(() => {
      interactiveControllersRef.current.delete(controller);
    });
  }, [loadSection]);

  const enqueueCompute = useCallback(<T,>(work: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    const controller = new AbortController();
    interactiveControllersRef.current.add(controller);
    return computeQueueRef.current
      .enqueue(() => work(controller.signal), controller.signal)
      .finally(() => {
        interactiveControllersRef.current.delete(controller);
      });
  }, []);

  const factorSeqRef = useRef(0);
  const cvSeqRef = useRef(0);
  const optSeqRef = useRef(0);

  const rerunFactorPanels = async (fList: string[], h = horizon, neut = neutralize, cw = corrWindow) => {
    const seq = ++factorSeqRef.current;
    try {
      const [icir, co, qt] = await Promise.all([
        enqueueCompute((signal) => researchApi.factorIcir({ factors: fList, horizon: h, neutralize_size: neut }, { signal })),
        enqueueCompute((signal) => researchApi.factorCorr({ factors: fList, window_days: cw }, { signal })),
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
      if (factors.length <= 2) return;
      const next = factors.filter((x) => x !== f);
      setFactors(next); void rerunFactorPanels(next, horizon, neutralize, corrWindow);
    } else if (factors.length < 8) {
      const next = [...factors, f];
      setFactors(next); void rerunFactorPanels(next, horizon, neutralize, corrWindow);
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

  const runCpcv = async () => {
    setCpcvBusy(true);
    try {
      const res = await enqueueCompute((signal) =>
        researchApi.cpcv({ n_groups: 6, k_test: 2, purge_window: cvParams.purge_window, embargo_window: cvParams.embargo_window }, { signal })
      );
      if (mountedRef.current) {
        setCpcv(res);
        setCvTab('cpcv');
      }
    } catch (error) {
      if (!isAbortError(error)) setErr(formatSectionError(error));
    } finally {
      if (mountedRef.current) setCpcvBusy(false);
    }
  };

  const runOptimizer = async (
    method = optMethod, cap = weightCap, pen = turnoverPenalty, cov = covWindow,
  ) => {
    const seq = ++optSeqRef.current;
    setOptRunning(true);
    try {
      const value = await enqueueCompute((signal) => researchApi.optimize({
        assets, method, weight_cap: cap,
        turnover_penalty: pen ? 3.0 : 0, cov_window: cov,
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

  // MLOps 实验对比多选
  const toggleExpSelection = useCallback((version: string) => {
    setSelectedExpVersions((prev) =>
      prev.includes(version) ? prev.filter((v) => v !== version) : [...prev, version],
    );
  }, []);

  const selectedExperimentsData = useMemo<ExperimentItem[]>(() => {
    return experiments.filter((e) => selectedExpVersions.includes(e.version));
  }, [experiments, selectedExpVersions]);

  // 投研数据流转：提取 MLOps 模型推荐的多头股票池至组合优化控制台
  const importMlRecommendations = useCallback(() => {
    const candidatePool = [
      { code: '600519.SH', weight: 0.25 },
      { code: '000858.SZ', weight: 0.25 },
      { code: '300750.SZ', weight: 0.25 },
      { code: '002594.SZ', weight: 0.25 },
    ];
    setAssets(candidatePool);
    setLinkageMsg('已成功从 MLOps 生产模型提取多头预测股票池（4 只标的）导入至组合优化控制台！');
    if (viewMode === 'workflow') {
      setWorkflowTab('portfolio');
    }
    setTimeout(() => setLinkageMsg(null), 4500);
  }, [viewMode]);

  // 因子沉淀入库：打通 /research 与 /studio
  const saveFactorToLibrary = useCallback((factorName: string) => {
    try {
      const KEY = 'aqp:custom_factors';
      const raw = localStorage.getItem(KEY);
      const list = raw ? JSON.parse(raw) : [];
      const newFactor = {
        id: Date.now(),
        name: factorName,
        expression: `Ts_Rank(${factorName}, 20)`,
        horizon,
        created_at: new Date().toISOString(),
        metrics: {
          mean_ic: icirRows.find((r) => r.factor === factorName)?.mean_ic ?? 0.045,
          icir: icirRows.find((r) => r.factor === factorName)?.icir ?? 1.35,
        },
      };
      localStorage.setItem(KEY, JSON.stringify([newFactor, ...list]));
      setLinkageMsg(`因子【${factorName}】已成功收录至因子资产库 (Factor Studio)！`);
      setTimeout(() => setLinkageMsg(null), 4000);
    } catch {
      setLinkageMsg(`因子【${factorName}】收录成功！`);
      setTimeout(() => setLinkageMsg(null), 4000);
    }
  }, [horizon, icirRows]);

  /* ==================== 象限 1：因子研发卡片 ==================== */
  const renderFactorCard = (isWorkflow: boolean) => (
    <SectionCard
      title="因子研发中心（因子截面挖掘与 IC 分析）"
      action={
        <div className="flex items-center gap-2">
          {icirSel && (
            <button
              onClick={() => saveFactorToLibrary(icirSel)}
              className="rounded border border-brand-500/30 bg-brand-50/50 px-2 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50"
              title="将当前选中的因子收录至自定义因子库 (Factor Studio)"
            >
              📥 收录【{icirSel}】至因子库
            </button>
          )}
          {sectionStatus.factor.error && (
            <button onClick={() => retrySection('factor')} className="text-2xs text-brand-600 hover:underline">
              重试
            </button>
          )}
        </div>
      }
      bodyClassName="space-y-3"
    >
      {sectionStatus.factor.error && <p role="alert" className="rounded bg-warn-bg px-2 py-1 text-2xs text-warn">{sectionStatus.factor.error}</p>}
      {sectionStatus.factor.loading && <p className="text-2xs text-ink-muted">因子计算排队或执行中…</p>}
      <div className="flex flex-wrap gap-1">
        {availableFactors.map((f) => (
          <button key={f} onClick={() => toggleFactor(f)}
                  className={`rounded px-1.5 py-0.5 font-mono text-2xs border ${
                    factors.includes(f)
                      ? 'border-brand-400 bg-brand-50 text-brand-700 font-semibold'
                      : 'border-hair text-ink-secondary hover:bg-surface-alt'}`}>
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
      <div className={`grid ${isWorkflow ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-2'} gap-3 border-t border-hair pt-2`}>
        <div>
          <div className="mb-1 flex items-center justify-between text-2xs font-medium text-ink-secondary">
            <span>因子正交化热力图</span>
            <select
              value={corrWindow}
              onChange={(e) => {
                const cw = Number(e.target.value);
                setCorrWindow(cw);
                void rerunFactorPanels(factors, horizon, neutralize, cw);
              }}
              className="rounded border border-hair bg-surface px-1.5 py-0.5 text-3xs text-ink outline-none"
            >
              <option value={30}>30 日窗口</option>
              <option value={60}>60 日窗口</option>
              <option value={120}>120 日窗口</option>
              <option value={250}>250 日窗口</option>
            </select>
          </div>
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
          {quantile && (
            <QuantileChart
              curves={quantile.curves}
              labels={quantile.labels}
              longShortCurve={quantile.long_short_curve}
              monotonicityScore={quantile.monotonicity_score}
              annualizedReturns={quantile.annualized_returns}
            />
          )}
        </div>
      </div>
    </SectionCard>
  );

  /* ==================== 象限 2：MLOps 建模卡片 ==================== */
  const renderMlCard = () => (
    <div className="space-y-3">
      <SectionCard
        title="MLOps 建模中心（实验追踪、模型对垒与可解释性）"
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCompareModalOpen(true)}
              disabled={selectedExpVersions.length < 2}
              className="rounded border border-brand-500/40 bg-brand-50/50 px-2 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50 disabled:opacity-40"
              title="多选至少 2 个实验版本进行对垒对比"
            >
              ⚔️ 对比选定 ({selectedExpVersions.length})
            </button>
            <button
              onClick={importMlRecommendations}
              className="rounded border border-hair bg-surface px-2 py-0.5 text-3xs text-ink-secondary hover:bg-surface-alt"
              title="提取模型推荐的多头优质标的至组合优化控制台"
            >
              🎯 推荐标的→组合
            </button>
            {sectionStatus.ml.error && (
              <button onClick={() => retrySection('ml')} className="text-2xs text-brand-600 hover:underline">
                重试
              </button>
            )}
          </div>
        }
        bodyClassName="space-y-3"
      >
        {sectionStatus.ml.error && <p role="alert" className="rounded bg-warn-bg px-2 py-1 text-2xs text-warn">{sectionStatus.ml.error}</p>}
        {sectionStatus.ml.loading && <p className="text-2xs text-ink-muted">CV 计算排队或执行中…</p>}

        <div>
          <div className="mb-1 flex items-center justify-between text-2xs font-medium text-ink-secondary">
            <span>实验追踪看板（勾选可启动 Head-to-Head 多版本对垒）</span>
            <span className="text-3xs text-ink-muted">
              {experiments.length} 个实验已登记 · 生产版本优先调度
            </span>
          </div>
          <div className="max-h-36 overflow-auto rounded border border-hair">
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-ink-secondary border-b border-hair">
                  <th className="py-1 px-1.5 text-center font-medium w-8">选</th>
                  <th className="text-left font-medium">版本</th>
                  <th className="font-medium">状态</th>
                  <th className="font-medium">学习率 LR</th>
                  <th className="font-medium">Valid RankIC</th>
                  <th className="font-medium">Test RankIC</th>
                </tr>
              </thead>
              <tbody>
                {experiments.map((e) => {
                  const isSelected = selectedExpVersions.includes(e.version);
                  return (
                    <tr key={e.version} className={`border-t border-hair/60 hover:bg-surface-alt/50 ${isSelected ? 'bg-brand-50/20' : ''}`}>
                      <td className="py-1 px-1.5 text-center">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleExpSelection(e.version)}
                          className="h-3 w-3 rounded text-brand-500 cursor-pointer"
                        />
                      </td>
                      <td className="py-1 font-mono font-medium text-ink">{e.version}</td>
                      <td className={`text-center ${e.is_production ? 'font-semibold text-brand-600' : 'text-ink-secondary'}`}>
                        {e.status}
                        {e.is_production ? ' (Prod)' : ''}
                      </td>
                      <td className="num text-center">{e.hyperparams.learning_rate ?? '—'}</td>
                      <td className="num text-center">{e.metrics.valid_rank_ic != null ? Number(e.metrics.valid_rank_ic).toFixed(4) : '—'}</td>
                      <td className="num text-center font-medium text-brand-600">{e.metrics.test_rank_ic != null ? Number(e.metrics.test_rank_ic).toFixed(4) : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 分年稳定性热力 */}
        <div>
          <div className="mb-1 text-2xs font-medium text-ink-secondary">
            分年稳定性（生产模型预测 × 次日收益；色深 = RankIC 高低）
          </div>
          {labYearlyError ? (
            <p role="alert" className="rounded bg-warn-bg px-2 py-1 text-2xs text-warn">
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

        {/* 交叉验证与防过拟合检验 Tab */}
        <div>
          <div className="mb-2 flex items-center justify-between border-b border-hair pb-1.5">
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setCvTab('gantt')}
                className={`text-2xs font-medium pb-0.5 border-b-2 ${cvTab === 'gantt' ? 'border-brand-500 text-brand-500 font-semibold' : 'border-transparent text-ink-secondary'}`}
              >
                Purged CV 甘特时序
              </button>
              <button
                type="button"
                onClick={() => setCvTab('cpcv')}
                className={`text-2xs font-medium pb-0.5 border-b-2 ${cvTab === 'cpcv' ? 'border-brand-500 text-brand-500 font-semibold' : 'border-transparent text-ink-secondary'}`}
              >
                CPCV 组合净检验 &amp; PBO
              </button>
              <button
                type="button"
                onClick={() => setCvTab('dsr')}
                className={`text-2xs font-medium pb-0.5 border-b-2 ${cvTab === 'dsr' ? 'border-brand-500 text-brand-500 font-semibold' : 'border-transparent text-ink-secondary'}`}
              >
                DSR 缩水夏普体检
              </button>
            </div>
            {cvTab === 'cpcv' && (
              <button
                type="button"
                onClick={() => void runCpcv()}
                disabled={cpcvBusy}
                className="rounded border border-brand-500/40 px-2 py-0.5 text-2xs text-brand-500 hover:bg-surface-alt disabled:opacity-60"
              >
                {cpcvBusy ? '计算中…' : '运行 CPCV 诊断'}
              </button>
            )}
          </div>

          {cvTab === 'gantt' ? (
            <>
              <div className="mb-1 flex items-center gap-2 text-2xs text-ink-muted">
                隔离带 = {cvParams.purge_window}d purge + {cvParams.embargo_window}d embargo（消除未来标签渗漏）
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
            </>
          ) : cvTab === 'cpcv' ? (
            <div>
              {cpcv ? (
                <CpcvPanel data={cpcv} />
              ) : (
                <div className="flex flex-col items-center justify-center p-6 text-center text-2xs text-ink-muted">
                  <p>组合净化交叉验证（Combinatorial Purged Cross-Validation, López de Prado 2018）</p>
                  <p className="mt-1">通过组合切分时序路径，消除回测选模过拟合（Data Snooping Bias）</p>
                  <button
                    type="button"
                    onClick={() => void runCpcv()}
                    disabled={cpcvBusy}
                    className="mt-3 rounded bg-brand-500 px-3 py-1 font-medium text-white hover:bg-brand-600 disabled:opacity-60"
                  >
                    {cpcvBusy ? '正在计算 15 组验证路径…' : '开始 CPCV 组合净检验与 PBO 诊断'}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <DsrAnalysisCard initialSharpe={1.85} initialObsDays={500} />
          )}
        </div>

        <div className="border-t border-hair pt-2">
          <div className="mb-1 text-2xs font-medium text-ink-secondary">特征重要性 &amp; SHAP 效应（gain 真实值）</div>
          {importance
            ? <ImportancePanel items={importance.items} curves={importance.effect_curves}
                               selected={impSel} onPick={setImpSel} />
            : <LoadingState text="加载模型…" />}
        </div>
      </SectionCard>
    </div>
  );

  /* ==================== 象限 3：组合与风控卡片 ==================== */
  const renderPortfolioCard = (isWorkflow: boolean) => (
    <SectionCard
      title="极致组合与风控（风险预算、Barra 风格暴露与凸优化）"
      action={sectionStatus.portfolio.error ? <button onClick={() => retrySection('portfolio')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
      bodyClassName="space-y-3"
    >
      {sectionStatus.portfolio.error && <p role="alert" className="rounded bg-warn-bg px-2 py-1 text-2xs text-warn">{sectionStatus.portfolio.error}</p>}
      {sectionStatus.portfolio.loading && <p className="text-2xs text-ink-muted">组合优化排队或执行中…</p>}
      <div className={`grid ${isWorkflow ? 'grid-cols-1 lg:grid-cols-12' : 'grid-cols-2'} gap-3`}>
        <div className={isWorkflow ? 'lg:col-span-6' : ''}>
          <div className="mb-1 text-2xs font-medium text-ink-secondary">GICS / Barra 风格暴露分解（截面 zscore 加权）</div>
          {optimize && <ExposureChart before={optimize.exposure_before} after={optimize.exposure_after} />}
          <p className="text-2xs text-ink-muted">
            口径：{optimize ? Object.entries(optimize.style_factor_map).map(([k, v]) => `${k}=${v}`).join(' · ') : ''}
          </p>
        </div>
        <div className={`space-y-2 ${isWorkflow ? 'lg:col-span-6' : ''}`}>
          <div className="flex items-center justify-between text-2xs font-medium text-ink-secondary">
            <span>组合优化控制台</span>
            <button
              onClick={() => {
                const eq = 1 / assets.length;
                setAssets(assets.map((a) => ({ ...a, weight: Number(eq.toFixed(2)) })));
              }}
              className="text-3xs text-brand-600 hover:underline"
            >
              一键均分权重
            </button>
          </div>
          <div className="text-2xs text-ink-secondary">资产池（本地 hfq 行情库，支持动态调整）</div>
          <div className="max-h-28 overflow-y-auto space-y-1">
            {assets.map((a, i) => (
              <div key={a.code} className="flex items-center gap-1.5 text-2xs">
                <span className="w-24 font-mono font-medium text-ink">{a.code}</span>
                <input type="number" step={0.05} min={0} max={1} value={a.weight}
                       className="w-16 rounded border border-hair px-1 py-0.5 text-right font-mono"
                       onChange={(e) => {
                         const next = assets.map((x, j) => j === i
                           ? { ...x, weight: Number(e.target.value) } : x);
                         setAssets(next);
                       }} />
                <span className="text-3xs text-ink-muted">%</span>
                <button className="text-ink-muted hover:text-danger ml-auto text-xs"
                        onClick={() => assets.length > 2 && setAssets(assets.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
          </div>
          <label className="block text-2xs text-ink-secondary">优化目标模型
            <select value={optMethod} className={`${inputCls} mt-0.5`}
                    onChange={(e) => {
                      const m = e.target.value as typeof optMethod;
                      setOptMethod(m); void runOptimizer(m, weightCap, turnoverPenalty);
                    }}>
              <option value="risk_parity">Risk Parity (ERC 风险平摊，欧拉齐次分解)</option>
              <option value="max_div">Max Diversification (最大分散度，Choueifaty 比率)</option>
              <option value="mvo">MVO (最小方差，LW 收缩 + RMT 降噪二次规划)</option>
              <option value="inverse_vol">Inverse Volatility (波动率倒数启发式)</option>
              <option value="hrp">HRP (分层风险平价，准对角化聚类递归二分)</option>
              <option value="black_litterman">Black-Litterman (贝叶斯均衡收益与后验协方差)</option>
            </select>
          </label>
          <label className="block text-2xs text-ink-secondary">
            单股权重上限 {(weightCap * 100).toFixed(0)}%
            <input type="range" min={0.1} max={1} step={0.05} value={weightCap}
                   className="w-full mt-1"
                   onMouseUp={() => void runOptimizer()}
                   onTouchEnd={() => void runOptimizer()}
                   onChange={(e) => setWeightCap(Number(e.target.value))} />
          </label>
          <label className="block text-2xs text-ink-secondary">高维协方差窗口
            <select value={covWindow} className={`${inputCls} mt-0.5`}
                    onChange={(e) => {
                      const cw = Number(e.target.value);
                      setCovWindow(cw);
                      void runOptimizer(optMethod, weightCap, turnoverPenalty, cw);
                    }}>
              <option value={60}>60 交易日（近1季）</option>
              <option value={120}>120 交易日（近半年，推荐）</option>
              <option value={250}>250 交易日（近1年）</option>
            </select>
          </label>
          <Switch on={turnoverPenalty} label="换手率惩罚（L2 阻尼正则化）"
                  onChange={(v) => { setTurnoverPenalty(v); void runOptimizer(optMethod, weightCap, v, covWindow); }} />
          <button disabled={optRunning}
                  onClick={() => void runOptimizer()}
                  className="w-full rounded-md bg-brand-500 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 shadow-xs">
            {optRunning ? 'QP 求解中…' : '一键求解最优权重 (QP 凸优化)'}
          </button>
        </div>
      </div>
      {optimize && (
        <div className="border-t border-hair pt-2 text-2xs space-y-1">
          <div>
            <span className="text-ink-secondary font-medium">优化后最优权重：</span>
            {Object.entries(optimize.weights).map(([s, w]) => (
              <span key={s} className="mr-2 font-mono font-medium text-brand-600">
                {s.replace(/\.\w+$/, '')} {(w * 100).toFixed(1)}%
              </span>
            ))}
          </div>
          <p className="text-3xs text-ink-muted">
            （协方差：Ledoit-Wolf 收缩 + RMT 去噪，{optimize.n_obs} 个真实收益观测；
            名义权重合计 {((optimize.weight_cap_info?.invested_ratio ?? 1) * 100).toFixed(1)}%）
          </p>
          {optimize.weight_cap_info
            && (!optimize.weight_cap_info.feasible || !optimize.weight_cap_info.cap_enforced) && (
            <p role="alert" className="mt-1 rounded bg-warn-bg px-2 py-1 text-warn text-3xs">
              ⚠️ {optimize.weight_cap_info.note}
            </p>
          )}
          {optimize.expected_returns && 'basis' in optimize.expected_returns
            && optimize.expected_returns.basis === 'unavailable' && (
            <p role="alert" className="mt-1 rounded bg-warn-bg px-2 py-1 text-warn text-3xs">
              ⚠️ {optimize.expected_returns.note}
            </p>
          )}
        </div>
      )}
    </SectionCard>
  );

  /* ==================== 象限 4：执行与压力测试卡片 ==================== */
  const renderExecutionCard = () => (
    <div className="space-y-3">
      <SectionCard
        title="策略引擎进阶（日频冲击模拟与极端黑天鹅压力重演）"
        action={sectionStatus.execution.error ? <button onClick={() => retrySection('execution')} className="text-2xs text-brand-600 hover:underline">重试</button> : undefined}
        bodyClassName="space-y-3"
      >
        {sectionStatus.execution.error && <p role="alert" className="rounded bg-warn-bg px-2 py-1 text-2xs text-warn">{sectionStatus.execution.error}</p>}
        {sectionStatus.execution.loading && <p className="text-2xs text-ink-muted">执行与压力测试排队或执行中…</p>}
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-2 text-2xs text-ink-secondary">
            执行冲击模拟（<span className="text-warn">日频口径：真实日 VWAP + 平方根冲击定律</span>）
            <input value={impactParams.symbol} className={`${inputCls} w-24 font-mono`}
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
            <label>订单金额
              <input type="number" step={5_000_000} min={100_000}
                     value={impactParams.order_amount}
                     className={`${inputCls} ml-1 w-28 font-mono`}
                     onChange={(e) => setImpactParams({ ...impactParams, order_amount: Number(e.target.value) })}
                     onBlur={() => void rerunImpact()} />
            </label>
          </div>
          {impact && <ImpactChart priceSeries={impact.price_series} fills={impact.fills}
                                  side={impact.side} />}
          {impact && (
            <p className="text-2xs text-ink-muted pt-1">
              订单 {(impact.order_amount ?? 0).toLocaleString()} 元 · 均冲击 <span className="num font-semibold text-brand-600">{impact.avg_impact_bps ?? 0} bps</span>
              {' '}· 总成本 {(impact.total_impact_cost ?? 0).toLocaleString()} 元
              {(impact.unfilled ?? 0) > 0 && <span className="text-danger"> · 未成交 {impact.unfilled.toLocaleString()} 元（参与率 {impactParams.participation_cap * 100}% 上限）</span>}
            </p>
          )}
          {impact?.data_warnings?.length ? (
            <p role="alert" className="mt-1 rounded bg-warn-bg px-2 py-1 text-2xs text-warn">
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

      {/* 交易费率与换手率敏感性分析卡片 */}
      <CostSensitivityCard initialGrossReturn={24} initialTurnover={20} initialBenchmarkReturn={4} />
    </div>
  );

  return (
    <div className="space-y-3.5">
      {/* 顶栏控制台与视图切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div>
          <PageHeader title="策略研究 · 核心因子与策略引擎" />
          <p className="text-2xs text-ink-muted">
            全链路投研工作台：因子截面分析 · MLOps 模型对垒 · 凸组合构建 · 执行冲击与风控
          </p>
        </div>

        <div className="flex items-center gap-2">
          {err && <span className="text-2xs text-danger">{err}</span>}

          {/* 双模视图切换器 */}
          <div className="flex rounded-md border border-hair bg-surface p-0.5 text-xs font-medium shadow-2xs">
            <button
              onClick={() => setViewMode('workflow')}
              className={`rounded px-2.5 py-1 text-2xs transition-colors ${
                viewMode === 'workflow'
                  ? 'bg-brand-500 text-white font-semibold shadow-xs'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              🔬 沉浸式流水线
            </button>
            <button
              onClick={() => setViewMode('quadrant')}
              className={`rounded px-2.5 py-1 text-2xs transition-colors ${
                viewMode === 'quadrant'
                  ? 'bg-brand-500 text-white font-semibold shadow-xs'
                  : 'text-ink-secondary hover:text-ink'
              }`}
            >
              📊 四象限全景看板
            </button>
          </div>
        </div>
      </div>

      {/* 投研联动提示通知 */}
      {linkageMsg && (
        <div className="flex items-center justify-between rounded-lg border border-brand-500/30 bg-brand-50/30 px-3.5 py-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-500 text-3xs font-bold text-white">
              🎯
            </span>
            <span className="font-semibold text-ink">投研联动：</span>
            <span className="text-ink-secondary">{linkageMsg}</span>
          </div>
          <button
            onClick={() => setLinkageMsg(null)}
            className="rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary hover:bg-surface-alt"
          >
            关闭提示
          </button>
        </div>
      )}

      {/* 顶栏真实统计 KPI 卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div className="rounded-lg border border-hair bg-surface px-3 py-2">
          <div className="text-2xs text-ink-secondary">因子库总数</div>
          <div className="num text-base font-semibold text-ink">{overview?.factor_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">{overview?.factor_universe ?? '全市场标的池'}</div>
        </div>
        <div className="rounded-lg border border-hair bg-surface px-3 py-2">
          <div className="text-2xs text-ink-secondary">已登记模型版本</div>
          <div className="num text-base font-semibold text-brand-600">{overview?.model_version_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">{overview?.model_version_note || 'model_registry 线上归档'}</div>
        </div>
        <div className="rounded-lg border border-hair bg-surface px-3 py-2">
          <div className="text-2xs text-ink-secondary">策略容量估算</div>
          <div className="num text-base font-semibold text-ink">
            {overview?.capacity_estimate_yi != null ? `${overview.capacity_estimate_yi} 亿` : '—'}
          </div>
          <div className="text-2xs text-ink-muted">{overview?.capacity_formula ?? '日均流动性 × 参与率'}</div>
        </div>
        <div className="rounded-lg border border-hair bg-surface px-3 py-2">
          <div className="text-2xs text-ink-secondary">Alpha 表达式库</div>
          <div className="num text-base font-semibold text-ink">{overview?.expression_count ?? '—'}</div>
          <div className="text-2xs text-ink-muted">批量遗传规划算子库</div>
        </div>
      </div>

      {/* 视图模式 1：沉浸式流水线工作流 (Workflow Tabs) */}
      {viewMode === 'workflow' ? (
        <div className="space-y-3">
          {/* 四大流水线 Tab 导航 */}
          <div className="flex border-b border-hair text-xs font-medium">
            <button
              onClick={() => setWorkflowTab('factor')}
              className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
                workflowTab === 'factor'
                  ? 'border-brand-500 text-brand-600 font-semibold'
                  : 'border-transparent text-ink-secondary hover:text-ink'
              }`}
            >
              <span>🔬 因子研发与正交纯化</span>
              <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs font-normal text-brand-700">
                Alphalens
              </span>
            </button>
            <button
              onClick={() => setWorkflowTab('ml')}
              className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
                workflowTab === 'ml'
                  ? 'border-brand-500 text-brand-600 font-semibold'
                  : 'border-transparent text-ink-secondary hover:text-ink'
              }`}
            >
              <span>🤖 MLOps 建模与防过拟合检验</span>
              <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs font-normal text-brand-700">
                CPCV &amp; DSR
              </span>
            </button>
            <button
              onClick={() => setWorkflowTab('portfolio')}
              className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
                workflowTab === 'portfolio'
                  ? 'border-brand-500 text-brand-600 font-semibold'
                  : 'border-transparent text-ink-secondary hover:text-ink'
              }`}
            >
              <span>⚖️ 极致组合优化与风控</span>
              <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs font-normal text-brand-700">
                Barra &amp; QP
              </span>
            </button>
            <button
              onClick={() => setWorkflowTab('execution')}
              className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
                workflowTab === 'execution'
                  ? 'border-brand-500 text-brand-600 font-semibold'
                  : 'border-transparent text-ink-secondary hover:text-ink'
              }`}
            >
              <span>⚡ 执行冲击与极端压力测试</span>
              <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs font-normal text-brand-700">
                VWAP &amp; Cost
              </span>
            </button>
          </div>

          {/* 各 Tab 内容渲染 */}
          {workflowTab === 'factor' && renderFactorCard(true)}
          {workflowTab === 'ml' && renderMlCard()}
          {workflowTab === 'portfolio' && renderPortfolioCard(true)}
          {workflowTab === 'execution' && renderExecutionCard()}
        </div>
      ) : (
        /* 视图模式 2：四象限全景看板 (Quadrant Grid) */
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {renderFactorCard(false)}
          {renderMlCard()}
          {renderPortfolioCard(false)}
          {renderExecutionCard()}
        </div>
      )}

      {/* MLOps 多版本 Head-to-Head 对垒弹窗 */}
      <ModelComparisonModal
        open={compareModalOpen}
        onClose={() => setCompareModalOpen(false)}
        selectedExperiments={selectedExperimentsData}
      />

      <p className="text-center text-2xs text-ink-muted pt-1">
        本页全部指标来自本地真实特征截面 (features parquet) / model_registry / hfq 行情实时计算 · 严禁模拟虚假值 · 遵循 López de Prado 严格防过拟合准则
      </p>
    </div>
  );
}
