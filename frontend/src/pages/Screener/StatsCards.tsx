/**
 * 选股结果概览卡（6 张），数据来自后端 /screener 的 stats 块 + KPI 历史序列。
 *
 * 视觉对齐 ETF 中心 OverviewCards（模板标准）：大卡 px-4 py-3.5、text-2xl 主值、
 * 右侧图形（真实趋势 / 真实构成环，内联 SVG 不引 ECharts）、hint ⓘ 口径提示。
 * 「较昨日」对比行由后端用前一交易日榜单同口径计算，前端不做任何编造；
 * prev 为 null 时该行显示「较昨日 —」占位。
 *
 * ## 本轮改造：删掉"假图形"
 * 改造前有 3 张卡是**用图形冒充信息**：`MiniLine values={[prev, today]}`（只有两个点，
 * 看似趋势实则不含趋势信息；`prev == null` 时还会画出一条贴底平线）、
 * `MiniBar values={[highRatio]}`（单根柱子没有任何比较语义）。
 * 现在：
 * - **百分比类**（今日胜率 / 平均涨跌幅 / 平均 Score）改接 `/screener/stats/series`
 *   的**真实序列**（后端实测各 29 个交易日），绘制开关交给 `Sparkline`；
 * - **计数类**（股票数量 / 强信号数量 / 覆盖行业）**不画趋势** —— 后端已把
 *   `pool_size` / `strong_signal` / `industry_count` 标为 `comparable=false`：
 *   窗口内预测覆盖度从 4.8% 变到 97.5%，序列起伏主要由**数据补全进度**驱动，
 *   画成趋势即误读。这三张卡改用**真实构成环**（分段全部来自真实计数）。
 */
import { useMemo } from 'react';
import type { ScreenerItem, ScreenerSeriesEnvelope } from '@/types/p1';
import Sparkline from '@/components/charts/Sparkline';
import { Donut, KpiCard, KpiCompare } from '@/components/charts/KpiBits';

export interface StatsDay {
  /** 榜单截断后的条目数（= top_k 上限，非股票池总数） */
  total: number;
  /** 股票池总数：universe 过滤 ST/停牌 + 板块筛选后、top_k 截断前的标的数 */
  pool_size: number;
  win_rate: number | null;
  avg_pct: number | null;
  avg_score: number | null;
  strong_signal: number;
  industry_count: number;
  /** 最大行业占比 %（行业集中度，环形图语义） */
  top_industry_ratio: number | null;
}

export interface StatsBlock {
  today: StatsDay;
  prev: StatsDay | null;
  prev_date: string | null;
}

/** 计数类卡片不画趋势的静态口径说明（挂在 hint 上，避免"没图"被当成缺数据） */
const COUNT_NO_TREND =
  '计数类序列不做趋势绘制：窗口内预测覆盖度变化大（后端标 comparable=false），'
  + '起伏由数据补全进度驱动，不是市场变化';

