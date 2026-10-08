import { useMemo } from 'react';
import type { MarketCompass } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  compass: MarketCompass;
}

export function MarketCompassCard({ compass }: Props) {
  const total = useMemo(() => {
    return Math.max(1, compass.up_count + compass.down_count + compass.flat_count);
  }, [compass]);

  const upPct = ((compass.up_count / total) * 100).toFixed(1);
  const downPct = ((compass.down_count / total) * 100).toFixed(1);
  const flatPct = ((compass.flat_count / total) * 100).toFixed(1);

  const sentimentBadgeClass = useMemo(() => {
    if (compass.sentiment_score >= 70) return 'bg-up/10 text-up border border-up/30';
    if (compass.sentiment_score >= 55) return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30';
    if (compass.sentiment_score >= 40) return 'bg-warn-bg text-warn border border-warn/30';
    return 'bg-down/10 text-down border border-down/30';
  }, [compass.sentiment_score]);

  const giniDesc = useMemo(() => {
    if (compass.turnover_gini >= 0.65) return '成交高度抱团集中';
    if (compass.turnover_gini >= 0.45) return '成交适度聚集';
    return '全市场流动性扩散均衡';
  }, [compass.turnover_gini]);

  const maxBinCount = useMemo(() => {
    if (!compass.breadth_bins || compass.breadth_bins.length === 0) return 1;
    return Math.max(1, ...compass.breadth_bins.map((b) => b.count));
  }, [compass.breadth_bins]);

  return (
    <SectionCard
      title="全市场微观流动性与情绪罗盘"
      action={
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-muted">基准日: {compass.trading_date}</span>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${sentimentBadgeClass}`}>
            {compass.sentiment_label} ({compass.sentiment_score}分)
          </span>
        </div>
      }
    >
      <div className="space-y-3.5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {/* 1. 情绪综合评分 */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between text-2xs text-ink-muted">
              <span>市场情绪指数</span>
              <span className="font-mono">{compass.sentiment_score}/100</span>
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="num text-2xl font-bold text-ink">{compass.sentiment_score}</span>
              <span className="text-xs font-medium text-ink-secondary">{compass.sentiment_label}</span>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${Math.min(100, Math.max(0, compass.sentiment_score))}%`,
                  backgroundColor:
                    compass.sentiment_score >= 60
                      ? 'var(--up)'
                      : compass.sentiment_score >= 45
                      ? 'var(--warn)'
                      : 'var(--down)',
                }}
              />
            </div>
            <div className="mt-2 text-3xs text-ink-muted">
              权重：成交额加权涨跌比 70% + 极限封板比 30%
            </div>
          </div>

          {/* 2. 涨跌广度分布 (Market Breadth) */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between text-2xs text-ink-muted">
              <span>截面涨跌广度 (ADL)</span>
              <span className={`font-mono font-semibold ${compass.adl_change >= 0 ? 'text-up' : 'text-down'}`}>
                腾落差 {compass.adl_change >= 0 ? `+${compass.adl_change}` : compass.adl_change}
              </span>
            </div>
            <div className="mt-1 flex items-baseline justify-between text-xs">
              <span className="text-up font-semibold">涨 {compass.up_count} ({upPct}%)</span>
              <span className="text-ink-muted">平 {compass.flat_count} ({flatPct}%)</span>
              <span className="text-down font-semibold">跌 {compass.down_count} ({downPct}%)</span>
            </div>
            {/* 三色分布比例条 */}
            <div className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div style={{ width: `${upPct}%` }} className="bg-up h-full" title={`上涨: ${compass.up_count}`} />
              <div style={{ width: `${flatPct}%` }} className="bg-ink-muted/40 h-full" title={`平盘: ${compass.flat_count}`} />
              <div style={{ width: `${downPct}%` }} className="bg-down h-full" title={`下跌: ${compass.down_count}`} />
            </div>
            <div className="mt-2 text-3xs text-ink-muted">
              覆盖全市场 {total.toLocaleString()} 只真实上市标的
            </div>
          </div>

          {/* 3. 极限涨跌停板 (Limit Up/Down) */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between text-2xs text-ink-muted">
              <span>极限涨跌停封板</span>
              <span className="text-3xs text-ink-muted">价格笼子边界</span>
            </div>
            <div className="mt-1 flex items-center justify-around py-1">
              <div className="text-center">
                <div className="num text-xl font-bold text-up">{compass.limit_up_count}</div>
                <div className="text-3xs text-ink-muted">涨停家数</div>
              </div>
              <div className="h-6 w-px bg-hair" />
              <div className="text-center">
                <div className="num text-xl font-bold text-down">{compass.limit_down_count}</div>
                <div className="text-3xs text-ink-muted">跌停家数</div>
              </div>
            </div>
            <div className="mt-1 text-center text-3xs text-ink-muted">
              封板博弈：{compass.limit_up_count > compass.limit_down_count * 2 ? '赚钱效应显著' : compass.limit_down_count > compass.limit_up_count * 2 ? '恐慌风险释放' : '多空力量相持'}
            </div>
          </div>

          {/* 4. 成交基尼集中度 (Turnover Gini) */}
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="flex items-center justify-between text-2xs text-ink-muted">
              <span>成交额基尼集中度</span>
              <span className="font-mono text-ink-secondary">{compass.turnover_gini.toFixed(3)}</span>
            </div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="num text-2xl font-bold text-ink">{compass.turnover_gini.toFixed(3)}</span>
              <span className="text-xs text-ink-secondary">{giniDesc}</span>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${Math.min(100, Math.max(0, compass.turnover_gini * 100))}%` }}
              />
            </div>
            <div className="mt-2 text-3xs text-ink-muted">
              以成交额为权重测度微观流动性抱团（0 均衡，1 极端垄断）
            </div>
          </div>
        </div>

        {/* 5. 截面涨跌幅 8 分档直方图 (Breadth Bins) */}
        {compass.breadth_bins && compass.breadth_bins.length > 0 && (
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="mb-2 flex items-center justify-between text-2xs">
              <span className="font-medium text-ink-secondary">
                截面涨跌幅分布直方图（8 分位分箱统计）
              </span>
              <span className="text-3xs text-ink-muted">
                直观揭示全市场收益率横截面偏度与厚尾特征
              </span>
            </div>
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
              {compass.breadth_bins.map((b) => {
                const isPositive = b.bin.includes('+') || b.bin.startsWith('[0%') || b.bin.startsWith('>=');
                const binHeightPct = Math.max(6, (b.count / maxBinCount) * 100);
                const pctOfTotal = ((b.count / total) * 100).toFixed(1);

                return (
                  <div key={b.bin} className="flex flex-col items-center justify-end rounded border border-hair bg-surface p-1.5 text-center">
                    <span className="num text-xs font-semibold text-ink">
                      {b.count}
                    </span>
                    <span className="text-3xs text-ink-muted">
                      {pctOfTotal}%
                    </span>
                    <div className="my-1.5 flex h-14 w-full items-end justify-center rounded bg-surface-sunken/60 p-0.5">
                      <div
                        className={`w-full rounded-sm transition-all ${isPositive ? 'bg-up' : 'bg-down'}`}
                        style={{ height: `${binHeightPct}%` }}
                        title={`${b.bin}: ${b.count} 只 (${pctOfTotal}%)`}
                      />
                    </div>
                    <span className={`truncate text-3xs font-medium ${isPositive ? 'text-up' : 'text-down'}`}>
                      {b.bin}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
