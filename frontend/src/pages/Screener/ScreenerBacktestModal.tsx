/**
 * 选股中心 · 选股组合历史调仓回测引擎 (Screener Basket Portfolio Backtest)。
 *
 * 核心量化价值（彻底拔除玩具级单股 MA_CROSS，直连真实组合回测引擎）：
 * - 联动选股截面候选池标的（Top 10~15 只）；
 * - 调用真实 portfolioApi.backtest 向量化再平衡引擎；
 * - 支持定期调仓（月度调仓 M / 季度调仓 Q / 买入持有 none）；
 * - 支持等权配置、风险平价 (Risk Parity)、逆波动率加权；
 * - 真实测算摩擦成本、夏普比率、最大回撤、年化收益、Alpha/Beta；
 * - 杜绝造假：纯真实历史行情切片与收益率复权测算。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha, CATEGORY_COLORS } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { Modal } from '@/components/ui';
import { portfolioApi } from '@/api/portfolio';
import type {
  PortfolioBacktestResult,
  PortfolioWeighting,
  RebalanceFreq,
} from '@/types/portfolio';
import { fmtPct } from '@/utils/format';

interface Props {
  open: boolean;
  onClose: () => void;
  symbols: string[];
  asOfDate?: string;
}

const PERIOD_OPTIONS = [
  { label: '近 1 年', years: 1 },
  { label: '近 2 年', years: 2 },
  { label: '近 3 年', years: 3 },
];

const REBALANCE_OPTIONS: Array<{ label: string; value: RebalanceFreq }> = [
  { label: '月度调仓 (M)', value: 'M' },
  { label: '季度调仓 (Q)', value: 'Q' },
  { label: '买入持有 (无调仓)', value: 'none' },
];

const BENCHMARK_OPTIONS = [
  { label: '沪深300 (000300.SH)', value: '000300.SH' },
  { label: '中证500 (000905.SH)', value: '000905.SH' },
  { label: '中证1000 (000852.SH)', value: '000852.SH' },
];

const WEIGHT_OPTIONS: Array<{ label: string; value: PortfolioWeighting }> = [
  { label: '等权配置', value: 'user' },
  { label: '风险平价 (Risk Parity)', value: 'risk_parity' },
  { label: '逆波动率 (Inverse Vol)', value: 'inverse_vol' },
];

export default function ScreenerBacktestModal({ open, onClose, symbols, asOfDate }: Props) {
  const [years, setYears] = useState(1);
  const [rebalance, setRebalance] = useState<RebalanceFreq>('M');
  const [benchmark, setBenchmark] = useState('000300.SH');
  const [weighting, setWeighting] = useState<PortfolioWeighting>('user');
  const [chartTab, setChartTab] = useState<'nav' | 'drawdown'>('nav');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PortfolioBacktestResult | null>(null);

  const chartRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  // 默认取当前选股池的前 12 只标的参与组合回测
  const testSymbols = useMemo(() => symbols.slice(0, 12), [symbols]);

  // 计算时间区间
  const { startStr, endStr } = useMemo(() => {
    const end = asOfDate ? new Date(asOfDate) : new Date();
    const start = new Date(end);
    start.setFullYear(start.getFullYear() - years);
    const toYMD = (d: Date) => d.toISOString().slice(0, 10);
    return { startStr: toYMD(start), endStr: toYMD(end) };
  }, [asOfDate, years]);

  const runBacktest = async () => {
    if (!testSymbols.length) {
      setError('没有可用于组合回测的选图标的');
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const equalWeight = Number((1 / testSymbols.length).toFixed(4));
      const assets = testSymbols.map((code) => ({
        code,
        type: 'stock' as const,
        weight: equalWeight,
      }));

      const res = await portfolioApi.backtest({
        assets,
        start_date: startStr,
        end_date: endStr,
        rebalance,
        benchmark,
        initial_cash: 1_000_000,
        weighting,
        cov_window: 60,
      });

      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : '选股组合回测执行异常');
    } finally {
      setLoading(false);
    }
  };

  // ECharts 净值与回撤曲线渲染
  useEffect(() => {
    if (!chartRef.current || !result) return;
    const chart = echarts.init(chartRef.current);
    const p = chartPalette();

    if (chartTab === 'nav') {
      const dates = result.nav_curve.map((pt) => pt.date);
      const portNav = result.nav_curve.map((pt) => Number(((pt.nav - 1) * 100).toFixed(2)));
      const benchNav = result.nav_curve.map((pt) =>
        pt.benchmark != null ? Number(((pt.benchmark - 1) * 100).toFixed(2)) : null,
      );

      chart.setOption({
        tooltip: {
          trigger: 'axis',
          formatter: (params: any[]) => {
            const d = params[0]?.axisValue;
            let tip = `<div style="font-size:11px;"><b>${d}</b><br/>`;
            params.forEach((it) => {
              tip += `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${it.color};margin-right:4px;"></span>${it.seriesName}: <b>${it.value != null ? (it.value >= 0 ? '+' : '') + it.value + '%' : '—'}</b><br/>`;
            });
            tip += '</div>';
            return tip;
          },
        },
        legend: {
          data: ['选股策略组合', '基准指数'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        grid: { left: 8, right: 12, top: 28, bottom: 15, containLabel: true },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: false,
          axisLabel: { fontSize: 9, color: p.INKM },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            formatter: (v: number) => `${v}%`,
          },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        series: [
          {
            name: '选股策略组合',
            type: 'line',
            data: portNav,
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 2, color: CATEGORY_COLORS.C1 },
            itemStyle: { color: CATEGORY_COLORS.C1 },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(59, 130, 246, 0.32)' },
                { offset: 1, color: 'rgba(59, 130, 246, 0.02)' },
              ]),
            },
          },
          {
            name: '基准指数',
            type: 'line',
            data: benchNav,
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 1.5, color: p.HAIR2, type: 'dashed' },
            itemStyle: { color: p.HAIR2 },
          },
        ],
      });
    } else {
      // 回撤曲线
      const dates = result.drawdown_curve.map((pt) => pt.date);
      const ddData = result.drawdown_curve.map((pt) => Number((pt.drawdown * 100).toFixed(2)));

      chart.setOption({
        tooltip: {
          trigger: 'axis',
          formatter: (params: any[]) => {
            const it = params[0];
            return `<div style="font-size:11px;"><b>${it?.axisValue}</b><br/>动态回撤: <b style="color:${p.DANGER}">${it?.value}%</b></div>`;
          },
        },
        grid: { left: 8, right: 12, top: 16, bottom: 15, containLabel: true },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: false,
          axisLabel: { fontSize: 9, color: p.INKM },
        },
        yAxis: {
          type: 'value',
          max: 0,
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            formatter: (v: number) => `${v}%`,
          },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        series: [
          {
            name: '动态回撤',
            type: 'line',
            data: ddData,
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 1.8, color: p.DANGER },
            itemStyle: { color: p.DANGER },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: withAlpha(p.DANGER, 0.35) },
                { offset: 1, color: withAlpha(p.DANGER, 0.05) },
              ]),
            },
          },
        ],
      });
    }

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [result, chartTab, theme]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="选股组合截面调仓历史回测"
      sub={`联动选股标的池 · 向量化多资产再平衡历史测算 (${startStr} ~ ${endStr})`}
    >
      <div className="space-y-3.5 text-xs">
        {/* 顶部候选标的展示与参数控制 */}
        <div className="rounded-md border border-hair bg-surface-alt/40 p-3 space-y-2.5">
          <div className="space-y-1">
            <div className="flex items-center justify-between text-2xs">
              <span className="font-medium text-ink">
                参与组合回测标的池 ({testSymbols.length} 只):
              </span>
              <span className="text-ink-muted">选股截面 Top 排序优先打包</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {testSymbols.map((s) => (
                <span
                  key={s}
                  className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-2xs text-ink-secondary"
                >
                  {s}
                </span>
              ))}
            </div>
          </div>

          {/* 回测参数选择 */}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-4 border-t border-hair/60 pt-2 text-2xs">
            <div>
              <span className="text-ink-secondary block mb-1">回测周期</span>
              <div className="flex rounded border border-hair bg-surface p-0.5">
                {PERIOD_OPTIONS.map((opt) => (
                  <button
                    key={opt.years}
                    type="button"
                    onClick={() => setYears(opt.years)}
                    className={`flex-1 rounded py-0.5 text-center transition-colors ${
                      years === opt.years
                        ? 'bg-brand-500 font-medium text-white'
                        : 'text-ink-secondary hover:text-ink'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <span className="text-ink-secondary block mb-1">再平衡调仓频率</span>
              <select
                value={rebalance}
                onChange={(e) => setRebalance(e.target.value as RebalanceFreq)}
                className="w-full rounded border border-hair bg-surface px-1.5 py-1 text-2xs text-ink outline-none"
              >
                {REBALANCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <span className="text-ink-secondary block mb-1">基准指数</span>
              <select
                value={benchmark}
                onChange={(e) => setBenchmark(e.target.value)}
                className="w-full rounded border border-hair bg-surface px-1.5 py-1 text-2xs text-ink outline-none"
              >
                {BENCHMARK_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <span className="text-ink-secondary block mb-1">权重模型</span>
              <select
                value={weighting}
                onChange={(e) => setWeighting(e.target.value as PortfolioWeighting)}
                className="w-full rounded border border-hair bg-surface px-1.5 py-1 text-2xs text-ink outline-none"
              >
                {WEIGHT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex items-center justify-between border-t border-hair/60 pt-2">
            <span className="text-2xs text-ink-muted">
              摩擦成本：双边佣金万三 + 印花税万五 + 滑点千一 · 初始资金 100 万元
            </span>
            <button
              type="button"
              onClick={() => void runBacktest()}
              disabled={loading || testSymbols.length === 0}
              className="rounded bg-brand-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-brand-600 disabled:opacity-50"
            >
              {loading ? '正在进行组合向量化测算...' : '执行组合回测'}
            </button>
          </div>

          {error && (
            <div className="mt-1 text-2xs text-danger font-medium">
              回测异常: {error}
            </div>
          )}
        </div>

        {/* 回测结果指标与净值曲线 */}
        {result && (
          <div className="space-y-3">
            {/* KPI 指标卡片 */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">累计总收益率</div>
                <div
                  className={`num text-sm font-semibold ${
                    result.metrics.total_return >= 0 ? 'text-up' : 'text-down'
                  }`}
                >
                  {fmtPct(result.metrics.total_return * 100)}
                </div>
                <div className="text-[10px] text-ink-muted">
                  年化 CAGR: {fmtPct(result.metrics.cagr * 100)}
                </div>
              </div>

              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">夏普比率 (Sharpe)</div>
                <div className="num text-sm font-semibold text-ink">
                  {result.metrics.sharpe.toFixed(2)}
                </div>
                <div className="text-[10px] text-ink-muted">
                  年化波动率: {fmtPct(result.metrics.volatility * 100)}
                </div>
              </div>

              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">最大回撤 (MaxDD)</div>
                <div className="num text-sm font-semibold text-down">
                  {fmtPct(result.metrics.max_drawdown * 100)}
                </div>
                <div className="text-[10px] text-ink-muted">
                  卡玛 Calmar: {result.metrics.calmar != null ? result.metrics.calmar.toFixed(2) : '—'}
                </div>
              </div>

              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">阿尔法 (Alpha)</div>
                <div
                  className={`num text-sm font-semibold ${
                    result.metrics.alpha >= 0 ? 'text-up' : 'text-down'
                  }`}
                >
                  {fmtPct(result.metrics.alpha * 100)}
                </div>
                <div className="text-[10px] text-ink-muted">
                  贝塔 Beta: {result.metrics.beta.toFixed(2)}
                </div>
              </div>
            </div>

            {/* ECharts 净值曲线与回撤选项卡 */}
            <div className="rounded border border-hair bg-surface p-2.5">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-1 rounded bg-surface-sunken p-0.5 text-2xs">
                  <button
                    type="button"
                    onClick={() => setChartTab('nav')}
                    className={`rounded px-2 py-0.5 transition-colors ${
                      chartTab === 'nav'
                        ? 'bg-surface font-medium text-brand-600 shadow-sm'
                        : 'text-ink-muted hover:text-ink-secondary'
                    }`}
                  >
                    累计收益率走势 (% vs 基准)
                  </button>
                  <button
                    type="button"
                    onClick={() => setChartTab('drawdown')}
                    className={`rounded px-2 py-0.5 transition-colors ${
                      chartTab === 'drawdown'
                        ? 'bg-surface font-medium text-brand-600 shadow-sm'
                        : 'text-ink-muted hover:text-ink-secondary'
                    }`}
                  >
                    动态水下回撤曲线 (Drawdown)
                  </button>
                </div>
                <span className="text-2xs text-ink-muted">
                  交易日数: {result.trading_days} 天
                </span>
              </div>

              <div ref={chartRef} className="h-[210px] w-full" />
            </div>
          </div>
        )}

        {!result && !loading && !error && (
          <div className="py-8 text-center text-xs text-ink-muted">
            点击“执行组合回测”，直连向量化资产调仓引擎，检验选股篮子在真实摩擦下的历史表现。
          </div>
        )}
      </div>
    </Modal>
  );
}
