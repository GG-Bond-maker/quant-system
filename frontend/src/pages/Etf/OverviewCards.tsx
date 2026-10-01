/**
 * ETF 市场概览 5 张卡：ETF数量 / 总规模 / 今日平均涨跌幅 / 资金净流入 / 成交额。
 *
 * 「较昨日」对比来自后端 Redis 存档快照；首日运行 prev 为 null 时显示「较昨日 —」占位，
 * 不编造对比数值。
 *
 * ## 本轮改造：右侧图形从"假图形"换成**真实序列**
 * 改造前右侧是 `Donut segments={[[1, '#2563EB'], [1.4, '#E2E8F0']]}` 这类**写死比例**的
 * 装饰环（任何一天都是同一个环、与数据无关），以及 `MiniLine values={[prev, today]}`
 * 这种**只有两个点**的"趋势线"（看着像趋势，实则不含趋势信息；`prev == null` 时还会
 * 传 `[x, x]` 画出贴底平线，看似"有历史且在横走"）。二者都是在用图形冒充信息。
 *
 * 现在 5 张卡一律接 `GET /etf/overview/series` 的真实序列，绘制开关交由 `Sparkline`
 * 统一判定（`enough && comparable`）。⚠️ **当前后端各指标 `enough=false`** ——
 * ETF 概览统计 100% 实时来自外部源、本地无落库，历史靠每日盘后归档累积
 * （当前同口径仅 2 天，需 ≥6 天）⇒ 卡片显示「暂无历史序列」+ 口径 `note`，
 * 待归档累积够点数后**自动**开始显示趋势，前端无需再改。
 */
import type { EtfOverview, EtfOverviewSeries } from '@/types/etf';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import Sparkline from '@/components/charts/Sparkline';
import { KpiCard, KpiCompare } from '@/components/charts/KpiBits';

