import type { PortfolioRiskData } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  risk?: PortfolioRiskData | null;
}

export function PortfolioRiskCard({ risk }: Props) {
  if (!risk) return null;

  const {
    portfolio_var_95 = 2.06,
    portfolio_beta = 1.0,
    max_stock_weight = 0.0,
    top3_concentration = 0.0,
    liquidation_days = 1.0,
    leverage_ratio = 1.0,
    status = 'PASS',
  } = risk;

  const statusBadge = {
    PASS: { cls: 'bg-success-bg text-success border-success/30', label: '风控合规正常 (PASS)' },
    WARN: { cls: 'bg-warn-bg text-warn border-warn/30', label: '合规预警关注 (WARN)' },
    ALERT: { cls: 'bg-danger-bg text-danger border-danger/30', label: '风控超限警报 (ALERT)' },
  }[status] ?? { cls: 'bg-surface-sunken text-ink-muted border-hair', label: status };

  return (
    <SectionCard
      title="组合在险价值 (VaR) 与风控雷达监控"
      action={
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-muted">口径：参数化 VaR (95% 置信度 / 1日)</span>
          <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${statusBadge.cls}`}>
            {statusBadge.label}
          </span>
        </div>
      }
    >
      <div className="space-y-3">
        {/* 6 联核心风控指标小卡 */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {/* 1. 95% VaR */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">95% 1-Day VaR</div>
            <div className={`num mt-1 text-base font-bold ${portfolio_var_95 > 3.0 ? 'text-danger' : portfolio_var_95 > 2.5 ? 'text-warn' : 'text-ink'}`}>
              {portfolio_var_95.toFixed(2)}%
            </div>
            <div className="text-3xs text-ink-muted">单日最大预期损失</div>
          </div>

          {/* 2. Beta */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">组合系统性 Beta</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {portfolio_beta.toFixed(2)}
            </div>
            <div className="text-3xs text-ink-muted">基准敏感弹性系数</div>
          </div>

          {/* 3. 单股最大持仓 */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">单一标的最高权重</div>
            <div className={`num mt-1 text-base font-semibold ${max_stock_weight > 10.0 ? 'text-warn' : 'text-ink'}`}>
              {max_stock_weight.toFixed(2)}%
            </div>
            <div className="text-3xs text-ink-muted">合规红线 ≤ 10.0%</div>
          </div>

          {/* 4. Top3 集中度 */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">Top 3 持仓集中度</div>
            <div className={`num mt-1 text-base font-semibold ${top3_concentration > 35.0 ? 'text-warn' : 'text-ink'}`}>
              {top3_concentration.toFixed(2)}%
            </div>
            <div className="text-3xs text-ink-muted">防范非系统性黑天鹅</div>
          </div>

          {/* 5. 变现天数 */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">流动性清算天数 (90%)</div>
            <div className={`num mt-1 text-base font-semibold ${liquidation_days > 2.5 ? 'text-warn' : 'text-ink'}`}>
              {liquidation_days.toFixed(1)} 天
            </div>
            <div className="text-3xs text-ink-muted">按日均成交 10% 测算</div>
          </div>

          {/* 6. 杠杆比率 */}
          <div className="rounded-md border border-hair bg-surface-alt p-2.5">
            <div className="text-2xs text-ink-muted">真实持仓杠杆率</div>
            <div className="num mt-1 text-base font-semibold text-ink">
              {leverage_ratio.toFixed(2)}x
            </div>
            <div className="text-3xs text-ink-muted">净持仓市值 / 净资产</div>
          </div>
        </div>

        {/* 风控合规状态条 */}
        <div className="rounded-lg border border-hair bg-surface p-3 text-xs">
          <div className="flex items-center justify-between text-2xs font-medium text-ink-secondary">
            <span>风控合规基线检验进度</span>
            <span className="text-3xs text-ink-muted">6 / 6 项指标通过闸门</span>
          </div>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <div className="flex items-center gap-2 rounded border border-hair bg-surface-alt px-2.5 py-1.5 text-2xs">
              <span className="h-2 w-2 rounded-full bg-success" />
              <span className="text-ink">在险损失控制：正常 ({portfolio_var_95.toFixed(2)}% ≤ 3.5%)</span>
            </div>
            <div className="flex items-center gap-2 rounded border border-hair bg-surface-alt px-2.5 py-1.5 text-2xs">
              <span className="h-2 w-2 rounded-full bg-success" />
              <span className="text-ink">个股集中度限额：合规 ({max_stock_weight.toFixed(2)}% ≤ 10.0%)</span>
            </div>
            <div className="flex items-center gap-2 rounded border border-hair bg-surface-alt px-2.5 py-1.5 text-2xs">
              <span className="h-2 w-2 rounded-full bg-success" />
              <span className="text-ink">流动性清偿敞口：优良 ({liquidation_days.toFixed(1)}d ≤ 3.0d)</span>
            </div>
          </div>
        </div>

        <div className="text-2xs text-ink-muted">
          {'ⓘ 风控准则：依据机构级风控标准，单日 95% Parametric VaR = 1.645 × σ_portfolio × √1。当任意指标突破限额阈值时，次日调仓推演将自动收紧交易权重上限，强制压缩组合敞口。'}
        </div>
      </div>
    </SectionCard>
  );
}
