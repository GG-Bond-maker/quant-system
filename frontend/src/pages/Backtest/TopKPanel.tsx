/** Top-K 模型信号回测（POST /backtest/run）。 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { backtestApi } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { exportApi } from '@/api/export';
import type { BacktestResultData } from '@/types/p1';
import { fmtNum, fmtPct } from '@/utils/format';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

export default function TopKPanel() {
  const [start, setStart] = useState('2024-01-01');
  const [end, setEnd] = useState('2025-12-31');
  const [topK, setTopK] = useState(10);
  const [initCash, setInitCash] = useState(1_000_000);
  const [freq, setFreq] = useState<'daily' | 'weekly'>('daily');
  const [friction, setFriction] = useState(false);
  const [result, setResult] = useState<BacktestResultData | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const ds = await datacenterApi.datasets();
        const pred = ds.items.find((d) => d.dataset === 'predictions');
        if (pred?.end) setEnd(pred.end);
        if (pred?.start) setStart(pred.start);
      } catch { /* 默认区间 */ }
    })();
  }, []);

  const run = useCallback(async () => {
    setRunning(true); setErr(null);
    try {
      setResult(await backtestApi.runTopK({
        start, end, top_k: topK, init_cash: initCash,
        rebalance_freq: freq, enable_friction: friction,
      }));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '回测失败');
    } finally { setRunning(false); }
  }, [start, end, topK, initCash, freq, friction]);

  const doExport = async () => {
    try {
      await exportApi.backtest({ start, end, top_k: topK, init_cash: initCash,
        rebalance_freq: freq, enable_friction: friction });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '导出失败');
    }
  };

  const m = result?.metrics;

  return (
    <div className="space-y-3">
      <p className="text-2xs text-ink-muted">
        基于本地 predictions 模型信号的 Top-K 等权调仓回测（universe_daily + pred_daily）。
      </p>
      {err && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{err}</div>}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-hair bg-white p-3 text-2xs">
        <label>起始<input type="date" value={start} onChange={(e) => setStart(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>结束<input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
          className={`${inputCls} ml-1`} /></label>
        <label>Top-K<input type="number" min={1} max={100} value={topK}
          onChange={(e) => setTopK(Number(e.target.value))} className={`${inputCls} ml-1 w-16`} /></label>
        <label>初始资金<input type="number" value={initCash}
          onChange={(e) => setInitCash(Number(e.target.value))} className={`${inputCls} ml-1 w-28`} /></label>
        <select value={freq} onChange={(e) => setFreq(e.target.value as 'daily' | 'weekly')}
          className={inputCls}>
          <option value="daily">日调仓</option>
          <option value="weekly">周调仓</option>
        </select>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={friction} onChange={(e) => setFriction(e.target.checked)} />
          启用摩擦成本
        </label>
        <button disabled={running} onClick={() => void run()}
          className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-60">
          {running ? '回测中…' : '运行 Top-K 回测'}
        </button>
        {result && (
          <button onClick={() => void doExport()}
            className="rounded-md border border-hair px-3 py-1.5 text-xs hover:bg-slate-50">
            导出 Excel
          </button>
        )}
      </div>
      {result && m && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            { label: '年化收益', value: fmtPct(m.annual_return) },
            { label: '夏普', value: m.sharpe?.toFixed(2) ?? '—' },
            { label: '最大回撤', value: fmtPct(m.max_drawdown) },
            { label: '胜率', value: fmtPct(m.win_rate) },
            { label: '交易天数', value: String(m.n_days) },
            { label: '成交笔数', value: String(result.filled_trades) },
            { label: '模型版本', value: result.model_version },
            { label: '来源', value: result.from_cache ? '缓存' : '实时' },
          ].map((k) => (
            <div key={k.label} className="rounded-lg border border-hair bg-white px-3 py-2">
              <div className="text-2xs text-ink-muted">{k.label}</div>
              <div className="num text-sm font-semibold text-ink">{k.value}</div>
            </div>
          ))}
        </div>
      )}
      {result?.nav_tail?.length ? (
        <div className="rounded-lg border border-hair bg-white p-3 text-2xs text-ink-muted">
          最近净值：{result.nav_tail.slice(-5).map((p) =>
            `${p.date} ${fmtNum(p.nav)}`).join(' · ')}
        </div>
      ) : null}
    </div>
  );
}