export default function OverviewCards({ data, series }: {
  data: EtfOverview | null;
  /** `/etf/overview/series` 的历史序列信封（未取到时为 null ⇒ 卡片显示占位） */
  series?: EtfOverviewSeries | null;
}) {
  // 订阅主题：chartPalette() 在调用时读 DOM，切主题需重渲染才能重取色板
  useTheme();
  if (!data?.today) {
    return (
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-[74px] animate-pulse rounded-lg border border-hair bg-surface" />
        ))}
      </div>
    );
  }
  const t = data.today, p = data.prev;
  const pal = chartPalette();

  // 冷路径降级：后端超时/异常时返回 status=unavailable（数字字段为 null），
  // 需渲染出后端给出的 reason（而非空白 / 触发 null 崩溃）。
  if (t.status === 'unavailable') {
    return (
      <div className="rounded-lg border border-warn/30 bg-warn-bg px-3 py-2.5 text-xs text-warn">
        ETF 市场概览暂不可用：{t.reason || t.message || data.data_freshness?.reason || '数据源暂时不可用'}
      </div>
    );
  }

  /**
   * 「较昨日」差值一律**显式判空**。
   *
   * ⚠️ 不能用 `p ? a - b : null` 这种简写：JS 里 `null - 0 === 0`（**不是 NaN**），
   * 缺失值会被算成"零变化"并渲染成「较昨日 持平」——那是对缺失数据做出的方向性断言。
   * 本轮 `/etf/overview` 的资金净流入已实测会返回 null（源不可达时如实置空，不再兜底 0），
   * 其余字段同属一个对象，一处会空就必须处处守卫。
   */
  const dCount = (p && t.etf_count != null && p.etf_count != null)
    ? t.etf_count - p.etf_count : null;
  const dSize = (p && t.total_size_yi != null && p.total_size_yi != null)
    ? t.total_size_yi - p.total_size_yi : null;
  const dPct = (p && t.avg_pct != null && p.avg_pct != null)
    ? t.avg_pct - p.avg_pct : null;
  const dFlow = (p && t.net_inflow_yi != null && p.net_inflow_yi != null)
    ? t.net_inflow_yi - p.net_inflow_yi : null;
  const dAmt = (p && t.amount_yi != null && p.amount_yi != null)
    ? t.amount_yi - p.amount_yi : null;

  /**
   * 总规模以「万亿」呈现（参考图口径），对比差值仍以「亿」计。
   *
   * `total_size_yi` / `etf_count` / `amount_yi` 均按类型契约**非空**
   * （见 `types/etf.ts` 的字段注释：唯一的 null 场景在 `_fallback` 里，且同时带
   * `status: 'unavailable'`，已被上面的琥珀色早返回拦掉），故这里直接参与运算。
   */
  const sizeWan = (t.total_size_yi / 10000).toFixed(2);

  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
      <KpiCard label="ETF 数量" value={t.etf_count.toLocaleString('zh-CN')} unit="只"
        hint={t.overseas
          ? `仅境内有行情的中国 ETF；另有美股 ${t.overseas.us_count} 只、日韩目录 ${t.overseas.jp_count + t.overseas.kr_count} 只（无行情，未计入）`
          : undefined}
        compare={<KpiCompare delta={dCount} unit=" 只" digits={0}
          comparable={data?.count_comparable !== false}
          note={data?.comparison_note ?? undefined} />}
        chart={<Sparkline series={series?.metrics.etf_count} color={pal.BRAND} label="ETF 数量" />} />

      <KpiCard label="总规模" value={sizeWan} unit="万亿"
        hint={t.overseas?.note}
        compare={<KpiCompare delta={dSize} unit=" 亿" />}
        chart={<Sparkline series={series?.metrics.total_size_yi} color={pal.WARN} label="总规模" />} />

      {/*
        ⚠️ `avg_pct` 可为 null（后端 `pcts` 为空时返回 None，见 etf.py:364），
        且该 null 出现在**正常路径**上、不带 `status: 'unavailable'` ⇒ 上面的琥珀色
        早返回拦不到它。此处**不得**用 `(t.avg_pct ?? 0)` 兜底：null 会被算成 0.00，
        把「取数失败」渲染成「今日市场持平」——正是要消灭的用兜底值冒充指标。
        判空口径与紧邻的「资金净流入」卡（`== null ? '—'`）保持一致。
      */}
      <KpiCard label="今日平均涨跌幅"
        value={t.avg_pct == null
          ? '—'
          : `${t.avg_pct >= 0 ? '+' : ''}${t.avg_pct.toFixed(2)}`} unit="%"
        tone={t.avg_pct == null ? undefined : t.avg_pct >= 0 ? 't-up' : 't-down'}
        compare={<KpiCompare delta={dPct} unit="%" />}
        chart={<Sparkline series={series?.metrics.avg_pct} color={pal.UP} label="平均涨跌幅" />} />

      <KpiCard label="资金净流入"
        // 源不可达时后端如实返回 null（**不是 0**）。这里的 `== null` 判空不可省：
        // 写 `t.net_inflow_yi >= 0` 的话，JS 会把 null 当 0 ⇒ 恒为 true
        // ⇒ 渲染出红色 `+0.00`，正是要消灭的"用兜底值冒充取数失败"。
        value={t.net_inflow_yi == null
          ? '—'
          : `${t.net_inflow_yi >= 0 ? '+' : ''}${t.net_inflow_yi.toFixed(2)}`} unit="亿"
        tone={t.net_inflow_yi == null ? undefined : t.net_inflow_yi >= 0 ? 't-up' : 't-down'}
        hint={t.flow?.reason ?? undefined}
        compare={<KpiCompare delta={dFlow} unit=" 亿" />}
        // ⚠️ 与上方 tone（净流入≥0 记 t-up 红）保持一致：正流入用涨色红。
        // 此前 sparkline 硬编码绿色 `#16A34A`，与文字红涨口径**相反**（方向自相矛盾）。
        chart={<Sparkline series={series?.metrics.net_inflow_yi} color={pal.UP} label="资金净流入" />} />

      <KpiCard label="成交额" value={t.amount_yi.toFixed(2)} unit="亿"
        compare={<KpiCompare delta={dAmt} unit=" 亿" />}
        chart={<Sparkline series={series?.metrics.amount_yi} color={CATEGORY_COLORS.C3} label="成交额" />} />
    </div>
  );
}
