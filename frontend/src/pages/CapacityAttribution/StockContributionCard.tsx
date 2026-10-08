/**
 * 个股微观盈亏与超额贡献度下钻穿透卡片（StockContributionCard）。
 * 对标 Barra Aegis / FactSet 个股选股超额贡献 Leaderboard 体系。
 */
import { useMemo, useState } from 'react';
import type { StockContributionItem } from '@/api/production';

export interface StockContributionCardProps {
  topContributors?: StockContributionItem[];
  bottomDrags?: StockContributionItem[];
  allHoldings?: StockContributionItem[];
}

export default function StockContributionCard({
  topContributors = [],
  bottomDrags = [],
  allHoldings = [],
}: StockContributionCardProps) {
  const [tab, setTab] = useState<'leaderboard' | 'all'>('leaderboard');

  const { totalPnlContrib, totalExcessBps } = useMemo(() => {
    let pnl = 0;
    let excess = 0;
    for (const h of allHoldings) {
      pnl += h.pnl_contrib_pct;
      excess += h.excess_contrib_bps;
    }
    return { totalPnlContrib: pnl, totalExcessBps: excess };
  }, [allHoldings]);

  if (!allHoldings || allHoldings.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      {/* 头部统计与视图切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2 text-2xs">
        <div className="flex items-center gap-3">
          <span className="font-medium text-ink">个股微观超额贡献穿透</span>
          <span className="text-ink-muted">·</span>
          <span>
            持仓累计贡献合计：{' '}
            <span className={`num font-semibold ${totalPnlContrib >= 0 ? 'text-danger' : 'text-success'}`}>
              {totalPnlContrib >= 0 ? '+' : ''}{totalPnlContrib.toFixed(2)}%
            </span>
          </span>
          <span className="text-ink-muted">·</span>
          <span>
            超额贡献合计：{' '}
            <span className={`num font-semibold ${totalExcessBps >= 0 ? 'text-danger' : 'text-success'}`}>
              {totalExcessBps >= 0 ? '+' : ''}{totalExcessBps.toFixed(0)} bps
            </span>
          </span>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => setTab('leaderboard')}
            className={`rounded px-2 py-0.5 text-2xs ${
              tab === 'leaderboard'
                ? 'bg-brand-50 font-medium text-brand-700'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            赢家与拖累榜
          </button>
          <button
            onClick={() => setTab('all')}
            className={`rounded px-2 py-0.5 text-2xs ${
              tab === 'all'
                ? 'bg-brand-50 font-medium text-brand-700'
                : 'text-ink-secondary hover:bg-surface-alt'
            }`}
          >
            全部持仓明细 ({allHoldings.length})
          </button>
        </div>
      </div>

      {tab === 'leaderboard' ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {/* Top 5 赢家 */}
          <div className="rounded-lg border border-hair bg-surface p-2.5">
            <div className="mb-2 flex items-center justify-between text-2xs font-semibold text-danger">
              <span>🏆 Top 5 正向选股超额赢家</span>
              <span>超额贡献 (bps)</span>
            </div>
            <div className="space-y-1.5">
              {topContributors.map((c, i) => (
                <div
                  key={c.symbol}
                  className="flex items-center justify-between rounded border border-hair/60 bg-surface-alt px-2 py-1 text-2xs"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-ink-muted">{i + 1}</span>
                    <span className="font-mono font-medium text-ink">{c.symbol}</span>
                    <span className="text-ink-secondary">{c.name}</span>
                    <span className="rounded bg-surface px-1 text-ink-muted">{c.industry}</span>
                  </div>
                  <div className="flex items-center gap-3 text-right">
                    <span className="num text-ink-muted">{(c.weight * 100).toFixed(1)}%</span>
                    <span className="num font-semibold text-danger">
                      +{c.excess_contrib_bps.toFixed(0)} bps
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Bottom 5 拖累 */}
          <div className="rounded-lg border border-hair bg-surface p-2.5">
            <div className="mb-2 flex items-center justify-between text-2xs font-semibold text-success">
              <span>🔻 Bottom 5 选股超额拖累标的</span>
              <span>拖累损失 (bps)</span>
            </div>
            <div className="space-y-1.5">
              {bottomDrags.map((d, i) => (
                <div
                  key={d.symbol}
                  className="flex items-center justify-between rounded border border-hair/60 bg-surface-alt px-2 py-1 text-2xs"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-ink-muted">{i + 1}</span>
                    <span className="font-mono font-medium text-ink">{d.symbol}</span>
                    <span className="text-ink-secondary">{d.name}</span>
                    <span className="rounded bg-surface px-1 text-ink-muted">{d.industry}</span>
                  </div>
                  <div className="flex items-center gap-3 text-right">
                    <span className="num text-ink-muted">{(d.weight * 100).toFixed(1)}%</span>
                    <span className="num font-semibold text-success">
                      {d.excess_contrib_bps.toFixed(0)} bps
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        /* 全部持仓明细表 */
        <div className="overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-hair text-ink-secondary">
                <th className="py-1 text-left font-medium">标的代码 / 名称</th>
                <th className="py-1 text-left font-medium">所属行业</th>
                <th className="py-1 text-right font-medium">组合权重</th>
                <th className="py-1 text-right font-medium">窗口累计收益</th>
                <th className="py-1 text-right font-medium">组合贡献 (PnL %)</th>
                <th className="py-1 text-right font-medium">选股超额贡献 (bps)</th>
              </tr>
            </thead>
            <tbody>
              {allHoldings.map((h) => (
                <tr key={h.symbol} className="border-b border-hair hover:bg-surface-alt">
                  <td className="py-1 font-mono">
                    {h.symbol} <span className="font-sans text-ink-muted">({h.name})</span>
                  </td>
                  <td className="py-1">{h.industry}</td>
                  <td className="num py-1 text-right">{(h.weight * 100).toFixed(1)}%</td>
                  <td className={`num py-1 text-right ${h.cum_return >= 0 ? 'text-danger' : 'text-success'}`}>
                    {(h.cum_return * 100).toFixed(2)}%
                  </td>
                  <td className={`num py-1 text-right font-medium ${h.pnl_contrib_pct >= 0 ? 'text-danger' : 'text-success'}`}>
                    {h.pnl_contrib_pct >= 0 ? '+' : ''}{h.pnl_contrib_pct.toFixed(2)}%
                  </td>
                  <td className={`num py-1 text-right font-semibold ${h.excess_contrib_bps >= 0 ? 'text-danger' : 'text-success'}`}>
                    {h.excess_contrib_bps >= 0 ? '+' : ''}{h.excess_contrib_bps.toFixed(1)} bps
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
