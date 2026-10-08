/**
 * 策略容量与业绩归因中心：
 * 1. 组合持仓流动性瓶颈与 Days-to-Liquidate 锁喉分析 + 全市场 Almgren-Chriss 冲击曲面；
 * 2. Menchero 多期连乘平滑 Brinson 行业归因（零残差）+ 时序累积瀑布走势；
 * 3. 个股微观选股贡献度穿透（Top 5 赢家/拖累）+ Barra 风格主动跟踪误差风险分解。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';

import { ApiError } from '@/api/client';
import {
  deskApi,
  type AttributionResult,
  type CapacityCurveResult,
  type PortfolioBottleneckResult,
} from '@/api/production';
import { PageHeader, SectionCard } from '@/components/ui';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

import CumulativeAttributionChart from './CumulativeAttributionChart';
import PortfolioBottleneckCard from './PortfolioBottleneckCard';
import StockContributionCard from './StockContributionCard';
import SectorActiveTiltCard from './SectorActiveTiltCard';
import BarraRiskDecompositionCard from './BarraRiskDecompositionCard';

const inputCls =
  'rounded-md border border-hair px-2 py-1 text-xs outline-none focus:border-brand-300';

const PRESET_PORTFOLIOS: Record<string, { label: string; assets: Array<{ code: string; weight: number }> }> = {
  hs300_core: {
    label: '沪深300白马核心 (茅台/平安/招行/宁德/五粮液)',
    assets: [
      { code: '600519.SH', weight: 0.25 },
      { code: '000858.SZ', weight: 0.20 },
      { code: '601318.SH', weight: 0.20 },
      { code: '600036.SH', weight: 0.20 },
      { code: '300750.SZ', weight: 0.15 },
    ],
  },
  growth_500: {
    label: '中证500成长先锋 (宁德/立讯/平安银/亿纬)',
    assets: [
      { code: '300750.SZ', weight: 0.35 },
      { code: '002475.SZ', weight: 0.25 },
      { code: '000001.SZ', weight: 0.25 },
      { code: '300014.SZ', weight: 0.15 },
    ],
  },
  dividend_defensive: {
    label: '红利高股息防御 (神华/长电/工行/陕煤)',
    assets: [
      { code: '601088.SH', weight: 0.30 },
      { code: '600900.SH', weight: 0.30 },
      { code: '601398.SH', weight: 0.20 },
      { code: '601225.SH', weight: 0.20 },
    ],
  },
  dual_pilot: {
    label: '双核心基准 (平安银行 + 宁德时代)',
    assets: [
      { code: '000001.SZ', weight: 0.50 },
      { code: '300750.SZ', weight: 0.50 },
    ],
  },
};

const DEFAULT_ASSETS = PRESET_PORTFOLIOS.dual_pilot.assets;
const WINDOW_OPTIONS = [30, 60, 120, 250];

type BenchmarkType = 'universe_equal' | 'custom_portfolio' | 'single_symbol';

const BENCHMARK_PRESETS = [
  { label: '本地全 A 股票池等权', type: 'universe_equal' as BenchmarkType, symbol: '' },
  { label: '沪深300 ETF (510300.SH)', type: 'single_symbol' as BenchmarkType, symbol: '510300.SH' },
  { label: '中证500 ETF (510500.SH)', type: 'single_symbol' as BenchmarkType, symbol: '510500.SH' },
  { label: '中证1000 ETF (512100.SH)', type: 'single_symbol' as BenchmarkType, symbol: '512100.SH' },
  { label: '创业板 ETF (159915.SZ)', type: 'single_symbol' as BenchmarkType, symbol: '159915.SZ' },
  { label: '自定义基准组合', type: 'custom_portfolio' as BenchmarkType, symbol: '' },
];

function CapacityCurveChart({ data }: { data: CapacityCurveResult }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data.curve || data.curve.length === 0) return null;
    const p = chartPalette();
    const xData = data.curve.map((c) => `${c.aum_yi}亿`);
    const netReturnData = data.curve.map((c) => c.net_return_pct);
    const netSharpeData = data.curve.map((c) => c.net_sharpe);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      legend: {
        data: ['净年化收益率 (%)', '净夏普比率'],
        bottom: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 48, right: 48, top: 24, bottom: 36 },
      xAxis: {
        type: 'category',
        data: xData,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: [
        {
          type: 'value',
          name: '净收益率',
          nameTextStyle: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            formatter: (v: number) => `${v.toFixed(1)}%`,
          },
        },
        {
          type: 'value',
          name: '净夏普',
          nameTextStyle: { fontSize: 9, color: p.INKM },
          splitLine: { show: false },
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            formatter: (v: number) => v.toFixed(2),
          },
        },
      ],
      series: [
        {
          name: '净年化收益率 (%)',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: netReturnData,
          lineStyle: { width: 2, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          markLine: {
            symbol: 'none',
            data: [
              {
                xAxis: `${data.optimal_aum_yi}亿`,
                lineStyle: { color: p.UP, type: 'dashed' as const, width: 2 },
                label: {
                  formatter: `黄金AUM: ${data.optimal_aum_yi}亿`,
                  position: 'insideEndTop' as const,
                  color: p.UP,
                  fontSize: 10,
                },
              },
              ...(data.breakeven_aum_yi != null
                ? [
                    {
                      xAxis: `${data.breakeven_aum_yi}亿`,
                      lineStyle: { color: p.DANGER, type: 'dotted' as const, width: 1.5 },
                      label: {
                        formatter: `盈亏平衡: ${data.breakeven_aum_yi}亿`,
                        position: 'insideEndTop' as const,
                        color: p.DANGER,
                        fontSize: 10,
                      },
                    },
                  ]
                : []),
            ],
          },
        },
        {
          name: '净夏普比率',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          showSymbol: false,
          data: netSharpeData,
          lineStyle: { width: 1.8, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
      ],
    } as echarts.EChartsOption;
  }, [data, theme]);

  const ref = useChart(option);
  if (!option) return null;
  return <div ref={ref} className="h-64 w-full" />;
}

export default function CapacityAttribution() {
  useTheme();
  const [capacity, setCapacity] = useState<{ aum_yi: number | null; formula: string } | null>(null);
  const [assets, setAssets] = useState(DEFAULT_ASSETS);
  const [attr, setAttr] = useState<AttributionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(120);

  // 容量宏观参数
  const [participationCap, setParticipationCap] = useState(0.01);
  const [holdings, setHoldings] = useState(20);
  const [rebalancePerYear, setRebalancePerYear] = useState(12);
  const [grossReturn, setGrossReturn] = useState(0.15);
  const [grossVol, setGrossVol] = useState(0.20);
  const [curveData, setCurveData] = useState<CapacityCurveResult | null>(null);
  const [curveLoading, setCurveLoading] = useState(false);

  // 持仓微观短板参数
  const [targetAum, setTargetAum] = useState(5e7); // 5000 万元
  const [liquidationParticipationCap, setLiquidationParticipationCap] = useState(0.10); // 10%
  const [liquidationDaysTarget, setLiquidationDaysTarget] = useState(3); // 3 交易日
  const [bottleneckData, setBottleneckData] = useState<PortfolioBottleneckResult | null>(null);
  const [bottleneckLoading, setBottleneckLoading] = useState(false);

  // 基准设置
  const [benchmarkType, setBenchmarkType] = useState<BenchmarkType>('universe_equal');
  const [benchmarkAssets, setBenchmarkAssets] = useState(DEFAULT_ASSETS);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState('510300.SH');
  const [importNotice, setImportNotice] = useState<string | null>(null);

  // 工作台 Tab 切换
  const [activeTab, setActiveTab] = useState<'capacity' | 'attribution' | 'alpha_risk'>('capacity');

  const attrTask = useAbortableTask();
  const capSeqRef = useRef(0);

  // 1. 加载宏观全市场容量
  const loadCapacity = useCallback(async () => {
    const seq = ++capSeqRef.current;
    setCurveLoading(true);
    try {
      const [value, curve] = await Promise.all([
        deskApi.capacity(participationCap, holdings, rebalancePerYear),
        deskApi.capacityCurve({
          participation_cap: participationCap,
          holdings,
          rebalance_per_year: rebalancePerYear,
          gross_return: grossReturn,
          gross_vol: grossVol,
        }).catch(() => null),
      ]);
      if (seq !== capSeqRef.current) return;
      setCapacity(value);
      setCurveData(curve);
    } catch (e) {
      if (seq !== capSeqRef.current) return;
      setErr(e instanceof ApiError ? e.message : '容量计算失败');
    } finally {
      if (seq === capSeqRef.current) setCurveLoading(false);
    }
  }, [participationCap, holdings, rebalancePerYear, grossReturn, grossVol]);

  useEffect(() => {
    const t = setTimeout(() => { void loadCapacity(); }, 400);
    return () => clearTimeout(t);
  }, [loadCapacity]);

  // 2. 快速计算持仓微观流动性短板（毫秒级响应，无需全量风格回归）
  const loadBottleneck = useCallback(async (aum: number, cap: number, days: number) => {
    setBottleneckLoading(true);
    try {
      const res = await deskApi.portfolioCapacity(assets, {
        target_aum: aum,
        participation_cap: cap,
        liquidation_days_target: days,
      });
      setBottleneckData(res);
    } catch {
      // 容错不阻塞
    } finally {
      setBottleneckLoading(false);
    }
  }, [assets]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadBottleneck(targetAum, liquidationParticipationCap, liquidationDaysTarget);
    }, 250);
    return () => clearTimeout(t);
  }, [loadBottleneck, targetAum, liquidationParticipationCap, liquidationDaysTarget]);

  // 3. 执行全量归因计算（Brinson多期 + 风格回归 + 个股微观穿透）
  const runAttr = useCallback(async () => {
    const ctrl = attrTask.begin();
    setBusy(true); setErr(null);
    try {
      const r = await deskApi.attribution(assets, windowDays, {
        benchmark_type: benchmarkType,
        ...(benchmarkType === 'custom_portfolio' ? { benchmark_assets: benchmarkAssets } : {}),
        ...(benchmarkType === 'single_symbol' ? { benchmark_symbol: benchmarkSymbol } : {}),
        target_aum: targetAum,
        participation_cap: liquidationParticipationCap,
        liquidation_days_target: liquidationDaysTarget,
      }, { signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setAttr(r);
      if (r.liquidity_bottleneck) {
        setBottleneckData(r.liquidity_bottleneck);
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '归因计算失败');
    } finally {
      if (attrTask.finish(ctrl)) setBusy(false);
    }
  }, [assets, windowDays, benchmarkType, benchmarkAssets, benchmarkSymbol, targetAum, liquidationParticipationCap, liquidationDaysTarget, attrTask]);

  useEffect(() => {
    void runAttr();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 一键导入模拟盘持仓
  const importFromPaperAccount = async () => {
    setBusy(true);
    try {
      const acc = await deskApi.account();
      const positions = acc.positions || {};
      const validPositions = Object.entries(positions)
        .filter(([_, p]) => (Number(p.market_value) || 0) > 0)
        .map(([code, p]) => ({
          code,
          weight: Number(p.market_value) || 0,
        }));

      if (!validPositions.length) {
        setImportNotice('模拟盘当前暂无持仓标的，已保留现有测试配置');
        setTimeout(() => setImportNotice(null), 3500);
        return;
      }
      const totalMv = validPositions.reduce((s, p) => s + p.weight, 0);
      const normalized = validPositions.map((p) => ({
        code: p.code,
        weight: Number((p.weight / totalMv).toFixed(4)),
      }));
      setAssets(normalized);
      setImportNotice(`成功导入模拟盘 ${normalized.length} 只持仓标的！已自动按市值比例归一。`);
      setTimeout(() => setImportNotice(null), 4000);
    } catch {
      setImportNotice('模拟盘持仓加载失败，请检查账户服务状态');
      setTimeout(() => setImportNotice(null), 4000);
    } finally {
      setBusy(false);
    }
  };

  // 预设组合快速加载
  const loadPresetPortfolio = (key: string) => {
    const p = PRESET_PORTFOLIOS[key];
    if (p) {
      setAssets(p.assets);
      setImportNotice(`已切换为【${p.label}】`);
      setTimeout(() => setImportNotice(null), 3000);
    }
  };

  // 权重求和提示
  const weightSum = assets.reduce((s, a) => s + (Number(a.weight) || 0), 0);
  const updateAsset = (i: number, patch: Partial<{ code: string; weight: number }>) =>
    setAssets(assets.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addAsset = () => setAssets([...assets, { code: '', weight: 0 }]);
  const removeAsset = (i: number) => setAssets(assets.filter((_, j) => j !== i));

  // 基准组合操作
  const updateBenchAsset = (i: number, patch: Partial<{ code: string; weight: number }>) =>
    setBenchmarkAssets(benchmarkAssets.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addBenchAsset = () =>
    setBenchmarkAssets([...benchmarkAssets, { code: '', weight: 0 }]);
  const removeBenchAsset = (i: number) =>
    setBenchmarkAssets(benchmarkAssets.filter((_, j) => j !== i));

  // 导出分析报表
  const exportReport = () => {
    const reportData = {
      export_time: new Date().toISOString(),
      assets,
      capacity_macro: capacity,
      capacity_curve: curveData,
      bottleneck_micro: bottleneckData,
      attribution: attr,
    };
    const blob = new Blob([JSON.stringify(reportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `capacity_attribution_report_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const fmtPct = (v: number | null | undefined, digits = 2) =>
    v == null ? '—' : `${(v * 100).toFixed(digits)}%`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader title="策略容量与业绩归因中心" />
        {/* 顶部快捷功能栏 */}
        <div className="flex items-center gap-2 text-2xs">
          <button
            onClick={() => void importFromPaperAccount()}
            disabled={busy}
            className="rounded-md border border-brand-300 bg-brand-50/50 px-2.5 py-1 font-medium text-brand-700 hover:bg-brand-100 disabled:opacity-60"
            title="一键读取当前实盘模拟账户中真实持仓标的与市值"
          >
            📥 导入模拟盘持仓
          </button>
          <div className="flex items-center gap-1 rounded-md border border-hair bg-surface px-2 py-1">
            <span className="text-ink-secondary">预设组合:</span>
            <select
              onChange={(e) => loadPresetPortfolio(e.target.value)}
              defaultValue="dual_pilot"
              className="bg-transparent text-2xs outline-none"
            >
              {Object.entries(PRESET_PORTFOLIOS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={exportReport}
            className="rounded-md border border-hair bg-surface px-2.5 py-1 text-ink-secondary hover:bg-surface-alt"
          >
            📑 导出报表
          </button>
        </div>
      </div>

      {importNotice && (
        <div className="rounded-md bg-brand-50 px-3 py-1.5 text-2xs font-medium text-brand-700">
          {importNotice}
        </div>
      )}

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}

      {/* 组合持仓编辑器与基准控制器 */}
      <SectionCard title="分析对象：持仓资产与基准设定" bodyClassName="space-y-3">
        <div className="flex flex-wrap items-end gap-2 text-2xs">
          {assets.map((a, i) => (
            <div key={i} className="flex items-center gap-1">
              <input
                value={a.code}
                placeholder="代码"
                onChange={(e) => updateAsset(i, { code: e.target.value })}
                className={`${inputCls} w-28 font-mono`}
              />
              <input
                type="number"
                step={0.05}
                min={0}
                max={1}
                value={a.weight}
                onChange={(e) => updateAsset(i, { weight: Number(e.target.value) })}
                className={`${inputCls} w-16`}
              />
              {assets.length > 1 && (
                <button
                  onClick={() => removeAsset(i)}
                  title="删除此行"
                  className="rounded border border-hair px-1 py-0.5 text-2xs text-danger hover:bg-danger-bg"
                >
                  ×
                </button>
              )}
            </div>
          ))}
          <button
            onClick={addAsset}
            disabled={assets.length >= 100}
            className="rounded-md border border-hair px-2 py-1 text-2xs hover:bg-surface-alt disabled:opacity-60"
          >
            + 添加资产
          </button>
          <button
            disabled={busy}
            onClick={() => void runAttr()}
            className="rounded-md bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {busy ? '正在计算中…' : '重新运行归因'}
          </button>
          {attr && (
            <span className="text-ink-muted">
              {attr.benchmark_desc} · 近 {attr.window_days} 交易日（{attr.n_obs} 个有效样本）
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4 text-2xs text-ink-secondary">
          <span>
            持仓权重和：{' '}
            <span
              className={`num font-semibold ${
                Math.abs(weightSum - 1) < 0.001 ? 'text-success' : 'text-warn'
              }`}
            >
              {weightSum.toFixed(2)}
            </span>
            {Math.abs(weightSum - 1) >= 0.001 && (
              <span className="ml-1 text-warn">（未完全等于 1，系统已自动归一化）</span>
            )}
          </span>

          <span className="text-ink-muted">·</span>
          <span>时间窗口:</span>
          {WINDOW_OPTIONS.map((w) => (
            <button
              key={w}
              onClick={() => setWindowDays(w)}
              className={`rounded px-1.5 py-0.5 text-2xs ${
                windowDays === w
                  ? 'bg-brand-50 font-medium text-brand-700'
                  : 'border border-hair text-ink-secondary hover:bg-surface-alt'
              }`}
            >
              {w} 日
            </button>
          ))}

          <span className="text-ink-muted">·</span>
          <div className="flex items-center gap-1.5">
            <span>业绩基准:</span>
            <select
              value={benchmarkType === 'single_symbol' ? benchmarkSymbol : benchmarkType}
              className={`${inputCls} max-w-xs`}
              onChange={(e) => {
                const val = e.target.value;
                const matchPreset = BENCHMARK_PRESETS.find((p) => p.symbol === val || p.type === val);
                if (matchPreset) {
                  setBenchmarkType(matchPreset.type);
                  if (matchPreset.type === 'single_symbol') {
                    setBenchmarkSymbol(matchPreset.symbol);
                  }
                }
              }}
            >
              {BENCHMARK_PRESETS.map((p) => (
                <option key={p.label} value={p.type === 'single_symbol' ? p.symbol : p.type}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* 自定义组合基准编辑器 */}
        {benchmarkType === 'custom_portfolio' && (
          <div className="flex flex-wrap items-center gap-2 rounded border border-hair bg-surface-alt p-2 text-2xs">
            <span className="font-medium text-ink">自定义基准成分：</span>
            {benchmarkAssets.map((b, i) => (
              <div key={i} className="flex items-center gap-1">
                <input
                  value={b.code}
                  placeholder="代码"
                  onChange={(e) => updateBenchAsset(i, { code: e.target.value })}
                  className={`${inputCls} w-24 font-mono`}
                />
                <input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={b.weight}
                  onChange={(e) => updateBenchAsset(i, { weight: Number(e.target.value) })}
                  className={`${inputCls} w-14`}
                />
                {benchmarkAssets.length > 1 && (
                  <button
                    onClick={() => removeBenchAsset(i)}
                    className="rounded border border-hair px-1 text-2xs text-danger hover:bg-danger-bg"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            <button
              onClick={addBenchAsset}
              disabled={benchmarkAssets.length >= 100}
              className="rounded border border-hair px-1.5 py-0.5 text-2xs hover:bg-surface"
            >
              + 基准标的
            </button>
          </div>
        )}
      </SectionCard>

      {/* 工作台主导航切换标签 */}
      <div className="flex border-b border-hair text-xs">
        <button
          onClick={() => setActiveTab('capacity')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2 font-medium transition-colors ${
            activeTab === 'capacity'
              ? 'border-brand-500 text-brand-700'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>⚖️ 策略容量与流动性短板</span>
          {bottleneckData?.bottleneck_symbol && (
            <span className="rounded bg-warn/20 px-1 text-2xs text-warn font-semibold">
              锁喉: {bottleneckData.bottleneck_symbol}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('attribution')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2 font-medium transition-colors ${
            activeTab === 'attribution'
              ? 'border-brand-500 text-brand-700'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>📊 Brinson 行业多期业绩归因</span>
          {attr && (
            <span className={`num text-2xs font-semibold ${attr.excess_return >= 0 ? 'text-danger' : 'text-success'}`}>
              超额 {fmtPct(attr.excess_return)}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('alpha_risk')}
          className={`flex items-center gap-1.5 border-b-2 px-4 py-2 font-medium transition-colors ${
            activeTab === 'alpha_risk'
              ? 'border-brand-500 text-brand-700'
              : 'border-transparent text-ink-secondary hover:text-ink'
          }`}
        >
          <span>🧬 个股贡献与 Barra 风格风险分解</span>
        </button>
      </div>

      {/* TAB 1: 策略容量与流动性短板 */}
      {activeTab === 'capacity' && (
        <div className="space-y-3">
          {/* 微观持仓流动性瓶颈与锁喉分析 */}
          <SectionCard
            title="组合微观持仓「流动性锁喉瓶颈分析」（Days to Liquidate 穿透）"
            bodyClassName="space-y-3"
          >
            <PortfolioBottleneckCard
              data={bottleneckData}
              targetAum={targetAum}
              participationCap={liquidationParticipationCap}
              liquidationDaysTarget={liquidationDaysTarget}
              onAumChange={setTargetAum}
              onCapChange={setLiquidationParticipationCap}
              onDaysChange={setLiquidationDaysTarget}
              loading={bottleneckLoading}
            />
          </SectionCard>

          {/* 宏观全市场 Almgren-Chriss 平方根冲击衰减曲面 */}
          <SectionCard
            title="全市场平方根市场冲击容量衰减曲面与黄金 AUM（Almgren-Chriss 模型）"
            bodyClassName="space-y-3"
          >
            <div className="flex items-baseline gap-3 rounded-md bg-surface-alt p-3">
              <span className="text-2xs text-ink-secondary">全市场基准容量上限：</span>
              <span className="num text-2xl font-semibold text-brand-700">
                {capacity?.aum_yi != null ? `${capacity.aum_yi} 亿元` : '—'}
              </span>
              <span className="text-2xs text-ink-muted">{capacity?.formula ?? ''}</span>
            </div>

            <div className="grid grid-cols-1 gap-3 border-b border-hair pb-3 sm:grid-cols-3">
              <label className="space-y-1 text-2xs text-ink-secondary">
                <span>单日参与上限 <span className="num font-medium text-ink">{(participationCap * 100).toFixed(1)}%</span> ADV</span>
                <input
                  type="range"
                  min={0.001}
                  max={0.3}
                  step={0.001}
                  value={participationCap}
                  onChange={(e) => setParticipationCap(Number(e.target.value))}
                  className="w-full accent-brand-500"
                />
                <span className="block text-ink-muted">0.1% ~ 30%（默认 1%）</span>
              </label>
              <label className="space-y-1 text-2xs text-ink-secondary">
                <span>持仓数量 <span className="num font-medium text-ink">{holdings}</span> 只</span>
                <input
                  type="range"
                  min={1}
                  max={100}
                  step={1}
                  value={holdings}
                  onChange={(e) => setHoldings(Number(e.target.value))}
                  className="w-full accent-brand-500"
                />
                <span className="block text-ink-muted">1 ~ 100（默认 20）</span>
              </label>
              <label className="space-y-1 text-2xs text-ink-secondary">
                <span>年调仓次数 <span className="num font-medium text-ink">{rebalancePerYear}</span> 次</span>
                <input
                  type="range"
                  min={1}
                  max={252}
                  step={1}
                  value={rebalancePerYear}
                  onChange={(e) => setRebalancePerYear(Number(e.target.value))}
                  className="w-full accent-brand-500"
                />
                <span className="block text-ink-muted">1 ~ 252（默认 12）</span>
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-4 text-2xs text-ink-secondary">
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-1.5">
                  <span>毛年化收益假设：</span>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    value={Math.round(grossReturn * 100)}
                    onChange={(e) => setGrossReturn(Number(e.target.value) / 100)}
                    className="w-16 rounded border border-hair px-1.5 py-0.5 text-right text-xs"
                  />
                  <span>%</span>
                </label>
                <label className="flex items-center gap-1.5">
                  <span>毛年化波动假设：</span>
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    value={Math.round(grossVol * 100)}
                    onChange={(e) => setGrossVol(Number(e.target.value) / 100)}
                    className="w-16 rounded border border-hair px-1.5 py-0.5 text-right text-xs"
                  />
                  <span>%</span>
                </label>
              </div>
              {curveLoading && <span className="text-brand-600">正在重算容量曲面…</span>}
            </div>

            {curveData && curveData.has_data ? (
              <>
                <div className="grid grid-cols-2 gap-2 text-2xs md:grid-cols-5">
                  <div className="rounded-md border border-hair bg-surface-alt p-2">
                    <div className="text-ink-muted">黄金最优 AUM</div>
                    <div className="num mt-0.5 text-base font-semibold text-brand-700">
                      {curveData.optimal_aum_yi} 亿元
                    </div>
                    <div className="text-2xs text-ink-muted">约 {curveData.optimal_aum_wan.toLocaleString()} 万元</div>
                  </div>
                  <div className="rounded-md border border-hair bg-surface-alt p-2">
                    <div className="text-ink-muted">黄金规模净夏普</div>
                    <div className="num mt-0.5 text-base font-semibold text-ink">
                      {curveData.optimal_sharpe.toFixed(2)}
                    </div>
                    <div className="text-2xs text-ink-muted">冲击衰减后实得</div>
                  </div>
                  <div className="rounded-md border border-hair bg-surface-alt p-2">
                    <div className="text-ink-muted">峰值年创利额</div>
                    <div className="num mt-0.5 text-base font-semibold text-danger">
                      {(curveData.max_profit_wan / 10000).toFixed(2)} 亿元
                    </div>
                    <div className="text-2xs text-ink-muted">最大化 AUM × NetReturn</div>
                  </div>
                  <div className="rounded-md border border-hair bg-surface-alt p-2">
                    <div className="text-ink-muted">盈亏平衡容量上限</div>
                    <div className="num mt-0.5 text-base font-semibold text-warn">
                      {curveData.breakeven_aum_yi != null ? `${curveData.breakeven_aum_yi} 亿元` : '无约束'}
                    </div>
                    <div className="text-2xs text-ink-muted">超过后 Alpha 被冲击吃尽</div>
                  </div>
                  <div className="rounded-md border border-hair bg-surface-alt p-2">
                    <div className="text-ink-muted">股票池 ADV 中位数</div>
                    <div className="num mt-0.5 text-base font-semibold text-ink">
                      {curveData.adv_median_yi.toFixed(2)} 亿元
                    </div>
                    <div className="text-2xs text-ink-muted">全池日均成交流动性</div>
                  </div>
                </div>

                <CapacityCurveChart data={curveData} />

                {/* 多变现周期帕累托前沿对比 */}
                {curveData.horizon_frontiers && curveData.horizon_frontiers.length > 0 && (
                  <div className="rounded border border-hair bg-surface p-2.5 text-2xs">
                    <span className="font-semibold text-ink">不同建仓变现周期下的最大容量前沿：</span>
                    <div className="mt-1.5 flex flex-wrap gap-4">
                      {curveData.horizon_frontiers.map((h) => (
                        <div key={h.horizon_days} className="flex items-center gap-1.5">
                          <span className="text-ink-secondary">{h.desc}:</span>
                          <span className="num font-semibold text-brand-700">{h.capacity_yi} 亿元</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="py-6 text-center text-xs text-ink-muted">
                {curveLoading ? '正在加载容量曲面…' : '暂无可用的容量曲面数据'}
              </div>
            )}
          </SectionCard>
        </div>
      )}

      {/* TAB 2: Brinson 业绩归因 */}
      {activeTab === 'attribution' && (
        <div className="space-y-3">
          {attr && (
            <>
              {/* 核心收益 KPI 卡片 */}
              <div className="grid grid-cols-2 gap-2 text-2xs sm:grid-cols-4">
                <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                  <div className="text-ink-secondary">组合累计收益</div>
                  <div className={`num text-base font-semibold ${attr.portfolio_return >= 0 ? 'text-danger' : 'text-success'}`}>
                    {fmtPct(attr.portfolio_return)}
                  </div>
                </div>
                <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                  <div className="text-ink-secondary">基准累计收益</div>
                  <div className="num text-base font-semibold">{fmtPct(attr.benchmark_return)}</div>
                </div>
                <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                  <div className="text-ink-secondary">超额收益 (Active Return)</div>
                  <div className={`num text-base font-semibold ${attr.excess_return >= 0 ? 'text-danger' : 'text-success'}`}>
                    {fmtPct(attr.excess_return)}
                  </div>
                </div>
                <div className="rounded-lg border border-hair bg-surface px-3 py-2">
                  <div className="text-ink-secondary">风格残差 Alpha (年化)</div>
                  <div className="num text-base font-semibold">{fmtPct(attr.style.alpha_annualized)}</div>
                </div>
              </div>

              {/* 多期时序连乘平滑归因面积图 */}
              {attr.multi_period_brinson?.timeseries && (
                <SectionCard
                  title="多期连乘平滑累积归因走势（Carino / Menchero 模型时序分解）"
                  bodyClassName="space-y-3"
                >
                  <CumulativeAttributionChart timeseries={attr.multi_period_brinson.timeseries} />
                </SectionCard>
              )}

              {/* 行业主动暴露与 Brinson 归因透视卡片 */}
              <SectionCard title="Brinson 行业归因与主动暴露透视" bodyClassName="space-y-3">
                <SectorActiveTiltCard attr={attr} />
              </SectionCard>
            </>
          )}
        </div>
      )}

      {/* TAB 3: 个股微观贡献与 Barra 风格风险分解 */}
      {activeTab === 'alpha_risk' && (
        <div className="space-y-3">
          {attr && (
            <>
              {/* 个股选股贡献 Top 5 赢家与 Bottom 5 拖累 */}
              {attr.stock_contributions && (
                <SectionCard
                  title="个股微观选股贡献度穿透（Top 5 赢家 vs Bottom 5 拖累）"
                  bodyClassName="space-y-3"
                >
                  <StockContributionCard
                    topContributors={attr.stock_contributions.top_contributors}
                    bottomDrags={attr.stock_contributions.bottom_drags}
                    allHoldings={attr.stock_contributions.all_holdings}
                  />
                </SectionCard>
              )}

              {/* Barra 风格归因与跟踪误差主动风险结构分解 */}
              <SectionCard
                title="Barra 风格因子归因与跟踪误差风险结构分解"
                bodyClassName="space-y-3"
              >
                <BarraRiskDecompositionCard style={attr.style} />
              </SectionCard>
            </>
          )}
        </div>
      )}
    </div>
  );
}
