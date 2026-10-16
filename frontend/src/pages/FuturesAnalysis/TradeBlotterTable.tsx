/**
 * 逐笔开平仓交易明细流水表 (Trade Blotter)。
 *
 * 记录每一次入场、离场、成交价格、持仓手数、手续费成本与单笔净盈亏。
 */
import { useMemo, useState } from 'react';
import { SectionCard } from '@/components/ui';
import type { FuturesTradeRecord } from '@/types/futures';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';

interface Props {
  trades?: FuturesTradeRecord[];
}

export function TradeBlotterTable({ trades = [] }: Props) {
  const [filterDirection, setFilterDirection] = useState<'all' | 'LONG' | 'SHORT'>('all');
  const [filterPnl, setFilterPnl] = useState<'all' | 'win' | 'loss'>('all');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const pageSize = 10;

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (filterDirection !== 'all' && t.direction !== filterDirection) return false;
      if (filterPnl === 'win' && t.pnl <= 0) return false;
      if (filterPnl === 'loss' && t.pnl >= 0) return false;
      return true;
    });
  }, [trades, filterDirection, filterPnl]);

  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / pageSize));
  const pagedTrades = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredTrades.slice(start, start + pageSize);
  }, [filteredTrades, currentPage, pageSize]);

  // 总体统计数据
  const stats = useMemo(() => {
    const total = trades.length;
    if (total === 0) {
      return {
        total: 0,
        wins: 0,
        losses: 0,
        winRate: 0,
        netPnl: 0,
        totalFee: 0,
        profitFactor: 0,
        payoffRatio: 0,
        avgWin: 0,
        avgLoss: 0,
        maxWin: 0,
        maxLoss: 0,
      };
    }
    const wins = trades.filter((t) => t.pnl > 0);
    const losses = trades.filter((t) => t.pnl < 0);
    const winRate = (wins.length / total) * 100;
    const netPnl = trades.reduce((acc, t) => acc + t.pnl, 0);
    const totalFee = trades.reduce((acc, t) => acc + t.fee, 0);

    const grossProfit = wins.reduce((acc, t) => acc + t.pnl, 0);
    const grossLoss = Math.abs(losses.reduce((acc, t) => acc + t.pnl, 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 99.9 : 0;

    const avgWin = wins.length > 0 ? grossProfit / wins.length : 0;
    const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0;
    const payoffRatio = avgLoss > 0 ? avgWin / avgLoss : avgWin > 0 ? 99.9 : 0;

    const maxWin = wins.length > 0 ? Math.max(...wins.map((t) => t.pnl)) : 0;
    const maxLoss = losses.length > 0 ? Math.min(...losses.map((t) => t.pnl)) : 0;

    return {
      total,
      wins: wins.length,
      losses: losses.length,
      winRate,
      netPnl,
      totalFee,
      profitFactor,
      payoffRatio,
      avgWin,
      avgLoss,
      maxWin,
      maxLoss,
    };
  }, [trades]);

  return (
    <SectionCard
      title="逐笔开平仓交易流水表 (Trade Blotter)"
      action={
        <span className="text-2xs text-text-muted">
          全周期共记录 {stats.total} 笔实盘撮合明细，已扣除标准手续费与滑点成本
        </span>
      }
    >
      <div className="space-y-4">
        {/* 顶部统计条 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">总交易笔数</div>
            <div className="mt-1 font-mono text-base font-semibold text-text">{stats.total} 笔</div>
            <div className="text-2xs text-text-muted mt-0.5">平昨/平今双向记录</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">胜负比例 (胜率)</div>
            <div className="mt-1 font-mono text-base font-semibold text-text">
              <span className="text-emerald-500">{stats.wins}</span>
              <span className="text-text-muted mx-1">/</span>
              <span className="text-rose-500">{stats.losses}</span>
              <span className="ml-1 text-xs text-text-muted">({stats.winRate.toFixed(1)}%)</span>
            </div>
            <div className="text-2xs text-text-muted mt-0.5">多空全天候结算</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">盈亏比 (Payoff)</div>
            <div className="mt-1 font-mono text-base font-semibold text-text">
              {stats.payoffRatio.toFixed(2)}:1
            </div>
            <div className="text-2xs text-text-muted mt-0.5">均赢 {fmtNum(stats.avgWin)} / 均亏 {fmtNum(stats.avgLoss)}</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">获利因子 (Profit Factor)</div>
            <div className={`mt-1 font-mono text-base font-semibold ${stats.profitFactor >= 1.5 ? 'text-emerald-500' : stats.profitFactor >= 1.0 ? 'text-amber-500' : 'text-rose-500'}`}>
              {stats.profitFactor.toFixed(2)}
            </div>
            <div className="text-2xs text-text-muted mt-0.5">毛盈利 / 毛亏损</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">累计手续费支出</div>
            <div className="mt-1 font-mono text-base font-semibold text-amber-500">
              {fmtNum(stats.totalFee, 2)} 元
            </div>
            <div className="text-2xs text-text-muted mt-0.5">含平今15倍高频惩罚</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">累计净盈亏 (扣费后)</div>
            <div className={`mt-1 font-mono text-base font-semibold ${pctClass(stats.netPnl)}`}>
              {stats.netPnl >= 0 ? `+${fmtNum(stats.netPnl, 2)}` : fmtNum(stats.netPnl, 2)} 元
            </div>
            <div className="text-2xs text-text-muted mt-0.5">单笔最高 +{fmtNum(stats.maxWin)}</div>
          </div>
        </div>

        {/* 筛选过滤工具条 */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-surface-raised px-4 py-2.5 text-xs">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <span className="text-text-muted">交易方向:</span>
              <select
                aria-label="交易方向筛选"
                value={filterDirection}
                onChange={(e) => {
                  setFilterDirection(e.target.value as any);
                  setCurrentPage(1);
                }}
                className="rounded border border-border bg-card px-2 py-1 text-xs text-text focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="all">全部方向</option>
                <option value="LONG">做多 (LONG)</option>
                <option value="SHORT">做空 (SHORT)</option>
              </select>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-text-muted">盈亏筛选:</span>
              <select
                aria-label="盈亏结果筛选"
                value={filterPnl}
                onChange={(e) => {
                  setFilterPnl(e.target.value as any);
                  setCurrentPage(1);
                }}
                className="rounded border border-border bg-card px-2 py-1 text-xs text-text focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="all">全部结果</option>
                <option value="win">仅盈利单 (&gt;0)</option>
                <option value="loss">仅亏损单 (&lt;0)</option>
              </select>
            </div>
          </div>

          <div className="text-text-muted">
            显示 {filteredTrades.length} 条记录 (第 {currentPage} / {totalPages} 页)
          </div>
        </div>

        {/* 流水明细数据表 */}
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-raised font-medium text-text-muted">
              <tr>
                <th className="px-3 py-2.5">交易编号</th>
                <th className="px-3 py-2.5">方向</th>
                <th className="px-3 py-2.5">开仓日期</th>
                <th className="px-3 py-2.5 text-right">开仓价</th>
                <th className="px-3 py-2.5">平仓日期</th>
                <th className="px-3 py-2.5 text-right">平仓价</th>
                <th className="px-3 py-2.5 text-right">手数</th>
                <th className="px-3 py-2.5 text-right">手续费</th>
                <th className="px-3 py-2.5 text-right">变动率</th>
                <th className="px-3 py-2.5 text-right">净盈亏</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              {pagedTrades.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-3 py-8 text-center text-text-muted font-sans">
                    暂无符合条件的交易记录
                  </td>
                </tr>
              ) : (
                pagedTrades.map((t) => (
                  <tr key={t.id} className="hover:bg-surface-raised/50">
                    <td className="px-3 py-2.5 font-medium text-text">{t.id}</td>
                    <td className="px-3 py-2.5">
                      <span
                        className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-semibold ${
                          t.direction === 'LONG'
                            ? 'bg-emerald-500/10 text-emerald-500'
                            : 'bg-indigo-500/10 text-indigo-400'
                        }`}
                      >
                        {t.direction === 'LONG' ? '做多' : '做空'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-text-muted font-sans">{t.entry_date}</td>
                    <td className="px-3 py-2.5 text-right text-text">{fmtNum(t.entry_price, 2)}</td>
                    <td className="px-3 py-2.5 text-text-muted font-sans">{t.exit_date}</td>
                    <td className="px-3 py-2.5 text-right text-text">{fmtNum(t.exit_price, 2)}</td>
                    <td className="px-3 py-2.5 text-right text-text">{t.lots} 手</td>
                    <td className="px-3 py-2.5 text-right text-amber-500">{fmtNum(t.fee, 2)}</td>
                    <td className={`px-3 py-2.5 text-right font-semibold ${pctClass(t.pnl_pct)}`}>
                      {fmtPct(t.pnl_pct)}
                    </td>
                    <td className={`px-3 py-2.5 text-right font-semibold ${pctClass(t.pnl)}`}>
                      {t.pnl >= 0 ? `+${fmtNum(t.pnl, 2)}` : fmtNum(t.pnl, 2)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 分页控制栏 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-end gap-2 text-xs">
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage <= 1}
              className="rounded border border-border px-3 py-1 text-text disabled:opacity-40 hover:bg-surface-raised"
            >
              上一页
            </button>
            <span className="text-text-muted">
              {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage >= totalPages}
              className="rounded border border-border px-3 py-1 text-text disabled:opacity-40 hover:bg-surface-raised"
            >
              下一页
            </button>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
