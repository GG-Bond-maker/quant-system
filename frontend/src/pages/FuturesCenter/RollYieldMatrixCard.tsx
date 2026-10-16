/**
 * 期货中心 · 全市场年化展期收益率矩阵与升贴水天梯榜 (RollYieldMatrixCard)。
 *
 * 量化商品与股指衍生品的核心因子之一（Carry / Roll Yield Factor）：
 * 1. 测算全市场主力合约近月与远月的真实展期收益率；
 * 2. 梳理 Backwardation 贴水天梯榜（做多买入展期吃贴水收益最佳选品）；
 * 3. 梳理 Contango 升水天梯榜（做空展期有利选品）。
 */
import { useEffect, useState } from 'react';
import { SectionCard } from '@/components/ui';
import { futuresApi } from '@/api/futures';
import type { RollYieldMatrixResult } from '@/types/futures';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';

interface Props {
  onSelectSymbol?: (symbol: string) => void;
}

export default function RollYieldMatrixCard({ onSelectSymbol }: Props) {
  const [data, setData] = useState<RollYieldMatrixResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'all' | 'backwardation' | 'contango'>('all');

  useEffect(() => {
    let unmounted = false;
    setLoading(true);
    futuresApi.rollYieldMatrix()
      .then((res) => {
        if (!unmounted) setData(res);
      })
      .catch((err) => {
        console.error('Failed to load roll yield matrix', err);
      })
      .finally(() => {
        if (!unmounted) setLoading(false);
      });

    return () => {
      unmounted = true;
    };
  }, []);

  const displayItems =
    activeTab === 'backwardation'
      ? data?.top_backwardation ?? []
      : activeTab === 'contango'
      ? data?.top_contango ?? []
      : data?.all_items ?? [];

  return (
    <SectionCard
      title="全市场年化展期收益率矩阵 (Roll Yield Matrix)"
      action={
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveTab('all')}
            className={`rounded px-2 py-0.5 text-2xs transition-colors ${
              activeTab === 'all'
                ? 'bg-brand-600 text-white font-medium'
                : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
            }`}
          >
            全部品种 ({data?.total_count ?? 0})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('backwardation')}
            className={`rounded px-2 py-0.5 text-2xs transition-colors ${
              activeTab === 'backwardation'
                ? 'bg-up-bg text-up font-medium border border-up/30'
                : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
            }`}
          >
            🔥 贴水天梯 (多头吃贴水)
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('contango')}
            className={`rounded px-2 py-0.5 text-2xs transition-colors ${
              activeTab === 'contango'
                ? 'bg-down-bg text-down font-medium border border-down/30'
                : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
            }`}
          >
            ❄️ 升水天梯 (做空展期利好)
          </button>
        </div>
      }
    >
      {loading && (
        <div className="py-12 text-center text-xs text-ink-muted animate-pulse">
          正在测算全市场各主力合约展期收益率与期限价差...
        </div>
      )}

      {!loading && (
        <div className="overflow-x-auto">
          <table className="quant-table dense w-full">
            <thead>
              <tr>
                <th>品种代码</th>
                <th>品种名称</th>
                <th>交易所</th>
                <th className="text-right">近月参考价</th>
                <th className="text-right">远季月参考价</th>
                <th className="text-right">到期跨度</th>
                <th className="text-right">年化展期收益率</th>
                <th>市场结构形态</th>
                <th>量化套利建议</th>
                <th className="text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {displayItems.length ? (
                displayItems.map((it) => (
                  <tr key={it.prefix} className="hover:bg-surface-alt">
                    <td className="font-semibold text-brand-600 num">{it.prefix}</td>
                    <td className="text-xs text-ink">{it.name}</td>
                    <td className="text-2xs text-ink-muted">{it.exchange}</td>
                    <td className="num text-right text-xs text-ink">{fmtNum(it.near_price)}</td>
                    <td className="num text-right text-xs text-ink-secondary">{fmtNum(it.far_price)}</td>
                    <td className="num text-right text-2xs text-ink-muted">
                      {it.near_dte}d ➔ {it.far_dte}d
                    </td>
                    <td className={`num text-right text-xs font-bold ${pctClass(it.roll_yield_pct)}`}>
                      {fmtPct(it.roll_yield_pct)}
                    </td>
                    <td className="text-2xs">
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          it.structure_type.includes('贴水')
                            ? 'bg-up-bg text-up'
                            : 'bg-down-bg text-down'
                        }`}
                      >
                        {it.structure_type}
                      </span>
                    </td>
                    <td className="text-2xs text-ink-secondary font-medium">{it.action_hint}</td>
                    <td className="text-center">
                      <button
                        type="button"
                        onClick={() => onSelectSymbol?.(it.prefix)}
                        className="rounded px-2 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-50 border border-brand-200"
                      >
                        联动图表
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={10} className="py-6 text-center text-xs text-ink-muted">
                    暂无该类型展期数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  );
}
