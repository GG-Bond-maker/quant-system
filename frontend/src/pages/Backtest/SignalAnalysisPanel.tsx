/** 信号分析：IC 衰减 + 分层多空（POST /backtest/signal-analysis）。 */
import { useCallback, useEffect, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { ApiError } from '@/api/client';
import { backtestApi, type SignalAnalysisResult } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

export default function SignalAnalysisPanel() {
  const [start, setStart] = useState('2024-01-01');
  const [end, setEnd] = useState('2025-12-31');
  const [result, setResult] = useState<SignalAnalysisResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 主题订阅：下方 navOption 里的 chartPalette() 现读 CSS 变量，
  // 而 ECharts canvas 不参与 CSS 级联——必须让本组件在主题切换时重渲染，
  // 才能把新色值经 useChart 的 setOption 落进画布。
  useTheme();
  // P2-4：信号分析 120s；本面板在 Backtest 页内以 Tab 形式挂载，切 Tab 即卸载，
  // 必须在卸载时中断在途请求（同页 TopKPanel / 趋势跟踪 Tab 已具备该行为）。
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
      if (ctrl.signal.aborted) return; // 中断不是错误，不弹给用户
      setErr(e instanceof ApiError ? e.message : '分析失败');
    } finally {
      if (task.finish(ctrl)) setRunning(false);
    }
  }, [start, end, task]);

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
          lineStyle: { width: 1.5, color: chartPalette().BRAND },
        }],
      } as echarts.EChartsOption)
    : null;
  const navRef = useChart(navOption);

  return (
    <div className="space-y-3">
      <p className="text-2xs text-ink-muted">
        对 predictions 信号做 Rank IC 多周期衰减与分层多空价差（qlib 式诊断）。
      </p>
      {err && <div className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger">{err}</div>}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-hair bg-surface p-3 text-2xs">
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
          <div className="overflow-x-auto rounded-lg border border-hair bg-surface">
            <table className="quant-table dense w-full text-xs">
              <thead><tr>
                <th>Horizon</th><th className="text-right">Mean IC</th>
                <th className="text-right">ICIR</th>
                <th className="text-right" title="重叠校正后（n_eff = n/h）：h 日收益在相邻日期重叠 h−1 天，朴素 sqrt(n) 会高估 ≈√h">
                  t-stat
                </th>
                <th className="text-right" title="左：观测日期数；右：有效独立样本（n/h）">N 日 / 有效</th>
              </tr></thead>
              <tbody>
                {result.ic_summary.map((row) => (
                  <tr key={row.horizon}>
                    <td className="num">{row.horizon} 日</td>
                    <td className="num text-right">{row.mean_ic?.toFixed(4) ?? '—'}</td>
                    <td className="num text-right">{row.icir?.toFixed(2) ?? '—'}</td>
                    <td className="num text-right">{row.t_stat?.toFixed(2) ?? '—'}</td>
                    <td className="num text-right">
                      {row.n_days}{row.n_independent != null ? ` / ${row.n_independent}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.quantile_spread && (
            <div className="rounded-lg border border-hair bg-surface p-3">
              <div className="mb-2 text-xs font-semibold text-ink">
                分层多空净值（{result.quantile_spread.n_quantiles} 分位 · H={result.quantile_spread.horizon}）
                {result.quantile_spread.ls_annualized != null && (
                  <span className="ml-2 font-normal text-ink-muted">
                    年化价差 {(result.quantile_spread.ls_annualized * 100).toFixed(2)}%
                  </span>
                )}
                {/* 审计 P1-25：`monotonic` 与 `ls_t_stat` 后端早已算好、前端此前直接丢弃 */}
                <span className="ml-2 font-normal text-ink-muted">
                  单调 {result.quantile_spread.monotonic ? '是' : '否'}
                  {result.quantile_spread.ls_t_stat != null
                    && ` · t=${result.quantile_spread.ls_t_stat.toFixed(2)}`}
                </span>
              </div>
              <div ref={navRef} className="h-56 w-full" />
            </div>
          )}
        </>
      )}
    </div>
  );
}
