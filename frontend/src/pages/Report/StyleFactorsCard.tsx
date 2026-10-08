import type { StyleFactorsData } from '@/api/monitor';
import { SectionCard } from '@/components/ui';

interface Props {
  data?: StyleFactorsData | null;
}

function fmtBps(ret: number | null | undefined): string {
  if (ret == null || !Number.isFinite(ret)) return '—';
  const bps = ret * 10000;
  const sign = bps > 0 ? '+' : '';
  return `${sign}${bps.toFixed(1)} bps`;
}

function fmtPct(ret: number | null | undefined): string {
  if (ret == null || !Number.isFinite(ret)) return '—';
  const pct = ret * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

function colorCls(n: number | null | undefined): string {
  if (n == null || Math.abs(n) < 1e-6) return 'text-ink-secondary';
  return n > 0 ? 'text-up' : 'text-down';
}

export function StyleFactorsCard({ data }: Props) {
  if (!data || (!data.styles?.length && !data.top_industries?.length)) {
    return null;
  }

  const { styles = [], top_industries = [], bottom_industries = [] } = data;

  return (
    <SectionCard
      title="Barra 风格因子每日全景与行业轮动透视"
      action={
        <span className="text-2xs text-ink-muted">
          口径：Barra 截面多空对冲组合收益 · 申万一级行业等权
        </span>
      }
    >
      <div className="space-y-4">
        {/* 1. Barra 风格因子多空表现 */}
        <div>
          <div className="mb-2 flex items-center justify-between text-2xs font-medium text-ink-secondary">
            <span>风格因子纯因子收益率 (Pure Factor Returns)</span>
            <span className="text-3xs text-ink-muted">基于全市场横截面标准化回归/多空组合</span>
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {styles.map((item) => (
              <div
                key={item.code}
                className="flex flex-col justify-between rounded-lg border border-hair bg-surface-alt p-3 transition-colors hover:border-hair-strong"
              >
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-ink">{item.name}</span>
                    <span className={`num text-xs font-bold ${colorCls(item.return)}`}>
                      {fmtBps(item.return)}
                    </span>
                  </div>
                  <div className="mt-1 text-3xs text-ink-muted">{item.description}</div>
                </div>
                <div className="mt-2.5 flex items-center gap-1.5">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-sunken">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(100, Math.max(10, Math.abs(item.return) * 10000 * 2))}%`,
                        backgroundColor: item.return >= 0 ? 'var(--up)' : 'var(--down)',
                      }}
                    />
                  </div>
                  <span className={`num text-3xs font-medium ${colorCls(item.return)}`}>
                    {fmtPct(item.return)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 2. 行业轮动双栏对照（前 3 领涨 vs 前 3 领跌） */}
        <div>
          <div className="mb-2 text-2xs font-medium text-ink-secondary">
            申万一级行业截面动量轮动
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {/* 领涨 */}
            <div className="overflow-hidden rounded-lg border border-hair">
              <div className="flex items-center justify-between border-b border-hair bg-surface-alt px-3 py-1.5 text-2xs font-semibold text-up">
                <span>▲ 领涨行业 Top 3</span>
                <span className="text-3xs text-ink-muted">超额动量聚集</span>
              </div>
              <div className="divide-y divide-hair bg-surface">
                {top_industries.map((ind, idx) => (
                  <div key={ind.industry} className="flex items-center justify-between px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="flex h-4 w-4 items-center justify-center rounded bg-up/10 text-3xs font-bold text-up">
                        {idx + 1}
                      </span>
                      <span className="font-medium text-ink">{ind.industry}</span>
                    </div>
                    <span className="num font-semibold text-up">
                      {fmtPct(ind.return)}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* 领跌 */}
            <div className="overflow-hidden rounded-lg border border-hair">
              <div className="flex items-center justify-between border-b border-hair bg-surface-alt px-3 py-1.5 text-2xs font-semibold text-down">
                <span>▼ 领跌行业 Bottom 3</span>
                <span className="text-3xs text-ink-muted">资金持续流出</span>
              </div>
              <div className="divide-y divide-hair bg-surface">
                {bottom_industries.map((ind, idx) => (
                  <div key={ind.industry} className="flex items-center justify-between px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="flex h-4 w-4 items-center justify-center rounded bg-down/10 text-3xs font-bold text-down">
                        {idx + 1}
                      </span>
                      <span className="font-medium text-ink">{ind.industry}</span>
                    </div>
                    <span className="num font-semibold text-down">
                      {fmtPct(ind.return)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="text-2xs text-ink-muted">
          {'ⓘ Barra 风格剖析：监测微观市场是否发生风格剧烈突变（如动量崩塌或微盘挤兑）。在风格高拥挤与快速轮动期，应降低高 Beta 个股敞口，加大风格中性化惩罚因子。'}
        </div>
      </div>
    </SectionCard>
  );
}
