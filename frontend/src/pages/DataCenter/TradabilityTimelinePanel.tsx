import { useCallback, useEffect, useState } from 'react';
import { datacenterApi, type TradabilityTimelineResult } from '@/api/datacenter';
import { ApiError } from '@/api/client';
import { SectionCard } from '@/components/ui';

const QUICK_SYMBOLS = [
  { symbol: '000001.SZ', name: '平安银行' },
  { symbol: '000002.SZ', name: '万科A' },
  { symbol: '600519.SH', name: '贵州茅台' },
  { symbol: '000858.SZ', name: '五粮液' },
];

export default function TradabilityTimelinePanel() {
  const [symbol, setSymbol] = useState('000001.SZ');
  const [inputSym, setInputSym] = useState('000001.SZ');
  const [limit, setLimit] = useState(250);
  const [data, setData] = useState<TradabilityTimelineResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (sym: string, days: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await datacenterApi.tradabilityTimeline(sym, days);
      setData(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '获取标的可交易性时序失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(symbol, limit);
  }, [symbol, limit, load]);

  const handleSearch = () => {
    if (!inputSym.trim()) return;
    setSymbol(inputSym.trim().toUpperCase());
  };

  const getStatusBadge = (status: string, label: string) => {
    switch (status) {
      case 'NORMAL':
        return <span className="rounded bg-success-bg px-1.5 py-0.5 text-3xs font-medium text-success">{label}</span>;
      case 'SUSPENDED':
        return <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-medium text-ink-muted">{label}</span>;
      case 'ST':
        return <span className="rounded bg-warn-bg px-1.5 py-0.5 text-3xs font-medium text-warn">{label}</span>;
      case 'LIMIT_UP':
        return <span className="rounded bg-up/10 px-1.5 py-0.5 text-3xs font-semibold text-up">{label}</span>;
      case 'LIMIT_DOWN':
        return <span className="rounded bg-down/10 px-1.5 py-0.5 text-3xs font-semibold text-down">{label}</span>;
      default:
        return <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-3xs text-ink-muted">{label}</span>;
    }
  };

  return (
    <SectionCard
      title="标的全生命周期可交易性状态机（停牌 · ST · 涨跌停封板时序）"
      action={
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {QUICK_SYMBOLS.map((q) => (
              <button
                key={q.symbol}
                onClick={() => {
                  setInputSym(q.symbol);
                  setSymbol(q.symbol);
                }}
                className={`rounded px-1.5 py-0.5 text-2xs transition-colors ${
                  symbol === q.symbol
                    ? 'bg-brand-500 text-white font-medium'
                    : 'bg-surface-sunken text-ink-secondary hover:bg-hair'
                }`}
              >
                {q.name}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <input
              type="text"
              value={inputSym}
              onChange={(e) => setInputSym(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="代码 (000001.SZ)"
              className="w-24 rounded border border-hair bg-surface px-2 py-0.5 text-xs font-mono outline-none focus:border-brand-300"
            />
            <button
              onClick={handleSearch}
              className="rounded bg-brand-500 px-2 py-0.5 text-xs font-medium text-white hover:bg-brand-600"
            >
              查询
            </button>
          </div>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded border border-hair bg-surface px-2 py-0.5 text-xs outline-none focus:border-brand-300"
          >
            <option value={120}>近 120 日</option>
            <option value={250}>近 250 日 (1年)</option>
            <option value={500}>近 500 日 (2年)</option>
          </select>
        </div>
      }
    >
      {error && (
        <div className="rounded-md border border-danger/30 bg-danger-bg p-2 text-2xs text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-10 text-center text-xs text-ink-muted">正在检索标的全生命周期状态机时序…</div>
      ) : data ? (
        <div className="space-y-3">
          {/* 指标卡片 */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">标的名称</div>
              <div className="mt-1 text-sm font-semibold text-ink">
                {data.name} <span className="font-mono text-2xs text-ink-muted">({data.symbol})</span>
              </div>
              <div className="text-3xs text-ink-muted">样本总交易日: {data.total_days}</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">可交易性比例</div>
              <div className="num mt-1 text-base font-semibold text-success">
                {(data.tradable_ratio * 100).toFixed(1)}%
              </div>
              <div className="text-3xs text-ink-muted">非停牌可撮合交易日占比</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">正常交易天数</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.normal_days} 天
              </div>
              <div className="text-3xs text-ink-muted">价格笼子内连续竞价</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">异常停牌天数</div>
              <div className={`num mt-1 text-base font-semibold ${data.suspended_days > 0 ? 'text-warn' : 'text-ink-secondary'}`}>
                {data.suspended_days} 天
              </div>
              <div className="text-3xs text-ink-muted">流动性完全冻结</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">涨跌停封板极限</div>
              <div className="mt-1 flex items-baseline gap-1 text-base font-semibold">
                <span className="num text-up">{data.limit_up_days} 涨停</span>
                <span className="text-ink-muted">/</span>
                <span className="num text-down">{data.limit_down_days} 跌停</span>
              </div>
              <div className="text-3xs text-ink-muted">价格触及日内硬限价</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">ST 风险警示</div>
              <div className={`num mt-1 text-base font-semibold ${data.st_days > 0 ? 'text-danger' : 'text-ink-secondary'}`}>
                {data.st_days} 天
              </div>
              <div className="text-3xs text-ink-muted">ST 或 *ST 状态</div>
            </div>
          </div>

          {/* 状态机时序连续色带条 (Timeline Horizon) */}
          <div className="rounded-lg border border-hair bg-surface p-3">
            <div className="flex items-center justify-between text-2xs text-ink-muted">
              <span>状态机演进色带（从左至右历史时序）</span>
              <div className="flex items-center gap-3">
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" />正常交易</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-slate-400" />停牌</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" />涨停</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-teal-500" />跌停</span>
                <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-amber-500" />ST警示</span>
              </div>
            </div>

            {/* 密集时序微缩条 */}
            <div className="mt-2.5 flex h-4 w-full gap-px overflow-hidden rounded bg-surface-sunken">
              {data.timeline.map((p) => {
                let colorCls = 'bg-emerald-500';
                if (p.status === 'SUSPENDED') colorCls = 'bg-slate-400';
                else if (p.status === 'LIMIT_UP') colorCls = 'bg-rose-500';
                else if (p.status === 'LIMIT_DOWN') colorCls = 'bg-teal-500';
                else if (p.status === 'ST') colorCls = 'bg-amber-500';

                return (
                  <div
                    key={p.date}
                    className={`h-full flex-1 transition-opacity hover:opacity-75 ${colorCls}`}
                    title={`${p.date}: ${p.status_label} (收盘价: ${p.close})`}
                  />
                );
              })}
            </div>
            <div className="mt-1.5 flex justify-between text-3xs text-ink-muted">
              <span>{data.timeline[0]?.date ?? '—'}</span>
              <span>{data.timeline[data.timeline.length - 1]?.date ?? '—'}</span>
            </div>
          </div>

          {/* 异常异动截面列表 */}
          <div className="overflow-x-auto rounded-lg border border-hair">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt text-2xs text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">交易日期</th>
                  <th className="px-3 py-2 font-medium">可交易状态</th>
                  <th className="px-3 py-2 text-right font-medium">收盘价</th>
                  <th className="px-3 py-2 text-right font-medium">涨停价</th>
                  <th className="px-3 py-2 text-right font-medium">跌停价</th>
                  <th className="px-3 py-2 text-center font-medium">停牌标识</th>
                  <th className="px-3 py-2 text-center font-medium">ST 标识</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {data.timeline.slice(-15).reverse().map((r) => (
                  <tr key={r.date} className="hover:bg-surface-alt/50">
                    <td className="px-3 py-1.5 font-mono text-ink-secondary">{r.date}</td>
                    <td className="px-3 py-1.5">{getStatusBadge(r.status, r.status_label)}</td>
                    <td className="num px-3 py-1.5 text-right font-semibold text-ink">{r.close.toFixed(2)}</td>
                    <td className="num px-3 py-1.5 text-right text-ink-muted">{r.limit_up ? r.limit_up.toFixed(2) : '—'}</td>
                    <td className="num px-3 py-1.5 text-right text-ink-muted">{r.limit_down ? r.limit_down.toFixed(2) : '—'}</td>
                    <td className="px-3 py-1.5 text-center text-2xs text-ink-muted">{r.is_halted ? '是' : '否'}</td>
                    <td className="px-3 py-1.5 text-center text-2xs text-ink-muted">{r.is_st ? '是' : '否'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="text-2xs text-ink-muted">
            {'ⓘ 可交易性状态机准则：回测与模拟盘严守真实撮合硬约束：跌停板禁止卖出、涨停板禁止买入、停牌期间资金完全冻结。'}
          </div>
        </div>
      ) : null}
    </SectionCard>
  );
}
