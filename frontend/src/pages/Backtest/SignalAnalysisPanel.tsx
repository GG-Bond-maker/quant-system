/**
 * 信号分析面板：IC 多周期衰减 + Q1~Q5 分层收益阶梯 + 多空价差净值（POST /backtest/signal-analysis）。
 * 对标 Qlib / Alphalens 因子与模型信号诊断标准。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { ApiError } from '@/api/client';
import { backtestApi, type SignalAnalysisResult } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300 bg-surface';

export default function SignalAnalysisPanel() {
  const [start, setStart] = useState('2024-01-01');
  const [end, setEnd] = useState('2025-12-31');
  const [result, setResult] = useState<SignalAnalysisResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useTheme();
  const task = useAbortableTask();

  useEffect(() => {
    void (async () => {
      try {
        const ds = await datacenterApi.datasets();
        const pred = ds.items.find((d) => d.dataset === 'predictions');
        if (pred?.end) setEnd(pred.end);
        if (pred?.start) setStart(pred.start);
      } catch { /* 默认 */ }
    })();
  }, []);

  const run = useCallback(async () => {
    const ctrl = task.begin();
    setRunning(true); setErr(null);
    try {
      const r = await backtestApi.signalAnalysis({ start, end }, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '分析失败');
    } finally {
      if (task.finish(ctrl)) setRunning(false);
    }
  }, [start, end, task]);

  // 1. IC 衰减与 ICIR 柱折混合图 (IC Decay & ICIR)
  const icDecayOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.ic_summary?.length) return null;
    const p = chartPalette();
    const horizons = result.ic_summary.map((row) => `${row.horizon} 日`);
    const meanIcs = result.ic_summary.map((row) => row.mean_ic ?? 0);
    const icirs = result.ic_summary.map((row) => row.icir ?? 0);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const row = result.ic_summary[idx];
          if (!row) return '';
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>预测周期: ${row.horizon} 个交易日</b><br/>
              Rank IC 均值: <b style="color:${(row.mean_ic ?? 0) >= 0 ? p.UP : p.DOWN}">
                ${row.mean_ic != null ? row.mean_ic.toFixed(4) : '—'}
              </b><br/>
              IC 信息比率 (ICIR): <b style="color:${p.WARN}">${row.icir != null ? row.icir.toFixed(2) : '—'}</b><br/>
              重叠校正 t 统计量: ${row.t_stat != null ? row.t_stat.toFixed(2) : '—'}<br/>
              观测天数/有效独立: ${row.n_days} / ${row.n_independent ?? '—'}
            </div>
          `;
        },
      },
      legend: {
        data: ['Mean IC (Rank IC 均值)', 'ICIR (稳定性比率)'],
        top: 2,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: { left: 44, right: 44, top: 30, bottom: 24 },
      xAxis: {
        type: 'category',
        data: horizons,
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: [
        {
          type: 'value',
          name: 'Mean IC',
          axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => v.toFixed(3) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        {
          type: 'value',
          name: 'ICIR',
          axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => v.toFixed(1) },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: 'Mean IC (Rank IC 均值)',
          type: 'bar',
          barMaxWidth: 28,
          yAxisIndex: 0,
          data: meanIcs.map((val) => ({
            value: val,
            itemStyle: {
              color: val >= 0 ? p.BRAND : p.DOWN,
              borderRadius: [2, 2, 0, 0],
            },
          })),
        },
        {
          name: 'ICIR (稳定性比率)',
          type: 'line',
          yAxisIndex: 1,
          data: icirs,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 2, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
      ],
    };
  }, [result]);

  const icDecayChartRef = useChart(icDecayOption);

  // 2. Q1~Q5 分位数单调性收益阶梯柱状图 (Quantile Return Ladder)
  const quantileLadderOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.quantile_spread?.quantile_mean_ret) return null;
    const p = chartPalette();
    const retMap = result.quantile_spread.quantile_mean_ret;
    const keys = Object.keys(retMap).sort();
    if (!keys.length) return null;

    const values = keys.map((k) => (retMap[k] ?? 0) * 100);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const k = keys[idx];
          const val = values[idx];
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>分位桶: ${k}</b><br/>
              周期平均收益: <b style="color:${val >= 0 ? p.UP : p.DOWN}">${val >= 0 ? '+' : ''}${val.toFixed(3)}%</b>
            </div>
          `;
        },
      },
      grid: { left: 44, right: 16, top: 20, bottom: 24 },
      xAxis: {
        type: 'category',
        data: keys,
        axisLabel: { fontSize: 9, color: p.INK2 },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => `${v.toFixed(1)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 32,
          data: values.map((val) => ({
            value: val,
            itemStyle: {
              color: val >= 0 ? p.UP : p.DOWN,
              borderRadius: [2, 2, 0, 0],
            },
          })),
        },
      ],
    };
  }, [result]);

  const quantileLadderChartRef = useChart(quantileLadderOption);

  // 3. 多空累计净值曲线 (Long-Short NAV)
  const navOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!result?.quantile_spread?.long_short_nav?.length) return null;
    const p = chartPalette();
    const curve = result.quantile_spread.long_short_nav;

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const pt = curve[idx];
          if (!pt) return '';
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>交易日: ${pt.date}</b><br/>
              多空净值: <b style="color:${p.BRAND}">${pt.nav.toFixed(4)}</b> (${((pt.nav - 1) * 100).toFixed(2)}%)
            </div>
          `;
        },
      },
      grid: { left: 48, right: 16, top: 20, bottom: 28 },
      xAxis: {
        type: 'category',
        data: curve.map((p) => p.date),
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          type: 'line',
          name: '多空净值',
          showSymbol: false,
          data: curve.map((p) => p.nav),
          lineStyle: { width: 1.8, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.BRAND, 0.25) },
              { offset: 1, color: withAlpha(p.BRAND, 0.02) },
            ]),
          },
        },
      ],
    };
  }, [result]);

  const navRef = useChart(navOption);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-2xs text-ink-muted">
          对 predictions 信号做 Rank IC 多周期衰减、分层单调性阶梯与多空对冲净值（Qlib 式系统诊断）。
        </p>
        {result && (
          <span className="text-3xs text-ink-muted">
            {result.n_symbols} 只标的 · {result.n_days} 个交易日 · 模型 {result.model_version}
            {result.from_cache ? ' · 缓存' : ''}
          </span>
        )}
      </div>

      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}

      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-hair bg-surface p-3 text-2xs">
        <label>起始<input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>结束<input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <button disabled={running} onClick={() => void run()}
          className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60 transition-colors">
          {running ? '分析中…' : '运行信号分析'}
        </button>
      </div>

      {result && (
        <>
          {/* IC 多周期衰减指标表格与衰减图 */}
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
            <div className="overflow-x-auto rounded-lg border border-hair bg-surface p-3.5 xl:col-span-5 flex flex-col justify-between">
              <div>
                <div className="text-xs font-semibold text-ink mb-2">IC 多周期统计指标</div>
                <table className="quant-table dense w-full text-xs">
                  <thead>
                    <tr>
                      <th>Horizon</th>
                      <th className="text-right">Mean IC</th>
                      <th className="text-right">ICIR</th>
                      <th className="text-right" title="重叠校正后（n_eff = n/h）">t-stat</th>
                      <th className="text-right">N 日 / 有效</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(result.ic_summary ?? []).map((row) => (
                      <tr key={row.horizon}>
                        <td className="num">{row.horizon} 日</td>
                        <td className={`num text-right font-medium ${(row.mean_ic ?? 0) >= 0 ? 't-up' : 't-down'}`}>
                          {row.mean_ic?.toFixed(4) ?? '—'}
                        </td>
                        <td className="num text-right">{row.icir?.toFixed(2) ?? '—'}</td>
                        <td className="num text-right">{row.t_stat?.toFixed(2) ?? '—'}</td>
                        <td className="num text-right text-ink-muted">
                          {row.n_days}{row.n_independent != null ? ` / ${row.n_independent}` : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-3xs text-ink-muted leading-relaxed">
                注：t-stat 采用重叠校正（n_eff = n/h），消除相邻预测重叠导致的自相关膨胀。
              </p>
            </div>

            <div className="rounded-lg border border-hair bg-surface p-3.5 xl:col-span-7">
              <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
                <span className="text-xs font-semibold text-ink">IC 衰减半衰期与 ICIR (IC Decay)</span>
                <span className="text-3xs text-ink-muted">柱状=Mean IC · 折线=ICIR</span>
              </div>
              <div ref={icDecayChartRef} className="h-56 w-full" />
            </div>
          </div>

          {/* Q1~Q5 分位数单调性阶梯与多空净值曲线 */}
          {result.quantile_spread && (
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
              <div className="rounded-lg border border-hair bg-surface p-3.5 xl:col-span-5">
                <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-ink">分层收益单调性阶梯</span>
                    <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                      result.quantile_spread.monotonic ? 'bg-success-bg text-success' : 'bg-warning-bg text-warning'
                    }`}>
                      {result.quantile_spread.monotonic ? '严格单调' : '非单调'}
                    </span>
                  </div>
                  <span className="text-3xs text-ink-muted">
                    {result.quantile_spread.n_quantiles} 分位 · H={result.quantile_spread.horizon}
                  </span>
                </div>
                <div ref={quantileLadderChartRef} className="h-56 w-full" />
              </div>

              <div className="rounded-lg border border-hair bg-surface p-3.5 xl:col-span-7">
                <div className="flex items-center justify-between border-b border-hair pb-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-ink">多空对冲累计净值</span>
                    {result.quantile_spread.ls_annualized != null && (
                      <span className="num text-2xs font-medium t-up">
                        年化价差 {(result.quantile_spread.ls_annualized * 100).toFixed(2)}%
                      </span>
                    )}
                  </div>
                  <span className="text-3xs text-ink-muted">
                    {result.quantile_spread.ls_t_stat != null && `t=${result.quantile_spread.ls_t_stat.toFixed(2)}`}
                  </span>
                </div>
                <div ref={navRef} className="h-56 w-full" />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
