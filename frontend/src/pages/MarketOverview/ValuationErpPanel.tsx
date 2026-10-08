/**
 * 大盘估值与股债平衡 (ERP) 温度计面板：
 *   展示宽基指数 (沪深300 / 中证500 / 科创50) 历史 PE 百分位
 *   以及 Equity Risk Premium (ERP = 1/PE - 10Y国债收益率)。
 */
import type { ValuationErpBlock } from '@/types/stock';

export default function ValuationErpPanel({
  data,
  loading,
}: {
  data?: ValuationErpBlock;
  loading: boolean;
}) {
  const ok = data?.status === 'ok';
  const csi = data?.csi300;
  const erp = data?.erp;
  const indices = data?.indices ?? [];

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">大盘估值与股债平衡 (ERP)</h2>
          <span className="text-2xs text-ink-muted">宽基历史分位 · 宏观风险溢价</span>
        </div>
        {csi?.date && (
          <span className="text-2xs text-ink-muted">{csi.date}</span>
        )}
      </div>

      <div className="min-w-0 flex-1 p-4">
        {ok ? (
          <div className="space-y-3.5">
            {/* 上部：股债性价比 ERP 核心指示器 */}
            <div className="rounded-lg border border-hair bg-surface-alt/50 p-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs text-ink-secondary">股债风险溢价 (ERP)</div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="num text-xl font-bold text-ink">
                      {erp?.val != null ? `${erp.val.toFixed(2)}%` : '—'}
                    </span>
                    <span className="text-2xs text-ink-muted">
                      (10Y国债 {erp?.bond_yield ?? 2.15}%)
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <span className={`inline-block rounded px-2 py-0.5 text-2xs font-semibold ${
                    (erp?.val ?? 0) > 1.5 ? 'bg-up-bg text-up' :
                    (erp?.val ?? 0) > 0.5 ? 'bg-warn-bg text-warn' : 'bg-down-bg text-down'
                  }`}>
                    {erp?.zone ?? '中性区间'}
                  </span>
                  <div className="mt-1 text-2xs text-ink-muted">
                    ERP = 1/PE - 10Y利率
                  </div>
                </div>
              </div>

              {/* 进度刻度条 */}
              <div className="mt-2.5">
                <div className="relative h-2 w-full overflow-hidden rounded-full bg-surface-sunken">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-emerald-500 via-amber-400 to-rose-500 transition-all duration-300"
                    style={{ width: `${Math.min(Math.max(((erp?.val ?? 3) / 7) * 100, 10), 100)}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-ink-muted">
                  <span>防御 (高估)</span>
                  <span>中枢均衡</span>
                  <span>高赔率 (低估)</span>
                </div>
              </div>
            </div>

            {/* 下部：三宽基指数历史 PE 百分位柱 */}
            <div className="grid grid-cols-3 gap-2">
              {indices.map((idx) => {
                const pct = idx.percentile;
                const zoneColor =
                  pct < 30 ? 'text-up bg-up-bg' :
                  pct < 70 ? 'text-brand-600 bg-brand-50 dark:bg-brand-900/30 dark:text-brand-300' :
                  'text-down bg-down-bg';

                return (
                  <div key={idx.code} className="rounded-md border border-hair bg-surface p-2.5 text-center">
                    <div className="truncate text-xs font-medium text-ink">{idx.name}</div>
                    <div className="num mt-1 text-sm font-semibold text-ink">
                      {idx.pe?.toFixed(1) ?? '—'} <span className="text-2xs text-ink-muted font-normal">PE</span>
                    </div>
                    <div className="mt-1 text-2xs text-ink-muted">
                      分位 {pct?.toFixed(0)}%
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                      <div
                        className="h-full rounded-full bg-brand-500"
                        style={{ width: `${Math.min(pct, 100)}%` }}
                      />
                    </div>
                    <span className={`mt-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${zoneColor}`}>
                      {idx.zone}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-3 py-2">
            <div className="h-16 w-full rounded bg-surface-sunken" />
            <div className="grid grid-cols-3 gap-2">
              <div className="h-20 rounded bg-surface-sunken" />
              <div className="h-20 rounded bg-surface-sunken" />
              <div className="h-20 rounded bg-surface-sunken" />
            </div>
          </div>
        ) : (
          <div className="flex h-36 items-center justify-center text-xs text-ink-muted">
            {data?.reason ?? '估值与 ERP 数据暂不可用'}
          </div>
        )}
      </div>
    </div>
  );
}
