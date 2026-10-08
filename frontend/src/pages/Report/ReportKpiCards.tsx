import type { BrinsonSummary, MarketCompass, PortfolioRiskData, SignalPreview } from '@/api/monitor';

interface Props {
  brinson?: BrinsonSummary | null;
  compass?: MarketCompass | null;
  preview?: SignalPreview | null;
  risk?: PortfolioRiskData | null;
}

function fmtPct(n: number | null | undefined, sign = true): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const val = n * 100;
  const prefix = sign && val > 0 ? '+' : '';
  return `${prefix}${val.toFixed(2)}%`;
}

function colorCls(n: number | null | undefined): string {
  if (n == null || Math.abs(n) < 1e-6) return 'text-ink-secondary';
  return n > 0 ? 'text-up' : 'text-down';
}

export function ReportKpiCards({ brinson, compass, preview, risk }: Props) {
  const excess = brinson?.excess_return;
  const portRet = brinson?.portfolio_return;
  const benchRet = brinson?.benchmark_return;

  const sentimentScore = compass?.sentiment_score;
  const sentimentLabel = compass?.sentiment_label ?? '—';
  const adl = compass?.adl_change ?? 0;

  const turnover = preview?.expected_turnover;
  const slippage = preview?.estimated_slippage_bps;

  const var95 = risk?.portfolio_var_95;
  const beta = risk?.portfolio_beta;
  const riskStatus = risk?.status ?? 'PASS';

  const riskStatusBadge = {
    PASS: 'bg-success-bg text-success border-success/30',
    WARN: 'bg-warn-bg text-warn border-warn/30',
    ALERT: 'bg-danger-bg text-danger border-danger/30',
  }[riskStatus] ?? 'bg-surface-sunken text-ink-muted border-hair';

  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
      {/* 1. 超额收益 */}
      <div className="rounded-lg border border-hair bg-surface p-3 transition-colors">
        <div className="flex items-center justify-between text-2xs text-ink-muted">
          <span>窗口组合超额 (Active Return)</span>
          <span className="rounded bg-brand-500/10 px-1.5 py-0.5 font-medium text-brand-600 dark:text-brand-400">
            Brinson
          </span>
        </div>
        <div className={`num mt-1.5 text-2xl font-bold ${colorCls(excess)}`}>
          {fmtPct(excess)}
        </div>
        <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
          <span>组合: <span className={colorCls(portRet)}>{fmtPct(portRet)}</span></span>
          <span>基准: <span className={colorCls(benchRet)}>{fmtPct(benchRet)}</span></span>
        </div>
      </div>

      {/* 2. 微观情绪 */}
      <div className="rounded-lg border border-hair bg-surface p-3 transition-colors">
        <div className="flex items-center justify-between text-2xs text-ink-muted">
          <span>全市场微观情绪指数</span>
          <span className="rounded bg-brand-500/10 px-1.5 py-0.5 font-medium text-brand-600 dark:text-brand-400">
            {compass?.trading_date ?? '当日'}
          </span>
        </div>
        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="num text-2xl font-bold text-ink">
            {sentimentScore != null ? sentimentScore : '—'}
          </span>
          <span className="text-xs font-semibold text-ink-secondary">
            {sentimentLabel}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
          <span>腾落差: <span className={`num font-semibold ${adl >= 0 ? 'text-up' : 'text-down'}`}>{adl >= 0 ? `+${adl}` : adl}</span></span>
          <span>基尼系数: <span className="num font-semibold text-ink">{compass?.turnover_gini ? compass.turnover_gini.toFixed(3) : '—'}</span></span>
        </div>
      </div>

      {/* 3. 次日调仓换手与冲击 */}
      <div className="rounded-lg border border-hair bg-surface p-3 transition-colors">
        <div className="flex items-center justify-between text-2xs text-ink-muted">
          <span>次日预期单边换手 & 滑点</span>
          <span className="rounded bg-brand-500/10 px-1.5 py-0.5 font-medium text-brand-600 dark:text-brand-400">
            Almgren-Chriss
          </span>
        </div>
        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="num text-2xl font-bold text-ink">
            {turnover != null ? `${(turnover * 100).toFixed(1)}%` : '—'}
          </span>
          <span className="text-xs font-semibold text-warn">
            {slippage != null ? `${slippage} bps` : '—'}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
          <span>拟增持: <span className="font-semibold text-up">{preview?.buys?.length ?? 0}</span> 只</span>
          <span>拟减持: <span className="font-semibold text-down">{preview?.sells?.length ?? 0}</span> 只</span>
        </div>
      </div>

      {/* 4. 组合在险价值 */}
      <div className="rounded-lg border border-hair bg-surface p-3 transition-colors">
        <div className="flex items-center justify-between text-2xs text-ink-muted">
          <span>组合在险价值 (95% 1d VaR)</span>
          <span className={`rounded border px-1.5 py-0.5 font-semibold text-3xs ${riskStatusBadge}`}>
            {riskStatus}
          </span>
        </div>
        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="num text-2xl font-bold text-ink">
            {var95 != null ? `${var95}%` : '—'}
          </span>
          <span className="text-xs text-ink-muted">
            Beta: {beta != null ? beta.toFixed(2) : '1.00'}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-3xs text-ink-muted">
          <span>最大个股: <span className="font-semibold text-ink">{risk?.max_stock_weight != null ? `${risk.max_stock_weight}%` : '—'}</span></span>
          <span>变现天数: <span className="font-semibold text-ink">{risk?.liquidation_days != null ? `${risk.liquidation_days}d` : '—'}</span></span>
        </div>
      </div>
    </div>
  );
}
