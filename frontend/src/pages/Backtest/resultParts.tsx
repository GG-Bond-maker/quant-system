/**
 * 策略回测结果组件（对照设计稿）：
 * KpiCards（策略表现概览 4 卡） / NavChart（净值曲线对策 + 买卖 markPoint + DataZoom）
 * / MonthlyChart（月度收益分布） / TradesPanel（最新交易记录） / RiskPanel（风险与收益指标）。
 */
import { useMemo, useRef, useEffect } from 'react';
import * as echarts from '@/lib/echarts';
import type {
  StrategyKpi, StrategyNavPoint, StrategyRisk, StrategySignal, StrategyTrade,
  StrategyMonthly,
} from '@/api/strategyBacktest';

/* ---------- KPI 4 卡 ---------- */
export function KpiCards({ kpi, nav, loading, benchmarkSynthetic }: {
  kpi: StrategyKpi; nav: StrategyNavPoint[]; loading: boolean;
  /** 基准为构造常数（审计 P0-4）：此时"基准年化 0%"不是真实基准收益 */
  benchmarkSynthetic?: boolean;
}) {
  const spark = nav.slice(-60).map((p) => p.strategy);
  const benchSpark = nav.slice(-60).map((p) => p.benchmark ?? p.strategy);
  const pct = (v: number | null | undefined) =>
    v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`;
  const cards = [
    // ⚠️ 三个比率均可为 null（StrategyKpi 声明 number | null）：不得用 `(x ?? 0) >= 0` 兜方向，
    // 否则取数失败会被染成涨/跌。null ⇒ up=undefined（中性），非 null 行为不变。
    { label: '策略年化收益', value: pct(kpi.annual_strategy),
      up: kpi.annual_strategy == null ? undefined : kpi.annual_strategy >= 0,
      data: spark, hl: true },
    { label: benchmarkSynthetic ? '基准年化收益（构造基准）' : '基准年化收益',
      value: pct(kpi.annual_benchmark),
      up: kpi.annual_benchmark == null ? undefined : kpi.annual_benchmark >= 0,
      data: benchSpark, hl: false },
    { label: '夏普比率', value: kpi.sharpe?.toFixed(2) ?? '—',
      up: kpi.sharpe == null ? undefined : kpi.sharpe >= 0,
      data: spark, hl: false },
    // ⚠️ 回撤恒为负 ⇒ 有值时 up:false（绿）是正确的定义性配色；但 max_drawdown 为 null 时
    // 值显 '—' 却仍染绿 = 替"未知"表态。null ⇒ up=undefined（中性），与上面三张卡同判据。
    { label: '最大回撤', value: kpi.max_drawdown != null ? `-${(kpi.max_drawdown * 100).toFixed(1)}%` : '—',
      up: kpi.max_drawdown == null ? undefined : false, data: spark, hl: false },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {cards.map((c) => (
        <div key={c.label} className={`rounded-lg border bg-white px-4 py-3 ${
          c.hl && !loading ? 'border-brand-400 ring-2 ring-brand-100' : 'border-hair'}`}>
          <div className="text-xs text-ink-secondary">{c.label}</div>
          {/* 未知方向（c.up === undefined）必须走中性色，不得落进 t-down（绿）——那是在给"未知"表态 */}
          {loading ? (
            <div className="mt-1.5 h-6 w-20 animate-pulse rounded bg-slate-100" />
          ) : (
            <div className={`num mt-0.5 text-xl font-semibold ${c.up === undefined ? 'text-ink-muted' : c.up ? 't-up' : 't-down'}`}>{c.value}</div>
          )}
          {/* 方向只由 color 单一来源承载（未知走 #94A3B8）；SparkArea 不再收 up（死 prop 已删） */}
          {!loading && c.data.length > 3 && (
            <SparkArea data={c.data}
              color={c.hl ? '#3B82F6' : c.label === '基准年化收益' ? '#F59E0B' : c.up == null ? '#94A3B8' : c.up ? '#3B82F6' : '#EF4444'} />
          )}
        </div>
      ))}
    </div>
  );
}

function SparkArea({ data, color }: { data: number[]; color: string }) {
  const w = 150, h = 34;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) =>
    `${i === 0 ? 'M' : 'L'}${((i / (data.length - 1)) * w).toFixed(1)},${(h - 3 - ((v - min) / range) * (h - 8)).toFixed(1)}`);
  const d = pts.join(' ');
  return (
    <svg width={w} height={h} className="mt-1 w-full" preserveAspectRatio="none" viewBox={`0 0 ${w} ${h}`}>
      <defs>
        <linearGradient id={`kpk-${color.slice(1)}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.25} />
          <stop offset="100%" stopColor={color} stopOpacity={0.02} />
        </linearGradient>
      </defs>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={`url(#kpk-${color.slice(1)})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

/* ---------- 净值曲线对策 ---------- */
export function NavChart({ nav, signals, loading }: {
  nav: StrategyNavPoint[]; signals: StrategySignal[]; loading: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const option = useMemo(() => {
    if (!nav.length) return null;
    const dates = nav.map((p) => p.date);
    // 全量信号通过 tooltip 可查；markPoint 仅标注最近 4 个，
    // 买入标签推到点下方、卖出推到点上方，避免相邻标签压盖
    const pts = signals
      .slice(-4)
      .map((s) => {
        const i = dates.indexOf(s.date);
        return i >= 0 ? { coord: [s.date, nav[i].strategy], value: s.action, symbol: s.symbol } : null;
      })
      .filter((x): x is NonNullable<typeof x> => x != null);
    return {
      tooltip: { trigger: 'axis',
        valueFormatter: (v: number) => v != null ? `${((v - 1) * 100).toFixed(2)}%` : '—' },
      legend: { data: ['策略净值', '基准净值'], top: 0, textStyle: { fontSize: 10 },
        itemWidth: 12, itemHeight: 8 },
      grid: { left: 10, right: 14, top: 26, bottom: 42, containLabel: true },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, color: '#94A3B8' },
        axisTick: { show: false }, axisLine: { lineStyle: { color: '#E2E8F0' } } },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { fontSize: 9, color: '#94A3B8', formatter: (v: number) => `${((v - 1) * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: '#F1F5F9' } },
      },
      dataZoom: [
        { type: 'inside' },
        { type: 'slider', bottom: 4, height: 16, borderColor: '#E2E8F0',
          fillerColor: 'rgba(59,130,246,0.15)', handleStyle: { color: '#3B82F6' } },
      ],
      series: [
        {
          name: '策略净值', type: 'line', data: nav.map((p) => p.strategy),
          showSymbol: false, lineStyle: { width: 1.8, color: '#3B82F6' },
          itemStyle: { color: '#3B82F6' },
          areaStyle: { color: 'rgba(59,130,246,0.08)' },
          markPoint: pts.length ? {
            symbol: 'roundRect', symbolSize: [38, 16], label: {
              show: true, fontSize: 9, color: '#fff',
              formatter: (p: { data: { value: string } }) => p.data.value,
            },
            data: pts.map((p) => ({
              coord: p.coord, value: p.value,
              symbolOffset: p.value === '买入' ? [0, 22] : [0, -22],
              itemStyle: { color: p.value === '买入' ? '#EF4444' : '#22C55E' },
            })),
          } : undefined,
        },
        {
          name: '基准净值', type: 'line', data: nav.map((p) => p.benchmark),
          showSymbol: false, lineStyle: { width: 1.4, color: '#F59E0B' },
          itemStyle: { color: '#F59E0B' },
        },
      ],
    } as echarts.EChartsOption;
  }, [nav, signals]);
  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); };
  }, [option]);

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-white">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">净值曲线对策</h2>
        <span className="text-2xs text-ink-muted">标记点为 MA 交叉产生的实际成交</span>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="animate-pulse" style={{ height: 300 }}>
            <div className="h-full w-full rounded bg-slate-100" />
          </div>
        ) : nav.length ? (
          <div ref={ref} style={{ height: 300 }} className="w-full" />
        ) : (
          <div className="flex h-[300px] items-center justify-center text-xs text-ink-muted">
            运行回测后展示净值曲线
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- 月度收益分布 ---------- */
export function MonthlyChart({ monthly, loading }: {
  monthly: StrategyMonthly[]; loading: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const option = useMemo(() => {
    if (!monthly.length) return null;
    return {
      tooltip: { trigger: 'axis',
        valueFormatter: (v: number) => `${v > 0 ? '+' : ''}${v?.toFixed(2)}%` },
      grid: { left: 10, right: 10, top: 20, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: monthly.map((m) => m.month),
        axisLabel: { fontSize: 9, color: '#94A3B8' }, axisTick: { show: false },
        axisLine: { lineStyle: { color: '#E2E8F0' } } },
      yAxis: {
        type: 'value', axisLabel: { fontSize: 9, color: '#94A3B8', formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: '#F1F5F9' } },
      },
      series: [{
        type: 'bar', barMaxWidth: 22,
        data: monthly.map((m) => ({
          value: m.value,
          itemStyle: { color: m.value >= 0 ? '#3B82F6' : '#F59E0B', borderRadius: [2, 2, 0, 0] },
        })),
      }],
    } as echarts.EChartsOption;
  }, [monthly]);
  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); };
  }, [option]);
  return (
    <div className="flex flex-col rounded-lg border border-hair bg-white">
      <div className="border-b border-hair px-4 py-2.5">
        <h2 className="text-sm font-semibold text-ink">月度收益分布</h2>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="animate-pulse rounded bg-slate-100" style={{ height: 170 }} />
        ) : monthly.length ? (
          <div ref={ref} style={{ height: 170 }} className="w-full" />
        ) : (
          <div className="flex h-[170px] items-center justify-center text-xs text-ink-muted">
            运行回测后展示月度收益
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- 最新交易记录 ---------- */
export function TradesPanel({ trades, loading }: { trades: StrategyTrade[]; loading: boolean }) {
  const shown = trades.slice(-8).reverse(); // 最新在前
  return (
    <div className="flex min-h-0 flex-col">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-ink">最新交易记录</span>
        <span className="num text-2xs text-ink-muted">共 {trades.length} 笔</span>
      </div>
      <div className="max-h-[220px] overflow-auto rounded-md border border-hair">
        <table className="quant-table w-full min-w-[560px]">
          <thead><tr>
            <th>日期</th><th>标的代码</th><th className="text-center">方向</th>
            <th className="text-right">成交价格</th><th className="text-right">成交数量</th>
            <th className="text-right">手续费</th><th className="text-right">单次盈亏</th>
          </tr></thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="py-6 text-center text-xs text-ink-muted">加载中…</td></tr>
            ) : shown.length ? shown.map((t, i) => (
              <tr key={`${t.date}-${t.symbol}-${i}`}>
                <td className="num whitespace-nowrap text-2xs text-ink-secondary">{t.date}</td>
                <td className="num text-xs text-ink">{t.symbol.split('.')[0]}</td>
                <td className="text-center">
                  <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${
                    t.side === '买入' ? 'bg-red-50 text-up' : 'bg-green-50 text-down'}`}>
                    {t.side}
                  </span>
                </td>
                <td className="num text-right text-xs text-ink">{t.price.toFixed(2)}</td>
                <td className="num text-right text-xs text-ink-secondary">{t.qty.toLocaleString('en-US')}</td>
                <td className="num text-right text-xs text-ink-secondary">{t.fee.toFixed(2)}</td>
                <td className={`num text-right text-xs font-medium ${t.pnl == null ? 'text-ink-muted' : t.pnl >= 0 ? 't-up' : 't-down'}`}>
                  {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${(t.pnl / 1e4).toFixed(0)} 万`}
                </td>
              </tr>
            )) : (
              <tr><td colSpan={7} className="py-6 text-center text-xs text-ink-muted">暂无交易记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------- 风险与收益指标 ---------- */
export function RiskPanel({ risk, loading }: { risk: StrategyRisk | null; loading: boolean }) {
  const rows: Array<[string, string]> = [
    ['阿尔法 Alpha', risk?.alpha != null ? risk.alpha.toFixed(2) : '—'],
    ['贝塔 Beta', risk?.beta != null ? risk.beta.toFixed(2) : '—'],
    ['索提诺比率 Sortino', risk?.sortino != null ? risk.sortino.toFixed(2) : '—'],
    ['信息比率 Information Ratio', risk?.info_ratio != null ? risk.info_ratio.toFixed(2) : '—'],
    ['平均盈亏比', risk?.avg_pnl_ratio != null ? risk.avg_pnl_ratio.toFixed(2) : '—'],
    ['胜率', risk?.win_rate != null ? `${(risk.win_rate * 100).toFixed(2)}%` : '—'],
  ];
  return (
    <div className="mt-4 border-t border-hair pt-3">
      <div className="mb-2 text-xs font-semibold text-ink">风险与收益指标</div>
      <div className="space-y-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 text-xs">
            <span className="shrink-0 text-ink-secondary">{k}</span>
            {loading ? (
              <span className="h-3 w-12 animate-pulse rounded bg-slate-100" />
            ) : (
              <span className="num font-medium text-ink">{v}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
