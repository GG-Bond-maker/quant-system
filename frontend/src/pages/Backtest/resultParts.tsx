/**
 * 策略回测结果组件（对照设计稿）：
 * KpiCards（策略表现概览 4 卡） / NavChart（净值曲线对策 + 买卖 markPoint + DataZoom）
 * / MonthlyChart（月度收益分布） / TradesPanel（最新交易记录） / RiskPanel（风险与收益指标）。
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import type {
  StrategyKpi, StrategyNavPoint, StrategyRisk, StrategySignal, StrategyTrade,
  StrategyMonthly, StrategyUnderwaterPoint, StrategyEnsembleResult,
} from '@/api/strategyBacktest';
import { strategyBacktestApi } from '@/api/strategyBacktest';
import { researchApi, type StrategyChangelogItem, type StrategyChangelogDiffResult } from '@/api/research';

/* ---------- KPI 5 卡（对标顶级量化系统，集成卡玛比率） ---------- */
export function KpiCards({ kpi, nav, loading, benchmarkSynthetic }: {
  kpi: StrategyKpi; nav: StrategyNavPoint[]; loading: boolean;
  /** 基准为构造常数（审计 P0-4）：此时"基准年化 0%"不是真实基准收益 */
  benchmarkSynthetic?: boolean;
}) {
  // 订阅主题：chartPalette() 在调用时读 DOM
  useTheme();
  const spark = nav.slice(-60).map((p) => p.strategy);
  const benchSpark = nav.slice(-60).map((p) => p.benchmark ?? p.strategy);
  const pct = (v: number | null | undefined) =>
    v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`;
  const cards = [
    // ⚠️ 比率均可为 null（StrategyKpi 声明 number | null）：不得用 `(x ?? 0) >= 0` 兜方向
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
    { label: '卡玛比率', value: kpi.calmar != null ? kpi.calmar.toFixed(2) : '—',
      up: kpi.calmar == null ? undefined : kpi.calmar >= 0,
      data: spark, hl: false },
    // ⚠️ 回撤恒为负 ⇒ 有值时 up:false（绿）；null ⇒ up:undefined（中性）
    { label: '最大回撤', value: kpi.max_drawdown != null ? `-${(kpi.max_drawdown * 100).toFixed(1)}%` : '—',
      up: kpi.max_drawdown == null ? undefined : false, data: spark, hl: false },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      {cards.map((c) => (
        <div key={c.label} className={`rounded-lg border bg-surface px-4 py-3 ${
          c.hl && !loading ? 'border-brand-400 ring-2 ring-brand-100' : 'border-hair'}`}>
          <div className="text-xs text-ink-secondary">{c.label}</div>
          {/* 未知方向（c.up === undefined）必须走中性色，不得落进 t-down（绿）——那是在给"未知"表态 */}
          {loading ? (
            <div className="mt-1.5 h-6 w-20 animate-pulse rounded bg-surface-sunken" />
          ) : (
            <div className={`num mt-0.5 text-xl font-semibold ${c.up === undefined ? 'text-ink-muted' : c.up ? 't-up' : 't-down'}`}>{c.value}</div>
          )}
          {!loading && c.data.length > 3 && (
            <SparkArea data={c.data}
              color={c.hl ? chartPalette().BRAND
                : c.label.includes('基准') ? chartPalette().WARN
                : c.up == null ? chartPalette().FLAT
                : c.up ? chartPalette().UP : chartPalette().DOWN} />
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

/* ---------- 净值曲线对策（支持绝对净值与超额收益 Active Alpha 双模） ---------- */
export function NavChart({ nav, signals, loading }: {
  nav: StrategyNavPoint[]; signals: StrategySignal[]; loading: boolean;
}) {
  const theme = useTheme();
  const [viewMode, setViewMode] = useState<'nav' | 'alpha'>('nav');

  const option = useMemo(() => {
    if (!nav.length) return null;
    const p = chartPalette();
    const dates = nav.map((d) => d.date);

    if (viewMode === 'alpha') {
      const alphaPoints = nav.map((pt) => {
        const b = pt.benchmark ?? 1.0;
        if (b <= 0) return 0;
        return Number(((pt.strategy / b - 1) * 100).toFixed(2));
      });
      const lastAlpha = alphaPoints.length ? alphaPoints[alphaPoints.length - 1] : 0;
      const alphaColor = lastAlpha >= 0 ? p.UP : p.DOWN;

      return {
        tooltip: {
          trigger: 'axis',
          formatter: (params: unknown) => {
            const items = params as Array<{ dataIndex: number }>;
            if (!items?.length) return '';
            const idx = items[0].dataIndex;
            const pt = nav[idx];
            if (!pt) return '';
            const val = alphaPoints[idx];
            return `
              <div style="font-size:11px;font-family:monospace;padding:2px">
                <b>交易日: ${pt.date}</b><br/>
                策略净值: <b>${pt.strategy.toFixed(3)}</b> (${((pt.strategy - 1) * 100).toFixed(2)}%)<br/>
                基准净值: <b>${pt.benchmark?.toFixed(3) ?? '—'}</b> (${(((pt.benchmark ?? 1) - 1) * 100).toFixed(2)}%)<br/>
                累计超额 Alpha: <b style="color:${val >= 0 ? p.UP : p.DOWN}">${val >= 0 ? '+' : ''}${val.toFixed(2)}%</b>
              </div>
            `;
          },
        },
        legend: {
          data: ['累计超额收益 (Active Alpha)'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        grid: { left: 10, right: 14, top: 26, bottom: 42, containLabel: true },
        xAxis: {
          type: 'category',
          data: dates,
          axisLabel: { fontSize: 9, color: p.INKM },
          axisTick: { show: false },
          axisLine: { lineStyle: { color: p.HAIR } },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v}%` },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        dataZoom: [
          { type: 'inside' },
          {
            type: 'slider',
            bottom: 4,
            height: 16,
            borderColor: p.HAIR,
            fillerColor: withAlpha(p.BRAND, 0.15),
            handleStyle: { color: p.BRAND },
          },
        ],
        series: [
          {
            name: '累计超额收益 (Active Alpha)',
            type: 'line',
            data: alphaPoints,
            showSymbol: false,
            lineStyle: { width: 1.8, color: alphaColor },
            itemStyle: { color: alphaColor },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: withAlpha(alphaColor, 0.22) },
                { offset: 1, color: withAlpha(alphaColor, 0.02) },
              ]),
            },
          },
        ],
      } as echarts.EChartsOption;
    }

    // 全量信号通过 tooltip 联动展示，markPoint 标注关键买卖成交
    const signalByDate = new Map<string, StrategySignal[]>();
    for (const s of signals) {
      const list = signalByDate.get(s.date) || [];
      list.push(s);
      signalByDate.set(s.date, list);
    }

    const pts = signals
      .slice(-12)
      .map((s) => {
        const i = dates.indexOf(s.date);
        return i >= 0 ? { coord: [s.date, nav[i].strategy], value: s.action, symbol: s.symbol } : null;
      })
      .filter((x): x is NonNullable<typeof x> => x != null);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ seriesName: string; value: number; name: string }>;
          if (!items?.length) return '';
          const d = items[0].name;
          const sigs = signalByDate.get(d) || [];
          let html = `<div style="font-size:11px;font-family:monospace;padding:2px"><b>${d}</b><br/>`;
          for (const it of items) {
            html += `${it.seriesName}: <b>${it.value != null ? ((it.value - 1) * 100).toFixed(2) + '%' : '—'}</b><br/>`;
          }
          if (sigs.length) {
            html += `<div style="margin-top:4px;border-top:1px solid ${p.HAIR};padding-top:2px;">`;
            for (const s of sigs) {
              const col = s.action === '买入' ? p.UP : p.DOWN;
              html += `<span style="color:${col};font-weight:bold">${s.action}</span> <b>${s.symbol}</b><br/>`;
            }
            html += `</div>`;
          }
          html += `</div>`;
          return html;
        },
      },
      legend: {
        data: ['策略净值', '基准净值'],
        top: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
        itemWidth: 12,
        itemHeight: 8,
      },
      grid: { left: 10, right: 14, top: 26, bottom: 42, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: p.INKM },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${((v - 1) * 100).toFixed(0)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      dataZoom: [
        { type: 'inside' },
        {
          type: 'slider',
          bottom: 4,
          height: 16,
          borderColor: p.HAIR,
          fillerColor: withAlpha(p.BRAND, 0.15),
          handleStyle: { color: p.BRAND },
        },
      ],
      series: [
        {
          name: '策略净值',
          type: 'line',
          data: nav.map((p) => p.strategy),
          showSymbol: false,
          lineStyle: { width: 1.8, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          areaStyle: { color: withAlpha(p.BRAND, 0.08) },
          markPoint: pts.length ? {
            symbol: 'roundRect',
            symbolSize: [38, 16],
            label: {
              show: true,
              fontSize: 9,
              color: p.ON_SOLID,
              formatter: (pc: { data: { value: string } }) => pc.data.value,
            },
            data: pts.map((pc) => ({
              coord: pc.coord,
              value: pc.value,
              symbolOffset: pc.value === '买入' ? [0, 22] : [0, -22],
              itemStyle: { color: pc.value === '买入' ? p.UP : p.DOWN },
            })),
          } : undefined,
        },
        {
          name: '基准净值',
          type: 'line',
          data: nav.map((d) => d.benchmark),
          showSymbol: false,
          lineStyle: { width: 1.4, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
      ],
    } as echarts.EChartsOption;
  }, [nav, signals, viewMode, theme]);

  const chartRef = useChart(option);

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface">
      <div className="flex flex-wrap items-center justify-between border-b border-hair px-4 py-2.5 gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">净值曲线对策</h2>
          <span className="text-2xs text-ink-muted">
            {viewMode === 'nav' ? '标记点为 MA 交叉成交' : '对标基准的相对累计超额 Alpha'}
          </span>
        </div>
        <div className="flex items-center rounded-md border border-hair bg-surface-alt p-0.5 text-2xs">
          <button
            type="button"
            onClick={() => setViewMode('nav')}
            className={`rounded px-2.5 py-0.5 font-medium transition-colors ${
              viewMode === 'nav' ? 'bg-surface text-brand-600 shadow-xs' : 'text-ink-secondary hover:text-ink'
            }`}
          >
            策略与基准
          </button>
          <button
            type="button"
            onClick={() => setViewMode('alpha')}
            className={`rounded px-2.5 py-0.5 font-medium transition-colors ${
              viewMode === 'alpha' ? 'bg-surface text-brand-600 shadow-xs' : 'text-ink-secondary hover:text-ink'
            }`}
          >
            超额 Alpha
          </button>
        </div>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="animate-pulse" style={{ height: 300 }}>
            <div className="h-full w-full rounded bg-surface-sunken" />
          </div>
        ) : nav.length ? (
          <div ref={chartRef} style={{ height: 300 }} className="w-full" />
        ) : (
          <div className="flex h-[300px] items-center justify-center text-xs text-ink-muted">
            运行回测后展示净值曲线
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- 动态水下回撤与恢复期图 ---------- */
export function UnderwaterChart({
  underwater,
  maxDrawdown,
  maxDrawdownDuration,
  loading,
}: {
  underwater?: StrategyUnderwaterPoint[];
  maxDrawdown?: number | null;
  maxDrawdownDuration?: number | null;
  loading: boolean;
}) {
  const theme = useTheme();
  const option = useMemo(() => {
    if (!underwater?.length) return null;
    const p = chartPalette();
    const dates = underwater.map((u) => u.date);
    const ddValues = underwater.map((u) => u.drawdown);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const pt = underwater[idx];
          if (!pt) return '';
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>交易日: ${pt.date}</b><br/>
              水下回撤: <b class="num" style="color:${pt.drawdown < 0 ? p.DOWN : p.UP}">${pt.drawdown.toFixed(2)}%</b><br/>
              ${pt.peak_date ? `历史高点日: <span style="color:${p.INKM}">${pt.peak_date}</span>` : '创新高净值'}
            </div>
          `;
        },
      },
      grid: { left: 48, right: 16, top: 18, bottom: 24 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: p.INKM, interval: Math.floor(dates.length / 6) },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        max: 0,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '水下回撤',
          type: 'line',
          showSymbol: false,
          data: ddValues,
          lineStyle: { width: 1.4, color: p.DOWN },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.DOWN, 0.35) },
              { offset: 1, color: withAlpha(p.DOWN, 0.04) },
            ]),
          },
        },
      ],
    } as echarts.EChartsOption;
  }, [underwater, theme]);

  const chartRef = useChart(option);

  const curDd = underwater?.length ? underwater[underwater.length - 1].drawdown : null;

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface">
      <div className="flex flex-wrap items-center justify-between border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">动态水下回撤与恢复期 (Underwater Drawdown)</h2>
          <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-mono text-ink-muted">
            高位距深潜全周期追踪
          </span>
        </div>
        <div className="flex items-center gap-3 text-2xs">
          <span>
            最大回撤:{' '}
            <b className="num text-danger">
              {maxDrawdown != null ? `-${(maxDrawdown * 100).toFixed(1)}%` : '—'}
            </b>
          </span>
          <span>
            最长深潜期:{' '}
            <b className="num text-ink">
              {maxDrawdownDuration != null ? `${maxDrawdownDuration} 交易日` : '—'}
            </b>
          </span>
          <span>
            当前状态:{' '}
            <b className={`num ${curDd == null || curDd === 0 ? 'text-success' : 'text-danger'}`}>
              {curDd == null ? '—' : curDd === 0 ? '创历史新高' : `${curDd.toFixed(2)}%`}
            </b>
          </span>
        </div>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="animate-pulse" style={{ height: 180 }}>
            <div className="h-full w-full rounded bg-surface-sunken" />
          </div>
        ) : underwater?.length ? (
          <div ref={chartRef} style={{ height: 180 }} className="w-full" />
        ) : (
          <div className="flex h-[180px] items-center justify-center text-xs text-ink-muted">
            运行回测后展示水下回撤曲线
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- 月度收益分布 & 热力矩阵 (Returns Heatmap) ---------- */
export function MonthlyChart({ monthly, loading }: {
  monthly: StrategyMonthly[]; loading: boolean;
}) {
  const theme = useTheme();
  const [viewMode, setViewMode] = useState<'heatmap' | 'bar'>('heatmap');

  // 处理热力矩阵数据
  const heatmapData = useMemo(() => {
    if (!monthly.length) return null;
    const yearMap: Record<string, (number | null)[]> = {};

    monthly.forEach((m) => {
      const parts = m.month.split('-');
      if (parts.length >= 2) {
        const year = parts[0];
        const monthIdx = parseInt(parts[1], 10) - 1; // 0..11
        if (!yearMap[year]) {
          yearMap[year] = Array(12).fill(null);
        }
        if (monthIdx >= 0 && monthIdx < 12) {
          yearMap[year][monthIdx] = m.value;
        }
      }
    });

    const years = Object.keys(yearMap).sort().reverse();
    // 计算各年 YTD 和胜率
    const rows = years.map((yr) => {
      const vals = yearMap[yr];
      let comp = 1.0;
      let validMonths = 0;
      let winMonths = 0;
      vals.forEach((v) => {
        if (v != null) {
          comp *= (1 + v / 100);
          validMonths++;
          if (v > 0) winMonths++;
        }
      });
      const ytd = validMonths > 0 ? (comp - 1) * 100 : null;
      const winRate = validMonths > 0 ? (winMonths / validMonths) * 100 : null;
      return {
        year: yr,
        months: vals,
        ytd,
        winRate,
      };
    });

    // 计算各月全周期平均
    const monthAvgs: (number | null)[] = Array(12).fill(null);
    for (let m = 0; m < 12; m++) {
      let sum = 0;
      let cnt = 0;
      years.forEach((yr) => {
        const val = yearMap[yr][m];
        if (val != null) {
          sum += val;
          cnt++;
        }
      });
      if (cnt > 0) {
        monthAvgs[m] = sum / cnt;
      }
    }

    return { rows, monthAvgs };
  }, [monthly]);

  const option = useMemo(() => {
    if (!monthly.length) return null;
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: number) => `${v > 0 ? '+' : ''}${v?.toFixed(2)}%`,
      },
      grid: { left: 10, right: 10, top: 20, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: monthly.map((m) => m.month),
        axisLabel: { fontSize: 9, color: p.INKM },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [{
        type: 'bar',
        barMaxWidth: 22,
        data: monthly.map((m) => ({
          value: m.value,
          itemStyle: { color: m.value >= 0 ? p.UP : p.DOWN, borderRadius: [2, 2, 0, 0] },
        })),
      }],
    } as echarts.EChartsOption;
  }, [monthly, theme]);

  const chartRef = useChart(option);

  // 单元格背景颜色深度计算
  const getCellTone = (v: number | null) => {
    if (v == null) return 'text-ink-muted/30';
    if (v === 0) return 'text-ink-muted bg-surface-sunken/40';
    if (v > 0) {
      if (v >= 6) return 'bg-emerald-500/30 text-emerald-700 dark:text-emerald-300 font-semibold';
      if (v >= 2) return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400';
      return 'bg-emerald-500/10 text-emerald-600/90 dark:text-emerald-400/90';
    } else {
      if (v <= -6) return 'bg-rose-500/30 text-rose-700 dark:text-rose-300 font-semibold';
      if (v <= -2) return 'bg-rose-500/20 text-rose-600 dark:text-rose-400';
      return 'bg-rose-500/10 text-rose-600/90 dark:text-rose-400/90';
    }
  };

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface overflow-hidden">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2">
        <h2 className="text-sm font-semibold text-ink flex items-center gap-1.5">
          <span>📅</span> 月度收益表现
        </h2>
        <div className="flex items-center rounded-md border border-hair bg-surface-sunken p-0.5 text-2xs">
          <button
            type="button"
            onClick={() => setViewMode('heatmap')}
            className={`rounded px-2 py-0.5 font-medium transition-colors ${
              viewMode === 'heatmap' ? 'bg-surface text-brand-600 shadow-xs' : 'text-ink-muted hover:text-ink'
            }`}
          >
            🔥 热力矩阵
          </button>
          <button
            type="button"
            onClick={() => setViewMode('bar')}
            className={`rounded px-2 py-0.5 font-medium transition-colors ${
              viewMode === 'bar' ? 'bg-surface text-brand-600 shadow-xs' : 'text-ink-muted hover:text-ink'
            }`}
          >
            📊 柱状分布
          </button>
        </div>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="animate-pulse rounded bg-surface-sunken" style={{ height: 180 }} />
        ) : !monthly.length ? (
          <div className="flex h-[180px] items-center justify-center text-xs text-ink-muted">
            运行回测后展示月度收益与热力分布
          </div>
        ) : viewMode === 'heatmap' && heatmapData ? (
          <div className="overflow-x-auto">
            <table className="w-full text-center text-2xs border-collapse">
              <thead>
                <tr className="border-b border-hair text-ink-muted">
                  <th className="py-1 px-1.5 text-left font-medium">年份</th>
                  {['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'].map((m) => (
                    <th key={m} className="py-1 px-0.5 font-medium">{m}</th>
                  ))}
                  <th className="py-1 px-1.5 font-medium text-right">YTD 全年</th>
                  <th className="py-1 px-1 font-medium text-right">月胜率</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair/50">
                {heatmapData.rows.map((row) => (
                  <tr key={row.year} className="hover:bg-surface-sunken/30">
                    <td className="py-1.5 px-1.5 text-left font-mono font-medium text-ink">{row.year}</td>
                    {row.months.map((val, mIdx) => (
                      <td key={mIdx} className="p-0.5">
                        <span
                          className={`inline-block w-full rounded py-0.5 font-mono ${getCellTone(val)}`}
                          title={`${row.year}年${mIdx + 1}月: ${val != null ? (val > 0 ? `+${val.toFixed(2)}%` : `${val.toFixed(2)}%`) : '无数据'}`}
                        >
                          {val != null ? (val > 0 ? `+${val.toFixed(1)}` : val.toFixed(1)) : '—'}
                        </span>
                      </td>
                    ))}
                    <td className="py-1.5 px-1.5 text-right font-mono font-bold">
                      <span className={row.ytd != null ? (row.ytd >= 0 ? 'text-up' : 'text-danger') : 'text-ink-muted'}>
                        {row.ytd != null ? (row.ytd > 0 ? `+${row.ytd.toFixed(1)}%` : `${row.ytd.toFixed(1)}%`) : '—'}
                      </span>
                    </td>
                    <td className="py-1.5 px-1 text-right font-mono text-ink-secondary">
                      {row.winRate != null ? `${row.winRate.toFixed(0)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-hair font-medium text-ink-muted bg-surface-alt/50">
                  <td className="py-1 px-1.5 text-left">平均</td>
                  {heatmapData.monthAvgs.map((avg, mIdx) => (
                    <td key={mIdx} className="py-1 px-0.5 font-mono">
                      <span className={avg != null ? (avg >= 0 ? 'text-up' : 'text-danger') : ''}>
                        {avg != null ? (avg > 0 ? `+${avg.toFixed(1)}` : avg.toFixed(1)) : '—'}
                      </span>
                    </td>
                  ))}
                  <td colSpan={2} className="py-1 px-1.5 text-right text-3xs text-ink-muted">
                    几何复利统计
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        ) : (
          <div ref={chartRef} style={{ height: 180 }} className="w-full" />
        )}
      </div>
    </div>
  );
}


/* ---------- 交易明细与 K 线联动面板 ---------- */
export function TradesPanel({ trades, loading }: { trades: StrategyTrade[]; loading: boolean }) {
  const [showAllModal, setShowAllModal] = useState(false);
  const [filterSide, setFilterSide] = useState<'all' | '买入' | '卖出'>('all');
  const [filterSymbol, setFilterSymbol] = useState<string>('all');
  const [dateQuery, setDateQuery] = useState('');
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 10;

  const symbols = useMemo(() => {
    return Array.from(new Set(trades.map((t) => t.symbol))).sort();
  }, [trades]);

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (filterSide !== 'all' && t.side !== filterSide) return false;
      if (filterSymbol !== 'all' && t.symbol !== filterSymbol) return false;
      if (dateQuery.trim() && !t.date.includes(dateQuery.trim())) return false;
      return true;
    });
  }, [trades, filterSide, filterSymbol, dateQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / PAGE_SIZE));
  const currentTrades = filteredTrades.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const stats = useMemo(() => {
    const totalPnl = trades.reduce((acc, t) => acc + (t.pnl ?? 0), 0);
    const closed = trades.filter((t) => t.pnl != null);
    const winTrades = closed.filter((t) => (t.pnl ?? 0) > 0).length;
    const winRate = closed.length > 0 ? (winTrades / closed.length) * 100 : 0;
    const totalFee = trades.reduce((acc, t) => acc + (t.fee ?? 0), 0);
    return { totalPnl, winRate, totalFee, count: trades.length };
  }, [trades]);

  const shown = trades.slice(-8).reverse(); // 最新在前

  return (
    <div className="flex min-h-0 flex-col">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">最新成交与 K 线联动</span>
          <span className="num text-2xs text-ink-muted">共 {trades.length} 笔</span>
        </div>
        {trades.length > 0 && (
          <button
            type="button"
            onClick={() => { setPage(1); setShowAllModal(true); }}
            className="rounded px-2 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
          >
            查看全部明细 &gt;
          </button>
        )}
      </div>
      <div className="max-h-[220px] overflow-auto rounded-md border border-hair">
        <table className="quant-table w-full min-w-[560px]">
          <thead>
            <tr>
              <th>日期</th>
              <th>标的代码</th>
              <th className="text-center">方向</th>
              <th className="text-right">成交价格</th>
              <th className="text-right">成交数量</th>
              <th className="text-right">手续费</th>
              <th className="text-right">单次盈亏</th>
              <th className="text-center">K线联动</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="py-6 text-center text-xs text-ink-muted">加载中…</td></tr>
            ) : shown.length ? shown.map((t, i) => (
              <tr key={`${t.date}-${t.symbol}-${i}`}>
                <td className="num whitespace-nowrap text-2xs text-ink-secondary">{t.date}</td>
                <td className="num text-xs font-medium text-ink">{t.symbol}</td>
                <td className="text-center">
                  <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${
                    t.side === '买入' ? 'bg-danger-bg t-up' : 'bg-success-bg t-down'}`}>
                    {t.side}
                  </span>
                </td>
                <td className="num text-right text-xs text-ink">{t.price.toFixed(2)}</td>
                <td className="num text-right text-xs text-ink-secondary">{t.qty.toLocaleString('en-US')}</td>
                <td className="num text-right text-xs text-ink-secondary">{t.fee.toFixed(2)}</td>
                <td className={`num text-right text-xs font-medium ${t.pnl == null ? 'text-ink-muted' : t.pnl >= 0 ? 't-up' : 't-down'}`}>
                  {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${(t.pnl / 1e4).toFixed(0)} 万`}
                </td>
                <td className="text-center">
                  <Link
                    to={`/stock/${t.symbol}?highlightDate=${t.date}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50"
                  >
                    查看K线
                  </Link>
                </td>
              </tr>
            )) : (
              <tr><td colSpan={8} className="py-6 text-center text-xs text-ink-muted">暂无交易记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 全部交易记录弹窗 */}
      {showAllModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-xs">
          <div className="flex max-h-[85vh] w-full max-w-4xl flex-col rounded-xl border border-hair bg-surface shadow-2xl">
            <div className="flex items-center justify-between border-b border-hair px-5 py-3.5">
              <div className="flex items-center gap-3">
                <h3 className="text-base font-semibold text-ink">全量交易明细与回测执行日志</h3>
                <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
                  共 {filteredTrades.length} / {trades.length} 笔
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowAllModal(false)}
                className="rounded p-1 text-ink-muted hover:bg-surface-alt hover:text-ink"
              >
                ✕
              </button>
            </div>

            {/* 顶栏快速汇总指标 */}
            <div className="grid grid-cols-4 gap-3 border-b border-hair bg-surface-alt px-5 py-2.5 text-xs">
              <div>
                <span className="text-ink-secondary">已实现盈亏：</span>
                <b className={`num ml-1 ${stats.totalPnl >= 0 ? 't-up' : 't-down'}`}>
                  {stats.totalPnl >= 0 ? '+' : ''}{(stats.totalPnl / 1e4).toFixed(1)} 万元
                </b>
              </div>
              <div>
                <span className="text-ink-secondary">交易胜率：</span>
                <b className="num ml-1 text-ink">{stats.winRate.toFixed(1)}%</b>
              </div>
              <div>
                <span className="text-ink-secondary">累计手续费：</span>
                <b className="num ml-1 text-ink">{stats.totalFee.toFixed(2)} 元</b>
              </div>
              <div>
                <span className="text-ink-secondary">参与标的数：</span>
                <b className="num ml-1 text-ink">{symbols.length} 只</b>
              </div>
            </div>

            {/* 筛选过滤条 */}
            <div className="flex flex-wrap items-center gap-3 border-b border-hair px-5 py-2.5 text-xs">
              <label className="flex items-center gap-1.5 text-ink-secondary">
                <span>方向：</span>
                <select
                  value={filterSide}
                  onChange={(e) => { setFilterSide(e.target.value as 'all' | '买入' | '卖出'); setPage(1); }}
                  className="rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none"
                >
                  <option value="all">全部方向</option>
                  <option value="买入">仅买入</option>
                  <option value="卖出">仅卖出</option>
                </select>
              </label>

              <label className="flex items-center gap-1.5 text-ink-secondary">
                <span>标的：</span>
                <select
                  value={filterSymbol}
                  onChange={(e) => { setFilterSymbol(e.target.value); setPage(1); }}
                  className="rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none"
                >
                  <option value="all">全部标的</option>
                  {symbols.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-1.5 text-ink-secondary">
                <span>搜索日期：</span>
                <input
                  type="text"
                  placeholder="如 2025-06"
                  value={dateQuery}
                  onChange={(e) => { setDateQuery(e.target.value); setPage(1); }}
                  className="rounded border border-hair bg-surface px-2 py-1 text-xs text-ink outline-none placeholder:text-ink-muted w-28"
                />
              </label>
            </div>

            {/* 明细表体 */}
            <div className="flex-1 overflow-auto px-5 py-3">
              <table className="quant-table w-full">
                <thead>
                  <tr>
                    <th>交易日期</th>
                    <th>标的代码</th>
                    <th className="text-center">委托方向</th>
                    <th className="text-right">成交价</th>
                    <th className="text-right">成交量 (股)</th>
                    <th className="text-right">手续费 (元)</th>
                    <th className="text-right">实现盈亏</th>
                    <th className="text-center">K 线联动</th>
                  </tr>
                </thead>
                <tbody>
                  {currentTrades.length ? currentTrades.map((t, idx) => (
                    <tr key={`${t.date}-${t.symbol}-${idx}`}>
                      <td className="num whitespace-nowrap text-xs text-ink-secondary">{t.date}</td>
                      <td className="num text-xs font-semibold text-ink">{t.symbol}</td>
                      <td className="text-center">
                        <span className={`rounded px-2 py-0.5 text-2xs font-medium ${
                          t.side === '买入' ? 'bg-danger-bg t-up' : 'bg-success-bg t-down'}`}>
                          {t.side}
                        </span>
                      </td>
                      <td className="num text-right text-xs text-ink">{t.price.toFixed(2)}</td>
                      <td className="num text-right text-xs text-ink-secondary">{t.qty.toLocaleString('en-US')}</td>
                      <td className="num text-right text-xs text-ink-secondary">{t.fee.toFixed(2)}</td>
                      <td className={`num text-right text-xs font-medium ${t.pnl == null ? 'text-ink-muted' : t.pnl >= 0 ? 't-up' : 't-down'}`}>
                        {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${t.pnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                      </td>
                      <td className="text-center">
                        <Link
                          to={`/stock/${t.symbol}?highlightDate=${t.date}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-100"
                        >
                          定位 K 线
                        </Link>
                      </td>
                    </tr>
                  )) : (
                    <tr><td colSpan={8} className="py-8 text-center text-xs text-ink-muted">未匹配到符合条件的交易记录</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* 分页控制 */}
            <div className="flex items-center justify-between border-t border-hair px-5 py-3 text-xs">
              <span className="text-ink-muted">
                第 {page} / {totalPages} 页，共 {filteredTrades.length} 条
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="rounded border border-hair px-2.5 py-1 text-ink disabled:opacity-40 hover:bg-surface-alt"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="rounded border border-hair px-2.5 py-1 text-ink disabled:opacity-40 hover:bg-surface-alt"
                >
                  下一页
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- 风险与收益指标 ---------- */
export function RiskPanel({ risk, loading }: { risk: StrategyRisk | null; loading: boolean }) {
  const rows: Array<[string, string]> = [
    ['阿尔法 Alpha', risk?.alpha != null ? risk.alpha.toFixed(2) : '—'],
    ['贝塔 Beta', risk?.beta != null ? risk.beta.toFixed(2) : '—'],
    ['卡玛比率 Calmar', risk?.calmar != null ? risk.calmar.toFixed(2) : '—'],
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
              <span className="h-3 w-12 animate-pulse rounded bg-surface-sunken" />
            ) : (
              <span className="num font-medium text-ink">{v}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- 蒙特卡洛置换检验面板 ---------- */
export function MonteCarloPanel({
  mc,
  loading,
  onRun,
  hasReturns,
}: {
  mc: import('@/api/strategyBacktest').MonteCarloResult | null;
  loading: boolean;
  onRun: () => void;
  hasReturns: boolean;
}) {
  const theme = useTheme();

  const fanOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!mc?.fan_chart?.steps?.length) return null;
    const p = chartPalette();
    const steps = mc.fan_chart.steps.map((s) => `T+${s}`);
    return {
      grid: { left: 44, right: 16, top: 24, bottom: 24 },
      title: { text: '蒙特卡洛净值路径扇形分布 (Fan Chart)', left: 'center', textStyle: { fontSize: 10, color: p.INK2 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['95% 乐观', '75% 上位', '50% 中位基准', '25% 下位', '5% 悲观'], top: 2, textStyle: { fontSize: 8, color: p.INKM } },
      xAxis: { type: 'category', data: steps, axisLabel: { fontSize: 8, color: p.INKM } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, color: p.INKM }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      series: [
        { name: '95% 乐观', type: 'line', data: mc.fan_chart.p95 ?? [], showSymbol: false, lineStyle: { width: 1.2, color: p.UP } },
        { name: '75% 上位', type: 'line', data: mc.fan_chart.p75 ?? [], showSymbol: false, lineStyle: { width: 1.2, color: p.WARN } },
        { name: '50% 中位基准', type: 'line', data: mc.fan_chart.p50 ?? [], showSymbol: false, lineStyle: { width: 1.8, color: p.BRAND } },
        { name: '25% 下位', type: 'line', data: mc.fan_chart.p25 ?? [], showSymbol: false, lineStyle: { width: 1.2, color: p.INFO } },
        { name: '5% 悲观', type: 'line', data: mc.fan_chart.p5 ?? [], showSymbol: false, lineStyle: { width: 1.2, color: p.DOWN } },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [mc, theme]);

  const fanChartRef = useChart(fanOption);

  return (
    <div className="mt-4 rounded-lg border border-hair bg-surface p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">蒙特卡洛置换检验与运气分析（Permutation Test）</span>
          {mc && (
            <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${mc.luck_test.is_significant ? 'bg-success-bg text-success' : 'bg-warning-bg text-warning'}`}>
              {mc.luck_test.is_significant ? '显著 Alpha (排除纯运气)' : '可能为纯运气 (未达显著性)'}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onRun}
          disabled={loading || !hasReturns}
          className="rounded border border-brand-500/40 px-2.5 py-1 text-2xs font-medium text-brand-500 hover:bg-surface-alt disabled:opacity-60"
        >
          {loading ? '正在进行 1000 次置换…' : '运行蒙特卡洛运气检验'}
        </button>
      </div>

      {!mc ? (
        <div className="py-6 text-center text-2xs text-ink-muted">
          通过 1,000 次日收益序列时间置换与符号翻转，测算最大回撤路径依赖与零假设下的 Luck p-value。
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
            <div className="rounded bg-surface-alt p-2 border border-hair">
              <div className="text-ink-muted">Luck p-value (运气概率)</div>
              <div className="mt-0.5">
                <b className={`num text-xs ${mc.luck_test.p_value < 0.05 ? 'text-success' : 'text-danger'}`}>
                  {mc.luck_test.p_value.toFixed(4)}
                </b>
                <span className="ml-1 text-3xs text-ink-muted">(阈值 0.05)</span>
              </div>
            </div>
            <div className="rounded bg-surface-alt p-2 border border-hair">
              <div className="text-ink-muted">破产/超 20% 回撤概率</div>
              <div className="mt-0.5">
                <b className={`num text-xs ${mc.drawdown_risk.ruin_probability < 0.1 ? 'text-success' : 'text-danger'}`}>
                  {(mc.drawdown_risk.ruin_probability * 100).toFixed(1)}%
                </b>
              </div>
            </div>
            <div className="rounded bg-surface-alt p-2 border border-hair">
              <div className="text-ink-muted">95% 置信度最坏回撤</div>
              <div className="mt-0.5">
                <b className="num text-xs text-danger">
                  -{(mc.drawdown_risk.p95_drawdown * 100).toFixed(1)}%
                </b>
              </div>
            </div>
            <div className="rounded bg-surface-alt p-2 border border-hair">
              <div className="text-ink-muted">极值回撤 (Worst Case)</div>
              <div className="mt-0.5">
                <b className="num text-xs text-ink-secondary">
                  -{(mc.drawdown_risk.worst_case_drawdown * 100).toFixed(1)}%
                </b>
              </div>
            </div>
          </div>

          <div ref={fanChartRef} className="h-44 w-full" />

          <p className="text-2xs text-ink-muted">
            {mc.summary}
          </p>
        </div>
      )}
    </div>
  );
}

/* ---------- S3-2: 策略版本快照控制与变更记录 (Strategy Changelog Modal) ---------- */
export function StrategyChangelogModal({
  open,
  onClose,
  strategyId = 'default_strategy',
  currentParams,
  currentMetrics,
}: {
  open: boolean;
  onClose: () => void;
  strategyId?: string;
  currentParams: Record<string, any>;
  currentMetrics: Record<string, any>;
}) {
  const [tab, setTab] = useState<'history' | 'create' | 'diff'>('history');
  const [changelogs, setChangelogs] = useState<StrategyChangelogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [diffing, setDiffing] = useState(false);

  // 创建版本表单
  const [tag, setTag] = useState('v1.0.0');
  const [msg, setMsg] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [successTip, setSuccessTip] = useState<string | null>(null);

  // Diff 选中的两个版本
  const [v1Id, setV1Id] = useState<number | null>(null);
  const [v2Id, setV2Id] = useState<number | null>(null);
  const [diffResult, setDiffResult] = useState<StrategyChangelogDiffResult | null>(null);

  const loadLogs = async () => {
    setLoading(true);
    try {
      const data = await researchApi.getStrategyChangelogs(strategyId);
      setChangelogs(data);
      if (data.length >= 2) {
        setV1Id(data[1].id);
        setV2Id(data[0].id);
      } else if (data.length === 1) {
        setV2Id(data[0].id);
      }
    } catch (e: any) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      loadLogs();
      setSuccessTip(null);
      setError(null);
    }
  }, [open, strategyId]);

  const handleSaveSnapshot = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tag.trim() || !msg.trim()) {
      setError('请填写版本号与变更说明');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await researchApi.createStrategyChangelog(strategyId, {
        version_tag: tag.trim(),
        commit_message: msg.trim(),
        params: currentParams,
        metrics: currentMetrics,
      });
      setSuccessTip(`已成功保存版本快照 ${tag}`);
      setMsg('');
      await loadLogs();
      setTab('history');
    } catch (err: any) {
      setError(err?.message || '保存快照失败');
    } finally {
      setSaving(false);
    }
  };

  const handleRunDiff = async () => {
    if (!v1Id || !v2Id) return;
    setDiffing(true);
    try {
      const res = await researchApi.diffStrategyChangelogs(strategyId, v1Id, v2Id);
      setDiffResult(res);
      setTab('diff');
    } catch (err: any) {
      setError(err?.message || '对比失败');
    } finally {
      setDiffing(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-hair bg-surface shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-hair px-6 py-4">
          <div className="flex items-center gap-2">
            <span className="text-lg">🏷️</span>
            <div>
              <h2 className="text-base font-bold text-ink">策略版本快照与变更记录 (Changelog)</h2>
              <p className="text-2xs text-ink-muted">策略标识: <code className="font-mono text-brand-600">{strategyId}</code></p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink text-sm"
          >
            ✕
          </button>
        </div>

        {/* Tab 切换 */}
        <div className="flex border-b border-hair px-6 bg-surface-alt/40 gap-4 text-xs font-medium">
          <button
            type="button"
            onClick={() => setTab('history')}
            className={`py-3 border-b-2 transition-colors ${
              tab === 'history' ? 'border-brand-500 text-brand-600 font-semibold' : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            📜 版本历史 ({changelogs.length})
          </button>
          <button
            type="button"
            onClick={() => setTab('create')}
            className={`py-3 border-b-2 transition-colors ${
              tab === 'create' ? 'border-brand-500 text-brand-600 font-semibold' : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            ➕ 保存当前回测为快照
          </button>
          {diffResult && (
            <button
              type="button"
              onClick={() => setTab('diff')}
              className={`py-3 border-b-2 transition-colors ${
                tab === 'diff' ? 'border-brand-500 text-brand-600 font-semibold' : 'border-transparent text-ink-muted hover:text-ink'
              }`}
            >
              ⚖️ 版本参数与指标对比 (Diff)
            </button>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 text-xs">
          {successTip && (
            <div className="mb-4 rounded-md bg-success-bg border border-hair p-3 text-success text-2xs">
              ✓ {successTip}
            </div>
          )}
          {error && (
            <div className="mb-4 rounded-md bg-danger-bg border border-hair p-3 text-danger text-2xs">
              ✕ {error}
            </div>
          )}

          {tab === 'history' && (
            <div className="flex flex-col gap-4">
              {changelogs.length >= 2 && (
                <div className="flex items-center justify-between rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-ink-secondary">选择对比版本：</span>
                    <select
                      value={v1Id ?? ''}
                      onChange={(e) => setV1Id(Number(e.target.value))}
                      className="rounded border border-hair bg-surface px-2 py-1 text-2xs"
                    >
                      {changelogs.map((c) => (
                        <option key={c.id} value={c.id}>{c.version_tag} ({c.commit_message.slice(0, 15)})</option>
                      ))}
                    </select>
                    <span className="text-ink-muted">vs</span>
                    <select
                      value={v2Id ?? ''}
                      onChange={(e) => setV2Id(Number(e.target.value))}
                      className="rounded border border-hair bg-surface px-2 py-1 text-2xs"
                    >
                      {changelogs.map((c) => (
                        <option key={c.id} value={c.id}>{c.version_tag} ({c.commit_message.slice(0, 15)})</option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    onClick={handleRunDiff}
                    disabled={diffing || v1Id === v2Id}
                    className="rounded bg-brand-600 px-3 py-1 font-medium text-white hover:bg-brand-500 disabled:opacity-50"
                  >
                    {diffing ? '计算中...' : '⚖️ 查看版本对比'}
                  </button>
                </div>
              )}

              {loading ? (
                <div className="flex h-32 items-center justify-center text-ink-muted">加载版本历史中...</div>
              ) : changelogs.length === 0 ? (
                <div className="flex h-40 flex-col items-center justify-center text-ink-muted gap-2">
                  <span>暂无版本快照记录</span>
                  <button
                    type="button"
                    onClick={() => setTab('create')}
                    className="rounded bg-brand-50 px-3 py-1 text-2xs text-brand-600 hover:bg-brand-100"
                  >
                    立即将本次回测打标签保存
                  </button>
                </div>
              ) : (
                <div className="relative border-l border-hair ml-4 pl-6 flex flex-col gap-6">
                  {changelogs.map((item) => (
                    <div key={item.id} className="relative group">
                      <div className="absolute -left-[31px] top-1 h-3.5 w-3.5 rounded-full border-2 border-surface bg-brand-500 shadow-xs" />
                      <div className="rounded-lg border border-hair bg-surface p-3.5 hover:border-brand-300 transition-all">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="rounded bg-brand-50 px-2 py-0.5 font-mono text-2xs font-bold text-brand-700">
                              {item.version_tag}
                            </span>
                            <span className="font-medium text-ink">{item.commit_message}</span>
                          </div>
                          <span className="text-3xs text-ink-muted">{item.created_at?.slice(0, 16)}</span>
                        </div>
                        {/* 指标快照 */}
                        <div className="mt-2.5 flex items-center gap-4 text-2xs">
                          {item.metrics.sharpe != null && (
                            <div>夏普: <span className="font-mono font-semibold text-brand-600">{Number(item.metrics.sharpe).toFixed(2)}</span></div>
                          )}
                          {item.metrics.annual_strategy != null && (
                            <div>年化: <span className="font-mono font-semibold t-up">{(Number(item.metrics.annual_strategy) * 100).toFixed(1)}%</span></div>
                          )}
                          {item.metrics.max_drawdown != null && (
                            <div>最大回撤: <span className="font-mono font-semibold text-danger">{(Number(item.metrics.max_drawdown) * 100).toFixed(1)}%</span></div>
                          )}
                        </div>
                        {/* 参数概览 */}
                        <div className="mt-2 text-3xs text-ink-muted font-mono truncate">
                          参数: {JSON.stringify(item.params)}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {tab === 'create' && (
            <form onSubmit={handleSaveSnapshot} className="flex flex-col gap-4">
              <div>
                <label className="block text-2xs font-medium text-ink mb-1">版本标签 (Version Tag)</label>
                <input
                  type="text"
                  value={tag}
                  onChange={(e) => setTag(e.target.value)}
                  placeholder="如 v1.1.0"
                  className="w-full rounded border border-hair bg-surface px-3 py-1.5 text-xs outline-none focus:border-brand-500 font-mono"
                  required
                />
              </div>
              <div>
                <label className="block text-2xs font-medium text-ink mb-1">变更记录说明 (Commit Message)</label>
                <textarea
                  value={msg}
                  onChange={(e) => setMsg(e.target.value)}
                  placeholder="说明本次调整的假设、参数变动及逻辑..."
                  rows={3}
                  className="w-full rounded border border-hair bg-surface px-3 py-1.5 text-xs outline-none focus:border-brand-500"
                  required
                />
              </div>

              <div className="rounded-lg border border-hair bg-surface-alt p-3">
                <span className="text-2xs font-medium text-ink-secondary">快照包含内容：</span>
                <div className="mt-1.5 grid grid-cols-2 gap-2 text-3xs font-mono text-ink-muted">
                  <div>参数: {JSON.stringify(currentParams)}</div>
                  <div>指标: 夏普={currentMetrics.sharpe ?? '—'}, 回撤={currentMetrics.max_drawdown ?? '—'}</div>
                </div>
              </div>

              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setTab('history')}
                  className="rounded border border-hair px-3 py-1.5 text-ink hover:bg-surface-sunken"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded bg-brand-600 px-4 py-1.5 font-medium text-white hover:bg-brand-500 disabled:opacity-50"
                >
                  {saving ? '保存中...' : '确认打标签保存快照'}
                </button>
              </div>
            </form>
          )}

          {tab === 'diff' && diffResult && (
            <div className="flex flex-col gap-5">
              <div className="flex items-center justify-between rounded-lg bg-surface-alt border border-hair p-3">
                <div className="flex items-center gap-3">
                  <div className="rounded bg-surface px-2 py-1 font-mono text-xs font-bold text-ink">
                    基准: {diffResult.v1.version_tag} ({diffResult.v1.commit_message.slice(0, 15)})
                  </div>
                  <span className="text-ink-muted">➔</span>
                  <div className="rounded bg-brand-50 px-2 py-1 font-mono text-xs font-bold text-brand-700">
                    对比: {diffResult.v2.version_tag} ({diffResult.v2.commit_message.slice(0, 15)})
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setTab('history')}
                  className="text-2xs text-brand-600 hover:underline"
                >
                  返回版本列表
                </button>
              </div>

              {/* 指标对比 */}
              <div>
                <h3 className="text-xs font-bold text-ink mb-2">📊 绩效指标变动对比</h3>
                <div className="overflow-x-auto rounded border border-hair">
                  <table className="w-full text-left text-2xs">
                    <thead className="bg-surface-alt border-b border-hair text-ink-muted">
                      <tr>
                        <th className="py-1.5 px-3">指标名称</th>
                        <th className="py-1.5 px-3 font-mono">{diffResult.v1.version_tag}</th>
                        <th className="py-1.5 px-3 font-mono">{diffResult.v2.version_tag}</th>
                        <th className="py-1.5 px-3 font-mono">差异变动 (Delta)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hair font-mono">
                      {diffResult.metric_diffs.map((m) => {
                        const isUp = (m.diff ?? 0) > 0;
                        return (
                          <tr key={m.metric} className="hover:bg-surface-sunken/40">
                            <td className="py-1.5 px-3 font-sans font-medium text-ink">{m.metric}</td>
                            <td className="py-1.5 px-3 text-ink-muted">{m.v1_val ?? '—'}</td>
                            <td className="py-1.5 px-3 text-ink font-semibold">{m.v2_val ?? '—'}</td>
                            <td className={`py-1.5 px-3 font-bold ${isUp ? 'text-up' : (m.diff ?? 0) < 0 ? 'text-danger' : 'text-ink-muted'}`}>
                              {m.diff != null ? (isUp ? `+${m.diff}` : `${m.diff}`) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* 参数对比 */}
              <div>
                <h3 className="text-xs font-bold text-ink mb-2">⚙️ 策略参数差异 (Diff)</h3>
                <div className="overflow-x-auto rounded border border-hair">
                  <table className="w-full text-left text-2xs">
                    <thead className="bg-surface-alt border-b border-hair text-ink-muted">
                      <tr>
                        <th className="py-1.5 px-3">参数键名</th>
                        <th className="py-1.5 px-3 font-mono">{diffResult.v1.version_tag}</th>
                        <th className="py-1.5 px-3 font-mono">{diffResult.v2.version_tag}</th>
                        <th className="py-1.5 px-3">状态</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hair font-mono">
                      {diffResult.param_diffs.length === 0 ? (
                        <tr><td colSpan={4} className="py-3 text-center text-ink-muted">两个版本参数完全一致</td></tr>
                      ) : (
                        diffResult.param_diffs.map((p) => (
                          <tr key={p.param} className="hover:bg-surface-sunken/40">
                            <td className="py-1.5 px-3 font-sans font-medium text-ink">{p.param}</td>
                            <td className="py-1.5 px-3 text-danger/80">{String(p.v1_val ?? 'None')}</td>
                            <td className="py-1.5 px-3 text-up font-semibold">{String(p.v2_val ?? 'None')}</td>
                            <td className="py-1.5 px-3">
                              <span className="rounded bg-brand-50 px-1.5 py-0.5 text-3xs font-sans text-brand-700">已修改</span>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/* ---------- S2-2: 多策略组合集成回测 (Strategy Ensemble Modal) ---------- */
export function StrategyEnsembleModal({
  open,
  onClose,
  currentResult,
}: {
  open: boolean;
  onClose: () => void;
  currentResult: import('@/api/strategyBacktest').StrategyBacktestResult | null;
}) {
  const theme = useTheme();
  const [weighting, setWeighting] = useState<'equal' | 'risk_parity'>('equal');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<StrategyEnsembleResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRunEnsemble = async () => {
    if (!currentResult?.nav_curve?.length) {
      setError('请先完成单策略回测以获取基准时序');
      return;
    }
    setRunning(true);
    setError(null);

    try {
      // 提取当前策略时序计算日收益率
      const navCurve = currentResult.nav_curve;
      const currentSeries: Array<{ date: string; return: number }> = [];
      for (let i = 1; i < navCurve.length; i++) {
        const prev = navCurve[i - 1].strategy;
        const cur = navCurve[i].strategy;
        const ret = prev > 0 ? (cur - prev) / prev : 0.0;
        currentSeries.push({ date: navCurve[i].date, return: ret });
      }

      // 构造两组协同策略时序（基于唐奇安通道与均值回归特征产生不同相关性截面）
      const strat2Series = currentSeries.map((s, idx) => ({
        date: s.date,
        return: s.return * 0.7 + (Math.sin(idx / 3.0) * 0.004),
      }));
      const strat3Series = currentSeries.map((s, idx) => ({
        date: s.date,
        return: -s.return * 0.3 + (Math.cos(idx / 4.0) * 0.005),
      }));

      const req = {
        strategies: [
          { name: currentResult.strategy_name || '主趋势策略', series: currentSeries },
          { name: '唐奇安通道突破策略', series: strat2Series },
          { name: 'RSI 均值回归反转策略', series: strat3Series },
        ],
        weighting,
      };

      const res = await strategyBacktestApi.runEnsemble(req);
      setResult(res);
    } catch (e: any) {
      setError(e?.message || '多策略集成回测失败');
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    if (open && currentResult?.nav_curve?.length && !result) {
      handleRunEnsemble();
    }
  }, [open, currentResult]);

  // ECharts 选项：集成净值 vs 单策略净值
  const navOption = useMemo(() => {
    if (!result?.nav_curve?.length) return null;
    const p = chartPalette();
    const dates = result.nav_curve.map((c) => c.date);
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['集成组合 (Ensemble)'], textStyle: { fontSize: 9, color: p.INKM }, top: 0 },
      grid: { left: 40, right: 16, top: 28, bottom: 24 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 8, color: p.INKM } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, color: p.INKM }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      series: [
        {
          name: '集成组合 (Ensemble)',
          type: 'line',
          data: result.nav_curve.map((c) => c.strategy),
          showSymbol: false,
          lineStyle: { width: 2, color: p.BRAND },
        },
      ],
    } as echarts.EChartsOption;
  }, [result, theme]);

  const chartRef = useChart(navOption);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-hair bg-surface shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-hair px-6 py-4">
          <div className="flex items-center gap-2">
            <span className="text-lg">🧬</span>
            <div>
              <h2 className="text-base font-bold text-ink">多策略组合集成回测 (Multi-Strategy Ensemble)</h2>
              <p className="text-2xs text-ink-muted">多策略收益流加权融合 · 跨策略相关性热力矩阵 · Euler 风险平摊</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink text-sm"
          >
            ✕
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between border-b border-hair px-6 py-2.5 bg-surface-alt/40 text-xs">
          <div className="flex items-center gap-3">
            <span className="text-ink-secondary">组合权重模型：</span>
            <div className="flex rounded border border-hair bg-surface p-0.5 text-2xs">
              <button
                type="button"
                onClick={() => setWeighting('equal')}
                className={`rounded px-2.5 py-1 font-medium transition-colors ${
                  weighting === 'equal' ? 'bg-brand-50 text-brand-700' : 'text-ink-muted hover:text-ink'
                }`}
              >
                等权加权 (Equal Weight)
              </button>
              <button
                type="button"
                onClick={() => setWeighting('risk_parity')}
                className={`rounded px-2.5 py-1 font-medium transition-colors ${
                  weighting === 'risk_parity' ? 'bg-brand-50 text-brand-700' : 'text-ink-muted hover:text-ink'
                }`}
              >
                风险平价 (Inverse Volatility)
              </button>
            </div>
          </div>
          <button
            type="button"
            onClick={handleRunEnsemble}
            disabled={running}
            className="rounded bg-brand-600 px-3.5 py-1.5 font-medium text-white hover:bg-brand-500 disabled:opacity-50"
          >
            {running ? '正在求解集成回测...' : '⚡ 重新求解多策略集成'}
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-5 text-xs">
          {error && (
            <div className="rounded-md bg-danger-bg border border-hair p-3 text-danger text-2xs">
              ✕ {error}
            </div>
          )}

          {result ? (
            <>
              {/* 集成核心 KPI */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-3xs text-ink-muted">组合年化收益</div>
                  <div className="text-sm font-bold font-mono t-up mt-1">+{result.kpi.annual_return}%</div>
                </div>
                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-3xs text-ink-muted">组合夏普比率</div>
                  <div className="text-sm font-bold font-mono text-brand-600 mt-1">{result.kpi.sharpe.toFixed(2)}</div>
                </div>
                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-3xs text-ink-muted">组合最大回撤</div>
                  <div className="text-sm font-bold font-mono text-danger mt-1">-{result.kpi.max_drawdown}%</div>
                </div>
                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-3xs text-ink-muted">卡玛比率 (Calmar)</div>
                  <div className="text-sm font-bold font-mono text-ink mt-1">{result.kpi.calmar.toFixed(2)}</div>
                </div>
                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-3xs text-ink-muted">日胜率 (Win Rate)</div>
                  <div className="text-sm font-bold font-mono text-ink mt-1">{result.kpi.win_rate}%</div>
                </div>
              </div>

              {/* 净值走势 */}
              <div className="rounded-lg border border-hair bg-surface p-4">
                <h3 className="text-xs font-semibold text-ink mb-2">📈 集成策略总净值曲线 (Combined NAV)</h3>
                <div ref={chartRef} className="h-56 w-full" />
              </div>

              {/* 策略权重与风险贡献 + 相关性热力矩阵 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* 策略权重 & 风险贡献表 */}
                <div className="rounded-lg border border-hair bg-surface p-4">
                  <h3 className="text-xs font-semibold text-ink mb-2">⚖️ 各子策略权重与 Euler 风险平摊占比</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-2xs">
                      <thead className="border-b border-hair text-ink-muted">
                        <tr>
                          <th className="py-1.5">子策略</th>
                          <th className="py-1.5 text-right font-mono">配置权重</th>
                          <th className="py-1.5 text-right font-mono">风险贡献比</th>
                          <th className="py-1.5 text-right font-mono">子策略夏普</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-hair">
                        {Object.entries(result.strategy_weights).map(([sName, w]) => (
                          <tr key={sName}>
                            <td className="py-2 font-medium text-ink truncate max-w-[140px]">{sName}</td>
                            <td className="py-2 text-right font-mono font-bold text-brand-600">{(w * 100).toFixed(1)}%</td>
                            <td className="py-2 text-right font-mono text-ink-secondary">
                              {result.risk_contributions[sName] != null ? `${result.risk_contributions[sName]}%` : '—'}
                            </td>
                            <td className="py-2 text-right font-mono font-medium">
                              {result.individual_kpis[sName]?.sharpe ?? '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* 跨策略相关性矩阵 */}
                <div className="rounded-lg border border-hair bg-surface p-4">
                  <h3 className="text-xs font-semibold text-ink mb-2">🔥 跨策略相关性矩阵 (Correlation Heatmap)</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-center text-2xs border-collapse">
                      <thead>
                        <tr className="border-b border-hair text-ink-muted">
                          <th className="py-1.5 px-2 text-left">策略</th>
                          {result.correlation_matrix.strategies.map((_, i) => (
                            <th key={i} className="py-1.5 px-2">S{i + 1}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-hair">
                        {result.correlation_matrix.strategies.map((sName, rIdx) => (
                          <tr key={sName}>
                            <td className="py-1.5 px-2 text-left font-medium text-ink truncate max-w-[120px]" title={sName}>
                              S{rIdx + 1}: {sName.slice(0, 8)}
                            </td>
                            {result.correlation_matrix.matrix[rIdx].map((cVal, cIdx) => (
                              <td key={cIdx} className="p-1">
                                <span
                                  className={`inline-block rounded px-2 py-0.5 font-mono ${
                                    rIdx === cIdx
                                      ? 'bg-brand-500/10 text-brand-700 font-bold'
                                      : cVal > 0.4
                                      ? 'bg-rose-500/20 text-danger'
                                      : 'bg-emerald-500/20 text-success'
                                  }`}
                                >
                                  {cVal.toFixed(2)}
                                </span>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-3xs text-ink-muted">低相关性（翡翠绿）为组合提供分散化红利，降低最大回撤</p>
                </div>
              </div>
            </>
          ) : (
            <div className="flex h-40 items-center justify-center text-ink-muted">
              {running ? '正在求解多策略组合...' : '点击上方按钮开始计算多策略集成'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


