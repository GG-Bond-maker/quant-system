/**
 * 选股结果概览卡（6 张），数据来自后端 /screener 的 stats 块 + KPI 历史序列。
 *
 * 视觉对齐 ETF 中心 OverviewCards（模板标准）：大卡 px-4 py-3.5、text-2xl 主值、
 * 右侧图形（真实趋势 / 真实构成环，内联 SVG 不引 ECharts）、hint ⓘ 口径提示。
 *
 * ## 对比行（本轮修正，2026-10-03）
 * 后端 `stats.prev_date` 是「上一份**可用预测快照**的日期」，**不一定是昨日** ——
 * 实测 2026-09-30 的对比日是 2026-09-17（相隔 13 天）。此前把标签写死成「较昨日」
 * 是**陈述错误**。现在：
 * - 标签一律用真实对比日（`对比 2026-09-17`），经 `KpiCompare.prevLabel` 传入；
 * - **计数类**（股票数量 / 覆盖行业）**不展示差值数字**：池子规模由「当日有预测快照的
 *   标的数」决定（2,431 → 5,000），后端已标 `comparable=false`，展示差值即误读；
 * - **榜单内百分比类**（今日胜率 / 平均涨跌幅 / 平均 Score）两日同为 `total` 条，
 *   口径一致，保留差值。
 * `prev_date` 为空（首份快照）时 `prev` 也为 null，标签退化为「无可比日」。
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
import type { ScreenerItem, ScreenerResult, ScreenerSeriesEnvelope } from '@/types/p1';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
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
export default function StatsCards({ stats, series, items, universe }: {
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
  /**
   * `/screener` 的 `universe_filter` 披露块（P1-34）。
   *
   * 「股票数量」卡的主值是**过滤后**的股票池（5,000），比全市场小；只显示 5,000
   * 会让人以为这就是全市场。这里把 universe 快照的**全市场在册证券数**并列出来，
   * 但**不断言**差额等于 ST/停牌数（差额里还混着"当日无预测"的标的，见
   * `types/p1.ts` 的字段注释）。传 null / 缺字段 ⇒ 不并列，并在 hint 里如实披露
   * 「ST/停牌过滤未生效」。
   */
  universe?: ScreenerResult['universe_filter'] | null;
}) {
  // 订阅主题：下方 chartPalette() 内联调用（pal / signalRing）
  useTheme();
  const t = stats?.today;
  const p = stats?.prev ?? null;
  const pal = chartPalette();

  /**
   * 强信号构成环：按榜单内 `signal_strength` 分档的**真实计数**（strong / neutral / weak）。
   * 拿不到榜单条目时返回 undefined ⇒ 不画（宁可空着，也不用写死比例凑一个环）。
   */
  const signalRing = useMemo<Array<[number, string]> | undefined>(() => {
    if (!items.length) return undefined;
    const p = chartPalette();
    let strong = 0, neutral = 0, weak = 0;
    for (const it of items) {
      if (it.signal_strength === 'strong') strong += 1;
      else if (it.signal_strength === 'neutral') neutral += 1;
      else weak += 1;
    }
    // strong/neutral/weak 是**信号强度档位**（类别），非涨跌语义：
    // 用系统状态色 DANGER/WARN/SUNKEN（与涨跌正交），避免把"强信号"说成"上涨"。
    return [[strong, p.DANGER], [neutral, p.WARN], [weak, p.SUNKEN]];
  }, [items]);

  if (!t) {
    return (
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-[96px] animate-pulse rounded-lg border border-hair bg-surface" />
        ))}
      </div>
    );
  }

  /**
   * 对比基准的**真实名称**（不写死「较昨日」）。
   *
   * 后端 `prev_date` 是「上一份可用预测快照的日期」，实测 2026-09-30 的对比日是
   * 2026-09-17（相隔 13 天）。`prev_date` 为空（首份快照）时 `prev` 也为 null、
   * 差值为 null，此时只能说「无可比日」。
   */
  const prevLabel = stats?.prev_date ? `对比 ${stats.prev_date}` : '无可比日';

  /**
   * 计数类指标"不可比"的原因（挂在 title 上，**不渲染成差值数字**）。
   * `pool_size` 由「当日有预测快照的标的数」决定，数据补全进度一变它就从
   * 2,431 跳到 5,000（后端已把这三项标 `comparable=false`）。
   */
  const countNote = p
    ? `前一份快照（${stats?.prev_date ?? '—'}）股票池 ${p.pool_size.toLocaleString('zh-CN')} 只，`
      + `当日 ${t.pool_size.toLocaleString('zh-CN')} 只；差值由「当日有预测快照的标的数」`
      + `（数据补全进度）驱动，不是市场变化，故不展示差值`
    : '无前一份快照，无可比日';

  /**
   * 计数卡是否"可比"。有对比日时**不可比**（池子规模由「当日有预测快照的标的数」
   * 驱动，见 `countNote`）；**没有**对比日时不存在口径冲突，只该显示「无可比日 —」，
   * 若仍传 `comparable=false` 会凭空多出一句「（口径不同，不可比）」——那是假陈述。
   */
  const countComparable = p == null;

  /** 全市场在册证券数（universe 快照行数，含 ST/停牌）；未真正过滤时为 null */
  const uniRows = universe && universe.applied && universe.rows > 0 ? universe.rows : null;

  /**
   * 全市场披露文案（口径必须与事实一致）。
   *
   * 两种降级要分开说 —— 后端 `universe_reason` 也是分开的：
   * - 快照**在**但缺关键列（`universe_schema_incomplete`）⇒ 过滤只做了一部分；
   * - 快照**不在**（`universe_partition_missing` / `universe_date_absent`）⇒ 完全没过滤。
   * 混成一句「未取到快照」会把前者说错（快照其实在，只是列不全）。
   */
  const uniHint = uniRows != null
    ? `当日有预测快照、进入 Alpha 榜前（top_k 截断前）的标的数；已剔除 ST/停牌、按当前板块筛选；`
      + `全市场（universe 快照）${uniRows.toLocaleString('zh-CN')} 只 —— 差额含 ST/停牌`
      + `以及当日无预测快照的标的，不全是 ST/停牌；`
    : `⚠️ ST/停牌过滤未生效：`
      + (universe && universe.rows > 0
        ? `universe 快照有 ${universe.rows.toLocaleString('zh-CN')} 只但缺关键列`
        : '未取到当日 universe 快照')
      + `${universe?.reason ? `（${universe.reason}）` : ''}，池内可能含 ST/停牌标的；`;

  // 榜单内百分比类：两日同为 total 条，口径一致，差值可比。
  const dWin = p && t.win_rate != null && p.win_rate != null ? t.win_rate - p.win_rate : null;
  const dPct = p && t.avg_pct != null && p.avg_pct != null ? t.avg_pct - p.avg_pct : null;
  const dScore = p && t.avg_score != null && p.avg_score != null ? t.avg_score - p.avg_score : null;

  /**
   * 「强信号占比」：**分子分母必须同口径**。
   *
   * 后端 `compute_stats` 的 `strong_signal` 只在 `items`（= `total` 条榜单）上统计，
   * 而 `pool_size` 是**截断前**的股票池 —— 用 pool_size 做分母是跨口径：
   * 实测 2026-09-30 `strong_signal=40 / pool_size=5,000` ⇒ 0.8%，而真实口径是
   * `40 / total=50` ⇒ 80%。且 0.8% 经 `toFixed(0)` 会显示成「1%」（池子更大时
   * 直接显示「0%」），把「榜单里 80% 是强信号」说成「全市场不到 1% 是强信号」。
   */
  const strongRatio = t.total ? (t.strong_signal / t.total) * 100 : null;
  /** 占比文案：正数一律不得被四舍五入成 0% */
  const strongRatioText = strongRatio == null
    ? '—'
    : strongRatio > 0 && strongRatio < 1 ? '< 1%' : `${strongRatio.toFixed(0)}%`;

  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
      {/* 股票数量（当前股票池，top_k 截断前的标的数） */}
      {/* 主值 5,000 是**过滤后**的池子，比全市场小 —— 旁边并列 universe 快照的全市场数，
          否则用户会以为这就是全市场（本次改造的起因）。差额**不断言**等于 ST/停牌数：
          后端只给了全市场行数，池子还额外丢掉了"当日无预测"的标的。 */}
      <KpiCard label="股票数量"
        value={<>
          {t.pool_size.toLocaleString('zh-CN')}
          <span className="text-base font-normal text-ink-muted">
            {uniRows != null ? ` / 全市场 ${uniRows.toLocaleString('zh-CN')} 只` : ' 只'}
          </span>
        </>}
        hint={uniHint
          + `环形 = 榜单已覆盖 ${t.total} 只 / 池内未入选 ${Math.max(t.pool_size - t.total, 0)} 只（真实计数）；`
          + COUNT_NO_TREND}
        compare={<KpiCompare delta={null} comparable={countComparable} prevLabel={prevLabel} note={countNote} />}
        chart={<Donut segments={[[t.total, pal.BRAND], [Math.max(t.pool_size - t.total, 0), pal.SUNKEN]]} />} />

      {/* 今日胜率：真实序列（后端 29 个交易日可复算） */}
      {/* ⚠️ win_rate 可为 null（value 已显 '—'）：不得写 `win_rate != null && >= 50 ? t-up : t-down`
          —— null 会落进 t-down（绿），替"未知"表态。口径与同屏「平均涨跌幅」卡一致：null ⇒ undefined（中性）。 */}
      <KpiCard label="今日胜率"
        value={t.win_rate != null ? t.win_rate.toFixed(2) : '—'} unit="%"
        tone={t.win_rate == null ? undefined : t.win_rate >= 50 ? 't-up' : 't-down'}
        hint="榜单内标的当日实际收盘上涨的比例（A股口径：红涨绿跌）；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dWin} unit="%" prevLabel={prevLabel} />}
        chart={<Sparkline series={series?.metrics.win_rate} color={pal.UP} label="今日胜率" />} />

      {/* 平均涨跌幅：真实序列 */}
      <KpiCard label="平均涨跌幅"
        value={t.avg_pct != null ? `${t.avg_pct >= 0 ? '+' : ''}${t.avg_pct.toFixed(2)}` : '—'} unit="%"
        tone={t.avg_pct == null ? undefined : t.avg_pct >= 0 ? 't-up' : 't-down'}
        hint="榜单内标的当日涨跌幅等权平均；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dPct} unit="%" prevLabel={prevLabel} />}
        chart={<Sparkline series={series?.metrics.avg_pct} color={pal.UP} label="平均涨跌幅" />} />

      {/* 平均 Score：真实序列 */}
      <KpiCard label="平均 Score"
        value={t.avg_score != null ? t.avg_score.toFixed(4) : '—'}
        hint="alpha_basic_v1 模型预测的未来收益（小数口径，×100 为百分比预期）；右侧为近 30 个预测交易日的同口径序列"
        compare={<KpiCompare delta={dScore} digits={4} prevLabel={prevLabel} />}
        chart={<Sparkline series={series?.metrics.avg_score} color={pal.BRAND} label="平均 Score" />} />

      {/* 强信号数量：构成环（真实计数，非趋势） */}
      {/* 分母必须是**榜单条目数 total**：strong_signal 的统计面就是 items（后端
          `compute_stats` 逐条统计 signal_strength），用 pool_size 做分母是跨口径。
          占比文字用中性色 —— 它是"档位构成"，不是价格方向，不得染涨跌色。 */}
      <KpiCard label="强信号数量"
        value={<>{t.strong_signal}<span className="text-base font-normal text-ink-muted"> / {t.total}</span></>}
        hint={`strong 档 = pred_score 名次落在参考总体前 20%（参考深度 200 只）的标的，`
          + `属相对分位、非绝对分数阈值，不代表投资风险；`
          + `分母为当前榜单条目数（top_k 截断后 = ${t.total}），不是股票池；`
          + `环形 = 榜单内 strong / neutral / weak 的真实档位构成；${COUNT_NO_TREND}`}
        compare={<span>榜单内占比{' '}
          <span className="num font-medium text-ink">{strongRatioText}</span>
        </span>}
        chart={signalRing && signalRing.some(([v]) => v > 0)
          ? <Donut segments={signalRing} />
          : undefined} />

      {/* 覆盖行业：构成环（真实占比，非趋势） */}
      <KpiCard label="覆盖行业"
        value={t.industry_count.toLocaleString('zh-CN')} unit="个"
        hint={`榜单条目覆盖的行业数（统计面为榜单 ${t.total} 条，非股票池）；`
          + `环形图为最大行业的集中度（${t.top_industry_ratio != null ? `${t.top_industry_ratio.toFixed(0)}%` : '—'}，同为榜单口径）；`
          + COUNT_NO_TREND}
        compare={<KpiCompare delta={null} comparable={countComparable} prevLabel={prevLabel} note={countNote} />}
        chart={<Donut segments={t.top_industry_ratio != null
          ? [[t.top_industry_ratio, pal.SUCCESS], [100 - t.top_industry_ratio, pal.SUNKEN]]
          : []} />} />
    </div>
  );
}