/* ---------------- 主组件 ---------------- */
export default function StatsCards({ stats, series, items }: {
  stats: StatsBlock | null;
  /** `/screener/stats/series` 序列信封（未取到为 null ⇒ 趋势位显示「暂无历史序列」） */
  series?: ScreenerSeriesEnvelope | null;
  /**
   * 当前**榜单原始条目**（后端 top_k 结果，未经页面客户端筛选/排序）。
   *
   * ⚠️ 必须是"原始榜单"而不是页面筛选后的 `items`：卡片数值（`stats`）来自后端
   * 对同一份 top_k 榜单的统计，若构成环改用筛选后的子集，两者口径就不一致了。
   */
  items: ScreenerItem[];
}) {
  const t = stats?.today;
  const p = stats?.prev ?? null;

  /**
   * 强信号构成环：按榜单内 `signal_strength` 分档的**真实计数**（strong / neutral / weak）。
   * 拿不到榜单条目时返回 undefined ⇒ 不画（宁可空着，也不用写死比例凑一个环）。
   */
  const signalRing = useMemo<Array<[number, string]> | undefined>(() => {
    if (!items.length) return undefined;
    let strong = 0, neutral = 0, weak = 0;
    for (const it of items) {
      if (it.signal_strength === 'strong') strong += 1;
      else if (it.signal_strength === 'neutral') neutral += 1;
      else weak += 1;
    }
    return [[strong, '#F87171'], [neutral, '#FBBF24'], [weak, '#E2E8F0']];
  }, [items]);

  if (!t) {
    return (
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[96px] animate-pulse rounded-lg border border-hair bg-white" />
        ))}
      </div>
    );
  }

  // 「股票数量」与「较昨日」对比基于 pool_size（截断前的股票池数），
  // 而非 total（= top_k 截断后的榜单条目数，恒为 top_k 上限）。
  const dPool = p ? t.pool_size - p.pool_size : null;
  const dWin = p && t.win_rate != null && p.win_rate != null ? t.win_rate - p.win_rate : null;
  const dPct = p && t.avg_pct != null && p.avg_pct != null ? t.avg_pct - p.avg_pct : null;
  const dScore = p && t.avg_score != null && p.avg_score != null ? t.avg_score - p.avg_score : null;
  const dInd = p ? t.industry_count - p.industry_count : null;

  // 强信号占比：分母用 pool_size（更真实的池子规模）。
  const highRatio = t.pool_size ? (t.strong_signal / t.pool_size) * 100 : 0;

  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
      {/* 股票数量（当前股票池，top_k 截断前的标的数） */}
      <KpiCard label="股票数量" value={t.pool_size.toLocaleString('zh-CN')} unit="只"
        hint={`当日有预测快照、进入 Alpha 榜前（top_k 截断前）的标的数；已剔除 ST/停牌，按当前板块筛选；`
          + `环形 = 榜单已覆盖 ${t.total} 只 / 池内未入选 ${Math.max(t.pool_size - t.total, 0)} 只（真实计数）；`
          + COUNT_NO_TREND}
        compare={<KpiCompare delta={dPool} unit="" digits={0} />}
        chart={<Donut segments={[[t.total, '#2563EB'], [Math.max(t.pool_size - t.total, 0), '#E2E8F0']]} />} />

      {/* 今日胜率：真实序列（后端 29 个交易日可复算） */}
      {/* ⚠️ win_rate 可为 null（value 已显 '—'）：不得写 `win_rate != null && >= 50 ? t-up : t-down`
          —— null 会落进 t-down（绿），替"未知"表态。口径与同屏「平均涨跌幅」卡一致：null ⇒ undefined（中性）。 */}
      <KpiCard label="今日胜率"
        value={t.win_rate != null ? t.win_rate.toFixed(2) : '—'} unit="%"
        tone={t.win_rate == null ? undefined : t.win_rate >= 50 ? 't-up' : 't-down'}
        hint="榜单内标的当日实际收盘上涨的比例（A股口径：红涨绿跌）；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dWin} unit="%" />}
        chart={<Sparkline series={series?.metrics.win_rate} color="#DC2626" label="今日胜率" />} />

      {/* 平均涨跌幅：真实序列 */}
      <KpiCard label="平均涨跌幅"
        value={t.avg_pct != null ? `${t.avg_pct >= 0 ? '+' : ''}${t.avg_pct.toFixed(2)}` : '—'} unit="%"
        tone={t.avg_pct == null ? undefined : t.avg_pct >= 0 ? 't-up' : 't-down'}
        hint="榜单内标的当日涨跌幅等权平均；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dPct} unit="%" />}
        chart={<Sparkline series={series?.metrics.avg_pct} color="#DC2626" label="平均涨跌幅" />} />

      {/* 平均 Score：真实序列 */}
      <KpiCard label="平均 Score"
        value={t.avg_score != null ? t.avg_score.toFixed(4) : '—'}
        hint="alpha_basic_v1 模型预测的未来收益（小数口径，×100 为百分比预期）；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dScore} digits={4} />}
        chart={<Sparkline series={series?.metrics.avg_score} color="#2563EB" label="平均 Score" />} />

      {/* 强信号数量：构成环（真实计数，非趋势） */}
      <KpiCard label="强信号数量"
        value={<>{t.strong_signal}<span className="text-base font-normal text-ink-muted"> / {t.pool_size}</span></>}
        hint={`strong（模型预测强度最强）的标的数，反映模型预测强度，不代表投资风险；分母为股票池总数（pool_size）；`
          + `环形 = 榜单内 strong / neutral / weak 的真实档位构成；${COUNT_NO_TREND}`}
        compare={<span>占比 <span className="num font-medium t-up">{highRatio.toFixed(0)}%</span></span>}
        chart={signalRing && signalRing.some(([v]) => v > 0)
          ? <Donut segments={signalRing} />
          : undefined} />

      {/* 覆盖行业：构成环（真实占比，非趋势） */}
      <KpiCard label="覆盖行业"
        value={t.industry_count.toLocaleString('zh-CN')} unit="个"
        hint={`环形图为最大行业的集中度（${t.top_industry_ratio != null ? `${t.top_industry_ratio.toFixed(0)}%` : '—'}）；`
          + COUNT_NO_TREND}
        compare={<KpiCompare delta={dInd} digits={0} />}
        chart={<Donut segments={t.top_industry_ratio != null
          ? [[t.top_industry_ratio, '#10B981'], [100 - t.top_industry_ratio, '#E2E8F0']]
          : []} />} />
    </div>
  );
}
