/**
 * 微观流动性与投机情绪面板：
 *   展示连板高度天梯、炸板率、昨日涨停溢价率以及市场综合情绪。
 */
import type { SpeculationBlock, SentimentBlock } from '@/types/stock';
import { fmtPct, pctClass } from '@/utils/format';

export default function SpeculationPanel({
  speculation,
  sentiment,
  loading,
}: {
  speculation?: SpeculationBlock;
  sentiment?: SentimentBlock;
  loading: boolean;
}) {
  const ok = speculation?.status === 'ok';
  const ladder = speculation?.ladder;
  const brokenRate = speculation?.broken_board_rate;
  const premium = speculation?.yesterday_zt_premium;
  const score = sentiment?.score ?? null;

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">微观投机生态与流动性</h2>
          <span className="text-2xs text-ink-muted">连板天梯 · 炸板率 · 溢价率</span>
        </div>
        {speculation?.stat_date && (
          <span className="text-2xs text-ink-muted">统计日 {speculation.stat_date}</span>
        )}
      </div>

      <div className="min-w-0 flex-1 p-4">
        {ok ? (
          <div className="space-y-3.5">
            {/* 顶栏：三大微观流动性指标卡 */}
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-md border border-hair bg-surface-alt/40 p-2 text-center">
                <div className="text-2xs text-ink-muted">今日涨停 / 触板</div>
                <div className="num mt-1 text-sm font-bold t-up">
                  {speculation?.limit_up_count ?? '—'} <span className="text-2xs font-normal text-ink-muted">只</span>
                </div>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  触及 {speculation ? (speculation.limit_up_count ?? 0) + (speculation.broken_board_count ?? 0) : '—'}
                </div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt/40 p-2 text-center">
                <div className="text-2xs text-ink-muted">封板失败 (炸板率)</div>
                <div className={`num mt-1 text-sm font-bold ${(brokenRate ?? 0) > 40 ? 'text-down' : (brokenRate ?? 0) > 25 ? 'text-warn' : 't-up'}`}>
                  {brokenRate != null ? `${brokenRate.toFixed(1)}%` : '—'}
                </div>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  炸板 {speculation?.broken_board_count ?? '—'} 只
                </div>
              </div>

              <div className="rounded-md border border-hair bg-surface-alt/40 p-2 text-center">
                <div className="text-2xs text-ink-muted">昨日涨停今日溢价</div>
                <div className={`num mt-1 text-sm font-bold ${premium != null ? pctClass(premium) : 'text-ink'}`}>
                  {premium != null ? fmtPct(premium) : '—'}
                </div>
                <div className="mt-0.5 text-2xs text-ink-muted">
                  赚钱效应锚
                </div>
              </div>
            </div>

            {/* 中栏：连板高度天梯 */}
            <div className="rounded-lg border border-hair bg-surface p-2.5">
              <div className="mb-2 text-2xs font-medium text-ink-secondary flex items-center justify-between">
                <span>连板梯队分布 (Limit-Up Ladder)</span>
                <span className="text-ink-muted">短线情绪接力</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded bg-surface-sunken p-1.5">
                  <div className="text-2xs text-ink-muted">首板进阶</div>
                  <div className="num mt-0.5 text-xs font-semibold text-ink">{ladder?.first_board ?? 0} 只</div>
                </div>
                <div className="rounded bg-surface-sunken p-1.5">
                  <div className="text-2xs text-ink-muted">二板接力</div>
                  <div className="num mt-0.5 text-xs font-semibold text-ink">{ladder?.second_board ?? 0} 只</div>
                </div>
                <div className="rounded bg-surface-sunken p-1.5">
                  <div className="text-2xs text-ink-muted">高标连板 (3板+)</div>
                  <div className="num mt-0.5 text-xs font-semibold t-up">{ladder?.high_board ?? 0} 只</div>
                </div>
              </div>
            </div>

            {/* 底栏：情绪综合状态 */}
            {score != null && (
              <div className="rounded-md bg-surface-alt/60 px-3 py-2 text-xs flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-2xs text-ink-muted">市场情绪:</span>
                  <span className="num font-semibold text-ink">{score}</span>
                  <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${
                    score >= 60 ? 'bg-up-bg text-up' : score >= 40 ? 'bg-surface-sunken text-ink-muted' : 'bg-down-bg text-down'
                  }`}>
                    {sentiment?.label ?? '中性'}
                  </span>
                </div>
                <div className="text-2xs text-ink-muted">
                  确定性加权指标
                </div>
              </div>
            )}
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-3 py-2">
            <div className="grid grid-cols-3 gap-2">
              <div className="h-16 rounded bg-surface-sunken" />
              <div className="h-16 rounded bg-surface-sunken" />
              <div className="h-16 rounded bg-surface-sunken" />
            </div>
            <div className="h-16 rounded bg-surface-sunken" />
          </div>
        ) : (
          <div className="flex h-36 items-center justify-center text-xs text-ink-muted">
            {speculation?.reason ?? '短线投机流动性数据暂不可用'}
          </div>
        )}
      </div>
    </div>
  );
}
