/** 信号分析：IC 衰减 + 分层多空（POST /backtest/signal-analysis）。 */
import { useCallback, useEffect, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { ApiError } from '@/api/client';
import { backtestApi, type SignalAnalysisResult } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { useChart } from '@/utils/useChart';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

export default function SignalAnalysisPanel() {
  const [start, setStart] = useState('2024-01-01');
  const [end, setEnd] = useState('2025-12-31');
  const [result, setResult] = useState<SignalAnalysisResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
    setRunning(true); setErr(null);
    try {
      setResult(await backtestApi.signalAnalysis({ start, end }));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '分析失败');
    } finally { setRunning(false); }
  }, [start, end]);

  const navOption = result?.quantile_spread?.long_short_nav?.length
    ? ({
        grid: { left: 48, right: 16, top: 20, bottom: 28 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: result.quantile_spread.long_short_nav.map((p) => p.date),
                  axisLabel: { fontSize: 9 } },
        yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
        series: [{
          type: 'line', name: '多空净值', showSymbol: false,
          data: result.quantile_spread.long_short_nav.map((p) => p.nav),
          lineStyle: { width: 1.5, color: '#2563EB' },
        }],
      } as echarts.EChartsOption)
    : null;
  const navRef = useChart(navOption);

  return (
    <div className="space-y-3">
      <p className="text-2xs text-ink-muted">
        对 predictions 信号做 Rank IC 多周期衰减与分层多空价差（qlib 式诊断）。
      </p>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-hair bg-white p-3 text-2xs">
        <label>起始<input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>结束<input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <button disabled={running} onClick={() => void run()}
          className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
          {running ? '分析中…' : '运行信号分析'}
        </button>
      </div>
      {result && (
        <>
          <div className="text-2xs text-ink-muted">
            {result.n_symbols} 只标的 · {result.n_days} 个交易日 · 模型 {result.model_version}
            {result.from_cache ? ' · 缓存' : ''}
          </div>
          <div className="overflow-x-auto rounded-lg border border-hair bg-white">
            <table className="quant-table dense w-full text-xs">
              <thead><tr>
                <th>Horizon</th><th className="text-right">Mean IC</th>
                <th className="text-right">ICIR</th><th className="text-right">t-stat</th>
                <th className="text-right">N 日</th>
              </tr></thead>
              <tbody>
                {result.ic_summary.map((row) => (
                  <tr key={row.horizon}>
                    <td className="num">{row.horizon} 日</td>
                    <td className="num text-right">{row.mean_ic?.toFixed(4) ?? '—'}</td>
                    <td className="num text-right">{row.icir?.toFixed(2) ?? '—'}</td>
                    <td className="num text-right">{row.t_stat?.toFixed(2) ?? '—'}</td>
                    <td className="num text-right">{row.n_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.quantile_spread && (
            <div className="rounded-lg border border-hair bg-white p-3">
              <div className="mb-2 text-xs font-semibold text-ink">
                分层多空净值（{result.quantile_spread.n_quantiles} 分位 · H={result.quantile_spread.horizon}）
                {result.quantile_spread.spread_annualized != null && (
                  <span className="ml-2 font-normal text-ink-muted">
                    年化价差 {(result.quantile_spread.spread_annualized * 100).toFixed(2)}%
                  </span>
                )}
              </div>
              <div ref={navRef} className="h-56 w-full" />
            </div>
          )}
        </>
      )}
    </div>
  );
}
