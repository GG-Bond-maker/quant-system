/**
 * 组合回测页面。
 *
 * 布局参考用户截图：
 *   左侧：资产搜索/权重、时间范围、初始资金、策略类型、调仓频率、基准、开始回测
 *   中间：净值曲线、关键指标 KPI、年度收益分布
 *   右侧：组合性能详情、持仓变动堆叠图、回测状态
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';

import { ApiError } from '@/api/client';
import { portfolioApi } from '@/api/portfolio';
import ResearchDisclaimer from '@/components/ResearchDisclaimer';
import { hasMinimumRole } from '@/components/RequireAuth';
import { EmptyState, LoadingState, PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useAuthStore } from '@/stores/useAuthStore';
import type {
  AssetSearchItem, PortfolioAsset, PortfolioBacktestResult, PortfolioWeighting,
  RebalanceFreq,
} from '@/types/portfolio';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';

/* ==================== 常量 ==================== */
const REBALANCE_OPTIONS: Array<{ key: RebalanceFreq; label: string }> = [
  { key: 'M', label: '月度' },
  { key: 'Q', label: '季度' },
  { key: 'Y', label: '年度' },
  { key: 'none', label: '不调仓' },
];

const BENCHMARK_OPTIONS = [
  { key: '000300', label: '沪深300' },
  { key: '000905', label: '中证500' },
  { key: '000852', label: '中证1000' },
];

/** 权重方案：与后端 run_portfolio_backtest 的 weighting 参数一一对应（真实生效） */
const STRATEGY_OPTIONS: Array<{ key: PortfolioWeighting; label: string }> = [
  { key: 'user', label: '给定权重（自定义/等权）' },
  { key: 'risk_parity', label: '风险平价' },
  { key: 'max_div', label: '最大分散度' },
  { key: 'inverse_vol', label: '波动率倒数加权' },
];

const DEFAULT_ASSETS: PortfolioAsset[] = [
  { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.3 },
  { code: '600036', name: '招商银行', type: 'stock', weight: 0.4 },
  { code: '300750', name: '宁德时代', type: 'stock', weight: 0.3 },
];

/* ==================== 复权口径披露 ==================== */
/**
 * 复权口径标签：**只显示后端披露的事实**，不在前端替后端担保（对齐 Backtest 页
 * `basisLabel` 的先例）。`price_basis` 缺失（旧缓存 payload / 升级期间命中）时
 * 如实显示"口径未知"。
 */
function portfolioBasisLabel(result: PortfolioBacktestResult): string {
  const pb = result.price_basis;
  if (!pb) return '复权口径未知';
  if (pb.basis === 'qfq') return 'QFQ（前复权）';
  if (pb.basis === 'raw') return '不复权（备用源降级）';
  if (pb.basis === 'unknown') return '复权口径未知';
  const n = pb.raw_fallback_symbols?.length ?? 0;
  return `QFQ + 不复权混用（${n} 个标的回退）`;
}

/** 口径是否需要琥珀警示：非纯 QFQ（含缺失）一律警示，避免把不纯口径展示成纯 QFQ。 */
function portfolioBasisWarn(result: PortfolioBacktestResult): boolean {
  return !result.price_basis || result.price_basis.basis !== 'qfq';
}

/* ==================== 图表 Hook ==================== */
function useChart(ref: React.RefObject<HTMLDivElement | null>, option: echarts.EChartsOption | null) {
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    inst.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); inst.current = null; };
  }, []);
  useEffect(() => { if (option) inst.current?.setOption(option, true); }, [option]);
}

/* ==================== 子组件 ==================== */

function KpiCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-hair bg-surface px-3 py-3">
      <div className="text-2xs text-ink-secondary">{label}</div>
      <div className={`num mt-0.5 text-base font-semibold ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  );
}

function NetValueChart({ data }: { data: PortfolioBacktestResult['nav_curve'] }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['组合净值', '基准净值'], bottom: 0, textStyle: { fontSize: 11, color: p.INK2 } },
    grid: { left: 16, right: 16, top: 24, bottom: 32, containLabel: true },
    xAxis: { type: 'category', data: data.map((d) => d.date), axisLabel: { fontSize: 10, color: p.INKM } },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: p.SUNKEN } }, axisLabel: { color: p.INKM } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 0, height: 14, borderColor: p.HAIR }],
    series: [
      { name: '组合净值', type: 'line', showSymbol: false, smooth: true, lineStyle: { width: 1.5 }, data: data.map((d) => d.nav) },
      { name: '基准净值', type: 'line', showSymbol: false, smooth: true, lineStyle: { width: 1.5, type: 'dashed' }, data: data.map((d) => d.benchmark) },
    ],
    };
  }, [data, theme]);
  useChart(ref, option);
  return <div ref={ref} className="h-80 w-full" />;
}

function AnnualReturnsChart({ data }: { data: PortfolioBacktestResult['annual_returns'] }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
    tooltip: { trigger: 'axis', formatter: (pt: unknown) => {
      const ps = pt as Array<{ axisValue: string; data: number }>;
      return `${ps[0]?.axisValue ?? ''}<br/>组合 ${fmtPct((ps[0]?.data ?? 0) * 100)}`;
    }},
    grid: { left: 16, right: 16, top: 16, bottom: 24, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: p.INKM, formatter: (v: number) => fmtPct(v * 100) }, splitLine: { lineStyle: { color: p.SUNKEN } } },
    yAxis: { type: 'category', data: data.map((d) => `${d.year}年`), axisLabel: { fontSize: 10, color: p.INKM } },
    series: [{
      type: 'bar' as const,
      // 年度收益按涨跌语义着色（正收益=涨色红 / 负收益=跌色绿），与全站口径一致
      data: data.map((d) => ({
        value: d.portfolio,
        itemStyle: { color: d.portfolio >= 0 ? p.UP : p.DOWN },
      })),
      label: { show: true, position: 'right', fontSize: 9, color: p.INK2, formatter: (pt: echarts.DefaultLabelFormatterCallbackParams) => fmtPct(Number(pt.value) * 100) },
    }],
    };
  }, [data, theme]);
  useChart(ref, option);
  return <div ref={ref} className="h-64 w-full" />;
}

function HoldingsDriftChart({ data, codes }: { data: PortfolioBacktestResult['holdings_drift']; codes: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const pal = chartPalette();
    const dates = data.map((d) => d.date);
    const series = codes.map((code) => ({
      name: code,
      type: 'line' as const,
      stack: 'total',
      areaStyle: {},
      showSymbol: false,
      data: data.map((d) => (d.weights[code] ?? 0) * 100),
    }));
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: codes, bottom: 0, textStyle: { fontSize: 10, color: pal.INK2 }, type: 'scroll' },
      grid: { left: 16, right: 16, top: 16, bottom: 40, containLabel: true },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, color: pal.INKM } },
      yAxis: { type: 'value', max: 100, axisLabel: { color: pal.INKM, formatter: '{value}%' }, splitLine: { lineStyle: { color: pal.SUNKEN } } },
      series,
    };
  }, [data, codes, theme]);
  useChart(ref, option);
  return <div ref={ref} className="h-64 w-full" />;
}

/** 收益类指标列表（风险类指标单独用宫格卡展示，与设计稿一致） */
function PerformanceTable({ metrics }: { metrics: PortfolioBacktestResult['metrics'] }) {
  // I-5：后端 NaN 经 pydantic 变 null 后 `null*100===0` ⇒ 会显示假「+0.00%」，
  // 故 null/非有限值一律显示「—」。
  const pct = (v: number | null | undefined) =>
    v != null && Number.isFinite(v) ? fmtPct(v * 100) : '—';
  const rows = [
    { label: '总收益率', value: pct(metrics.total_return), tone: metrics.total_return != null ? pctClass(metrics.total_return) : undefined },
    { label: '年化收益率', value: pct(metrics.cagr), tone: metrics.cagr != null ? pctClass(metrics.cagr) : undefined },
    { label: '无风险利率', value: pct(metrics.risk_free) },
  ];
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between text-xs">
          <span className="text-ink-secondary">{r.label}</span>
          <span className={`num font-medium ${r.tone ?? 'text-ink'}`}>{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** 风险指标四宫格：波动率 / 卡玛比率 / 贝塔系数 / 阿尔法 */
function RiskGrid({ metrics, benchDegenerate }: {
  metrics: PortfolioBacktestResult['metrics'];
  /** I-5：基准不可用时 beta/alpha 退化值（1.0 / 0.0）不可解释，显示为不可用 */
  benchDegenerate?: boolean;
}) {
  const cells = [
    {
      label: '波动率',
      value: metrics.volatility != null && Number.isFinite(metrics.volatility)
        ? fmtPct(metrics.volatility * 100) : '—',
    },
    { label: '卡玛比率', value: metrics.calmar != null ? metrics.calmar.toFixed(2) : '—' },
    { label: '贝塔系数', value: benchDegenerate ? '—' : fmtNum(metrics.beta) },
    {
      label: '阿尔法',
      value: benchDegenerate ? '—' : fmtNum(metrics.alpha),
      tone: !benchDegenerate && metrics.alpha != null
        ? (metrics.alpha >= 0 ? 't-up' : 't-down') : undefined,
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-2">
      {cells.map((c) => (
        <div key={c.label} className="rounded-md border border-hair bg-surface-alt px-3 py-2.5 text-center">
          <div className="text-2xs text-ink-secondary">{c.label}</div>
          <div className={`num mt-0.5 text-base font-semibold ${c.tone ?? 'text-ink'}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

/* ==================== 主页面 ==================== */
export default function PortfolioBacktest() {
  const [assets, setAssets] = useState<PortfolioAsset[]>(() => {
    // 我的收藏「一键导入组合回测」：一次性读取预置标的，权重均分
    try {
      const raw = localStorage.getItem('AQP_PORTFOLIO_IMPORT');
      if (raw) {
        const parsed = JSON.parse(raw) as PortfolioAsset[];
        if (Array.isArray(parsed) && parsed.length) return parsed;
      }
    } catch { /* 忽略脏数据 */ }
    return DEFAULT_ASSETS;
  });
  useEffect(() => { localStorage.removeItem('AQP_PORTFOLIO_IMPORT'); }, []);
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<AssetSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [startDate, setStartDate] = useState(dayjs().subtract(2, 'year').format('YYYY-MM-DD'));
  const [endDate, setEndDate] = useState(dayjs().format('YYYY-MM-DD'));
  const [initialCash, setInitialCash] = useState(1_000_000);
  const [rebalance, setRebalance] = useState<RebalanceFreq>('M');
  const [benchmark, setBenchmark] = useState('000300');
  const [strategy, setStrategy] = useState<PortfolioWeighting>(STRATEGY_OPTIONS[0].key);
  const [result, setResult] = useState<PortfolioBacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** P2-4：组合回测（120s）在途请求的中断控制 */
  const btTask = useAbortableTask();
  // P1-23：POST /portfolio/backtest 后端要求 researcher；viewer 点必然 40300
  const role = useAuthStore((s) => s.user?.role);
  const canRun = hasMinimumRole(role, 'researcher');

  const totalWeight = useMemo(() => assets.reduce((s, a) => s + a.weight, 0), [assets]);
  const weightOk = Math.abs(totalWeight - 1) < 0.001;
  const dateOk = dayjs(endDate).isAfter(dayjs(startDate));
  /** I-5：基准首值为 0/缺失时后端 beta/alpha 会静默退化成 1.0/0.0，
   *  基准净值线也会消失 ⇒ 必须显式披露而非当正常结果展示。 */
  const benchDegenerate = result != null && result.nav_curve.length > 0
    && (!Number.isFinite(result.nav_curve[0].benchmark) || result.nav_curve[0].benchmark === 0);

  /* 资产搜索 debounce（I-6：序号守卫 + abort；失败不得吞成「无匹配」，
   * 300ms 内清空输入必须复位 searching，否则「…」永久卡在输入框上） */
  const searchSeqRef = useRef(0);
  const [searchError, setSearchError] = useState<string | null>(null);
  useEffect(() => {
    if (!query.trim()) {
      searchSeqRef.current += 1; // 作废在途请求
      setSearchResults([]); setSearchError(null); setSearching(false);
      return;
    }
    const seq = ++searchSeqRef.current;
    setSearching(true); setSearchError(null);
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      portfolioApi.search(query.trim(), ctrl.signal)
        .then((r) => {
          if (seq !== searchSeqRef.current) return;
          setSearchResults(r);
        })
        .catch((e) => {
          if (seq !== searchSeqRef.current) return;
          setSearchResults([]);
          setSearchError(e instanceof ApiError ? e.message : '搜索失败');
        })
        .finally(() => { if (seq === searchSeqRef.current) setSearching(false); });
    }, 300);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query]);

  const addAsset = (item: AssetSearchItem) => {
    if (assets.some((a) => a.code === item.code && a.type === item.type)) return;
    const weight = assets.length === 0 ? 1 : 0;
    setAssets((cur) => [...cur, { code: item.code, name: item.name, type: item.type, weight }]);
    setQuery('');
    setSearchResults([]);
  };

  const removeAsset = (idx: number) => {
    setAssets((cur) => cur.filter((_, i) => i !== idx));
  };

  const updateWeight = (idx: number, w: number) => {
    setAssets((cur) => cur.map((a, i) => (i === idx ? { ...a, weight: Math.max(0, Math.min(1, w)) } : a)));
  };

  const equalize = () => {
    if (assets.length === 0) return;
    const w = 1 / assets.length;
    setAssets((cur) => cur.map((a) => ({ ...a, weight: Number(w.toFixed(4)) })));
  };

  const run = useCallback(async () => {
    if (!weightOk || !dateOk) return;
    // P2-4：组合回测（数据抓取 + 计算）120s，卸载（切路由）时中断在途请求
    const ctrl = btTask.begin();
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await portfolioApi.backtest({
        assets: assets.map((a) => ({ code: a.code, type: a.type, weight: a.weight })),
        start_date: startDate,
        end_date: endDate,
        rebalance,
        benchmark,
        initial_cash: initialCash,
        weighting: strategy,
        cov_window: 60,
      }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setResult(res);
    } catch (e) {
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setError(e instanceof ApiError ? e.message : '回测失败');
    } finally {
      if (btTask.finish(ctrl)) setLoading(false);
    }
  }, [assets, benchmark, dateOk, endDate, initialCash, rebalance, startDate, strategy, weightOk, btTask]);

  return (
    <div className="space-y-3">
      <PageHeader title="组合回测" sub={<ResearchDisclaimer kind="backtest" />} />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        {/* 左侧：参数配置 */}
        <div className="xl:col-span-3 space-y-3">
          <SectionCard title="组合配置 & 回测参数">
            <div className="space-y-3">
              {/* 资产搜索 */}
              <div>
                <label className="text-2xs font-medium text-ink-secondary">组合资产</label>
                <div className="relative mt-1">
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="搜索代码 / 名称"
                    className="w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs outline-none focus:border-brand-300"
                  />
                  {searching && <span className="absolute right-2 top-1.5 text-2xs text-ink-muted">…</span>}
                  {searchResults.length > 0 && (
                    <div className="absolute z-10 mt-0.5 max-h-40 w-full overflow-auto rounded-md border border-hair bg-surface shadow-sm">
                      {searchResults.map((item) => (
                        <button key={`${item.type}-${item.code}`}
                          onClick={() => addAsset(item)}
                          className="flex w-full items-center justify-between px-2.5 py-1.5 text-left text-xs hover:bg-surface-alt">
                          <span className="truncate">{item.name}（{item.code}）</span>
                          <span className="shrink-0 rounded bg-surface-sunken px-1 py-0.5 text-2xs text-ink-muted">
                            {item.type === 'etf' ? 'ETF' : '股票'}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                  {/* I-6：搜索失败与「无匹配」必须区分，不得静默吞掉 */}
                  {searchError && (
                    <div className="absolute z-10 mt-0.5 w-full rounded-md border border-warn/30 bg-warn-bg px-2.5 py-1.5 text-2xs text-warn">
                      搜索失败：{searchError}
                    </div>
                  )}
                </div>

                {/* 资产列表 */}
                <div className="mt-2 space-y-1.5">
                  {assets.map((a, idx) => (
                    <div key={`${a.type}-${a.code}`} className="flex items-center gap-2 rounded-md border border-hair bg-surface-alt px-2 py-1.5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-ink">{a.name || a.code}</div>
                        <div className="text-2xs text-ink-muted">{a.code} · {a.type === 'etf' ? 'ETF' : '股票'}</div>
                      </div>
                      <div className="flex items-center gap-1">
                        <input
                          type="number" min={0} max={1} step={0.01}
                          value={a.weight}
                          onChange={(e) => updateWeight(idx, Number(e.target.value))}
                          className="w-14 rounded border border-hair px-1 py-0.5 text-right text-xs outline-none focus:border-brand-300"
                        />
                        <span className="text-2xs text-ink-muted">%</span>
                      </div>
                      <button onClick={() => removeAsset(idx)}
                        className="rounded p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink">
                        ×
                      </button>
                    </div>
                  ))}
                </div>

                <div className="mt-2 flex items-center justify-between">
                  <button onClick={equalize} className="text-xs text-brand-600 hover:underline">一键均分权重</button>
                  <span className={`num text-2xs ${weightOk ? 'text-ink-secondary' : 't-up'}`}>
                    当前权重和 {fmtPct(totalWeight * 100)} {weightOk ? '' : '（需等于 100%）'}
                  </span>
                </div>
              </div>

              <hr className="border-hair" />

              {/* 时间范围 */}
              <div className="grid grid-cols-2 gap-2">
                <label className="text-2xs text-ink-secondary">开始日期
                  <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
                    className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300" />
                </label>
                <label className="text-2xs text-ink-secondary">结束日期
                  <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
                    className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300" />
                </label>
              </div>
              {!dateOk && <p className="text-2xs t-up">结束日期必须晚于开始日期</p>}

              {/* 初始资金 */}
              <label className="text-2xs text-ink-secondary">初始资金
                <input type="number" min={10000} step={10000} value={initialCash}
                  onChange={(e) => setInitialCash(Number(e.target.value))}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300" />
              </label>

              {/* 策略类型 */}
              <label className="text-2xs text-ink-secondary">策略类型
                <select value={strategy} onChange={(e) => setStrategy(e.target.value as PortfolioWeighting)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300">
                  {STRATEGY_OPTIONS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
              </label>

              {/* 调仓频率 */}
              <label className="text-2xs text-ink-secondary">调仓频率
                <select value={rebalance} onChange={(e) => setRebalance(e.target.value as RebalanceFreq)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300">
                  {REBALANCE_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </label>

              {/* 基准 */}
              <label className="text-2xs text-ink-secondary">基准指数
                <select value={benchmark} onChange={(e) => setBenchmark(e.target.value)}
                  className="mt-1 w-full rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300">
                  {BENCHMARK_OPTIONS.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
                </select>
              </label>

              <button onClick={() => void run()} disabled={!canRun || loading || !weightOk || !dateOk}
                className="mt-2 w-full rounded-md bg-brand-500 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50">
                {loading ? '回测中…' : '开始回测'}
              </button>
              {!canRun && (
                <p className="mt-1 text-2xs text-ink-muted">
                  组合回测需要 researcher 及以上角色。
                </p>
              )}
            </div>
          </SectionCard>
        </div>

        {/* 中间：核心结果 */}
        <div className="xl:col-span-6 space-y-3">
          {error && (
            <div className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">{error}</div>
          )}
          {result && (
            <div className="rounded-md border border-brand-100 bg-brand-50 px-3 py-2 text-xs text-brand-700">
              回测完成。复权口径：{portfolioBasisLabel(result)}。
            </div>
          )}
          {/* 复权口径披露（审计 B7b F6 组合侧）：非纯 QFQ / 缺失时必须琥珀警示，
              此前硬编码"数据来源：AKShare"掩盖了 ETF 降级为不复权的事实。 */}
          {result && portfolioBasisWarn(result) && (
            <div role="alert"
              className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
              {result.price_basis?.note
                ?? '复权口径未知：部分标的未经平台数据源加载，结果口径无法确认。'}
            </div>
          )}
          {/* I-5：基准退化必须显式披露（beta/alpha 与基准线均不可解释） */}
          {result && benchDegenerate && (
            <div role="alert"
              className="rounded-md border border-warn/30 bg-warn-bg px-3 py-2 text-xs text-warn">
              基准序列不可用（首值为 0）⇒ 基准净值线不显示，且 Beta / Alpha 的 1.00 / 0.00
              是退化值、不可用于解释收益。
            </div>
          )}

          {!result && !loading && !error && (
            <EmptyState title="配置资产后点击「开始回测」" hint="权重和需等于 100%" />
          )}
          {loading && <LoadingState text="正在计算组合回测…" />}

          {result && (
            <div className="space-y-3">
              <SectionCard title="组合回测结果" bodyClassName="p-2">
                <NetValueChart data={result.nav_curve} />
              </SectionCard>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiCard label="年化收益率"
                  value={result.metrics.cagr != null && Number.isFinite(result.metrics.cagr)
                    ? fmtPct(result.metrics.cagr * 100) : '—'}
                  tone={result.metrics.cagr != null ? pctClass(result.metrics.cagr) : undefined} />
                <KpiCard label="最大回撤"
                  value={result.metrics.max_drawdown != null && Number.isFinite(result.metrics.max_drawdown)
                    ? fmtPct(result.metrics.max_drawdown * 100) : '—'} />
                <KpiCard label="夏普比率" value={fmtNum(result.metrics.sharpe)} />
                <KpiCard label="卡玛比率" value={result.metrics.calmar != null ? result.metrics.calmar.toFixed(2) : '—'} />
              </div>

              <SectionCard title="年度收益分布" bodyClassName="p-2">
                <AnnualReturnsChart data={result.annual_returns} />
              </SectionCard>
            </div>
          )}
        </div>

        {/* 右侧：风险与细节 */}
        <div className="xl:col-span-3 space-y-3">
          {result && (
            <>
              <SectionCard title="组合性能详情">
                <PerformanceTable metrics={result.metrics} />
              </SectionCard>

              <SectionCard title="风险指标">
                <RiskGrid metrics={result.metrics} benchDegenerate={benchDegenerate} />
              </SectionCard>

              <SectionCard title="持仓变动图" bodyClassName="p-2">
                <HoldingsDriftChart data={result.holdings_drift} codes={result.assets.map((a) => a.code)} />
              </SectionCard>

              <SectionCard title="回测状态">
                <div className="space-y-1 text-xs text-ink-secondary">
                  <div className="flex justify-between"><span>状态</span><span className="text-brand-600">回测完成</span></div>
                  <div className="flex justify-between"><span>交易日数</span><span className="num text-ink">{result.trading_days}</span></div>
                  <div className="flex justify-between"><span>初始资金</span><span className="num text-ink">{fmtNum(result.initial_cash)}</span></div>
                  <div className="flex justify-between"><span>调仓频率</span><span className="text-ink">{REBALANCE_OPTIONS.find((o) => o.key === result.rebalance)?.label}</span></div>
                  <div className="flex justify-between"><span>基准</span><span className="text-ink">{BENCHMARK_OPTIONS.find((b) => b.key === result.benchmark)?.label}</span></div>
                  <p className="pt-1 text-2xs text-ink-muted">复权口径：{portfolioBasisLabel(result)}。回测结果不代表未来收益。</p>
                </div>
              </SectionCard>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
