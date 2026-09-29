/** Top-K 模型信号回测（POST /backtest/run）。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import { backtestApi } from '@/api/backtest';
import { datacenterApi } from '@/api/datacenter';
import { exportApi } from '@/api/export';
import type { BacktestResultData } from '@/types/p1';
import { fmtNum, fmtPct } from '@/utils/format';

const inputCls =
  'rounded-md border border-hair px-2 py-1.5 text-xs outline-none focus:border-brand-300';

/** 摩擦成本分项（后端 broker.friction_costs 的键） */
const FRICTION_LABEL: Record<string, string> = {
  slippage: '滑点', impact: '冲击成本', decay: '换手衰减', delist_loss: '退市强平减记',
};

/** 拒单原因（后端 broker/engine 的 reason 取值） */
const REJECT_LABEL: Record<string, string> = {
  halted: '停牌', limit_up: '涨停不可买', limit_down: '跌停不可卖', t1: 'T+1 限制',
  no_position: '无可卖持仓', no_cash: '现金不足', liquidity_cap: '流动性上限',
  delisted_liquidation: '退市强平', no_bar: '当日无行情',
};

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
  const abortRef = useRef<AbortController | null>(null);

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
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setRunning(true); setErr(null);
    try {
      const r = await backtestApi.runTopK({
        start, end, top_k: topK, init_cash: initCash,
        rebalance_freq: freq, enable_friction: friction,
      }, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setResult(r);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '回测失败');
    } finally {
      if (abortRef.current === ctrl) setRunning(false);
    }
  }, [start, end, topK, initCash, freq, friction]);

  // I-10：卸载（切 Tab）时取消在途的 120s 回测请求
  useEffect(() => () => abortRef.current?.abort(), []);

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
        基于本地 predictions 模型信号的 Top-K 等权调仓回测（
        {result?.universe_scope?.dataset ?? 'universe_daily_bt'} + pred_daily）。
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
            // 审计 P1-26（2026-09-21）：`res.metrics` 来自 `metrics.all_metrics`
            // （`domain/metrics.py:221-231`），三个字段都是**小数**
            // （annual_return/win_rate ∈ [0,1]，max_drawdown 为**正幅值**），
            // 而 `fmtPct` 吃的是**百分数** ⇒ 原样传入会小 100 倍，
            // 且回撤会被加上正号显示成「+0.27%」（涨跌色语义也错）。
            // 与 `Portfolio/index.tsx:145-146,418-419`、`resultParts.tsx:28,275`
            // 的既有约定保持一致。
            { label: '年化收益', value: fmtPct(m.annual_return * 100) },
            { label: '夏普', value: m.sharpe?.toFixed(2) ?? '—' },
            { label: '最大回撤', value: m.max_drawdown != null
                ? `-${(m.max_drawdown * 100).toFixed(2)}%` : '—' },
            { label: '胜率', value: fmtPct(m.win_rate * 100) },
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
      {/* I-11：流动性/摩擦口径与拒单必须披露，不得静默丢弃 */}
      {result && (
        <div className="rounded-lg border border-hair bg-white p-3 text-2xs text-ink-secondary">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">成本与成交口径</span>
            <span className={`rounded px-1.5 py-0.5 ${
              result.enable_friction ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
              {result.enable_friction ? '已计入摩擦成本' : '未计入摩擦成本（收益偏乐观）'}
            </span>
          </div>
          <div className="num flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(result.friction_costs ?? {}).map(([k, v]) => (
              <span key={k}>{FRICTION_LABEL[k] ?? k}：{fmtNum(v)}</span>
            ))}
          </div>
          <p className="mt-1 leading-relaxed text-ink-muted">
            流动性口径：{result.liquidity?.note ?? '后端未披露'}
          </p>
          {Object.keys(result.rejected_trades ?? {}).length > 0 && (
            <p className="mt-1 leading-relaxed text-ink-muted">
              被拒调仓（A 股闸门，未成交）：{Object.entries(result.rejected_trades)
                .map(([k, v]) => `${REJECT_LABEL[k] ?? k} ${v} 次`).join(' · ')}
            </p>
          )}
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
