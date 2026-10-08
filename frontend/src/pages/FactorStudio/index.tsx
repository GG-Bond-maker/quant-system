/**
 * 因子自动化挖掘工作室 (Factor Studio)。
 *
 * 体系化三级工作流架构：
 * 1. 【单因子深度检验 (Factor Analytics)】：对标 Alphalens & Barra，CumSum IC、月度热力图、扣费分层净值、单调性、风格雷达、半衰期衰减与 AST 解释；
 * 2. 【多因子相关性与纯化 (Multifactor & Orthogonalization)】：因子资产沉淀、流转选股与回测闭环、残差正交纯化、截面相关性矩阵；
 * 3. 【AI 因子挖掘与遗传进化 (AI Mining & Evolution)】：GP 遗传进化、双轴适应度曲线、Top 表达式、自然语言因子生成、任务历史。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { ApiError } from '@/api/client';
import {
  studioApi,
  type FactorReportResult,
  type GpStatus,
} from '@/api/production';
import { PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

import NlFactorCard from './NlFactorCard';
import FactorLab from './FactorLab';
import AlphalensAnalyticsCard from './AlphalensAnalyticsCard';
import QuantileSpreadCard from './QuantileSpreadCard';
import FactorExposureRadarCard from './FactorExposureRadarCard';
import FactorDecayCard from './FactorDecayCard';
import AlphaExplainabilityCard from './AlphaExplainabilityCard';

const ALL_FIELDS = [
  'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_60',
  'vol_5', 'vol_10', 'vol_20', 'vol_60',
  'ma_gap_5', 'ma_gap_20', 'ma_gap_60',
  'rsi_6', 'rsi_14', 'boll_pos',
  'skew_ret_20', 'kurt_ret_20', 'v_rank_20',
  'atr_14', 'hl_range', 'ma_slope_20',
];

const PRESET_EXPRS = [
  { label: '5日动量反转', expr: 'Sub(ret_5, Ref(ret_5, 10))' },
  { label: '低波加速动量', expr: 'Mul(ret_20, Sub(vol_10, vol_60))' },
  { label: '均线偏离强度', expr: 'Div(ma_gap_20, vol_20)' },
  { label: '量价博弈动量', expr: 'Mul(ret_5, v_rank_20)' },
  { label: 'RSI技术突破', expr: 'Sub(rsi_14, 50)' },
];

const FIELD_HINTS: Record<string, string> = {
  ret_1: '1 日收益率', ret_3: '3 日收益率', ret_5: '5 日收益率',
  ret_10: '10 日收益率', ret_20: '20 日收益率', ret_60: '60 日收益率',
  vol_5: '5 日波动率', vol_10: '10 日波动率', vol_20: '20 日波动率', vol_60: '60 日波动率',
  ma_gap_5: '5 日均线偏离度', ma_gap_20: '20 日均线偏离度', ma_gap_60: '60 日均线偏离度',
  rsi_6: '6 日 RSI', rsi_14: '14 日 RSI',
  boll_pos: '布林带位置 (0=下轨, 1=上轨)',
  skew_ret_20: '20 日收益偏度', kurt_ret_20: '20 日收益峰度',
  v_rank_20: '20 日成交量分位',
  atr_14: '14 日 ATR', hl_range: '日内振幅', ma_slope_20: '20 日均线斜率',
};

const HISTORY_KEY = 'aqp:gp:history';
const inputCls =
  'w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300 bg-surface';

interface HistoryEntry {
  task_id: string;
  params: Record<string, unknown>;
  started_at: string;
  status: string;
}

type TabType = 'analytics' | 'multifactor' | 'mining';

export default function FactorStudio() {
  useTheme();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabType>('analytics');

  // --- Tab 1: 单因子深度检验状态 ---
  const [targetExpr, setTargetExpr] = useState<string>('Sub(ret_5, Ref(ret_5, 10))');
  const [checkHorizon, setCheckHorizon] = useState<number>(5);
  const [report, setReport] = useState<FactorReportResult | null>(null);
  const [reportLoading, setReportLoading] = useState<boolean>(false);
  const [reportErr, setReportErr] = useState<string | null>(null);
  const reportTask = useAbortableTask();

  // --- Tab 3: AI 挖掘任务状态 ---
  const [fields, setFields] = useState(['ret_5', 'vol_20', 'rsi_14']);
  const [population, setPopulation] = useState(20);
  const [generations, setGenerations] = useState(6);
  const [horizon, setHorizon] = useState(5);
  const [seed, setSeed] = useState(42);
  const [corrPenalty, setCorrPenalty] = useState(0.05);
  const [modalExpr, setModalExpr] = useState<string | null>(null);
  const [status, setStatus] = useState<GpStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [taskLost, setTaskLost] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [copiedExpr, setCopiedExpr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // 投研流转到选股与组合优化
  const sendToScreener = (expr: string, name = '因子选股') => {
    navigate(`/screener?custom_factor=${encodeURIComponent(expr)}&factor_name=${encodeURIComponent(name)}`);
  };

  const sendToPortfolio = (expr: string) => {
    const candidateAssets = [
      { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.25 },
      { code: '600036', name: '招商银行', type: 'stock', weight: 0.25 },
      { code: '300750', name: '宁德时代', type: 'stock', weight: 0.25 },
      { code: '002594', name: '比亚迪', type: 'stock', weight: 0.25 },
    ];
    sessionStorage.setItem(
      'aqp:portfolio:import_assets',
      JSON.stringify({ source: 'FactorStudio', expr, assets: candidateAssets }),
    );
    navigate('/portfolio?from=factor_studio');
  };

  // 加载本地历史
  useEffect(() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      if (raw) setHistory(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, []);

  const persistHistory = (h: HistoryEntry[]) => {
    setHistory(h);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(h));
    } catch {
      /* ignore */
    }
  };

  // 执行单因子深度检验
  const runDeepCheck = useCallback(
    async (exprToRun = targetExpr, h = checkHorizon) => {
      if (!exprToRun.trim()) {
        setReportErr('请输入有效的因子表达式');
        return;
      }
      const ctrl = reportTask.begin();
      setReportLoading(true);
      setReportErr(null);
      try {
        const res = await studioApi.factorReport(
          { expr: exprToRun.trim(), horizon: h },
          { signal: ctrl.signal },
        );
        if (ctrl.signal.aborted) return;
        setReport(res);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setReportErr(e instanceof ApiError ? e.message : '因子全景检验生成失败');
      } finally {
        if (reportTask.finish(ctrl)) setReportLoading(false);
      }
    },
    [targetExpr, checkHorizon, reportTask],
  );

  // 默认初始触发一次示例因子的深度检验
  useEffect(() => {
    void runDeepCheck(targetExpr, checkHorizon);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 轮询 GP 任务
  const poll = useCallback(async (taskId: string) => {
    try {
      const st = await studioApi.miningStatus(taskId);
      setStatus(st);
      setHistory((prev) => {
        const next = prev.map((h) => (h.task_id === taskId ? { ...h, status: st.status } : h));
        try {
          localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
        } catch {
          /* ignore */
        }
        return next;
      });
      if (st.status === 'DONE' || st.status === 'FAILED' || st.status === 'CANCELLED') {
        setRunning(false);
        if (timer.current) clearInterval(timer.current);
      }
    } catch (e) {
      if (e instanceof ApiError && (e.code === 51001 || e.code === 40400)) {
        setTaskLost(taskId);
        setHistory((prev) => {
          const next = prev.filter((h) => h.task_id !== taskId);
          try {
            localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
          } catch {
            /* ignore */
          }
          return next;
        });
      } else {
        setErr(e instanceof ApiError ? e.message : '状态查询失败');
      }
      setRunning(false);
      if (timer.current) clearInterval(timer.current);
    }
  }, []);

  const startMining = useCallback(async () => {
    setErr(null);
    setRunning(true);
    setStatus(null);
    setTaskLost(null);
    try {
      const res = await studioApi.startMining({
        fields,
        horizon,
        population,
        generations,
        seed,
        corr_penalty: corrPenalty,
      });
      const entry: HistoryEntry = {
        task_id: res.task_id,
        params: { fields, horizon, population, generations, seed, corr_penalty: corrPenalty },
        started_at: new Date().toISOString(),
        status: 'RUNNING',
      };
      persistHistory([entry, ...history].slice(0, 10));
      timer.current = setInterval(() => void poll(res.task_id), 2000);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '启动挖掘失败');
      setRunning(false);
    }
  }, [fields, horizon, population, generations, seed, poll, history]);

  const resumeTask = useCallback(
    (taskId: string) => {
      setErr(null);
      setTaskLost(null);
      setRunning(true);
      setStatus(null);
      timer.current = setInterval(() => void poll(taskId), 2000);
    },
    [poll],
  );

  const copyExpr = useCallback(async (expr: string) => {
    try {
      await navigator.clipboard.writeText(expr);
      setCopiedExpr(expr);
      setTimeout(() => setCopiedExpr(null), 1500);
    } catch {
      /* ignore */
    }
  }, []);

  const cancelRunning = useCallback(async () => {
    const tid = status?.task_id;
    if (!tid) return;
    try {
      await studioApi.cancelMining(tid);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '取消请求失败');
    }
  }, [status]);

  useEffect(() => () => {
    if (timer.current) clearInterval(timer.current);
  }, []);

  // 挖掘进化适应度图表
  const p = chartPalette();
  const miningOption = status?.fitness_curve?.length
    ? ({
        grid: { left: 48, right: 56, top: 28, bottom: 24 },
        tooltip: { trigger: 'axis' },
        legend: {
          data: ['Fitness', 'MeanIC'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        xAxis: {
          type: 'category',
          data: status.fitness_curve.map((_, i) => `Gen ${i + 1}`),
          axisLabel: { fontSize: 9, color: p.INKM },
        },
        yAxis: [
          {
            type: 'value',
            scale: true,
            name: 'Fitness',
            nameTextStyle: { fontSize: 9, color: p.INKM },
            axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(3) },
            splitLine: { lineStyle: { color: p.SUNKEN } },
          },
          {
            type: 'value',
            scale: true,
            name: 'MeanIC',
            nameTextStyle: { fontSize: 9, color: p.INKM },
            axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(4) },
            splitLine: { show: false },
          },
        ],
        series: [
          {
            name: 'Fitness',
            type: 'line',
            data: status.fitness_curve,
            lineStyle: { width: 1.6, color: CATEGORY_COLORS.C1 },
            itemStyle: { color: CATEGORY_COLORS.C1 },
            areaStyle: { opacity: 0.08 },
          },
          {
            name: 'MeanIC',
            type: 'line',
            yAxisIndex: 1,
            data: status.mean_ic_curve ?? [],
            lineStyle: { width: 1.4, color: CATEGORY_COLORS.C3 },
            itemStyle: { color: CATEGORY_COLORS.C3 },
            symbol: 'circle',
            symbolSize: 4,
          },
        ],
      } as echarts.EChartsOption)
    : null;
  const miningChartRef = useChart(miningOption);

  return (
    <div className="space-y-3.5">
      {/* 顶层页头 */}
      <PageHeader title="因子自动化挖掘工作室" />

      {/* 顶层三大工作流导航 Tab */}
      <div className="flex border-b border-hair text-xs font-medium">
        <button
          onClick={() => setActiveTab('analytics')}
          className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
            activeTab === 'analytics'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>🔬 单因子深度检验</span>
          <span className="rounded bg-brand-50 px-1.5 py-0.2 text-3xs font-normal text-brand-700">
            Alphalens & Barra
          </span>
        </button>
        <button
          onClick={() => setActiveTab('multifactor')}
          className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
            activeTab === 'multifactor'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>📊 多因子相关性与纯化</span>
          <span className="rounded bg-surface-alt px-1.5 py-0.2 text-3xs font-normal text-ink-muted">
            正交纯化 & 因子库
          </span>
        </button>
        <button
          onClick={() => setActiveTab('mining')}
          className={`flex items-center gap-1.5 px-4 py-2 border-b-2 transition-colors ${
            activeTab === 'mining'
              ? 'border-brand-500 text-brand-600 font-semibold'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>🧬 AI 因子挖掘与遗传进化</span>
          <span className="rounded bg-surface-alt px-1.5 py-0.2 text-3xs font-normal text-ink-muted">
            GP & 自然语言
          </span>
        </button>
      </div>

      {/* ===================== TAB 1: 单因子深度检验 ===================== */}
      {activeTab === 'analytics' && (
        <div className="space-y-4">
          {/* 因子选择与参数配置栏 */}
          <div className="rounded-lg border border-hair bg-surface p-3.5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-ink">单因子全景体检配置</span>
                <span className="text-2xs text-ink-muted">
                  输入 Alpha 表达式或直接选择预设经典因子
                </span>
              </div>
              <div className="flex items-center gap-2 text-2xs">
                <span className="text-ink-secondary">快速预设:</span>
                <div className="flex flex-wrap gap-1">
                  {PRESET_EXPRS.map((pItem) => (
                    <button
                      key={pItem.label}
                      onClick={() => {
                        setTargetExpr(pItem.expr);
                        void runDeepCheck(pItem.expr, checkHorizon);
                      }}
                      className="rounded border border-hair bg-surface px-2 py-0.5 text-3xs text-ink-secondary hover:border-brand-400 hover:text-brand-600"
                    >
                      {pItem.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              <input
                value={targetExpr}
                onChange={(e) => setTargetExpr(e.target.value)}
                placeholder="输入待检验的 Alpha 表达式，例如: Sub(ret_5, Ref(ret_5, 10))"
                className={`${inputCls} min-w-[280px] flex-1 font-mono`}
              />
              <div className="flex items-center gap-1.5 text-2xs text-ink-secondary">
                <span>预测周期 H:</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={checkHorizon}
                  onChange={(e) => setCheckHorizon(Number(e.target.value))}
                  className="w-16 rounded border border-hair bg-surface px-2 py-1 text-center font-mono outline-none focus:border-brand-300"
                />
                <span>日</span>
              </div>
              <button
                onClick={() => void runDeepCheck(targetExpr, checkHorizon)}
                disabled={reportLoading || !targetExpr.trim()}
                className="rounded bg-brand-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
              >
                {reportLoading ? '全景体检中…' : '执行全景体检'}
              </button>
            </div>

            {reportErr && (
              <div className="rounded-md bg-danger-bg px-3 py-1.5 text-2xs text-danger">
                {reportErr}
              </div>
            )}
          </div>

          {/* 全景体检四大组件 */}
          {report && (
            <div className="space-y-4">
              {/* 1. Alphalens IC 深度检验看板 */}
              <AlphalensAnalyticsCard report={report} />

              {/* 2. 分层超额与交易成本穿透看板 */}
              <QuantileSpreadCard report={report} />

              {/* 3. Barra 风格暴露雷达看板 */}
              <FactorExposureRadarCard
                report={report}
                onNavigateToOrtho={() => setActiveTab('multifactor')}
              />

              {/* 4. 因子 IC 衰减与换手半衰期分析 */}
              <FactorDecayCard initialFactor="ret_5" />

              {/* 5. Alpha 表达式 AST 语法分解与经济学解释 */}
              <AlphaExplainabilityCard expression={report.expr} horizon={report.horizon} />
            </div>
          )}
        </div>
      )}

      {/* ===================== TAB 2: 多因子相关性与纯化 ===================== */}
      {activeTab === 'multifactor' && (
        <div className="space-y-4">
          <FactorLab
            initialExpr={targetExpr}
            horizon={checkHorizon}
            onSelectForDeepCheck={(expr) => {
              setTargetExpr(expr);
              setActiveTab('analytics');
              void runDeepCheck(expr, checkHorizon);
            }}
          />
        </div>
      )}

      {/* ===================== TAB 3: AI 因子挖掘与遗传进化 ===================== */}
      {activeTab === 'mining' && (
        <div className="space-y-4">
          {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}
          {taskLost && (
            <div className="flex items-center justify-between rounded-md bg-warn-bg px-3 py-2 text-xs text-warn">
              <span>任务 {taskLost.slice(0, 8)}… 已失效（服务重启或任务过期），无法继续轮询。</span>
              <button
                onClick={() => void startMining()}
                disabled={running || fields.length < 2}
                className="rounded-md bg-warn px-2.5 py-1 text-2xs font-medium text-white hover:bg-warn-text disabled:opacity-60"
              >
                重新启动挖掘
              </button>
            </div>
          )}

          {/* 自然语言生成因子 */}
          <NlFactorCard />

          <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
            {/* 挖掘任务配置 (4 列) */}
            <SectionCard title="挖掘任务配置" bodyClassName="space-y-3" className="xl:col-span-4">
              <div>
                <div className="mb-1 text-2xs text-ink-secondary">
                  参与挖掘的因子字段 (真实 features 列，2~6 个 · hover 查看说明)
                </div>
                <div className="flex max-h-40 flex-wrap gap-1 overflow-auto">
                  {ALL_FIELDS.map((f) => (
                    <button
                      key={f}
                      title={FIELD_HINTS[f] ?? f}
                      onClick={() =>
                        setFields((prev) =>
                          prev.includes(f)
                            ? prev.filter((x) => x !== f)
                            : prev.length < 6
                            ? [...prev, f]
                            : prev,
                        )
                      }
                      className={`rounded px-1.5 py-0.5 font-mono text-2xs border ${
                        fields.includes(f)
                          ? 'border-brand-400 bg-brand-50 text-brand-700'
                          : 'border-hair text-ink-secondary hover:bg-surface-alt'
                      }`}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-2xs text-ink-secondary">
                <label>
                  种群规模
                  <input
                    type="number"
                    min={10}
                    max={80}
                    value={population}
                    className={`${inputCls} mt-0.5`}
                    onChange={(e) => setPopulation(Number(e.target.value))}
                  />
                </label>
                <label>
                  进化代数
                  <input
                    type="number"
                    min={3}
                    max={20}
                    value={generations}
                    className={`${inputCls} mt-0.5`}
                    onChange={(e) => setGenerations(Number(e.target.value))}
                  />
                </label>
                <label>
                  预测口径 (日)
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={horizon}
                    className={`${inputCls} mt-0.5`}
                    onChange={(e) => setHorizon(Number(e.target.value))}
                  />
                </label>
                <label>
                  相关性惩罚
                  <input
                    type="number"
                    step={0.01}
                    min={0.0}
                    max={0.5}
                    value={corrPenalty}
                    className={`${inputCls} mt-0.5`}
                    onChange={(e) => setCorrPenalty(Number(e.target.value))}
                  />
                </label>
                <label className="col-span-2">
                  随机种子
                  <input
                    type="number"
                    value={seed}
                    className={`${inputCls} mt-0.5`}
                    onChange={(e) => setSeed(Number(e.target.value))}
                  />
                </label>
              </div>

              {running ? (
                <button
                  onClick={() => void cancelRunning()}
                  className="w-full rounded-md bg-up py-2 text-xs font-medium text-white hover:bg-up-text"
                >
                  取消任务 (当前代完成后退出)
                </button>
              ) : (
                <button
                  disabled={fields.length < 2}
                  onClick={() => void startMining()}
                  className="w-full rounded-md bg-brand-500 py-2 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
                >
                  启动挖掘任务
                </button>
              )}
              <p className="text-2xs text-ink-muted">
                适应度 = |日均 Rank IC| − 复杂度惩罚 − 截面相关性惩罚；算子白名单来自表达式引擎，全部向后计算（PIT 安全）。
              </p>
            </SectionCard>

            {/* 进化曲线与生成表达式 (8 列) */}
            <div className="space-y-3 xl:col-span-8">
              <SectionCard title="进化进度与适应度曲线" bodyClassName="space-y-2">
                {status ? (
                  <>
                    <div className="flex items-center gap-3 text-2xs">
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          status.status === 'DONE'
                            ? 'bg-success-bg text-success'
                            : status.status === 'FAILED'
                            ? 'bg-danger-bg text-danger'
                            : status.status === 'CANCELLED'
                            ? 'bg-warn-bg text-warn'
                            : 'bg-brand-50 text-brand-700'
                        }`}
                      >
                        {status.status}
                      </span>
                      <span>
                        代数 {status.generation}/{status.total_generations}
                      </span>
                      <span>耗时 {status.elapsed_sec}s</span>
                      {status.status === 'RUNNING' && (
                        <div className="h-1.5 flex-1 rounded bg-surface-sunken">
                          <div
                            className="h-1.5 rounded bg-brand-500"
                            style={{
                              width: `${
                                (status.generation / Math.max(1, status.total_generations)) * 100
                              }%`,
                            }}
                          />
                        </div>
                      )}
                    </div>
                    <div ref={miningChartRef} className="h-40 w-full" />
                    {status.error && <p className="text-2xs text-danger">{status.error}</p>}
                  </>
                ) : (
                  <div className="py-10 text-center text-xs text-ink-muted">
                    尚无任务 —— 配置左侧参数并启动挖掘
                  </div>
                )}
              </SectionCard>

              <SectionCard title="新生成的 Alpha 表达式 (按适应度排序)">
                {status?.top_expressions?.length ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-2xs">
                      <thead>
                        <tr className="border-b border-hair text-ink-secondary">
                          <th className="text-left font-medium">#</th>
                          <th className="text-left font-medium">表达式</th>
                          <th className="text-center font-medium">Mean IC</th>
                          <th className="text-center font-medium">ICIR</th>
                          <th className="text-center font-medium">共线相关</th>
                          <th className="text-center font-medium">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {status.top_expressions.map((e, i) => (
                          <tr key={e.expr + i} className="border-b border-hair/50 hover:bg-surface-alt/50">
                            <td className="py-1.5 text-ink-muted">{i + 1}</td>
                            <td className="py-1.5 font-mono max-w-[200px] truncate" title={e.expr}>
                              {e.expr}
                            </td>
                            <td
                              className={`num text-center ${
                                e.mean_ic == null
                                  ? 'text-ink-muted'
                                  : e.mean_ic > 0
                                  ? 'text-danger'
                                  : 'text-success'
                              }`}
                            >
                              {e.mean_ic != null ? e.mean_ic.toFixed(4) : '—'}
                            </td>
                            <td className="num text-center">
                              {e.icir != null ? e.icir.toFixed(3) : '—'}
                            </td>
                            <td className="num text-center">
                              {e.max_base_corr != null ? (
                                <span className={e.max_base_corr >= 0.5 ? 'text-warn font-medium' : 'text-ink-muted'}>
                                  {e.max_base_corr.toFixed(2)}
                                </span>
                              ) : (
                                '—'
                              )}
                            </td>
                            <td className="text-center">
                              <div className="flex items-center justify-center gap-1">
                                <button
                                  onClick={() => setModalExpr(e.expr)}
                                  className="rounded border border-hair px-1.5 py-0.5 text-3xs text-ink-secondary hover:bg-surface-alt"
                                  title="查看 AST 语法树分解与经济学翻译"
                                >
                                  🌳 语法树
                                </button>
                                <button
                                  onClick={() => {
                                    setTargetExpr(e.expr);
                                    setActiveTab('analytics');
                                    void runDeepCheck(e.expr, horizon);
                                  }}
                                  className="rounded border border-brand-500/30 px-1.5 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50"
                                >
                                  深度检验
                                </button>
                                <button
                                  onClick={() => sendToScreener(e.expr, `AI因子#${i + 1}`)}
                                  className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt"
                                  title="带入多因子选股策略打分"
                                >
                                  🎯 选股
                                </button>
                                <button
                                  onClick={() => sendToPortfolio(e.expr)}
                                  className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt"
                                  title="提取组合去凸优化"
                                >
                                  💼 组合
                                </button>
                                <button
                                  onClick={() => void copyExpr(e.expr)}
                                  className="rounded border border-hair px-1.5 py-0.5 text-3xs hover:bg-surface-alt"
                                >
                                  {copiedExpr === e.expr ? '已复制' : '复制'}
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="py-6 text-center text-xs text-ink-muted">等待任务完成…</div>
                )}
              </SectionCard>
            </div>
          </div>

          {/* 任务历史记录 */}
          {history.length > 0 && (
            <SectionCard title="任务历史（本地保存，最近 10 条 · 点击「查看」恢复轮询）">
              <div className="max-h-40 overflow-auto">
                <table className="w-full text-2xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="border-b border-hair text-ink-secondary">
                      <th className="text-left font-medium">任务 ID</th>
                      <th className="text-left font-medium">参数配置</th>
                      <th className="text-center font-medium">启动时间</th>
                      <th className="text-center font-medium">状态</th>
                      <th className="text-center font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.task_id} className="border-b border-hair/50">
                        <td className="py-1 font-mono text-ink-muted">{h.task_id.slice(0, 8)}…</td>
                        <td className="py-1 font-mono text-ink-muted">
                          {`${(h.params.fields as string[])?.join(', ') ?? '—'} · pop=${
                            h.params.population
                          } gen=${h.params.generations} h=${h.params.horizon}`}
                        </td>
                        <td className="num text-center text-ink-muted">
                          {new Date(h.started_at).toLocaleString('zh-CN', { hour12: false })}
                        </td>
                        <td className="text-center">
                          <span
                            className={`rounded px-1.5 py-0.5 ${
                              h.status === 'DONE'
                                ? 'bg-success-bg text-success'
                                : h.status === 'FAILED'
                                ? 'bg-danger-bg text-danger'
                                : h.status === 'CANCELLED'
                                ? 'bg-warn-bg text-warn'
                                : 'bg-brand-50 text-brand-700'
                            }`}
                          >
                            {h.status}
                          </span>
                        </td>
                        <td className="text-center">
                          <button
                            onClick={() => resumeTask(h.task_id)}
                            disabled={running}
                            className="rounded border border-hair px-1.5 py-0.5 text-2xs hover:bg-surface-alt disabled:opacity-60"
                          >
                            查看
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>
          )}
        </div>
      )}

      {/* AST 语法树弹窗浮层 */}
      {modalExpr && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-4xl max-h-[85vh] overflow-auto rounded-xl border border-hair bg-surface shadow-2xl p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-hair pb-2">
              <span className="text-xs font-semibold text-ink">
                Alpha 表达式 AST 语法分解与经济学翻译
              </span>
              <button
                onClick={() => setModalExpr(null)}
                className="rounded px-2 py-0.5 text-xs text-ink-muted hover:bg-surface-alt hover:text-ink font-bold"
              >
                ✕ 关闭
              </button>
            </div>
            <AlphaExplainabilityCard expression={modalExpr} horizon={horizon} />
          </div>
        </div>
      )}
    </div>
  );
}
