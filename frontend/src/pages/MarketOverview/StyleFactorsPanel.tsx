/**
 * Barra 风格因子多空收益监控面板：
 *   展示 Size(规模), Momentum(动量), Volatility(低波), Liquidity(流动性), Value(价值)
 *   的当日横截面 Long-Short 多空组合收益率。
 *   A股量化常用：正值红色（多头超额），负值绿色（空头占优）。
 */
import { useMemo } from 'react';
import type { StyleFactorsBlock } from '@/types/stock';
import { fmtPct, pctClass } from '@/utils/format';

export default function StyleFactorsPanel({
  data,
  loading,
}: {
  data?: StyleFactorsBlock;
  loading: boolean;
}) {
  const items = data?.items ?? [];
  const ok = data?.status === 'ok';
  const maxAbs = useMemo(
    () => Math.max(...items.map((it) => Math.abs(it.ls_return)), 1.5),
    [items]
  );

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">Barra 风格因子收益</h2>
          <span className="text-2xs text-ink-muted">全市场截面 · 多空收益 (Long-Short)</span>
        </div>
        {data?.stat_date && (
          <span className="text-2xs text-ink-muted">统计日 {data.stat_date}</span>
        )}
      </div>

      <div className="min-w-0 flex-1 p-4">
        {ok && items.length > 0 ? (
          <div className="space-y-3">
            <div className="text-2xs text-ink-muted flex items-center justify-between border-b border-hair/50 pb-1.5">
              <span>因子名称 / 代码</span>
              <span>多空收益分布 (±{maxAbs.toFixed(1)}%)</span>
              <span>多空净超额</span>
            </div>

            {items.map((f) => {
              const isUp = f.ls_return >= 0;
              const barWidth = Math.min((Math.abs(f.ls_return) / maxAbs) * 50, 50);

              return (
                <div key={f.code} className="group rounded-md p-1.5 transition-colors hover:bg-surface-alt">
                  <div className="flex items-center justify-between gap-3">
                    <div className="w-36 shrink-0 min-w-0">
                      <div className="truncate text-xs font-medium text-ink">{f.name}</div>
                      <div className="truncate text-2xs text-ink-muted" title={f.desc}>{f.desc}</div>
                    </div>

                    {/* 双向发散柱 */}
                    <div className="relative flex-1 h-3 overflow-hidden rounded-sm bg-surface-sunken">
                      <div className="absolute left-1/2 top-0 h-full w-px bg-hair2 z-10" />
                      <div
                        className={`absolute top-0 h-full transition-all duration-300 ${isUp ? 'bg-up left-1/2 rounded-r-xs' : 'bg-down right-1/2 rounded-l-xs'}`}
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>

                    <div className="w-16 shrink-0 text-right">
                      <span className={`num text-xs font-semibold ${pctClass(f.ls_return)}`}>
                        {fmtPct(f.ls_return)}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}

            <div className="mt-2 rounded bg-surface-alt/60 p-2 text-2xs text-ink-muted flex items-center justify-between">
              <span>※ 多空收益 = 因子暴露前20%标的平均收益 - 暴露后20%标的平均收益</span>
              {data?.sample_size && <span>样本池: {data.sample_size} 只</span>}
            </div>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-3 py-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-6 w-full rounded bg-surface-sunken" />
            ))}
          </div>
        ) : (
          <div className="flex h-36 items-center justify-center text-xs text-ink-muted">
            {data?.reason ?? '风格因子数据计算中'}
          </div>
        )}
      </div>
    </div>
  );
}
