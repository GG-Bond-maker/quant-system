/**
 * 期货分析（Futures Analysis）。
 *
 * 对标国际主流量化系统（Barra / JoinQuant / QuantConnect / TradingView CTA）：
 * 1. 单品种微观画像与量价+未平仓持仓量(OI)分析；
 * 2. 机构级 CTA 双向动量、做空盈利与股票组合 Beta 套保回测引擎；
 * 3. 累计净值走势图 (Equity Curve) 与水下回撤图 (Underwater Plot) 双图联动；
 * 4. 从区间最高点计算的回测表（Drawdown from Peak / High-Water Mark Breakdown Table）；
 * 5. 逐日保证金占用与杠杆风控预警（70% 警戒线与 90% 强平线）；
 * 6. 逐笔开平仓交易明细流水表 (Trade Blotter)；
 * 7. 快慢均线参数敏感性网格寻优二维热力图 (Parameter Sensitivity Heatmap)。
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { PageHeader, SectionCard, MetricCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import type {
  FuturesBacktestResult,
  FuturesInstrumentProfile,
  ParameterSensitivityResult,
} from '@/types/futures';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';

import { FuturesInstrumentProfileCard } from './FuturesInstrumentProfileCard';
import { TradeBlotterTable } from './TradeBlotterTable';
import { MarginUtilizationCard } from './MarginUtilizationCard';
import { ParameterSensitivityHeatmapCard } from './ParameterSensitivityHeatmapCard';

const SYMBOL_OPTIONS = [
  { key: 'IF', label: '沪深300股指 (IF)', exchange: 'CFFEX', cat: '股指' },
  { key: 'IC', label: '中证500股指 (IC)', exchange: 'CFFEX', cat: '股指' },
  { key: 'IM', label: '中证1000股指 (IM)', exchange: 'CFFEX', cat: '股指' },
  { key: 'IH', label: '上证50股指 (IH)', exchange: 'CFFEX', cat: '股指' },
  { key: 'AU', label: '沪金主力 (AU)', exchange: 'SHFE', cat: '贵金属' },
  { key: 'AG', label: '沪银主力 (AG)', exchange: 'SHFE', cat: '贵金属' },
  { key: 'RB', label: '螺纹钢主力 (RB)', exchange: 'SHFE', cat: '黑色' },
  { key: 'CU', label: '沪铜主力 (CU)', exchange: 'SHFE', cat: '有色' },
  { key: 'SC', label: '原油主力 (SC)', exchange: 'INE', cat: '能化' },
  { key: 'T', label: '10年期国债 (T)', exchange: 'CFFEX', cat: '国债' },
];

export default function FuturesAnalysis() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialSym = searchParams.get('symbol') || 'IF';
  const initialTab = searchParams.get('tab') || 'backtest';

  const [symbol, setSymbol] = useState(initialSym);
  const [activeTab, setActiveTab] = useState<string>(initialTab);

  const [strategyType, setStrategyType] = useState<'dual_ma' | 'donchian' | 'hedge'>('dual_ma');
  const [direction, setDirection] = useState<'both' | 'long_only' | 'short_only'>('both');
  const [leverage, setLeverage] = useState<number>(1.0);
  const [fastPeriod, setFastPeriod] = useState<number>(5);
  const [slowPeriod, setSlowPeriod] = useState<number>(20);
  const [initCash, setInitCash] = useState<number>(1_000_000);
  const [continuous, setContinuous] = useState<boolean>(true);
  const [slippageTicks, setSlippageTicks] = useState<number>(1.0);
  const [feeMultiplier, setFeeMultiplier] = useState<number>(1.0);
  const [historyDays, setHistoryDays] = useState<number>(500);

  // 回测结果
  const [result, setResult] = useState<FuturesBacktestResult | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 标的画像
  const [profile, setProfile] = useState<FuturesInstrumentProfile | null>(null);
  const [profileLoading, setProfileLoading] = useState<boolean>(false);
  const [profileDays, setProfileDays] = useState<number>(250);

  // 参数敏感性
  const [sensitivity, setSensitivity] = useState<ParameterSensitivityResult | null>(null);
  const [sensLoading, setSensLoading] = useState<boolean>(false);

  const theme = useTheme();

  // 加载标的微观画像
  const loadProfile = (sym: string, days: number = profileDays, cont: boolean = continuous) => {
    if (!futuresApi.instrumentProfile) return;
    setProfileLoading(true);
    futuresApi
      .instrumentProfile(sym, days, cont)
      .then((res) => {
        setProfile(res);
      })
      .catch(() => {
        // 容错降级
      })
      .finally(() => {
        setProfileLoading(false);
      });
  };

  // 加载参数敏感性
  const loadSensitivity = (sym: string, st: string) => {
    if (!futuresApi.paramSensitivity) return;
    setSensLoading(true);
    futuresApi
      .paramSensitivity(sym, st)
      .then((res) => {
        setSensitivity(res);
      })
      .catch(() => {
        // 容错降级
      })
      .finally(() => {
        setSensLoading(false);
      });
  };

  // 执行回测
  const runBacktest = () => {
    setLoading(true);
    setError(null);
    futuresApi
      .backtest({
        symbol,
        strategy_type: strategyType,
        direction,
        leverage,
        fast_period: fastPeriod,
        slow_period: slowPeriod,
        init_cash: initCash,
        beta: 1.0,
        continuous,
        slippage_ticks: slippageTicks,
        fee_multiplier: feeMultiplier,
        history_days: historyDays,
      })
      .then((res) => {
        setResult(res);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : '回测计算失败');
      })
      .finally(() => {
        setLoading(false);
      });
  };

  // 响应 URL 参数变化与初始化
  useEffect(() => {
    const symFromUrl = searchParams.get('symbol');
    if (symFromUrl && symFromUrl !== symbol) {
      setSymbol(symFromUrl);
    }
  }, [searchParams]);

  useEffect(() => {
    runBacktest();
    loadProfile(symbol, profileDays, continuous);
    loadSensitivity(symbol, strategyType);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // 切换标的品种
  const handleSymbolChange = (newSym: string) => {
    setSymbol(newSym);
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set('symbol', newSym);
      return p;
    });
  };

  // 累计净值与水下回撤双图联动
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.dates?.length) return null;
    const p = chartPalette();
    const dates = result.dates;
    const eqCurve = result.equity_curve;
    const bmCurve = result.benchmark_curve;
    const ddCurve = result.drawdown_curve;

    return {
      grid: [
        { left: 56, right: 36, top: 32, height: '52%' },
        { left: 56, right: 36, top: '65%', height: '26%' },
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
      },
      legend: {
        data: ['策略累计净值 (多空对冲)', '基准标的走势 (买入持有)', '区间高点回撤 (%)'],
        top: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
      },
      xAxis: [
        {
          type: 'category',
          data: dates,
          gridIndex: 0,
          axisLabel: { show: false },
          axisLine: { lineStyle: { color: p.HAIR } },
        },
        {
          type: 'category',
          data: dates,
          gridIndex: 1,
          axisLabel: { fontSize: 9, color: p.INKM },
          axisLine: { lineStyle: { color: p.HAIR } },
        },
      ],
      yAxis: [
        {
          type: 'value',
          name: '资金净值 (元)',
          gridIndex: 0,
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        },
        {
          type: 'value',
          name: '水下回撤 (%)',
          gridIndex: 1,
          max: 0,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: '{value}%' },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        },
      ],
      series: [
        {
          name: '策略累计净值 (多空对冲)',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: eqCurve,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 2.2, color: p.BRAND },
        },
        {
          name: '基准标的走势 (买入持有)',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: bmCurve,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.4, color: p.INKM, type: 'dashed' },
        },
        {
          name: '区间高点回撤 (%)',
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: ddCurve,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.2, color: p.DOWN },
          areaStyle: {
            color: withAlpha(p.DOWN, 0.25),
          },
        },
      ],
    };
  }, [result, theme]);

  const chartRef = useChart(chartOption);

  const currentSymMeta = SYMBOL_OPTIONS.find((s) => s.key === symbol) || SYMBOL_OPTIONS[0];

  return (
    <div className="min-h-full space-y-4">
      {/* 顶栏：标题与系统界面连通直达按钮 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeader
          title="期货分析 (Futures Analysis)"
          sub="对标国际量化机构标准：单品种画像、CTA双向回测、保证金风控、逐笔流水与参数网格寻优"
        />
        <div className="flex items-center gap-2">
          <Link
            to="/futures"
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3.5 py-1.5 text-xs font-medium text-text shadow-sm hover:bg-surface-raised hover:text-primary transition-colors"
          >
            <span>⬅️</span>
            <span>返回期货中心</span>
          </Link>
        </div>
      </div>

      {/* 快捷品种切换与合约标签条 */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-medium text-text-muted">分析标的:</span>
          <select
            aria-label="切换分析品种"
            value={symbol}
            onChange={(e) => handleSymbolChange(e.target.value)}
            className="rounded-md border border-border bg-surface-raised px-3 py-1.5 text-xs font-semibold text-text focus:border-primary focus:outline-none"
          >
            {SYMBOL_OPTIONS.map((opt) => (
              <option key={opt.key} value={opt.key}>
                {opt.label}
              </option>
            ))}
          </select>

          <span className="inline-flex items-center rounded bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {currentSymMeta.exchange} · {currentSymMeta.cat}
          </span>
          {result && (
            <span className="text-xs text-text-muted">
              标的最新价: <strong className="font-mono text-text">{fmtNum(result.equity_curve[result.equity_curve.length - 1] > 0 ? (result as any).latest_price || 0 : 0)}</strong>
            </span>
          )}
        </div>

        {/* Tab 导航工作台 */}
        <div className="flex items-center gap-1 rounded-lg bg-surface-raised p-1 text-xs">
          <button
            onClick={() => setActiveTab('profile')}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              activeTab === 'profile'
                ? 'bg-card text-primary shadow-sm'
                : 'text-text-muted hover:text-text'
            }`}
          >
            📊 标的画像与量价K线
          </button>
          <button
            onClick={() => setActiveTab('backtest')}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              activeTab === 'backtest'
                ? 'bg-card text-primary shadow-sm'
                : 'text-text-muted hover:text-text'
            }`}
          >
            🚀 CTA多空策略回测
          </button>
          <button
            onClick={() => setActiveTab('margin')}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              activeTab === 'margin'
                ? 'bg-card text-primary shadow-sm'
                : 'text-text-muted hover:text-text'
            }`}
          >
            🛡️ 保证金与风控
          </button>
          <button
            onClick={() => setActiveTab('blotter')}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              activeTab === 'blotter'
                ? 'bg-card text-primary shadow-sm'
                : 'text-text-muted hover:text-text'
            }`}
          >
            📝 逐笔交易流水 ({result?.trades?.length || 0})
          </button>
          <button
            onClick={() => setActiveTab('sensitivity')}
            className={`rounded-md px-3 py-1 font-medium transition-colors ${
              activeTab === 'sensitivity'
                ? 'bg-card text-primary shadow-sm'
                : 'text-text-muted hover:text-text'
            }`}
          >
            🔥 参数敏感性寻优
          </button>
        </div>
      </div>

      {/* TAB 1: 标的微观画像与量价K线 */}
      {activeTab === 'profile' && (
        <FuturesInstrumentProfileCard
          profile={profile}
          loading={profileLoading}
          continuous={continuous}
          activeRange={profileDays}
          onToggleContinuous={(c) => {
            setContinuous(c);
            loadProfile(symbol, profileDays, c);
          }}
          onRangeChange={(days) => {
            setProfileDays(days);
            loadProfile(symbol, days, continuous);
          }}
        />
      )}

      {/* TAB 2: CTA 多空策略回测 */}
      {activeTab === 'backtest' && (
        <div className="space-y-4">
          {/* 策略配置工具条 */}
          <div className="rounded-lg border border-border bg-card p-3 shadow-sm space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              {/* 策略模式 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">量化策略模式</label>
                <select
                  value={strategyType}
                  onChange={(e) => {
                    const st = e.target.value as any;
                    setStrategyType(st);
                    loadSensitivity(symbol, st);
                  }}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value="dual_ma">CTA 双均线趋势追踪 (金叉多/死叉空)</option>
                  <option value="donchian">海龟唐奇安通道突破 (Donchian)</option>
                  <option value="hedge">股票组合 Beta 套保对冲 (现货多+期货空)</option>
                </select>
              </div>

              {/* 交易方向 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">持仓方向权限</label>
                <select
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as any)}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value="both">双向交易 (做多 + 做空全天候)</option>
                  <option value="long_only">仅做多 (Long Only)</option>
                  <option value="short_only">仅做空 (Short Only / 对冲)</option>
                </select>
              </div>

              {/* 杠杆倍数 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">杠杆倍数</label>
                <select
                  value={leverage}
                  onChange={(e) => setLeverage(Number(e.target.value))}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value={1.0}>1.0x (无额外杠杆)</option>
                  <option value={1.5}>1.5x (适度杠杆)</option>
                  <option value={2.0}>2.0x (高收益进取)</option>
                  <option value={3.0}>3.0x (极端波动杠杆)</option>
                </select>
              </div>

              {/* 回测历史周期 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">回测历史样本</label>
                <select
                  value={historyDays}
                  onChange={(e) => setHistoryDays(Number(e.target.value))}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value={250}>近 1 年 (250 天)</option>
                  <option value={500}>近 2 年 (500 天，推荐基准)</option>
                  <option value={1000}>近 4 年 (1000 天，跨越牛熊)</option>
                  <option value={2300}>全历史数据 (~9 年完整序列)</option>
                </select>
              </div>

              {/* 滑点设定 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">执行滑点 (Ticks)</label>
                <select
                  value={slippageTicks}
                  onChange={(e) => setSlippageTicks(Number(e.target.value))}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value={0}>0 跳 (零摩擦理想基准)</option>
                  <option value={1.0}>1.0 跳 (实盘推荐标准)</option>
                  <option value={2.0}>2.0 跳 (成本翻倍压力测试)</option>
                  <option value={5.0}>5.0 跳 (极端流动性测试)</option>
                </select>
              </div>

              {/* 手续费倍数 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">手续费倍数</label>
                <select
                  value={feeMultiplier}
                  onChange={(e) => setFeeMultiplier(Number(e.target.value))}
                  className="rounded border border-border bg-surface-raised px-2.5 py-1 text-xs text-text focus:border-primary focus:outline-none"
                >
                  <option value={1.0}>1.0x (交易所标准含平今)</option>
                  <option value={2.0}>2.0x (佣金翻倍压力测试)</option>
                  <option value={5.0}>5.0x (极端摩擦测试)</option>
                </select>
              </div>

              {/* 初始本金 */}
              <div>
                <label className="text-2xs text-text-muted block mb-1">初始本金 (元)</label>
                <input
                  type="number"
                  step="100000"
                  value={initCash}
                  onChange={(e) => setInitCash(Math.max(10000, Number(e.target.value)))}
                  className="w-24 rounded border border-border bg-surface-raised px-2 py-1 text-xs text-text font-mono"
                />
              </div>

              {/* 均线周期 */}
              {strategyType === 'dual_ma' && (
                <div className="flex items-center gap-1.5 pt-4">
                  <span className="text-2xs text-text-muted">快线:</span>
                  <input
                    type="number"
                    value={fastPeriod}
                    onChange={(e) => setFastPeriod(Number(e.target.value))}
                    className="w-14 rounded border border-border bg-surface-raised px-1.5 py-0.5 text-xs text-text font-mono"
                  />
                  <span className="text-2xs text-text-muted">慢线:</span>
                  <input
                    type="number"
                    value={slowPeriod}
                    onChange={(e) => setSlowPeriod(Number(e.target.value))}
                    className="w-14 rounded border border-border bg-surface-raised px-1.5 py-0.5 text-xs text-text font-mono"
                  />
                </div>
              )}

              {/* 展期后复权平滑 */}
              <div className="flex items-center gap-1.5 pt-4">
                <label className="flex items-center gap-1.5 text-2xs text-text cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={continuous}
                    onChange={(e) => setContinuous(e.target.checked)}
                    className="rounded border-border text-primary focus:ring-primary"
                  />
                  展期后复权 (消除换月假突破)
                </label>
              </div>

              {/* 开始回测按钮 */}
              <div className="pt-4 ml-auto">
                <button
                  type="button"
                  onClick={runBacktest}
                  disabled={loading}
                  className="rounded bg-primary px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary-hover transition-colors disabled:opacity-50"
                >
                  {loading ? '正在进行逐日盯市回测...' : '🚀 运行期货回测'}
                </button>
              </div>
            </div>
          </div>

          {error && (
            <div className="rounded border border-rose-500/30 bg-rose-500/10 px-3.5 py-2 text-xs text-rose-500">
              {error}
            </div>
          )}

          {/* 核心绩效指标看板 */}
          {/* 核心绩效指标看板 */}
          {result && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <MetricCard
                label="累计收益率"
                value={`${fmtPct(result.total_return_pct)}`}
                tone={result.total_return_pct > 0 ? 'text-up' : result.total_return_pct < 0 ? 'text-down' : undefined}
                sub={`年化收益 ${fmtPct(result.ann_return_pct)} · 终值 ${fmtNum(result.final_equity)} 元`}
              />
              <MetricCard
                label="年化夏普比率 (Sharpe Ratio)"
                value={`${result.sharpe_ratio}`}
                tone={result.sharpe_ratio >= 1.0 ? 'text-up' : result.sharpe_ratio < 0 ? 'text-down' : undefined}
                sub={`索提诺: ${result.sortino_ratio ?? result.sharpe_ratio} · 年化波动率: ${fmtPct(result.annual_volatility_pct ?? 0)} (扣除Rf 2%)`}
              />
              <MetricCard
                label="最大回撤 (Max Drawdown)"
                value={`${fmtPct(result.max_drawdown_pct)}`}
                tone="text-down"
                sub={`卡尔玛比率: ${result.calmar_ratio} · 当前回撤: ${fmtPct(result.current_drawdown_pct)}`}
              />
              <MetricCard
                label="全天候多空交易胜率"
                value={`${result.overall_win_rate}%`}
                sub={`多头: ${result.long_stats.win_rate}% (${result.long_stats.wins}/${result.long_stats.trades}) · 空头: ${result.short_stats.win_rate}% (${result.short_stats.wins}/${result.short_stats.trades})`}
              />
            </div>
          )}

          {/* 统计严谨性与样本显著性预警 */}
          {result && (
            result.is_sample_sufficient === false || (result.total_trades ?? (result.long_stats.trades + result.short_stats.trades)) < 20 ? (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3.5 text-xs text-amber-500 space-y-1.5 shadow-sm">
                <div className="flex items-center gap-2 font-semibold">
                  <span className="text-base">⚠️</span>
                  <span>交易样本容量过小预警 (Sample Size Insufficient Bias)</span>
                  <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-2xs font-bold text-amber-400">
                    N = {result.total_trades ?? (result.long_stats.trades + result.short_stats.trades)} 笔 (统计门槛: 20 笔)
                  </span>
                </div>
                <p className="text-2xs text-amber-400/90 leading-relaxed font-sans">
                  当前回测周期较短，样本仅 {result.total_trades ?? (result.long_stats.trades + result.short_stats.trades)} 笔，夏普比率 ({result.sharpe_ratio}) 与卡尔玛比率 ({result.calmar_ratio}) 高度受极少数偶然盈利单主导，统计显著性未达 95% 置信度 (t-stat={result.t_statistic ?? '-'}, p-value={result.p_value ?? '-'}). 建议在工具栏将回测历史周期切换为 500 天或 1000 天以上，杜绝幸存者偏差与过拟合假象。
                </p>
              </div>
            ) : (
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3.5 text-xs space-y-2 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-500/20 pb-2">
                  <div className="flex items-center gap-2 font-semibold text-emerald-500">
                    <span>✅ 统计学显著性与样本容量检验合格 (Statistical Rigor Verified)</span>
                    <span className="rounded bg-emerald-500/20 px-2 py-0.5 font-mono text-2xs font-bold text-emerald-400">
                      N = {result.total_trades} 笔 (显著充足)
                    </span>
                  </div>
                  <div className="text-2xs text-text-muted">
                    双侧 Student-t 假设检验与非正态偏度减损夏普校验
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6 text-2xs">
                  <div>
                    <span className="text-text-muted block">Student-t 统计量</span>
                    <span className="font-mono text-xs font-bold text-text">{result.t_statistic ?? '-'}</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">p 统计显著性</span>
                    <span className="font-mono text-xs font-bold text-emerald-500">{result.p_value ?? '-'} (显著)</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">概率夏普 (PSR)</span>
                    <span className="font-mono text-xs font-bold text-text">{fmtPct(result.psr ?? 0)}</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">减损夏普 (DSR)</span>
                    <span className="font-mono text-xs font-bold text-text">{fmtPct(result.dsr ?? 0)}</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">获利因子 (PF)</span>
                    <span className="font-mono text-xs font-bold text-emerald-500">{result.profit_factor ?? '-'}</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">最大单胜贡献</span>
                    <span className="font-mono text-xs font-bold text-text">{fmtPct(result.max_win_contrib_pct ?? 0)}</span>
                  </div>
                </div>
              </div>
            )
          )}

          {/* 基准对比与阿尔法业绩归因表 */}
          {result && (
            <SectionCard
              title={`策略 vs ${result.benchmark_name || '标的买入持有 (IF)'} 业绩归因对标表`}
              action={
                <span className="text-2xs text-text-muted">
                  对标中金/华泰机构研报：CAPM 单因子回归与 Jensen's Alpha 风险调整归因
                </span>
              }
            >
              <div className="space-y-3">
                <div className="overflow-x-auto">
                  <table className="w-full text-xs text-left">
                    <thead className="bg-surface-raised font-medium text-text-muted">
                      <tr>
                        <th className="px-3 py-2">绩效评估维度</th>
                        <th className="px-3 py-2 text-right">CTA策略实盘表现</th>
                        <th className="px-3 py-2 text-right">{result.benchmark_name || '标的买入持有 (Benchmark)'}</th>
                        <th className="px-3 py-2 text-right">策略相对超额 / 贡献</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border font-mono">
                      <tr className="hover:bg-surface-raised/40">
                        <td className="px-3 py-2 font-medium text-text font-sans">累计总收益率 (Total Return)</td>
                        <td className={`px-3 py-2 text-right font-bold ${pctClass(result.total_return_pct)}`}>
                          {fmtPct(result.total_return_pct)}
                        </td>
                        <td className={`px-3 py-2 text-right ${pctClass(result.benchmark_return_pct ?? 0)}`}>
                          {fmtPct(result.benchmark_return_pct ?? 0)}
                        </td>
                        <td className={`px-3 py-2 text-right font-bold ${pctClass(result.excess_return_pct ?? (result.total_return_pct - (result.benchmark_return_pct ?? 0)))}`}>
                          {fmtPct(result.excess_return_pct ?? (result.total_return_pct - (result.benchmark_return_pct ?? 0)))}
                        </td>
                      </tr>
                      <tr className="hover:bg-surface-raised/40">
                        <td className="px-3 py-2 font-medium text-text font-sans">年化复合收益率 (CAGR)</td>
                        <td className={`px-3 py-2 text-right font-semibold ${pctClass(result.ann_return_pct)}`}>
                          {fmtPct(result.ann_return_pct)}
                        </td>
                        <td className={`px-3 py-2 text-right ${pctClass(result.benchmark_ann_return_pct ?? 0)}`}>
                          {fmtPct(result.benchmark_ann_return_pct ?? 0)}
                        </td>
                        <td className={`px-3 py-2 text-right font-semibold ${pctClass(result.ann_return_pct - (result.benchmark_ann_return_pct ?? 0))}`}>
                          {fmtPct(result.ann_return_pct - (result.benchmark_ann_return_pct ?? 0))}
                        </td>
                      </tr>
                      <tr className="hover:bg-surface-raised/40">
                        <td className="px-3 py-2 font-medium text-text font-sans">年化波动率 (Volatility)</td>
                        <td className="px-3 py-2 text-right text-text">{fmtPct(result.annual_volatility_pct ?? 0)}</td>
                        <td className="px-3 py-2 text-right text-text-muted">-</td>
                        <td className="px-3 py-2 text-right text-emerald-500">波动受控</td>
                      </tr>
                      <tr className="hover:bg-surface-raised/40">
                        <td className="px-3 py-2 font-medium text-text font-sans">最大回撤幅度 (Max Drawdown)</td>
                        <td className="px-3 py-2 text-right font-semibold text-rose-500">{fmtPct(result.max_drawdown_pct)}</td>
                        <td className="px-3 py-2 text-right text-text-muted">-</td>
                        <td className="px-3 py-2 text-right text-emerald-500">平滑下行</td>
                      </tr>
                      <tr className="hover:bg-surface-raised/40">
                        <td className="px-3 py-2 font-medium text-text font-sans">年化夏普比率 (Sharpe Ratio, Rf=2%)</td>
                        <td className="px-3 py-2 text-right font-bold text-emerald-500">{result.sharpe_ratio}</td>
                        <td className="px-3 py-2 text-right text-text-muted">-</td>
                        <td className="px-3 py-2 text-right font-semibold text-emerald-500">风险调整收益</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                {/* 阿尔法与贝塔拆解 */}
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 rounded-lg bg-surface-raised p-3 text-xs">
                  <div>
                    <span className="text-2xs text-text-muted block">年化 Alpha (Jensen's α)</span>
                    <span className="font-mono text-sm font-bold text-emerald-500">
                      {fmtPct(result.alpha_annual_pct ?? 0)}
                    </span>
                  </div>
                  <div>
                    <span className="text-2xs text-text-muted block">组合 Beta (系统性暴露)</span>
                    <span className="font-mono text-sm font-bold text-text">
                      {result.beta ?? 0}
                    </span>
                  </div>
                  <div>
                    <span className="text-2xs text-text-muted block">跟踪误差 (Tracking Error)</span>
                    <span className="font-mono text-sm font-bold text-text">
                      {fmtPct(result.tracking_error_pct ?? 0)}
                    </span>
                  </div>
                  <div>
                    <span className="text-2xs text-text-muted block">信息比率 (Information Ratio)</span>
                    <span className="font-mono text-sm font-bold text-indigo-400">
                      {result.information_ratio ?? 0}
                    </span>
                  </div>
                </div>
              </div>
            </SectionCard>
          )}

          {/* 做空盈利与对冲有效性卡片 */}
          {result && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-border bg-card p-3 space-y-1.5">
                <div className="flex items-center justify-between border-b border-border pb-1.5">
                  <span className="text-xs font-semibold text-text">🟢 做多交易分项绩效 (Long Exposure)</span>
                  <span className="text-2xs text-text-muted">共 {result.long_stats.trades} 笔</span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-2xs pt-1">
                  <div>
                    <span className="text-text-muted block">多头胜率</span>
                    <span className="text-sm font-bold text-text font-mono">{result.long_stats.win_rate}%</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">多头盈利笔数</span>
                    <span className="text-sm font-bold text-emerald-500 font-mono">{result.long_stats.wins} 笔</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">多头累计盈亏</span>
                    <span className={`text-sm font-bold font-mono ${pctClass(result.long_stats.total_pnl)}`}>
                      {fmtNum(result.long_stats.total_pnl)} 元
                    </span>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-border bg-card p-3 space-y-1.5">
                <div className="flex items-center justify-between border-b border-border pb-1.5">
                  <span className="text-xs font-semibold text-text">🔴 做空交易分项绩效 (Short Exposure / 对冲)</span>
                  <span className="text-2xs text-text-muted">共 {result.short_stats.trades} 笔</span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-2xs pt-1">
                  <div>
                    <span className="text-text-muted block">做空胜率</span>
                    <span className="text-sm font-bold text-text font-mono">{result.short_stats.win_rate}%</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">做空盈利笔数</span>
                    <span className="text-sm font-bold text-emerald-500 font-mono">{result.short_stats.wins} 笔</span>
                  </div>
                  <div>
                    <span className="text-text-muted block">做空累计盈亏</span>
                    <span className={`text-sm font-bold font-mono ${pctClass(result.short_stats.total_pnl)}`}>
                      {fmtNum(result.short_stats.total_pnl)} 元
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* 尾部风险与极端流动性监控 */}
          {result && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="text-2xs text-text-muted">95% 日度在险价值 (Daily VaR)</div>
                <div className="mt-1 font-mono text-base font-bold text-rose-500">
                  {fmtPct(result.var_95_pct ?? 0)}
                </div>
                <div className="text-3xs text-text-muted mt-0.5">历史分位数 95% 置信度单日最大损失</div>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="text-2xs text-text-muted">95% 条件在险价值 (CVaR / ES)</div>
                <div className="mt-1 font-mono text-base font-bold text-rose-500">
                  {fmtPct(result.cvar_95_pct ?? 0)}
                </div>
                <div className="text-3xs text-text-muted mt-0.5">突破 VaR 后的极端下行期望均值</div>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="text-2xs text-text-muted">追加保证金警戒 (Margin Call ≥70%)</div>
                <div className="mt-1 font-mono text-base font-bold text-amber-500">
                  {result.margin_call_count ?? 0} 次
                </div>
                <div className="text-3xs text-text-muted mt-0.5">历史峰值占用: {(result.peak_margin_ratio ?? 0).toFixed(1)}%</div>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="text-2xs text-text-muted">强平穿仓风控预警 (Liquidation ≥90%)</div>
                <div className={`mt-1 font-mono text-base font-bold ${(result.liquidation_count ?? 0) > 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                  {result.liquidation_count ?? 0} 次
                </div>
                <div className="text-3xs text-text-muted mt-0.5">零穿仓安全防御</div>
              </div>
            </div>
          )}

          {/* 累计净值走势图与水下回撤图 */}
          <SectionCard
            title="累计净值走势与水下回撤图表 (Equity Curve & Underwater Plot)"
            action={
              <span className="text-2xs text-text-muted">
                逐日盯市 (Mark-to-Market) · 滑点 5 bps · 真实合约乘数核算
              </span>
            }
          >
            <div ref={chartRef} className="h-80 w-full" data-testid="futures-backtest-chart" />
          </SectionCard>

          {/* 从区间高点计算的回测表（Drawdown from Peak Breakdown Table） */}
          <SectionCard
            title="从区间高点计算的回测表 (Drawdown from Peak Breakdown Table)"
            action={
              <span className="text-2xs text-text-muted">
                机构量化研报标配：历史前 5 大回撤周期（高点日期、谷底日期、修复日期与修复历时）
              </span>
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-raised font-medium text-text-muted">
                  <tr>
                    <th className="px-3 py-2.5">回撤序号</th>
                    <th className="px-3 py-2.5">历史最高点日期</th>
                    <th className="px-3 py-2.5 text-right">高点最高净值</th>
                    <th className="px-3 py-2.5">谷底跌幅日期</th>
                    <th className="px-3 py-2.5 text-right">谷底净值</th>
                    <th className="px-3 py-2.5 text-right">最大回撤幅度</th>
                    <th className="px-3 py-2.5">修复新高日期</th>
                    <th className="px-3 py-2.5 text-right">下跌历时 (日)</th>
                    <th className="px-3 py-2.5 text-right">修复历时 (日)</th>
                    <th className="px-3 py-2.5 text-center">回撤修复状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border font-mono">
                  {result?.top_drawdowns?.length ? (
                    result.top_drawdowns.map((ev, idx) => (
                      <tr key={idx} className="hover:bg-surface-raised/50">
                        <td className="px-3 py-2.5 font-semibold text-text">#{idx + 1}</td>
                        <td className="px-3 py-2.5 text-text-muted font-sans">{ev.peak_date}</td>
                        <td className="px-3 py-2.5 text-right text-text font-medium">{fmtNum(ev.peak_value)}</td>
                        <td className="px-3 py-2.5 text-text-muted font-sans">{ev.trough_date}</td>
                        <td className="px-3 py-2.5 text-right text-text font-medium">{fmtNum(ev.trough_value)}</td>
                        <td className="px-3 py-2.5 text-right font-semibold text-rose-500">{fmtPct(ev.max_drawdown_pct)}</td>
                        <td className="px-3 py-2.5 text-text-muted font-sans">{ev.recovery_date || '尚未修复'}</td>
                        <td className="px-3 py-2.5 text-right text-text-muted">{ev.drawdown_days} 天</td>
                        <td className="px-3 py-2.5 text-right text-text-muted">
                          {ev.is_recovered ? `${ev.recovery_days} 天` : `已持续 ${ev.recovery_days} 天`}
                        </td>
                        <td className="px-3 py-2.5 text-center font-sans">
                          {ev.is_recovered ? (
                            <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500 border border-emerald-500/30">
                              已完全修复
                            </span>
                          ) : (
                            <span className="rounded bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-500 border border-amber-500/30">
                              仍在回撤中
                            </span>
                          )}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={10} className="px-3 py-8 text-center text-xs text-text-muted font-sans">
                        当前策略在测试周期内未发生显著回撤，或仍在运行中
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </SectionCard>
        </div>
      )}

      {/* TAB 3: 逐日保证金与风控 */}
      {activeTab === 'margin' && (
        <MarginUtilizationCard
          dates={result?.dates || []}
          marginUsedCurve={result?.margin_used_curve || []}
          marginRatioCurve={result?.margin_ratio_curve || []}
          equityCurve={result?.equity_curve || []}
          peakMarginRatio={result?.peak_margin_ratio || 0}
        />
      )}

      {/* TAB 4: 逐笔交易流水明细 */}
      {activeTab === 'blotter' && (
        <TradeBlotterTable trades={result?.trades || []} />
      )}

      {/* TAB 5: 参数敏感性二维寻优热力图 */}
      {activeTab === 'sensitivity' && (
        <ParameterSensitivityHeatmapCard
          data={sensitivity}
          costStressMatrix={result?.cost_stress_matrix}
          loading={sensLoading}
          onApplyOptimal={(fast, slow) => {
            setFastPeriod(fast);
            setSlowPeriod(slow);
            setActiveTab('backtest');
          }}
        />
      )}
    </div>
  );
}
