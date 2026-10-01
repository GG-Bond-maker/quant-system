/** 策略研究工作台：四象限图表子组件（数据全部来自 /api/v1/research 真实计算）。 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

import type { FactorIcirRow } from '@/api/research';
import { useChart } from '@/utils/useChart';

/*
 * 类别色（不跟随主题）：本文件多数配色用于区分「是哪条线 / 哪个 fold 阶段」，
 * 不表达涨跌语义，故保持固定色相。取值统一走 `CATEGORY_COLORS`，避免散落。
 */

/* 甘特三阶段类别色：取自 CATEGORY_COLORS.SEQ 前三项（Train/Purge/Test 仅用于区分阶段） */
const GANTT_TRAIN = CATEGORY_COLORS.SEQ[0];
const GANTT_PURGE = CATEGORY_COLORS.SEQ[2];
const GANTT_TEST = CATEGORY_COLORS.SEQ[1];

/* ---------- 因子 IC/IR 面板：矩阵 + 选中因子 IC 时序 ---------- */
export function IcirPanel({ rows, selected, onPick }: {
  rows: FactorIcirRow[];
  selected: string | null;
  onPick: (f: string) => void;
}) {
  const theme = useTheme();
  const sel = rows.find((r) => r.factor === selected) ?? rows[0];
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!sel?.ic_series?.length) return null;
    const p = chartPalette();
    return {
      grid: { left: 34, right: 8, top: 8, bottom: 20 },
      xAxis: { type: 'category', data: sel.ic_series.map((_, i) => i + 1),
               axisLabel: { fontSize: 9, interval: 19, color: p.INKM } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
               splitLine: { lineStyle: { color: p.SUNKEN } } },
      tooltip: { trigger: 'axis' },
      series: [{ type: 'line', data: sel.ic_series, showSymbol: false,
                 lineStyle: { width: 1.2, color: p.BRAND },
                 itemStyle: { color: p.BRAND },
                 areaStyle: { color: withAlpha(p.BRAND, 0.08) } }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [sel, theme]);
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
                className={`cursor-pointer border-t border-hair hover:bg-surface-alt ${sel?.factor === r.factor ? 'bg-brand-50' : ''}`}>
              <td className="py-1 font-mono">{r.factor}</td>
              {/* ⚠️ mean_ic 可为 null（下方已显 '—'）：不得写 `mean_ic != null && mean_ic > 0 ? 红 : 绿`
                  —— null 会落进 text-success（把"无数据"说成"负 IC"）。null ⇒ 中性色。 */}
              <td className={`num text-center ${r.mean_ic == null ? 'text-ink-muted' : r.mean_ic > 0 ? 'text-danger' : 'text-success'}`}>
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
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const data: Array<[number, number, number]> = [];
    for (let i = 0; i < factors.length; i++)
      for (let j = 0; j < factors.length; j++) data.push([j, factors.length - 1 - i, matrix[i]?.[j] ?? 0]);
    return {
      grid: { left: 76, right: 56, top: 4, bottom: 40 },
      xAxis: { type: 'category', data: factors, axisLabel: { fontSize: 8, rotate: 40, color: p.INKM } },
      yAxis: { type: 'category', data: [...factors].reverse(), axisLabel: { fontSize: 8, color: p.INKM } },
      // 相关性为「正相关偏红 / 负相关偏绿」的发散语义色（与全站红涨绿跌方向一致），中点为卡片底色
      visualMap: { min: -1, max: 1, calculable: false, orient: 'vertical',
                   right: 0, top: 'center', itemHeight: 70,
                   inRange: { color: [p.DOWN, p.CARD, p.UP] }, textStyle: { fontSize: 8, color: p.INKM } },
      tooltip: { formatter: (p2: unknown) => {
        const d = (p2 as { data: [number, number, number] }).data;
        return `${factors[d[1]]} × ${factors[d[0]]}<br/>ρ = ${d[2].toFixed(3)}`;
      } },
      series: [{
        type: 'heatmap', data,
        label: { show: true, fontSize: 8, formatter: (p2: unknown) => {
          const v = (p2 as { data: [number, number, number] }).data[2];
          return Math.abs(v) >= 0.7 ? v.toFixed(2) : '';
        } },
        // 格子描边用卡片底色，暗色下才会随主题分离网格
        itemStyle: { borderColor: p.CARD, borderWidth: 1 },
      }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [factors, matrix, highPairs, theme]);
  const ref = useChart(option);
  return (
    <div>
      <div ref={ref} className="h-44 w-full" />
      <p className="text-2xs text-ink-muted">
        近 60 日逐日截面 Spearman 时序均值 ·
        {highPairs.length
          ? <span className="text-danger"> {highPairs.map((p) => `${p.a}×${p.b}(${p.r.toFixed(2)})`).join('、')} |ρ|&gt;0.7 共线警告</span>
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
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const keys = Object.keys(curves);
    // 类别色：分层曲线仅用于区分「是哪一档」，无涨跌语义，故用 CATEGORY_COLORS.SEQ 固定色相
    const palette = CATEGORY_COLORS.SEQ;
    return {
      grid: { left: 44, right: 10, top: 24, bottom: 22 },
      legend: { data: keys, top: 0, textStyle: { fontSize: 9, color: p.INK2 }, itemWidth: 12 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: curves[keys[0]]?.map((d) => d.date) ?? [],
               axisLabel: { fontSize: 8, color: p.INKM, interval: Math.floor((curves[keys[0]]?.length ?? 1) / 5) } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9, color: p.INKM },
               splitLine: { lineStyle: { color: p.SUNKEN } } },
      series: keys.map((k, i) => ({
        name: labels[k] ?? k, type: 'line' as const, showSymbol: false,
        lineStyle: { width: 1.4, color: palette[i % palette.length] },
        data: curves[k].map((p) => p.nav),
      })),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [curves, labels, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-44 w-full" />;
}

/* ---------- Purged CV 甘特图（每 fold：train | purge+embargo | test） ---------- */
export function CvGantt({ folds, totalDays, dateStart, dateEnd }: {
  folds: Array<{ fold: number; train_start: string; train_end: string; test_start: string; test_end: string; train_days: number; test_days: number; gap_days: number; purge_window: number; embargo_window: number }>;
  totalDays: number; dateStart: string; dateEnd: string;
}) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const cats = folds.map((f) => `Fold ${f.fold}`);
    return {
      grid: { left: 52, right: 16, top: 24, bottom: 22 },
      legend: { data: ['Train', 'Purge+Embargo', 'Test'], top: 0, textStyle: { fontSize: 9, color: p.INK2 } },
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
      xAxis: { type: 'value', max: totalDays, axisLabel: { fontSize: 8, color: p.INKM } },
      yAxis: { type: 'category', data: cats, axisLabel: { fontSize: 9, color: p.INK2 } },
      // 三阶段为类别色（区分 Train/Purge/Test 阶段，非涨跌），固定色相
      series: [
        { name: 'Train', type: 'bar', stack: 'g',
          data: folds.map((f) => f.train_days),
          itemStyle: { color: GANTT_TRAIN }, barWidth: 10 },
        { name: 'Purge+Embargo', type: 'bar', stack: 'g',
          data: folds.map((f) => f.gap_days),
          itemStyle: { color: GANTT_PURGE }, barWidth: 10 },
        { name: 'Test', type: 'bar', stack: 'g',
          data: folds.map((f) => f.test_days),
          itemStyle: { color: GANTT_TEST }, barWidth: 10 },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [folds, totalDays, theme]);
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
  const theme = useTheme();
  const curve = curves.find((c) => c.feature === selected) ?? curves[0];
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!curve) return null;
    const p = chartPalette();
    return {
      grid: { left: 44, right: 8, top: 20, bottom: 22 },
      title: { text: `${curve.feature} 边际效应`, left: 'center', textStyle: { fontSize: 9, color: p.INK2 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, color: p.INKM } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => v.toFixed(3) },
               splitLine: { lineStyle: { color: p.SUNKEN } } },
      // 单条边际效应曲线，用类别色 C3 以示与主色曲线区分
      series: [{ type: 'line', data: curve.points.map((pt) => [pt.x, pt.y]),
                 showSymbol: false, lineStyle: { width: 1.4, color: CATEGORY_COLORS.C3 },
                 itemStyle: { color: CATEGORY_COLORS.C3 } }],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [curve, theme]);
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
            <div className="h-1.5 rounded bg-surface-sunken">
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
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const styles = Object.keys(before);
    return {
      grid: { left: 30, right: 8, top: 24, bottom: 22 },
      legend: { data: ['优化前', '优化后'], top: 0, textStyle: { fontSize: 9, color: p.INK2 } },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: styles, axisLabel: { fontSize: 9, color: p.INKM } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: p.INKM }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      // 「优化前/后」为对比类别色（浅色 vs 深色同色相），非涨跌
      series: [
        { name: '优化前', type: 'bar', data: styles.map((s) => before[s]),
          itemStyle: { color: withAlpha(p.BRAND, 0.45) }, barWidth: 18 },
        { name: '优化后', type: 'bar', data: styles.map((s) => after[s]),
          itemStyle: { color: p.BRAND }, barWidth: 18 },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [before, after, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-44 w-full" />;
}

/* ---------- 执行冲击：价格折线 + 成交标记 + 冲击 bps ---------- */
export function ImpactChart({ priceSeries, fills, side }: {
  priceSeries: Array<{ date: string; open: number; close: number; vwap: number }>;
  fills: Array<{ date: string; exec_price: number; impact_bps: number; amount: number }>;
  side: string;
}) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    const dates = priceSeries.map((d) => d.date);
    const fillMap = new Map(fills.map((f) => [f.date, f]));
    return {
      grid: { left: 44, right: 44, top: 24, bottom: 40 },
      legend: { data: ['收盘价', '日 VWAP', '成交价', '冲击 bps'], top: 0, textStyle: { fontSize: 9, color: p.INK2 } },
      tooltip: { trigger: 'axis' },
      dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 0, height: 12, borderColor: p.HAIR }],
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 8, color: p.INKM, interval: Math.floor(dates.length / 6) } },
      yAxis: [
        { type: 'value', scale: true, axisLabel: { fontSize: 8, color: p.INKM }, splitLine: { lineStyle: { color: p.SUNKEN } } },
        { type: 'value', name: 'bps', nameTextStyle: { color: p.INKM }, axisLabel: { fontSize: 8, color: p.INKM }, splitLine: { show: false } },
      ],
      series: [
        { name: '收盘价', type: 'line', data: priceSeries.map((d) => d.close), showSymbol: false,
          lineStyle: { width: 1, color: CATEGORY_COLORS.C1 } },
        { name: '日 VWAP', type: 'line', data: priceSeries.map((d) => d.vwap), showSymbol: false,
          lineStyle: { width: 1, type: 'dashed', color: CATEGORY_COLORS.C2 } },
        // 成交价按买/卖方向着色（买=涨色红、卖=跌色绿），与全站涨跌语义一致
        { name: '成交价', type: 'scatter',
          data: dates.map((d) => fillMap.get(d)?.exec_price ?? null),
          symbolSize: 9,
          itemStyle: { color: side === 'buy' ? p.UP : p.DOWN } },
        { name: '冲击 bps', type: 'bar', yAxisIndex: 1,
          data: dates.map((d) => fillMap.get(d)?.impact_bps ?? null),
          itemStyle: { color: withAlpha(p.BRAND, 0.55) }, barWidth: 8 },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [priceSeries, fills, side, theme]);
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
            <td className="num text-center text-danger">-{(s.market_mdd * 100).toFixed(1)}%</td>
            <td className="num text-center text-danger">
              {s.portfolio_mdd != null ? `-${(s.portfolio_mdd * 100).toFixed(1)}%` : '—'}</td>
            <td className="num text-center">
              {s.portfolio_var_95 != null ? `${(s.portfolio_var_95 * 100).toFixed(2)}%` : '—'}</td>
            {/* ⚠️ portfolio_return 可为 null（下方已显示 '—'）：不得用 `(x ?? 0) >= 0` 兜底，
                否则 null 会被染红。null ⇒ text-ink-muted 中性色。 */}
            <td className={`num text-center ${s.portfolio_return == null ? 'text-ink-muted' : s.portfolio_return >= 0 ? 'text-danger' : 'text-success'}`}>
              {s.portfolio_return != null ? `${(s.portfolio_return * 100).toFixed(1)}%` : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
