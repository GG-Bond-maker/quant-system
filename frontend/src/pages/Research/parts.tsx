/** 策略研究工作台：四象限图表子组件（数据全部来自 /api/v1/research 真实计算）。 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';

import type { FactorIcirRow } from '@/api/research';
import { useChart } from '@/utils/useChart';

/* ---------- 因子 IC/IR 面板：矩阵 + 选中因子 IC 时序 ---------- */
export function IcirPanel({ rows, selected, onPick }: {
  rows: FactorIcirRow[];
  selected: string | null;
  onPick: (f: string) => void;
}) {
  const sel = rows.find((r) => r.factor === selected) ?? rows[0];
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!sel?.ic_series?.length) return null;
    return {
      grid: { left: 34, right: 8, top: 8, bottom: 20 },
      xAxis: { type: 'category', data: sel.ic_series.map((_, i) => i + 1),
               axisLabel: { fontSize: 9, interval: 19 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: (v: number) => v.toFixed(2) } },
      tooltip: { trigger: 'axis' },
      series: [{ type: 'line', data: sel.ic_series, showSymbol: false,
                 lineStyle: { width: 1.2, color: '#2563EB' },
                 areaStyle: { opacity: 0.08 } }],
    };
  }, [sel]);
  const chartRef = useChart(option);

  if (!rows.length) return <div className="p-4 text-xs text-ink-muted">加载中…</div>;
  return (
    <div className="space-y-2">
      <table className="w-full text-2xs">
        <thead>
          <tr className="text-ink-secondary">
            <th className="text-left font-medium">因子</th>
            <th className="font-medium">Mean IC</th>
            <th className="font-medium">ICIR</th>
            <th className="font-medium">t 值</th>
            <th className="font-medium">IC&gt;0 占比</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.factor}
                onClick={() => onPick(r.factor)}
                className={`cursor-pointer border-t border-hair hover:bg-slate-50 ${sel?.factor === r.factor ? 'bg-brand-50' : ''}`}>
              <td className="py-1 font-mono">{r.factor}</td>
              <td className={`num text-center ${r.mean_ic != null && r.mean_ic > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {r.mean_ic != null ? r.mean_ic.toFixed(4) : '—'}
              </td>
              <td className="num text-center">{r.icir != null ? r.icir.toFixed(3) : '—'}</td>
              <td className="num text-center">{r.t_stat != null ? r.t_stat.toFixed(2) : '—'}</td>
              <td className="num text-center">{r.positive_ratio != null ? `${(r.positive_ratio * 100).toFixed(0)}%` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div ref={chartRef} className="h-24 w-full" />
      <p className="text-2xs text-ink-muted">
        Rank IC 逐日截面 Spearman · MAD 去极值 · 口径 5D · 展示近 {sel?.ic_series.length ?? 0} 个交易日
      </p>
    </div>
  );
}

/* ---------- 因子相关矩阵热力图（|r|>0.7 红牌） ---------- */
export function CorrHeatmap({ factors, matrix, highPairs }: {
  factors: string[]; matrix: number[][];
  highPairs: Array<{ a: string; b: string; r: number }>;
}) {
  const option = useMemo<echarts.EChartsOption>(() => {
    const data: Array<[number, number, number]> = [];
    for (let i = 0; i < factors.length; i++)
      for (let j = 0; j < factors.length; j++) data.push([j, factors.length - 1 - i, matrix[i]?.[j] ?? 0]);
    return {
      grid: { left: 76, right: 56, top: 4, bottom: 40 },
      xAxis: { type: 'category', data: factors, axisLabel: { fontSize: 8, rotate: 40 } },
      yAxis: { type: 'category', data: [...factors].reverse(), axisLabel: { fontSize: 8 } },
      visualMap: { min: -1, max: 1, calculable: false, orient: 'vertical',
                   right: 0, top: 'center', itemHeight: 70,
                   inRange: { color: ['#059669', '#FFFFFF', '#DC2626'] }, textStyle: { fontSize: 8 } },
      tooltip: { formatter: (p: unknown) => {
        const d = (p as { data: [number, number, number] }).data;
        return `${factors[d[1]]} × ${factors[d[0]]}<br/>ρ = ${d[2].toFixed(3)}`;
      } },
      series: [{
        type: 'heatmap', data,
        label: { show: true, fontSize: 8, formatter: (p: unknown) => {
          const v = (p as { data: [number, number, number] }).data[2];
          return Math.abs(v) >= 0.7 ? v.toFixed(2) : '';
        } },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
      }],
    };
  }, [factors, matrix, highPairs]);
  const ref = useChart(option);
  return (
    <div>
      <div ref={ref} className="h-44 w-full" />
      <p className="text-2xs text-ink-muted">
        近 60 日逐日截面 Spearman 时序均值 ·
        {highPairs.length
          ? <span className="text-red-600"> {highPairs.map((p) => `${p.a}×${p.b}(${p.r.toFixed(2)})`).join('、')} |ρ|&gt;0.7 共线警告</span>
          : ' 无 |ρ|>0.7 高相关因子对'}
      </p>
    </div>
  );
}

/* ---------- 因子分层收益曲线 ---------- */
export function QuantileChart({ curves, labels }: {
  curves: Record<string, Array<{ date: string; nav: number }>>;
  labels: Record<string, string>;
}) {
  const option = useMemo<echarts.EChartsOption>(() => {
    const keys = Object.keys(curves);
    const palette = ['#2563EB', '#0891B2', '#6B7280', '#F59E0B', '#DC2626'];
    return {
      grid: { left: 44, right: 10, top: 24, bottom: 22 },
      legend: { data: keys, top: 0, textStyle: { fontSize: 9 }, itemWidth: 12 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curves[keys[0]]?.map((p) => p.date) ?? [],
               axisLabel: { fontSize: 8, interval: Math.floor((curves[keys[0]]?.length ?? 1) / 5) } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
      series: keys.map((k, i) => ({
        name: labels[k] ?? k, type: 'line' as const, showSymbol: false,
        lineStyle: { width: 1.4, color: palette[i % palette.length] },
        data: curves[k].map((p) => p.nav),
      })),
    };
  }, [curves, labels]);
  const ref = useChart(option);
  return <div ref={ref} className="h-44 w-full" />;
}

/* ---------- Purged CV 甘特图（每 fold：train | purge+embargo | test） ---------- */
export function CvGantt({ folds, totalDays, dateStart, dateEnd }: {
  folds: Array<{ fold: number; train_start: string; train_end: string; test_start: string; test_end: string; train_days: number; test_days: number; gap_days: number; purge_window: number; embargo_window: number }>;
  totalDays: number; dateStart: string; dateEnd: string;
}) {
  const option = useMemo<echarts.EChartsOption>(() => {
    const cats = folds.map((f) => `Fold ${f.fold}`);
    return {
      grid: { left: 52, right: 16, top: 24, bottom: 22 },
      legend: { data: ['Train', 'Purge+Embargo', 'Test'], top: 0, textStyle: { fontSize: 9 } },
      tooltip: { trigger: 'item', formatter: (p: unknown) => {
        const name = (p as { name?: string }).name ?? '';
        const f = folds[Number(String(name).replace('Fold ', '')) - 1];
        if (!f) return String(name);
        if ((p as { seriesName?: string }).seriesName === 'Train')
          return `Train: ${f.train_start} ~ ${f.train_end}（${f.train_days} 交易日）`;
        if ((p as { seriesName?: string }).seriesName === 'Test')
          return `Test: ${f.test_start} ~ ${f.test_end}（${f.test_days} 交易日）`;
        return `隔离带 = Purge ${f.purge_window}d + Embargo ${f.embargo_window}d（防标签泄漏）`;
      } },
      xAxis: { type: 'value', max: totalDays, axisLabel: { fontSize: 8 } },
      yAxis: { type: 'category', data: cats, axisLabel: { fontSize: 9 } },
      series: [
        { name: 'Train', type: 'bar', stack: 'g',
          data: folds.map((f) => f.train_days),
          itemStyle: { color: '#2563EB' }, barWidth: 10 },
        { name: 'Purge+Embargo', type: 'bar', stack: 'g',
          data: folds.map((f) => f.gap_days),
          itemStyle: { color: '#F59E0B' }, barWidth: 10 },
        { name: 'Test', type: 'bar', stack: 'g',
          data: folds.map((f) => f.test_days),
          itemStyle: { color: '#10B981' }, barWidth: 10 },
      ],
    };
  }, [folds, totalDays]);
  const ref = useChart(option);
  return (
    <div>
      <div ref={ref} className="h-40 w-full" />
      <p className="text-2xs text-ink-muted">真实特征日期轴 {dateStart} ~ {dateEnd}（{totalDays} 交易日）· 每折 Train 单调扩张</p>
    </div>
  );
}

/* ---------- 特征重要性 + 边际效应 ---------- */
export function ImportancePanel({ items, curves, selected, onPick }: {
  items: Array<{ feature: string; gain: number }>;
  curves: Array<{ feature: string; points: Array<{ x: number; y: number }> }>;
  selected: string | null;
  onPick: (f: string) => void;
}) {
  const curve = curves.find((c) => c.feature === selected) ?? curves[0];
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!curve) return null;
    return {
      grid: { left: 44, right: 8, top: 20, bottom: 22 },
      title: { text: `${curve.feature} 边际效应`, left: 'center', textStyle: { fontSize: 9 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8 } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, formatter: (v: number) => v.toFixed(3) } },
      series: [{ type: 'line', data: curve.points.map((p) => [p.x, p.y]),
                 showSymbol: false, lineStyle: { width: 1.4, color: '#7C3AED' } }],
    };
  }, [curve]);
  const ref = useChart(option);
  const maxGain = Math.max(...items.map((i) => i.gain), 1);
  return (
    <div className="grid grid-cols-2 gap-2">
      <div className="space-y-1">
        {items.map((it) => (
          <button key={it.feature} onClick={() => onPick(it.feature)}
                  className={`w-full text-left ${selected === it.feature ? 'bg-brand-50 rounded' : ''}`}>
            <div className="flex justify-between text-2xs">
              <span className="font-mono">{it.feature}</span>
              <span className="num text-ink-secondary">{it.gain.toFixed(1)}</span>
            </div>
            <div className="h-1.5 rounded bg-slate-100">
              <div className="h-1.5 rounded bg-brand-500" style={{ width: `${(it.gain / maxGain) * 100}%` }} />
            </div>
          </button>
        ))}
        <p className="text-2xs text-ink-muted pt-1">production 模型真实 gain 重要性 · 点击查看边际效应</p>
      </div>
      <div ref={ref} className="h-40 w-full" />
    </div>
  );
}

/* ---------- 风格暴露：优化前后堆叠对比 ---------- */
export function ExposureChart({ before, after }: {
  before: Record<string, number | null>;
  after: Record<string, number | null>;
}) {
  const option = useMemo<echarts.EChartsOption>(() => {
    const styles = Object.keys(before);
    return {
      grid: { left: 30, right: 8, top: 24, bottom: 22 },
      legend: { data: ['优化前', '优化后'], top: 0, textStyle: { fontSize: 9 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: styles, axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
      series: [
        { name: '优化前', type: 'bar', data: styles.map((s) => before[s]),
          itemStyle: { color: '#93C5FD' }, barWidth: 18 },
        { name: '优化后', type: 'bar', data: styles.map((s) => after[s]),
          itemStyle: { color: '#2563EB' }, barWidth: 18 },
      ],
    };
  }, [before, after]);
  const ref = useChart(option);
  return <div ref={ref} className="h-44 w-full" />;
}

/* ---------- 执行冲击：价格折线 + 成交标记 + 冲击 bps ---------- */
export function ImpactChart({ priceSeries, fills, side }: {
  priceSeries: Array<{ date: string; open: number; close: number; vwap: number }>;
  fills: Array<{ date: string; exec_price: number; impact_bps: number; amount: number }>;
  side: string;
}) {
  const option = useMemo<echarts.EChartsOption>(() => {
    const dates = priceSeries.map((p) => p.date);
    const fillMap = new Map(fills.map((f) => [f.date, f]));
    return {
      grid: { left: 44, right: 44, top: 24, bottom: 40 },
      legend: { data: ['收盘价', '日 VWAP', '成交价', '冲击 bps'], top: 0, textStyle: { fontSize: 9 } },
      tooltip: { trigger: 'axis' },
      dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 0, height: 12 }],
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 8, interval: Math.floor(dates.length / 6) } },
      yAxis: [
        { type: 'value', scale: true, axisLabel: { fontSize: 8 } },
        { type: 'value', name: 'bps', axisLabel: { fontSize: 8 }, splitLine: { show: false } },
      ],
      series: [
        { name: '收盘价', type: 'line', data: priceSeries.map((p) => p.close), showSymbol: false,
          lineStyle: { width: 1, color: '#94A3B8' } },
        { name: '日 VWAP', type: 'line', data: priceSeries.map((p) => p.vwap), showSymbol: false,
          lineStyle: { width: 1, type: 'dashed', color: '#F59E0B' } },
        { name: '成交价', type: 'scatter',
          data: dates.map((d) => fillMap.get(d)?.exec_price ?? null),
          symbolSize: 9,
          itemStyle: { color: side === 'buy' ? '#DC2626' : '#10B981' } },
        { name: '冲击 bps', type: 'bar', yAxisIndex: 1,
          data: dates.map((d) => fillMap.get(d)?.impact_bps ?? null),
          itemStyle: { color: 'rgba(37,99,235,0.55)' }, barWidth: 8 },
      ],
    };
  }, [priceSeries, fills, side]);
  const ref = useChart(option);
  return <div ref={ref} className="h-44 w-full" />;
}

/* ---------- 压力测试表 ---------- */
export function StressTable({ scenarios }: {
  scenarios: Array<{
    window_start: string; window_end: string; market_mdd: number;
    portfolio_mdd: number | null; portfolio_var_95: number | null;
    portfolio_return: number | null; n_days: number;
  }>;
}) {
  if (!scenarios.length) return <div className="p-4 text-xs text-ink-muted">计算中…</div>;
  return (
    <table className="w-full text-2xs">
      <thead>
        <tr className="text-ink-secondary">
          <th className="text-left font-medium">窗口（自动识别）</th>
          <th className="font-medium">天数</th>
          <th className="font-medium">市场 MDD</th>
          <th className="font-medium">组合 MDD</th>
          <th className="font-medium">组合 VaR95</th>
          <th className="font-medium">组合收益</th>
        </tr>
      </thead>
      <tbody>
        {scenarios.map((s) => (
          <tr key={s.window_start} className="border-t border-hair">
            <td className="py-1 font-mono">{s.window_start} ~ {s.window_end}</td>
            <td className="num text-center">{s.n_days}</td>
            <td className="num text-center text-red-600">-{(s.market_mdd * 100).toFixed(1)}%</td>
            <td className="num text-center text-red-600">
              {s.portfolio_mdd != null ? `-${(s.portfolio_mdd * 100).toFixed(1)}%` : '—'}</td>
            <td className="num text-center">
              {s.portfolio_var_95 != null ? `${(s.portfolio_var_95 * 100).toFixed(2)}%` : '—'}</td>
            <td className={`num text-center ${(s.portfolio_return ?? 0) >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
              {s.portfolio_return != null ? `${(s.portfolio_return * 100).toFixed(1)}%` : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
