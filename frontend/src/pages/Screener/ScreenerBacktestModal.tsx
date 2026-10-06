import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, CATEGORY_COLORS } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { Modal } from '@/components/ui';
import { strategyBacktestApi, type StrategyBacktestResult } from '@/api/strategyBacktest';
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

export default function ScreenerBacktestModal({ open, onClose, symbols, asOfDate }: Props) {
  const [years, setYears] = useState(1);
  const [loading, setLoading] = useState(false);
  const [progressText, setProgressText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrategyBacktestResult | null>(null);

  const chartRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  // 默认取前 10 只标的参与回测
  const testSymbols = useMemo(() => symbols.slice(0, 10), [symbols]);

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
      setError('没有可用于回测的选图标的');
      return;
    }
    setLoading(true);
    setError(null);
    setProgressText('正在提交异步回测任务...');
    setResult(null);

    try {
      const res = await strategyBacktestApi.runAsyncWithPolling(
        {
          strategy_name: 'MA_CROSS',
          symbols: testSymbols,
          start: startStr,
          end: endStr,
          init_cash: 1_000_000,
          commission_rate: 0.0003,
          short_ma: 5,
          long_ma: 20,
          trailing_stop_pct: 5,
        },
        {
          pollIntervalMs: 1200,
          onProgress: (_p, s) => {
            setProgressText(s === 'queued' ? '任务排队中...' : '回测撮合与收益指标测算中...');
          },
        },
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : '回测执行异常');
    } finally {
      setLoading(false);
      setProgressText('');
    }
  };

  // ECharts 收益曲线渲染
  useEffect(() => {
    if (!chartRef.current || !result || !result.nav_curve?.length) return;
    const chart = echarts.init(chartRef.current);
    const p = chartPalette();

    const dates = result.nav_curve.map((pt) => pt.date);
    const strat = result.nav_curve.map((pt) => Number(((pt.strategy - 1) * 100).toFixed(2)));
    const bench = result.nav_curve.map((pt) =>
      pt.benchmark != null ? Number(((pt.benchmark - 1) * 100).toFixed(2)) : null,
    );

    chart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          const d = params[0]?.axisValue;
          let tip = `<div style="font-size:12px;"><b>${d}</b><br/>`;
          params.forEach((it) => {
            tip += `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${it.color};margin-right:4px;"></span>${it.seriesName}: <b>${it.value != null ? it.value + '%' : '—'}</b><br/>`;
          });
          tip += '</div>';
          return tip;
        },
      },
      legend: {
        data: ['选股策略组合', '沪深300基准'],
        top: 0,
        textStyle: { fontSize: 11, color: p.INK2 },
      },
      grid: { left: 8, right: 12, top: 28, bottom: 20, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
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
          data: strat,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: CATEGORY_COLORS.C1 },
          itemStyle: { color: CATEGORY_COLORS.C1 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(59, 130, 246, 0.35)' },
              { offset: 1, color: 'rgba(59, 130, 246, 0.02)' },
            ]),
          },
        },
        {
          name: '沪深300基准',
          type: 'line',
          data: bench,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: p.HAIR2, type: 'dashed' },
          itemStyle: { color: p.HAIR2 },
        },
      ],
    });

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [result, theme]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="一键选股历史回测验证"
      sub={`联动选股标的池 · 检验策略历史表现与风险指标 (${startStr} ~ ${endStr})`}
    >
      <div className="space-y-4 text-xs">
        {/* 顶部候选标的展示与参数控制 */}
        <div className="rounded-md border border-hair bg-surface-alt/40 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="space-y-1">
              <div className="font-medium text-ink">
                参与回测标的池 ({testSymbols.length} 只):
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
            <div className="flex items-center gap-2">
              <div className="flex items-center rounded border border-hair bg-surface p-0.5">
                {PERIOD_OPTIONS.map((opt) => (
                  <button
                    key={opt.years}
                    type="button"
                    onClick={() => setYears(opt.years)}
                    className={`rounded px-2 py-1 text-2xs transition-colors ${
                      years === opt.years
                        ? 'bg-brand-500 font-medium text-white'
                        : 'text-ink-secondary hover:text-ink'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => void runBacktest()}
                disabled={loading}
                className="rounded bg-brand-500 px-3 py-1.5 font-medium text-white shadow-sm hover:bg-brand-600 disabled:opacity-50"
              >
                {loading ? '测算中...' : '开始回测'}
              </button>
            </div>
          </div>
          {loading && (
            <div className="mt-2 text-2xs text-brand-600 animate-pulse font-medium">
              {progressText}
            </div>
          )}
          {error && (
            <div className="mt-2 text-2xs text-danger font-medium">
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
                <div className="text-2xs text-ink-muted">策略年化收益</div>
                <div
                  className={`num text-sm font-semibold ${
                    (result.kpi?.annual_strategy ?? 0) >= 0 ? 'text-up' : 'text-down'
                  }`}
                >
                  {result.kpi?.annual_strategy != null
                    ? fmtPct(result.kpi.annual_strategy * 100)
                    : '—'}
                </div>
              </div>
              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">基准年化收益</div>
                <div className="num text-sm font-semibold text-ink-secondary">
                  {result.kpi?.annual_benchmark != null
                    ? fmtPct(result.kpi.annual_benchmark * 100)
                    : '—'}
                </div>
              </div>
              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">夏普比率 (Sharpe)</div>
                <div className="num text-sm font-semibold text-ink">
                  {result.kpi?.sharpe != null ? result.kpi.sharpe.toFixed(2) : '—'}
                </div>
              </div>
              <div className="rounded border border-hair bg-surface p-2 text-center">
                <div className="text-2xs text-ink-muted">最大回撤 (MaxDD)</div>
                <div className="num text-sm font-semibold text-down">
                  {result.kpi?.max_drawdown != null
                    ? fmtPct(result.kpi.max_drawdown * 100)
                    : '—'}
                </div>
              </div>
            </div>

            {/* 附加风控指标条 */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded bg-surface-alt/40 px-3 py-1.5 text-2xs text-ink-secondary">
              <span>
                胜率:{' '}
                <b className="text-ink">
                  {result.risk?.win_rate != null ? fmtPct(result.risk.win_rate * 100) : '—'}
                </b>
              </span>
              <span>
                总交易次数: <b className="text-ink">{result.risk?.total_trades ?? 0} 次</b>
              </span>
              <span>
                盈亏比:{' '}
                <b className="text-ink">
                  {result.risk?.avg_pnl_ratio != null
                    ? result.risk.avg_pnl_ratio.toFixed(2)
                    : '—'}
                </b>
              </span>
              <span>
                信息比率:{' '}
                <b className="text-ink">
                  {result.risk?.info_ratio != null ? result.risk.info_ratio.toFixed(2) : '—'}
                </b>
              </span>
            </div>

            {/* ECharts 净值曲线 */}
            <div className="rounded border border-hair bg-surface p-2">
              <div className="mb-1 text-2xs font-medium text-ink-muted">
                累计收益率走势 (% vs 沪深300)
              </div>
              <div ref={chartRef} className="h-[220px] w-full" />
            </div>
          </div>
        )}

        {!result && !loading && !error && (
          <div className="py-8 text-center text-ink-muted">
            点击“开始回测”，直接对当前筛选出的 Top 候选池执行历史轮动走势测算
          </div>
        )}
      </div>
    </Modal>
  );
}
