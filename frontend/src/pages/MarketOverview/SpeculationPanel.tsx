/**
 * 微观流动性与投机情绪面板：
 *   展示连板高度天梯、炸板率、昨日涨停溢价率、短线情绪多日演化趋势图以及情绪周期研判罗盘。
 */
import { useMemo } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
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
  const theme = useTheme();
  const ok = speculation?.status === 'ok';
  const ladder = speculation?.ladder;
  const brokenRate = speculation?.broken_board_rate;
  const premium = speculation?.yesterday_zt_premium;
  const score = sentiment?.score ?? null;
  const history = speculation?.history ?? [];
  const cyclePhase = speculation?.cycle_phase;
  const sealSuccess = speculation?.seal_success_rate;
  const promRate = speculation?.promotion_rate_1_to_2;

  // 短线情绪近多日演化趋势图 (涨停/炸板柱状 + 昨日溢价折线双轴图)
  const trendOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!history || history.length < 2) return null;
    const p = chartPalette();
    const dates = history.map((h) => (h.date.length > 5 ? h.date.slice(5) : h.date));
    const ztCounts = history.map((h) => h.limit_up_count);
    const brokenCounts = history.map((h) => h.broken_board_count);
    const premiums = history.map((h) => h.yesterday_zt_premium);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const list = params as Array<{
            seriesName: string;
            value: number;
            dataIndex: number;
            color: string;
          }>;
          if (!list || !list.length) return '';
          const idx = list[0].dataIndex;
          const h = history[idx];
          return `
            <div class="font-medium text-xs mb-1">${h.date} 短线生态</div>
            <div class="text-2xs space-y-0.5">
              <div class="flex justify-between gap-3">
                <span class="text-ink-muted">涨停家数:</span>
                <b class="t-up">${h.limit_up_count} 只</b>
              </div>
              <div class="flex justify-between gap-3">
                <span class="text-ink-muted">炸板家数:</span>
                <b class="text-ink-secondary">${h.broken_board_count} 只</b>
              </div>
              <div class="flex justify-between gap-3">
                <span class="text-ink-muted">封板失败率:</span>
                <b>${h.broken_board_rate.toFixed(1)}%</b>
              </div>
              <div class="flex justify-between gap-3">
                <span class="text-ink-muted">昨日涨停今日溢价:</span>
                <b class="${pctClass(h.yesterday_zt_premium)}">${fmtPct(h.yesterday_zt_premium)}</b>
              </div>
            </div>
          `;
        },
      },
      grid: {
        left: 28,
        right: 36,
        top: 24,
        bottom: 20,
      },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: p.SUNKEN } },
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: [
        {
          type: 'value',
          name: '家数',
          nameTextStyle: { fontSize: 8, color: p.INKM, padding: [0, 0, 2, -10] },
          splitNumber: 3,
          axisLabel: { fontSize: 8, color: p.INKM },
          splitLine: { lineStyle: { color: p.SUNKEN, type: 'dashed' } },
        },
        {
          type: 'value',
          name: '溢价%',
          nameTextStyle: { fontSize: 8, color: p.INKM, padding: [0, -10, 2, 0] },
          splitNumber: 3,
          axisLabel: {
            fontSize: 8,
            color: p.INKM,
            formatter: '{value}%',
          },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '涨停家数',
          type: 'bar',
          data: ztCounts,
          barWidth: 8,
          itemStyle: {
            color: p.UP,
            borderRadius: [2, 2, 0, 0],
          },
        },
        {
          name: '炸板家数',
          type: 'bar',
          data: brokenCounts,
          barWidth: 8,
          itemStyle: {
            color: withAlpha(p.FLAT, 0.65),
            borderRadius: [2, 2, 0, 0],
          },
        },
        {
          name: '次日溢价率',
          type: 'line',
          yAxisIndex: 1,
          data: premiums,
          symbolSize: 4,
          itemStyle: { color: p.BRAND },
          lineStyle: { width: 1.8, color: p.BRAND },
        },
      ],
    };
  }, [history, theme]);

  const trendChartRef = useChart(trendOption);

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

      <div className="min-w-0 flex-1 p-4 flex flex-col justify-between">
        {ok ? (
          <div className="space-y-3">
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

            {/* 中栏：连板高度天梯与晋级率 */}
            <div className="rounded-lg border border-hair bg-surface p-2.5">
              <div className="mb-2 text-2xs font-medium text-ink-secondary flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <span>连板梯队分布 (Limit-Up Ladder)</span>
                  {promRate != null && (
                    <span className="rounded bg-brand-50 px-1 py-0.2 text-3xs font-medium text-brand-600 border border-brand-200">
                      1进2晋级率 {promRate}%
                    </span>
                  )}
                  {sealSuccess != null && (
                    <span className="rounded bg-slate-100 px-1 py-0.2 text-3xs font-medium text-ink-secondary border border-hair">
                      封板成率 {sealSuccess}%
                    </span>
                  )}
                </div>
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

            {/* 新增功能：短线情绪近多日演化趋势走势图 (双轴柱线复合) */}
            <div className="rounded-lg border border-hair bg-surface-alt/30 p-2.5">
              <div className="flex items-center justify-between text-2xs font-medium text-ink mb-1">
                <div className="flex items-center gap-1.5">
                  <span>短线投机情绪多日演化趋势</span>
                  <span className="text-ink-muted text-3xs">（近 {history.length || 7} 个交易日）</span>
                </div>
                <div className="flex items-center gap-3 text-3xs text-ink-muted">
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-up" />
                    涨停家数
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                    炸板家数
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />
                    次日溢价%
                  </span>
                </div>
              </div>

              {trendOption ? (
                <div className="relative w-full">
                  <div ref={trendChartRef} style={{ height: 130 }} className="w-full" />
                </div>
              ) : (
                <div className="py-8 text-center text-2xs text-ink-muted">
                  多日情绪历史样本整理中…
                </div>
              )}
            </div>

            {/* 底栏：情绪周期客观研判与量化定位罗盘 */}
            <div className="rounded-md bg-surface-alt/60 px-3 py-2 text-xs flex flex-wrap items-center justify-between gap-2 border border-hair">
              <div className="flex items-center gap-2">
                {cyclePhase && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-2xs font-semibold border ${
                      cyclePhase.phase.includes('发酵') || cyclePhase.phase.includes('进攻')
                        ? 'bg-up-bg text-up border-up/30'
                        : cyclePhase.phase.includes('退潮')
                        ? 'bg-down-bg text-down border-down/30'
                        : cyclePhase.phase.includes('冰点')
                        ? 'bg-blue-50 text-blue-600 border-blue-200'
                        : 'bg-warn-bg text-warn border-warn/30'
                    }`}
                  >
                    {cyclePhase.phase}
                  </span>
                )}
                <span className="text-2xs text-ink-secondary truncate max-w-[280px]" title={cyclePhase?.signal}>
                  {cyclePhase?.signal ?? '连板梯队推进，关注主线前排'}
                </span>
              </div>

              {score != null && (
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-2xs text-ink-muted">市场情绪:</span>
                  <span className="num font-semibold text-ink">{score}</span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-2xs font-medium ${
                      score >= 60
                        ? 'bg-up-bg text-up'
                        : score >= 40
                        ? 'bg-surface-sunken text-ink-muted'
                        : 'bg-down-bg text-down'
                    }`}
                  >
                    {sentiment?.label ?? '中性'}
                  </span>
                </div>
              )}
            </div>
          </div>
        ) : loading ? (
          <div className="animate-pulse space-y-3 py-2">
            <div className="grid grid-cols-3 gap-2">
              <div className="h-16 rounded bg-surface-sunken" />
              <div className="h-16 rounded bg-surface-sunken" />
              <div className="h-16 rounded bg-surface-sunken" />
            </div>
            <div className="h-24 rounded bg-surface-sunken" />
            <div className="h-28 rounded bg-surface-sunken" />
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
