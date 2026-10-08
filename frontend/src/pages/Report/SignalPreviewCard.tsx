import { useNavigate } from 'react-router-dom';
import type { SignalPreview } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  preview: SignalPreview;
  onNavigate?: (path: string) => void;
}

function useSafeNavigate() {
  try {
    return useNavigate();
  } catch {
    return null;
  }
}

export function SignalPreviewCard({ preview, onNavigate }: Props) {
  const safeNavigate = useSafeNavigate();

  const toPct = (val: number) => `${(val * 100).toFixed(2)}%`;
  const toDeltaPct = (val: number) => {
    const p = (val * 100).toFixed(2);
    return val > 0 ? `+${p}%` : `${p}%`;
  };

  const handleSyncToDesk = () => {
    const orders = [
      ...preview.buys.map((b) => ({
        symbol: b.symbol,
        side: 'buy' as const,
        order_amount: Math.max(10_000, Math.round(Math.abs(b.weight_delta) * 1_000_000)),
        algo: 'vwap' as const,
        split_days: 1,
        participation_cap: 0.05,
        target_weight: Number((b.target_weight * 100).toFixed(2)),
      })),
      ...preview.sells.map((s) => ({
        symbol: s.symbol,
        side: 'sell' as const,
        order_amount: Math.max(10_000, Math.round(Math.abs(s.weight_delta) * 1_000_000)),
        algo: 'vwap' as const,
        split_days: 1,
        participation_cap: 0.05,
        target_weight: Number((s.target_weight * 100).toFixed(2)),
      })),
    ];

    if (!orders.length) return;

    if (typeof window !== 'undefined' && window.sessionStorage) {
      window.sessionStorage.setItem(
        'aqp:desk:batch_import',
        JSON.stringify({
          source: `AI 日报次日调仓推演 (${preview.target_date})`,
          orders,
          createdAt: new Date().toISOString(),
        }),
      );
    }

    if (onNavigate) {
      onNavigate('/desk?from=report');
    } else if (safeNavigate) {
      safeNavigate('/desk?from=report');
    } else if (typeof window !== 'undefined') {
      window.location.href = '/desk?from=report';
    }
  };

  const hasOrders = preview.buys.length > 0 || preview.sells.length > 0;

  return (
    <SectionCard
      title="次日调仓推演与换手冲击预估（多因子信号预演）"
      action={
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-2xs text-ink-muted">信号期: {preview.target_date}</span>
          <span className="rounded bg-brand-500/10 px-2 py-0.5 text-xs font-semibold text-brand-600 dark:text-brand-400">
            预期换手 {(preview.expected_turnover * 100).toFixed(1)}% · 预估滑点 {preview.estimated_slippage_bps} bps
          </span>
          <button
            type="button"
            onClick={handleSyncToDesk}
            disabled={!hasOrders}
            title={hasOrders ? '将拟买入/拟卖出标的打包写入交易台批量下单草稿' : '当前无调仓订单'}
            className="inline-flex items-center gap-1 rounded-md bg-brand-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span>🚀 一键同步至交易台</span>
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        {/* 指标小卡 */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">预期单边换手率</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {(preview.expected_turnover * 100).toFixed(2)}%
            </div>
            <div className="text-3xs text-ink-muted">目标仓位 vs 现仓差额加总</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">预期冲击滑点 (Almgren-Chriss)</div>
            <div className="num mt-1 text-base font-semibold text-warn">
              {preview.estimated_slippage_bps} bps
            </div>
            <div className="text-3xs text-ink-muted">平方根流动性冲击估算</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">拟增持 / 买入标的</div>
            <div className="num mt-1 text-base font-semibold text-up">
              {preview.buys.length} 只
            </div>
            <div className="text-3xs text-ink-muted">模型 Top 打分高胜率标的</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">拟减持 / 剔除标的</div>
            <div className="num mt-1 text-base font-semibold text-down">
              {preview.sells.length} 只
            </div>
            <div className="text-3xs text-ink-muted">打分衰退或退出目标池标的</div>
          </div>
        </div>

        {/* 调仓标的双栏对照表 */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {/* 增持/买入 */}
          <div className="overflow-hidden rounded-lg border border-hair">
            <div className="border-b border-hair bg-surface-alt px-3 py-1.5 text-2xs font-semibold text-up">
              ▲ 拟增持 / 买入标的明细（Top {preview.buys.length}）
            </div>
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt/50 text-3xs text-ink-muted">
                <tr>
                  <th className="px-2.5 py-1.5 font-medium">标的代码</th>
                  <th className="px-2 py-1.5 text-right font-medium">模型打分</th>
                  <th className="px-2 py-1.5 text-right font-medium">现仓</th>
                  <th className="px-2 py-1.5 text-right font-medium">目标仓</th>
                  <th className="px-2.5 py-1.5 text-right font-medium">建议买入幅度</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {preview.buys.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-3xs text-ink-muted">暂无增持需求</td>
                  </tr>
                ) : (
                  preview.buys.map((b) => (
                    <tr key={b.symbol} className="hover:bg-surface-alt/50">
                      <td className="px-2.5 py-1.5 font-mono font-medium text-ink">{b.symbol}</td>
                      <td className="num px-2 py-1.5 text-right text-ink-secondary">{b.score?.toFixed(4) ?? '—'}</td>
                      <td className="num px-2 py-1.5 text-right text-ink-muted">{toPct(b.current_weight)}</td>
                      <td className="num px-2 py-1.5 text-right text-ink">{toPct(b.target_weight)}</td>
                      <td className="num px-2.5 py-1.5 text-right font-semibold text-up">{toDeltaPct(b.weight_delta)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* 减持/卖出 */}
          <div className="overflow-hidden rounded-lg border border-hair">
            <div className="border-b border-hair bg-surface-alt px-3 py-1.5 text-2xs font-semibold text-down">
              ▼ 拟减持 / 卖出标的明细（Top {preview.sells.length}）
            </div>
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt/50 text-3xs text-ink-muted">
                <tr>
                  <th className="px-2.5 py-1.5 font-medium">标的代码</th>
                  <th className="px-2 py-1.5 text-right font-medium">模型打分</th>
                  <th className="px-2 py-1.5 text-right font-medium">现仓</th>
                  <th className="px-2 py-1.5 text-right font-medium">目标仓</th>
                  <th className="px-2.5 py-1.5 text-right font-medium">建议减持幅度</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {preview.sells.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-3xs text-ink-muted">暂无减持需求</td>
                  </tr>
                ) : (
                  preview.sells.map((s) => (
                    <tr key={s.symbol} className="hover:bg-surface-alt/50">
                      <td className="px-2.5 py-1.5 font-mono font-medium text-ink">{s.symbol}</td>
                      <td className="num px-2 py-1.5 text-right text-ink-secondary">{s.score?.toFixed(4) ?? '—'}</td>
                      <td className="num px-2 py-1.5 text-right text-ink-muted">{toPct(s.current_weight)}</td>
                      <td className="num px-2 py-1.5 text-right text-ink">{toPct(s.target_weight)}</td>
                      <td className="num px-2.5 py-1.5 text-right font-semibold text-down">{toDeltaPct(s.weight_delta)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="text-2xs text-ink-muted">
          {'ⓘ 量化执行准则：基于日终最新因子预测打分结合当前实盘/模拟盘持仓推演次日目标持仓，采用 Almgren-Chriss 平方根冲击模型估算执行滑点损耗，辅助交易台提前制定拆单算法（TWAP/VWAP）。'}
        </div>
      </div>
    </SectionCard>
  );
}
