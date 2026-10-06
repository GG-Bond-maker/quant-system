import type { BrinsonSummary, BrinsonSector } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  summary: BrinsonSummary;
  sectors?: BrinsonSector[] | null;
}

function fmt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const pct = n * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

function colorCls(n: number | null | undefined): string {
  if (n == null || Math.abs(n) < 1e-6) return 'text-ink-secondary';
  return n > 0 ? 'text-up' : 'text-down';
}

export function BrinsonAttributionCard({ summary, sectors }: Props) {
  return (
    <SectionCard
      title="Brinson 绩效归因（行业配置 vs 个股选择）"
      action={
        <span className="text-2xs text-ink-muted">
          口径：真实模拟盘持仓 vs 同池等权基准
        </span>
      }
    >
      <div className="space-y-3">
        {/* 顶部总览卡片 */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">组合收益</div>
            <div className={`num mt-1 text-base font-semibold ${colorCls(summary.portfolio_return)}`}>
              {fmt(summary.portfolio_return)}
            </div>
            <div className="text-3xs text-ink-muted">真实组合加权</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">基准收益</div>
            <div className={`num mt-1 text-base font-semibold ${colorCls(summary.benchmark_return)}`}>
              {fmt(summary.benchmark_return)}
            </div>
            <div className="text-3xs text-ink-muted">同池等权基准</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">窗口超额收益</div>
            <div className={`num mt-1 text-base font-bold ${colorCls(summary.excess_return)}`}>
              {fmt(summary.excess_return)}
            </div>
            <div className="text-3xs text-ink-muted">Active Return</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">行业配置收益 (Q_AA)</div>
            <div className={`num mt-1 text-base font-semibold ${colorCls(summary.allocation)}`}>
              {fmt(summary.allocation)}
            </div>
            <div className="text-3xs text-ink-muted">行业超配/低配贡献</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">个股选择收益 (Q_SS)</div>
            <div className={`num mt-1 text-base font-semibold ${colorCls(summary.selection)}`}>
              {fmt(summary.selection)}
            </div>
            <div className="text-3xs text-ink-muted">行业内选股 Alpha</div>
          </div>
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">交互收益 (Q_IN)</div>
            <div className={`num mt-1 text-base font-semibold ${colorCls(summary.interaction)}`}>
              {fmt(summary.interaction)}
            </div>
            <div className="text-3xs text-ink-muted">权重与收益交叉乘积</div>
          </div>
        </div>

        {/* 行业贡献细分表 */}
        {sectors && sectors.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-hair">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt text-2xs text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">行业</th>
                  <th className="px-3 py-2 text-right font-medium">行业总超额</th>
                  <th className="px-3 py-2 text-right font-medium">资产配置贡献 (Q_AA)</th>
                  <th className="px-3 py-2 text-right font-medium">个股选择贡献 (Q_SS)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {sectors.map((sec) => (
                  <tr key={sec.industry} className="hover:bg-surface-alt/50">
                    <td className="px-3 py-2 font-medium text-ink">{sec.industry}</td>
                    <td className={`num px-3 py-2 text-right font-semibold ${colorCls(sec.total)}`}>
                      {fmt(sec.total)}
                    </td>
                    <td className={`num px-3 py-2 text-right ${colorCls(sec.allocation)}`}>
                      {fmt(sec.allocation)}
                    </td>
                    <td className={`num px-3 py-2 text-right ${colorCls(sec.selection)}`}>
                      {fmt(sec.selection)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="text-2xs text-ink-muted">
          {'ⓘ Brinson 归因模型：超额收益分解为配置收益 Q_AA = ∑ (w_p - w_b) × R_b、选股收益 Q_SS = ∑ w_b × (R_p - R_b) 与交互收益 Q_IN = ∑ (w_p - w_b) × (R_p - R_b)。'}
        </div>
      </div>
    </SectionCard>
  );
}
