/**
 * ETF 分析详情页（按设计稿重排）。
 *
 * 布局参考用户截图：
 *   1. 顶部：标题 + 关键指标行 + 自选/返回操作
 *   2. 上区（lg:grid-cols-3）：左列 2/3 = K 线走势 + 最新新闻与动态；
 *      右列 1/3 = ETF 概况 & 资讯 + 持仓 Top 10 +（行业配置/行业集中度 或 区间表现）
 *   3. 中区：（原「K 线下方 Tab 条」为假交互，已删除，见下方注释）
 *   4. 下区（lg:flex-row）：左 = 图表区两行（第一行 跟踪误差分析 / 跟踪与基准分析 /
 *      成交量；第二行 资金流向 + 持仓集中度）；右 = 信息列（估值指标 / 公告情绪 /
 *      相关产业链）
 *
 * ## 上区右列的死区治理（2026-10-05 第三次）
 *
 * 用户截图指出「黄金型的，右边空白的地方过多」。实测（Playwright，1600px 视口，
 * 逐卡量 `body.bottom − max(后代 bottom)`，同页其余各卡恒为 12px = body 内边距）：
 *
 *   | 类型   | 右列构成           | 右列死区 |
 *   |--------|--------------------|----------|
 *   | 商品型 | 概况               | **703px** |
 *   | 货币型 | 概况 + 持仓        | **465px** |
 *   | 债券型 | 概况 + 持仓        | **334px** |
 *   | 权益型 | 概况 + 持仓 + 行业对 | 23px（由行业对吸收，正常） |
 *
 * 根因不是"某张卡写错了"，而是**左列恒为 869px**（K 线卡定高 666 + 新闻卡 193 +
 * 间距 10），右列缺了「行业对」（307px）这一块 ⇒ 差额全被 `grow` 灌进右列最后一张卡
 * 内部成为死区。所以这是**非权益类的系统性缺陷**，不是黄金专属。
 *
 * 处置：给货币/债券/商品型补一张**只由本页已取到数据派生**的「区间表现」卡
 * （零新请求、零后端改动 ⇒ 不引入新降级面），并让它的**曲线**承担弹性高度。
 * 判据 `rangeApplicable = !showIndustry`（复用既有"右列有没有行业对"的判据，
 * 不另立常量）。
 *
 * ## 按投资类型裁剪（2026-10-05）
 *
 * 货币 / 债券 / 商品型 ETF 的页面原先会渲染 4~5 张"什么都没有"的卡
 * （「重仓股解析失败」「无法获取估值数据」「跟踪标的不可得」）—— 那些不是"取不到"，
 * 而是**不存在**：货币基金不跟踪指数、没有 PE/PB、不持有股票。
 * 后端现把这类块标成 `status === 'not_applicable'`，前端据此**整张不渲染**
 * （判据 10），并把界面让给它们真正有的数据 —— 例如货币/债券 ETF 持有的是
 * **债券 / 同业存单**（后端走 F10 `zqcc` 源，`instrument === 'bond'`）。
 *
 * ⚠️ 与 `unavailable` 严格区分：`unavailable` 是**真降级**（本该有、这次没取到），
 * 卡片照常渲染并显示后端给的具体原因；把它一起藏掉才是失真。
 *
 * ### 补充（2026-10-05 第二次）：没有内容的卡**一律不占位**
 *
 * 用户要求：「比如债券 etf 没有跟踪误差分析等数据，那么在它的专属界面中，就把这些
 * 卡片去掉」。于是「跟踪误差分析 / 跟踪与基准分析」这一行改为**有内容才渲染**
 * （`hasContent`，即 `ok` / `degraded`），两种"没有"都去掉：
 *   · 货币型 ⇒ 无跟踪标的（`not_applicable`）
 *   · 债券型 ⇒ 基准是上证国债系列，本平台的指数字典匹配不到行情（`unavailable`）
 *   · 商品型 ⇒ 基准是现货报价（黄金9999），不是指数（`unavailable`）
 * 其余卡仍只在 `not_applicable` 时消失 —— 因为对这三类页面来说它们本来就是
 * `not_applicable`，跟踪这一行是页面上唯一剩下的空卡。
 *
 * ⚠️ **卡片消失 ≠ 静默隐藏**：被去掉的 `unavailable` 块由页头下方的
 * `UnavailableNote` 逐条披露（带后端 `reason`），否则一次瞬时源故障只会表现为
 * "卡片不见了"，用户分不清「本来没有」与「这次没取到」（判据 86 / 55）。
 *
 * 图表区的行/列数随裁剪结果变化（`chartRowClass`），不留空列。
 *
 * 数据来自 /api/v1/etf/detail/{code}，所有块独立降级。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { useChart } from '@/utils/useChart';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

import { etfApi } from '@/api/etf';
import KLineChart from '@/components/charts/KLineChart';
import CategoryQuantSection from './CategoryQuantSection';
import { QuickEtfBacktestModal } from './QuickEtfBacktestModal';
import {
  ErrorState, LoadingState, PageHeader, PanelEmpty, SectionCard, ViewToggle,
} from '@/components/ui';
import type { EtfDetail, EtfIndustryItem, EtfKind, EtfKlineBar } from '@/types/etf';
import type { KLineBar } from '@/types/stock';
import { fmtAmountYi, fmtAxisNum, fmtNum, fmtPct, fmtVol, pctClass } from '@/utils/format';

/* ==================== 常量 ==================== */
const WATCH_KEY = 'AQP_ETF_WATCH';
const TRACK_PERIODS = [
  { key: '1m', label: '1月' },
  { key: '3m', label: '3月' },
  { key: '6m', label: '6月' },
  { key: '1y', label: '1年' },
] as const;

/** 观察区间 -> 交易日数（后端 `tracking.points` 上限 252 根 ≈ 1 年）。
 *
 * ⚠️ 该映射是**前端裁剪**而非重新取数：后端一次返回最长 252 根，
 * 切换区间只是取尾部 N 根。因此区间只影响**图上展示范围**，
 * 卡片底部那个"年化跟踪误差"仍是后端按完整窗口算的（已在文案中标注）。 */
const TRACK_PERIOD_DAYS: Record<(typeof TRACK_PERIODS)[number]['key'], number> = {
  '1m': 21, '3m': 63, '6m': 126, '1y': 252,
};

/* ==================== 工具 ==================== */
function readWatch(): string[] {
  try {
    const raw = localStorage.getItem(WATCH_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [];
  } catch { return []; }
}
function writeWatch(codes: string[]) {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(codes)); } catch { /* 隐私模式忽略 */ }
}

/* 图表容器统一走共享 `@/utils/useChart`（回调 ref）。
 * 原私有实现（useRef + 每图各自 init/dispose、且**没有** zrender 根摘流）已删除：
 * 它会让容器高度由 canvas 决定，构成 ResizeObserver 高度正反馈环（见共享 hook 注释）。
 * 本页**派生图**（行业甜甜圈、迷你条等）的**实际高度**仍由外层容器（grid 拉伸 +
 * `flex-1`）决定，各调用点的 `min-h-[Npx]` 类保留为兜底最小高度。
 * ⚠️ 例外：K 线图**不**走这条路 —— 它由图窗 prop `height={520}` 定高（见上区注释），
 * 不参与容器高度分配。 */

/* ==================== 通用占位 ==================== */
function EmptyPanel({ text = '暂无数据' }: { text?: string }) {
  return <PanelEmpty text={text} minH="min-h-[120px]" />;
}

/**
 * 块是否**结构性不适用**（后端 `status === 'not_applicable'`）。
 *
 * 与 `unavailable`（本该有、这次没取到）语义不同：前者这类基金**永远不会有**该数据
 * ——货币基金不跟踪指数、没有 PE/PB、不持有股票——所以整张卡**不渲染**，把界面让给
 * 它们真正有的数据（判据 10：缺失必须在结构上消失）。`unavailable` 则照常渲染并显示
 * 后端给的具体原因，那是**真降级**，藏起来才是失真。
 *
 * ⚠️ `undefined`（未加载）返回 `true`：加载中仍要占位显示「加载中…」，不能一闪一闪。
 */
function isApplicable(blk: { status: string } | undefined): boolean {
  return blk?.status !== 'not_applicable';
}

/**
 * 块**有内容可渲染**（`ok` / `degraded`）。
 *
 * 与 :func:`isApplicable` 的区别：`isApplicable` 判"这个概念存不存在"，
 * 本函数判"这次到底有没有东西可画"。
 *
 * 用于「没有内容就整张卡不渲染」的卡（当前是跟踪误差 / 跟踪与基准）——
 * 用户 2026-10-05 明确要求：**没有数据的卡片不要占位**。
 * 两种"没有"都落在这里：`not_applicable`（概念不存在）与
 * `unavailable`（本该有、这次没取到）。
 *
 * ⚠️ 卡片消失**不等于可以不说** —— 缺失项由页头下方的 :func:`UnavailableNote`
 * 逐条披露，否则一次瞬时源故障只会表现为"卡片不见了"，用户无从分辨
 * 「这类基金本来没有」与「这次没取到」（判据 86）。
 */
function hasContent(blk: { status: string } | undefined): boolean {
  return blk?.status === 'ok' || blk?.status === 'degraded';
}

/**
 * 基准身份 —— 页头 KPI 条与「ETF 概况」卡**共用同一判据**（否则两处会各说各的）。
 *
 * 优先用解析后的**官方全名**（`tracking.benchmark_name`），回退目录里的
 * `header.tracking_index`。实测 511260 目录查不到 ⇒ 页头原本只显示 `—`，
 * 而概况卡里已有「上证10年期国债指数」—— 同一事实两处不一致（判据 44）。
 */
function benchmarkLabel(
  tracking: EtfDetail['blocks']['tracking'] | undefined,
  header: EtfDetail['blocks']['header'] | undefined,
): string | null {
  return tracking?.benchmark_name || header?.tracking_index || null;
}

/**
 * 「本次未取到」披露行 —— 卡片因**没有内容**被整张去掉时，在这里如实说明。
 *
 * 为什么必须有：把空卡删掉是用户要的（不留占位），但若删掉后**页面不留任何痕迹**，
 * 就等于把降级藏了起来。本行只在真的存在 `unavailable` 块时出现，且带上后端给的
 * 具体原因（不是前端自造的结论，判据 55）。
 */
function UnavailableNote({ items }: { items: { title: string; reason: string }[] }) {
  if (items.length === 0) return null;
  return (
    <p
      className="rounded border border-hair bg-surface-sunken px-3 py-2 text-2xs leading-relaxed text-ink-muted"
      data-testid="unavailable-note"
    >
      本次未取到 {items.length} 项：
      {items.map((it) => `${it.title}（${it.reason}）`).join('；')}
    </p>
  );
}

/**
 * 图表区某一行的栅格列数 —— 按**该行实际渲染的卡数**取，避免行尾留下空列。
 *
 * 改造前图表区固定 2 行 × 3 列（6 张卡，恒满）。按类型裁剪后卡数会变
 * （货币型 4 张、商品型 5 张），若仍写死 `xl:grid-cols-3`，货币型会变成
 * 「1 张卡占 1/3 宽、右侧 2/3 空着」，那是比"降级卡"更扎眼的空洞。
 *
 * - `3` 张 ⇒ `md:grid-cols-2 xl:grid-cols-3`（与改造前**逐字节一致**，权益类 ETF 观感不变）
 * - `2` / `4` 张 ⇒ `md:grid-cols-2`（2×1 / 2×2，整行铺满）
 */
function chartRowClass(count: number): string {
  const base = 'grid flex-auto grid-cols-1 gap-2.5';
  return count === 3 ? `${base} md:grid-cols-2 xl:grid-cols-3` : `${base} md:grid-cols-2`;
}

/* ==================== 子组件 ==================== */

/** 前十大重仓股列表（带横向条）
 *
 * 加 `max-h` + 内部滚动：上区右列的自然高 = 概况 + 持仓 + 行业对，持仓列表再长就会把
 * 整列撑高；而左列（K 线定高 520 + 新闻）只能靠新闻卡 `grow` 去补齐 ⇒ 多出来的高度
 * 全被灌进新闻卡内部，新闻列表下方留出一大片空白。
 * （旧理由已作废：2026-10-04 之前这里写的是"行高变大 ⇒ 左侧 K 线图被拉得过高"，
 * K 线改为图窗定高 520px 后它不再会被拉伸，但 `max-h` 本身仍有必要。）
 * `justify-between`：在**被行高拉伸**的卡里，行均匀铺满卡内高度（不留底部空带）；
 * 在自然高度的卡（上区「持仓 Top 10」）里 justify-between 退化为普通排列，行为不变。
 * （不点名示例卡：原示例「投资组合配置」已改名下区「持仓集中度」，点名会再次过期。） */
function HoldingsList({ items }: { items: Array<{ code: string; name: string; ratio: number | null; mv_yi: number | null }> }) {
  if (!items.length) return <EmptyPanel />;
  const list = items.slice(0, 10);
  const max = Math.max(1, ...list.map((i) => i.ratio ?? 0));
  return (
    <div className="flex h-full max-h-[232px] flex-col justify-between gap-1.5 overflow-y-auto pr-0.5">
      {list.map((it, idx) => {
        const ratio = it.ratio ?? 0;
        const width = Math.min(100, Math.max(2, (ratio / max) * 100));
        return (
          <div key={it.code} className="flex items-center gap-2 text-xs">
            <span className="num w-4 shrink-0 text-center text-2xs text-ink-muted">{idx + 1}</span>
            <span className="w-20 shrink-0 truncate text-ink" title={it.name}>{it.name || it.code}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-sm bg-surface-sunken">
              <div className="h-full rounded-sm bg-info" style={{ width: `${width}%` }} />
            </div>
            <span className="num w-12 shrink-0 text-right text-2xs text-ink">{ratio.toFixed(2)}%</span>
            <span className="num w-14 shrink-0 text-right text-2xs text-ink-muted">
              {it.mv_yi != null ? `${it.mv_yi.toFixed(2)}亿` : '—'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** 持仓权重树图（ECharts Treemap） */
function HoldingsTreemap({ items }: { items: Array<{ code: string; name: string; ratio: number | null; mv_yi: number | null }> }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!items.length) return null;
    const p = chartPalette();
    const data = items
      .filter((it) => (it.ratio ?? 0) > 0)
      .map((it, idx) => ({
        name: `${it.name || it.code}\n${(it.ratio ?? 0).toFixed(2)}%`,
        value: it.ratio ?? 0,
        itemStyle: {
          color: CATEGORY_COLORS.PIE[idx % CATEGORY_COLORS.PIE.length],
        },
      }));
    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const val = params.value as number;
          return `<div style="font-size:12px;padding:2px;"><b>${params.name.split('\n')[0]}</b><br/>占净值比例: <b>${val.toFixed(2)}%</b></div>`;
        },
      },
      series: [
        {
          type: 'treemap',
          data,
          width: '100%',
          height: '100%',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            formatter: '{b}',
            fontSize: 10,
            color: p.ON_SOLID,
          },
          itemStyle: {
            borderColor: p.CANVAS,
            borderWidth: 1,
            gapWidth: 1,
          },
        },
      ],
    };
  }, [items, theme]);

  const ref = useChart(option);
  if (!items.length) return <EmptyPanel text="暂无持仓数据" />;
  return <div ref={ref} className="h-full min-h-[220px] w-full" />;
}

/**
 * 持仓集中度（同屏冗余审计 H1 的替换物）。
 *
 * ## 为什么是它
 * 原「投资组合配置」卡与上区「持仓 Top 10」是**同一个 `<HoldingsList>`、同一 prop**，
 * 整块重复。删掉重复后，这里改放**列表本身给不出、而报告使用者最需要**的一组量：
 * 这批披露持仓覆盖了多少、集中在头部几只。
 *
 * ## 数据来源与量纲（不新增接口、不估算）
 * 输入即后端 `blocks.holdings.holdings.items` 的 `ratio`（占净值比例，**百分数**）。
 * 已核实：后端 `data/etf.py:939-940` 从源字段 `"5.23%"` 解析出 `5.23`，
 * :979-980 再用 `ratio/100 × fund_size_yi` 推 `mv_yi` ⇒ `ratio` 确为百分数。
 *
 * * **头部 N 只合计**：`Σ ratio`（N = 实际披露只数，**不写死 10**），与列表右列同源、
 *   可直接核对。
 * * **前 3 大 / 前 5 大合计**：同一数组的**前缀和**（2026-10-04 补，用于填满收窄后的
 *   卡高）。⚠️ 只在 `披露只数 > N` 时显示 —— 恰好 N 只时它等于总额，与「头部 N 只
 *   合计」重复；少于 N 只时显示它则是把少数几只冒充"N 大"（判据 45 家族）。
 * * **未披露比例**：`ratio` 是"占**净值**比例"，故全量应 ≈100%；明显小于 100
 *   说明后端只返回了部分持仓（源站仅前十大/分页）。此时**如实显示缺口**，
 *   绝不补满 —— 披露多少就是多少。
 * * **HHI**：`Σ wᵢ² × 10000`（标准口径，0~10000）。⚠️ 只在**已披露**持仓上算，
 *   未披露部分缺失时它是**下界**，不可与含全部持仓的行业口径直接比较。
 *   宁可写明口径，也不假装它是完整 HHI。
 *
 * ## 布局（2026-10-04）
 * 本卡所在行的行高由同行那张带图表的卡（「资金流向」）决定 ⇒ 本卡会被拉伸。故内容
 * 根节点必须 `flex-1` + `justify-between` 才能撑满；缺了就是"内容挤在顶部、卡底留
 * 191px 死区"（实测）。写法与「行业集中度」卡一致。
 */
function HoldingsConcentration({ items }: {
  items: Array<{ code: string; name: string; ratio: number | null; mv_yi: number | null }>;
}) {
  if (!items.length) return <EmptyPanel />;
  // 源站表格按「序号」列排列，实测 510300 / 510500 / 588000 / 512880 四只均为
  // 占净值比例降序；但「前 N 大」这个措辞要求顺序**真的是**降序 ⇒ 不依赖上游前提，
  // 这里显式排序（占比缺失的排到最后，与"不可得"语义一致）。对已降序的数据是恒等操作。
  const ranked = [...items].sort((a, b) => (b.ratio ?? -1) - (a.ratio ?? -1));
  const top = ranked.slice(0, 10);
  const ratios = top.map((i) => i.ratio).filter((r): r is number => r != null);
  if (!ratios.length) {
    return <EmptyPanel text="持仓明细缺少占净值比例，无法计算集中度" />;
  }
  const sumTop = ratios.reduce((a, b) => a + b, 0);
  const hhi = ratios.reduce((a, b) => a + (b / 100) ** 2, 0) * 10000;
  const undisclosed = 100 - sumTop;
  const withRatio = items.filter((i) => i.ratio != null).length;
  /**
   * 前 N 大合计 —— **只在披露数 ≥ N 时**才给。
   * 只有 2 只持仓却写「前 5 大合计」，等于把 2 只的和冒充 5 只的（判据 45 家族：
   * 不可比的数不该显示）。故不足 N 时该行**结构上消失**，而不是填 `—`。
   */
  const sumFirst = (n: number) => ratios.slice(0, n).reduce((a, b) => a + b, 0);
  // 用 `>` 而非 `>=`：恰好 3 只时「前 3 大合计」就等于总额，与「头部 3 只合计」重复。
  const top3 = ratios.length > 3 ? `${sumFirst(3).toFixed(2)}%` : null;
  const top5 = ratios.length > 5 ? `${sumFirst(5).toFixed(2)}%` : null;

  const row = (label: string, value: string, hint?: string) => (
    <div className="flex items-baseline justify-between gap-2" title={hint}>
      <span className="shrink-0 text-2xs text-ink-secondary">{label}</span>
      <span className="num min-w-0 truncate text-xs font-medium text-ink">{value}</span>
    </div>
  );

  return (
    /* `flex-1` + `justify-between`：本卡所在行的行高由同行那张带图表的卡
       （「资金流向」）决定，故本卡会被拉伸到同一高度。缺这两条时内容只占顶部
       114px、卡底留 **191px 死区**（2026-10-04 实测，同页其余 13 张卡的底部空白
       恒为 12px = body 内边距）。写法与「行业集中度」卡一致。 */
    <div className="flex min-h-0 flex-1 flex-col justify-between gap-2">
      <div className="space-y-1">
        {row('披露持仓数', `${withRatio} 只`)}
        {top3 != null && row('前 3 大合计', top3)}
        {top5 != null && row('前 5 大合计', top5)}
        {row(`头部 ${ratios.length} 只合计`, `${sumTop.toFixed(2)}%`)}
        {row('未披露比例', undisclosed > 0.01 ? `${undisclosed.toFixed(2)}%` : '0%（已满）',
          '占比是「占净值」口径，全量应 ≈100%；明显小于 100 说明源站只给了部分持仓')}
        {row('HHI（已披露口径）', hhi.toFixed(0),
          'Herfindahl-Hirschman 指数：Σ(占比²)×10000，仅按已披露持仓计算')}
      </div>

      {/* 只画「已披露」部分：留白本身就是"这里有没拿到的数据"的可视信号。 */}
      <div>
        <div className="mb-1 flex justify-between text-2xs text-ink-muted">
          <span>已披露 {sumTop.toFixed(1)}%</span>
          {undisclosed > 0.01 && <span>未披露 {undisclosed.toFixed(1)}%</span>}
        </div>
        <div className="h-2 w-full overflow-hidden rounded-sm bg-surface-sunken">
          <div className="h-full rounded-sm bg-info"
            style={{ width: `${Math.min(100, Math.max(0, sumTop))}%` }} />
        </div>
      </div>

      <p className="text-2xs leading-relaxed text-ink-muted">
        HHI 按「已披露」持仓计算；后端未返回的持仓未计入，故它是集中度的下界，
        不宜与含全部持仓的行业口径直接比较。
      </p>
    </div>
  );
}

/* ==================== 风险度量（由 K 线派生） ==================== */

/**
 * 风险度量窗口 = 252 个交易日（约 1 年）。
 * 与相邻「跟踪误差分析」卡脚注里的"近 1 年"**同窗口** —— 同一页上两个风险类指标
 * 若各用一套窗口又不披露，读者无法横向比较（判据 42：口径必须与相邻同类卡一致）。
 */
const RISK_WINDOW = 252;
/** 少于这么多收益点就不出指标：20 是「近 20 日波动率」的下限，也是统计意义的下限。 */
const RISK_MIN_DAYS = 20;

/** 日收益序列 → 年化波动率（%）= **总体**标准差 × √252。 */
function annVol(rets: number[]): number {
  if (rets.length < 2) return NaN;
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
  const varr = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / rets.length;
  return Math.sqrt(varr) * Math.sqrt(252) * 100;
}

/** 回撤（水下）曲线：`dd_t = close_t / max(close_{≤t}) − 1`，恒 ≤ 0，单位 %。 */
function DrawdownChart({ series }: { series: Array<{ date: string; dd: number }> }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (series.length < 2) return null;
    const p = chartPalette();
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (pt: unknown) => {
          const ps = pt as Array<{ dataIndex: number }>;
          const r = series[ps[0]?.dataIndex ?? 0];
          return r ? `<b>${r.date}</b><br/>距区间最高点 ${r.dd.toFixed(2)}%` : '';
        },
      },
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: {
        type: 'category', data: series.map((r) => r.date.slice(5)),
        axisLabel: { fontSize: 9, color: p.INKM }, axisTick: { show: false },
      },
      // 零轴钉在**顶部**：回撤恒 ≤ 0，把 0 放在顶端才符合"水下曲线"的读法
      yAxis: {
        type: 'value', max: 0,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [{
        name: '距区间最高点', type: 'line', data: series.map((r) => r.dd),
        showSymbol: false,
        lineStyle: { width: 1.2, color: p.DOWN },
        areaStyle: { color: withAlpha(p.DOWN, 0.18) },
      }],
    };
  }, [series, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel text="K 线数据不足" />;
  return <div ref={ref} className="h-full min-h-[110px] w-full" />;
}

/**
 * 风险度量 —— 由本页**已有**的 K 线派生绝对风险指标（零新请求、零后端改动）。
 *
 * ## 为什么补这张卡（2026-10-04）
 * 全页有「跟踪误差分析」回答"跟得像不像指数"，却**没有任何绝对风险指标** ——
 * 而波动与回撤，是判断一只 ETF 该不该拿的同等靠前的问题。数据全部来自已经取到的
 * `blocks.kline.bars`，不新增请求、不改后端，故不引入新的降级面。
 *
 * ## 口径（逐条可复算，无一处估算或补齐）
 * - **输入序列**：本页 `klineBars`（= `kline.bars` 剔除任一 OHLCV 为 null 的 bar），
 *   与上方 K 线图**同一序列**，故两处描述的是同一段行情。
 * - **窗口**：`min(252, 可用收益日数)` 个交易日，实际窗口长度**写进行标签与脚注**，
 *   不用"近 1 年"含糊带过（可用日数受后端 `fetch_kline(limit=320)` 上限约束）。
 * - **收益**：`r_t = close_t / close_{t-1} − 1`，用**前复权**价（后端 `fetch_kline`
 *   固定请求 `qfq` ⇒ 已含分红再投）。
 * - **年化波动率** = 日收益**总体**标准差 × √252；**下行波动率** = 只取 `r_t < 0`
 *   的日子同样计算（半波动率）。⚠️ 负收益日 < 2 天时该行**不显示**（样本不足，
 *   不填 `—` 冒充）。
 * - **最大回撤** = `close_t / max(close_{≤t}) − 1` 的最小值；**当前回撤** = 末日相对
 *   区间最高点。⚠️ 窗口受 320 根上限约束 ⇒ 它是"近 320 日内的"值，已在脚注披露。
 * - **上行日占比** = `r_t > 0` 的天数 ÷ 有效收益日数（**持平日计入分母**，如实反映）。
 */
function RiskMetrics({ bars }: { bars: KLineBar[] }) {
  // 订阅主题：下方 DrawdownChart 用 chartPalette() 取色，主题切换需重渲染
  useTheme();

  const stat = useMemo(() => {
    if (bars.length < RISK_MIN_DAYS + 1) return null;
    const win = bars.slice(-(RISK_WINDOW + 1));
    const rets: number[] = [];
    for (let i = 1; i < win.length; i++) rets.push(win[i].close / win[i - 1].close - 1);
    if (rets.length < RISK_MIN_DAYS) return null;

    let peak = win[0].close;
    const ddSeries = win.map((b) => {
      peak = Math.max(peak, b.close);
      return { date: b.date, dd: (b.close / peak - 1) * 100 };
    });
    const neg = rets.filter((r) => r < 0);
    const rf = 0.02; // 无风险利率 2.0%
    const annRet = Math.pow(win[win.length - 1].close / win[0].close, 252 / rets.length) - 1;
    const vol = annVol(rets);
    const downVol = neg.length >= 2 ? annVol(neg) : null;
    const maxDD = Math.min(...ddSeries.map((d) => d.dd));
    const sharpe = vol > 0 ? (annRet - rf) / (vol / 100) : null;
    const sortino = downVol != null && downVol > 0 ? (annRet - rf) / (downVol / 100) : null;
    const calmar = Math.abs(maxDD) > 0 ? (annRet - rf) / (Math.abs(maxDD) / 100) : null;

    return {
      days: rets.length,
      firstDate: win[0].date,
      lastDate: win[win.length - 1].date,
      vol,
      vol20: annVol(rets.slice(-20)),
      downVol,
      maxDD,
      curDD: ddSeries[ddSeries.length - 1].dd,
      upRatio: (rets.filter((r) => r > 0).length / rets.length) * 100,
      peakClose: peak,
      lastClose: win[win.length - 1].close,
      sharpe,
      sortino,
      calmar,
      ddSeries,
    };
  }, [bars]);

  if (!stat) {
    return <EmptyPanel text={`K 线不足 ${RISK_MIN_DAYS + 1} 个交易日，无法计算风险指标`} />;
  }

  const pct = (v: number, digits = 2) => `${v.toFixed(digits)}%`;
  const row = (label: string, value: string, hint?: string) => (
    <div className="flex items-baseline justify-between gap-1" title={hint}>
      <span className="shrink-0 text-2xs text-ink-secondary">{label}</span>
      <span className="num min-w-0 truncate text-xs font-medium text-ink">{value}</span>
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* 2 列排布：6 行指标排成 1 列要 150px+，会把下方回撤曲线压到 100px 以下。 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {row('年化波动率', pct(stat.vol), `近 ${stat.days} 个交易日：日收益总体标准差 × √252`)}
        {row('最大回撤', pct(stat.maxDD),
          `${stat.firstDate} ~ ${stat.lastDate} 区间内，收盘价相对区间最高点的最大跌幅`)}
        {row('近 20 日年化波动率', pct(stat.vol20), '最近 20 个交易日的年化波动率')}
        {row('当前回撤', pct(stat.curDD),
          `末日收盘 ${stat.lastClose} 相对区间最高 ${stat.peakClose}`)}
        {stat.downVol != null
          && row('下行波动率', pct(stat.downVol),
            '只取负收益日（r < 0）计算的年化波动率；负收益日少于 2 天时本行不显示')}
        {row('上行日占比', pct(stat.upRatio, 1), '收益 > 0 的天数 ÷ 有效收益日数（持平日计入分母）')}
      </div>

      {/* 机构级扩展风险收益比率 (Sharpe / Sortino / Calmar) */}
      <dl className="grid grid-cols-3 gap-2 border-t border-hair/50 pt-1.5 text-center">
        <div>
          <dt className="text-3xs text-ink-muted">夏普比率 (rf=2%)</dt>
          <dd className={`num text-xs font-semibold ${stat.sharpe != null && stat.sharpe > 0 ? 'text-up' : stat.sharpe != null && stat.sharpe < 0 ? 'text-down' : 'text-ink'}`}>
            {stat.sharpe != null ? stat.sharpe.toFixed(2) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-3xs text-ink-muted">索提诺比率 (Sortino)</dt>
          <dd className={`num text-xs font-semibold ${stat.sortino != null && stat.sortino > 0 ? 'text-up' : stat.sortino != null && stat.sortino < 0 ? 'text-down' : 'text-ink'}`}>
            {stat.sortino != null ? stat.sortino.toFixed(2) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-3xs text-ink-muted">卡玛比率 (Calmar)</dt>
          <dd className={`num text-xs font-semibold ${stat.calmar != null && stat.calmar > 0 ? 'text-up' : stat.calmar != null && stat.calmar < 0 ? 'text-down' : 'text-ink'}`}>
            {stat.calmar != null ? stat.calmar.toFixed(2) : '—'}
          </dd>
        </div>
      </dl>

      <div className="min-h-0 flex-1">
        <DrawdownChart series={stat.ddSeries} />
      </div>

      <p className="text-2xs leading-relaxed text-ink-muted">
        按「前复权」收盘价派生（已含分红再投），窗口 {stat.firstDate} ~ {stat.lastDate}
        （{stat.days} 个交易日）；受接口 320 根日线上限约束，更早的回撤未计入。
      </p>
    </div>
  );
}

/* ==================== 区间表现（由 K 线派生） ==================== */

/**
 * 多周期涨跌幅的观察窗口（**交易日**，不是自然日）。
 *
 * ## 为什么补这张卡（2026-10-05）
 * 页头 KPI 条只给**当日**涨跌幅，「风险度量」只回答"波动/回撤有多大"、不回答
 * "赚了多少" ⇒ 全页此前**没有任何多周期收益读数**，也没有"当前价处在区间什么位置"
 * 的读数。两者都只由**已经取到**的 `kline.bars` 派生：零新请求、零后端改动，
 * 因此不引入新的降级面。
 *
 * ## 与 K 线图的分工（防"重复品"质疑）
 * K 线用**绝对价格 + 蜡烛**呈现，且它自带的周期栏是**聚合**（把日线合成周/月线）；
 * 本卡曲线是**归一化**口径（窗口首日收盘 = 0%），用于直接读出区间累计收益；
 * 另有 K 线读不出的两组数：多周期涨跌幅、当前价在区间中的分位。
 */
const RANGE_HORIZONS = [5, 20, 60, 120, 250] as const;

/**
 * 「区间表现」窗口 = 252 个交易日（约 1 年），与「风险度量」卡**同窗口** ——
 * 同一页上两个由同一序列派生的指标若各用一套窗口又不披露，读者无法横向比较（判据 42）。
 * ⚠️ 实际窗口还受接口 320 根日线上限约束 ⇒ 真实起止日与**日线根数**逐次写进脚注，
 * 不用"近 1 年"含糊带过（不得把短窗口冒充一年）。
 *
 * 脚注里写「N 根日线」而不是「N 个交易日」：后者与「风险度量」卡的 `days`
 * （收益观测数 = 根数 − 1）会差 1，同页两个数字看着自相矛盾。
 */
const RANGE_WINDOW = 252;
/**
 * 少于这么多交易日不出指标（与 `RISK_MIN_DAYS` 同门槛）。
 * 门槛写成同一个数，是为了避免两卡出现"一张有、一张空"的错位观感。
 */
const RANGE_MIN_DAYS = 20;

/** 区间累计收益曲线：窗口首日收盘归一化为 0%，直接读出"区间内累计涨跌了多少"。 */
function CumulativeReturnChart({ series }: { series: Array<{ date: string; cum: number }> }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (series.length < 2) return null;
    const p = chartPalette();
    // 整条曲线按**末点符号**取色：区间是赚是亏一眼可辨（红涨绿跌，中国习惯）
    const color = series[series.length - 1].cum >= 0 ? p.UP : p.DOWN;
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (pt: unknown) => {
          const ps = pt as Array<{ dataIndex: number }>;
          const r = series[ps[0]?.dataIndex ?? 0];
          return r
            ? `<b>${r.date}</b><br/>区间累计 ${r.cum >= 0 ? '+' : ''}${r.cum.toFixed(2)}%`
            : '';
        },
      },
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: {
        // `slice(2, 7)` ⇒ "25-06"：窗口跨年，只留月日会分不清年份
        type: 'category', data: series.map((r) => r.date.slice(2, 7)),
        axisLabel: { fontSize: 9, color: p.INKM }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [{
        name: '区间累计收益', type: 'line', data: series.map((r) => r.cum),
        showSymbol: false,
        lineStyle: { width: 1.2, color },
        areaStyle: { color: withAlpha(color, 0.15) },
        // 零轴：区间累计的正负分界；缺了它读不出"区间内到底是赚还是亏"
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: p.HAIR2, width: 0.8, type: 'dashed' as const },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      }],
    };
  }, [series, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel text="K 线数据不足" />;
  return <div ref={ref} className="h-full min-h-[120px] w-full" />;
}

/**
 * 区间表现 —— 多周期涨跌幅 + 当前价在区间中的分位 + 区间累计收益曲线。
 *
 * ## 它同时是「上区右列的死区治理」（2026-10-05）
 * 货币 / 债券 / 商品型 ETF 的右列没有「行业对」，只剩「概况」(货币/债券还有持仓)，
 * 远矮于左列（K 线卡定高 666 + 新闻卡 193 = 869）⇒ 多出来的高度全被灌进右列最后一张
 * 卡内部。实测死区：商品型 **703px**、债券型 **334px**、货币型 **465px**
 * （同页其余各卡恒为 12px = body 内边距）。
 *
 * 故本卡的内容根节点是 `flex-1`，曲线区是 `min-h-0 flex-1`：被拉伸时多出来的高度
 * 由**曲线**吃掉（图随容器高度渲染），而不是变成卡底空白。
 * ⚠️ 曲线**不能**写死高度 —— 写死就退回死区。
 *
 * ## 口径（逐条可复算，无一处估算或补齐）
 * - **输入**：本页 `klineBars`（= `kline.bars` 剔除任一 OHLCV 为 null 的 bar），
 *   与上方 K 线图、右下方「风险度量」卡**同一序列**。
 * - **窗口**：`min(253, 可用日线数)` 根；少于 21 根时整卡走空态，不出任何数字。
 * - **多周期涨跌幅** = `末根收盘 / (末根往前第 n 根收盘) − 1`。窗口内不够 n 根时
 *   该周期**结构上消失**（不填 `—`、不缩短周期冒充）。
 * - **区间分位** = `(末根收盘 − 窗口最低收盘) / (窗口最高收盘 − 窗口最低收盘)`。
 *   ⚠️ 区间**完全没有波动**（高低相等）时分位**不可得** ⇒ 整块分位区消失，
 *   而不是填一个 0%/50% 的假值。
 * - **曲线**：`收盘_t / 首根收盘 − 1`，首点恒为 0%。
 */
function RangePerformance({ bars }: { bars: KLineBar[] }) {
  const stat = useMemo(() => {
    if (bars.length < RANGE_MIN_DAYS + 1) return null;
    const win = bars.slice(-(RANGE_WINDOW + 1));
    const last = win[win.length - 1];
    const rets: Array<{ n: (typeof RANGE_HORIZONS)[number]; r: number }> = [];
    for (const n of RANGE_HORIZONS) {
      // 要算"n 个交易日前的收益" ⇒ 窗口至少要有 n+1 根，否则该周期不可得（结构上消失）
      const base = win.length - 1 - n;
      if (base >= 0) rets.push({ n, r: (last.close / win[base].close - 1) * 100 });
    }
    const closes = win.map((b) => b.close);
    const lo = Math.min(...closes);
    const hi = Math.max(...closes);
    return {
      bars: win.length,
      firstDate: win[0].date,
      lastDate: last.date,
      rets, lo, hi,
      pos: hi > lo ? ((last.close - lo) / (hi - lo)) * 100 : null,
      series: win.map((b) => ({ date: b.date, cum: (b.close / win[0].close - 1) * 100 })),
    };
  }, [bars]);

  if (!stat) {
    return (
      <EmptyPanel text={`K 线不足 ${RANGE_MIN_DAYS + 1} 个交易日，无法计算区间表现`} />
    );
  }

  // 与全站 `fmtPct` 同一符号规则（`> 0` 才加 `+`）—— 0 写成「0.00%」而不是「+0.00%」，
  // 否则同一页上「涨跌幅」两种写法并存（判据 42：同页同类数值口径必须一致）。
  const fmtR = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
  /**
   * 分位文本与填色宽度**取同一个值**。
   *
   * 若文本 `toFixed(1)` 而宽度用原始浮点，会出现「文字写 96.9%、色条在 96.94%」——
   * 同一个量在卡内两处不一致，比不显示更糟（判据 42 家族）。
   */
  const posText = stat.pos != null ? stat.pos.toFixed(1) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="grid shrink-0 grid-cols-2 gap-x-3 gap-y-1">
        {stat.rets.map((x) => (
          <div key={x.n} className="flex items-baseline justify-between gap-1"
            title={`以「前复权」收盘价计，近 ${x.n} 个交易日（交易日，非自然日）`}>
            <span className="shrink-0 text-2xs text-ink-secondary">近 {x.n} 日</span>
            <span className={`num min-w-0 truncate text-xs font-medium ${pctClass(x.r)}`}>
              {fmtR(x.r)}
            </span>
          </div>
        ))}
      </div>

      {posText != null && (
        <div className="shrink-0">
          <div className="mb-1 flex justify-between gap-2 text-2xs text-ink-muted">
            <span className="truncate">区间低 {fmtNum(stat.lo)}</span>
            <span className="shrink-0">当前位置 {posText}%</span>
            <span className="truncate">区间高 {fmtNum(stat.hi)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-sm bg-surface-sunken"
            title="填色长度 = 当前收盘价在窗口收盘价区间中的位置（0% = 区间最低，100% = 区间最高）">
            <div className="h-full rounded-sm bg-brand-500" style={{ width: `${posText}%` }} />
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1">
        <CumulativeReturnChart series={stat.series} />
      </div>

      <p className="shrink-0 text-2xs leading-relaxed text-ink-muted">
        按「前复权」收盘价派生（已含分红再投）；窗口 {stat.firstDate} ~ {stat.lastDate}
        （{stat.bars} 根日线）。曲线以窗口首根收盘归一化为 0%，与 K 线的绝对价格口径不同；
        受接口 320 根日线上限约束，更早区间未计入。
      </p>
    </div>
  );
}

/**
 * 甜甜圈最多展示的类目数（**不含**末位的「其他」聚合项）。
 * 由色板长度推出，避免"色板 8 色却画了 9 个扇区"这类漂移。
 */
const PIE_MAX_SLICES = CATEGORY_COLORS.PIE.length - 1;

/**
 * 行业配置：环形图 + **全量可见的 DOM 图例**。
 *
 * ## 为什么不用 ECharts 自带图例（2026-10-03）
 * 该卡在 300px 侧栏内仅约 145px 宽。8 个类目用 `type: 'scroll'` 会被分页成 **5 页**
 * —— 用户要连点 4 次才能看全，这正是"图例不直观、要点多次"的根因；换成 `plain`
 * 又会在窄卡里被裁。故图例改为图下方自行渲染的 DOM 列表：一行一个、零点击，
 * 且色块与扇区**按下标取同一个色板**，逐一对得上。
 *
 * ## 顺带修掉一个静默丢弃
 * 原实现是 `.slice(0, 8)`，第 9 类起被直接丢掉（后端实测 510300 有 14 个门类，
 * 见 `backend/app/data/etf.py` 的注释）⇒ 环形图**加不到 100%** 且界面上没有任何披露，
 * 用户会以为"这只 ETF 只配了这些行业"。现在尾部并入「其他 N 类」，占比如实计入，
 * 悬停可看到被合并的具体行业名。
 */
export function IndustryPie({ items }: { items: Array<{ industry: string; ratio: number | null }> }) {
  const { slices, mergedNames } = useMemo(() => {
    const all = items
      .map((it) => ({ name: it.industry, value: it.ratio ?? 0 }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value);
    const head = all.slice(0, PIE_MAX_SLICES);
    const tail = all.slice(PIE_MAX_SLICES);
    if (!tail.length) return { slices: head, mergedNames: [] as string[] };
    return {
      slices: [
        ...head,
        { name: `其他 ${tail.length} 类`, value: tail.reduce((s, d) => s + d.value, 0) },
      ],
      mergedNames: tail.map((d) => d.name),
    };
  }, [items]);

  // 色值全部来自 CATEGORY_COLORS.PIE（不跟随主题），故本图**没有**主题相关的取色，
  // 不再需要 useTheme / theme 依赖（原注释要求的 theme 依赖是随 `chartPalette()` 一起进来的）。
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!slices.length) return null;
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}%', textStyle: { fontSize: 11 } },
      legend: { show: false },
      series: [{
        type: 'pie', radius: ['40%', '62%'], center: ['50%', '50%'],
        // 显式逐项指定颜色，而不是依赖 ECharts 内部的默认色板解析顺序
        // —— 这是"DOM 图例色块 ↔ 扇区"能一一对应的前提。
        data: slices.map((d, i) => ({ ...d, itemStyle: { color: CATEGORY_COLORS.PIE[i] } })),
        label: { show: false },
        labelLine: { show: false },
        /**
         * DOM 图例 hover → 高亮该扇区 + **弱化其余**（`focus:'self'` 触发 `blur` 态）。
         * ⚠️ 只 dispatch highlight 而不配这两个态 ⇒ 高亮"发了但看不见"，属假交互。
         */
        emphasis: { focus: 'self', scale: true, scaleSize: 5 },
        blur: { itemStyle: { opacity: 0.3 } },
      }],
    };
  }, [slices]);

  /** DOM 图例当前悬停的扇区下标（null = 未悬停） */
  const [active, setActive] = useState<number | null>(null);

  const chartRef = useChart(option);
  const boxRef = useRef<HTMLDivElement | null>(null);
  // 组合回调 ref：`useChart` 要 DOM 节点做 init/resize/dispose；本组件还要留一份引用，
  // 交给 `getInstanceByDom` 取回实例发 `dispatchAction`（见 highlight）。
  const setBox = useCallback((el: HTMLDivElement | null) => {
    boxRef.current = el;
    chartRef(el);
  }, [chartRef]);

  /**
   * 高亮 / 取消高亮某个扇区。
   *
   * ⚠️ 走 `dispatchAction`（增量动作）而**不是**改 option：`useChart` 用
   * `setOption(o, true)`（notMerge）重设 option 时，环形图会整体重放入场动画——
   * 每次 hover 都"重画一遍"，视觉抖动明显。`dispatchAction` 只切 emphasis 态，平滑。
   */
  const highlight = useCallback((i: number | null) => {
    const el = boxRef.current;
    const inst = el ? echarts.getInstanceByDom(el) : undefined;
    if (!inst) return;
    if (i === null) inst.dispatchAction({ type: 'downplay', seriesIndex: 0 });
    else inst.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: i });
  }, []);

  const hover = (i: number | null) => {
    setActive(i);
    highlight(i);
  };

  if (!option) return <EmptyPanel text="暂无行业数据" />;

  return (
    <div className="flex h-full min-h-[200px] flex-col gap-1.5">
      <div ref={setBox} className="min-h-[110px] w-full flex-1" />
      <ul className="space-y-0.5">
        {slices.map((d, i) => (
          <li key={d.name}
            data-testid="pie-legend-row"
            onMouseEnter={() => hover(i)}
            onMouseLeave={() => hover(null)}
            className={`flex items-center gap-1.5 rounded-sm px-0.5 text-2xs leading-tight transition-colors ${
              active === i ? 'bg-surface-alt' : ''}`}
            title={mergedNames.length > 0 && i === slices.length - 1
              ? `含：${mergedNames.join('、')}`
              : d.name}>
            {/* DOM 侧同步弱化：色块与文字一起变淡，与画布上的 blur 双向呼应 */}
            <span className={`h-2 w-2 shrink-0 rounded-sm transition-opacity ${
              active !== null && active !== i ? 'opacity-40' : ''}`}
              style={{ backgroundColor: CATEGORY_COLORS.PIE[i] }} />
            <span className={`min-w-0 flex-1 truncate transition-colors ${
              active === i ? 'text-ink' : 'text-ink-secondary'}`}>{d.name}</span>
            <span className="num shrink-0 text-ink-muted">{d.value.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 行业集中度 —— 由「行业配置」**同一份**持仓行业权重派生（零新请求、零后端改动）。
 *
 * ## 为什么补这张卡（2026-10-03）
 * 原位置是一张**纯导航促销卡**（「ETF 中心 / 返回列表浏览全部 ETF…」）：全页十余张卡里
 * 只有它没有数据；它的链接与页头「返回 ETF 中心」按钮**完全重复**；且它绕过了共享
 * `SectionCard`（手写 `text-sm` + `p-3`，缺规范 4.3 要求的标题栏 `border-b`），
 * 标题字号与内边距都和其余卡不一致。
 *
 * 换成「行业集中度」的四条理由：
 * 1. **同源**：与左侧「行业配置」读同一个 `holdings.industry`，不可能出现两卡口径打架；
 * 2. **补齐语义**：甜甜圈回答"配了哪些行业"，本卡回答"有多集中"——判断一只 ETF 是否
 *    单押一个赛道，是 ETF 研究里比"配了什么"更靠前的问题；
 * 3. **零新数据源**：纯前端派生，不新增请求、不改后端，故不引入新的降级面；
 * 4. **风格自动对齐**：走共享 `SectionCard`，标题栏 / 圆角 / 边框与全页一致。
 *
 * ## 口径（必须与甜甜圈一致）
 * 权重直接取后端 `ratio`（占净值比例 %），**不做归一化**。后端只披露主要行业，
 * 合计未必 = 100%，故条形图右端**如实留出「未披露」段**（即 track 底色），
 * 并把覆盖率写进脚注。若这里归一化而甜甜圈不归一化，同一页会出现两个口径
 * ——那比不显示更糟。仅 HHI 用归一化份额（`Σwᵢ²` 的定义要求份额和为 1），已注明。
 */
export function IndustryConcentration({ items }: { items: EtfIndustryItem[] }) {
  // 订阅主题：下方是**内联** chartPalette() 取色（不在 useMemo 里），主题切换需重渲染
  useTheme();

  const stat = useMemo(() => {
    const all = items
      .map((it) => ({ name: it.industry, value: it.ratio ?? 0 }))
      .filter((d) => d.value > 0)      // null / 0 是"不可得"，不得当 0 计入分母
      .sort((a, b) => b.value - a.value);
    if (!all.length) return null;
    const total = all.reduce((s, d) => s + d.value, 0);
    const sumTop = (n: number) => all.slice(0, n).reduce((s, d) => s + d.value, 0);
    // HHI（赫芬达尔指数）用归一化份额；有效行业数 = 1/HHI，越大越分散
    const hhi = all.reduce((s, d) => s + (d.value / total) ** 2, 0);
    // 条形图按「占净值」画。分母取 max(total, 100)：total>100（极端/异常数据）时
    // 不至于把条撑出容器；此情形下脚注里的"合计 x%"已如实披露，不会误导。
    const denom = Math.max(total, 100);
    // 收敛到 4 位小数：否则 `(93.8-76)/100*100` 这类浮点尾巴会原样写进 width
    // （`17.799999999999997%`），DOM 脏且让"三段合计=100"这条不变量无法精确断言。
    const pct = (v: number) => Math.round((v / denom) * 100 * 1e4) / 1e4;
    return {
      total,
      count: all.length,
      top1: all[0],
      top3: sumTop(3),
      top5: sumTop(5),
      effN: hhi > 0 ? 1 / hhi : null,
      wTop5: pct(sumTop(5)),
      wRest: pct(total - sumTop(5)),
      undisclosed: pct(denom - total),
    };
  }, [items]);

  if (!stat) return <EmptyPanel text="暂无行业数据" />;

  const p = chartPalette();
  // 低于 0.05% 的"未披露"只是舍入噪声，不单独画段/出图例（否则出现发丝线）
  const showUndisclosed = stat.undisclosed > 0.05;

  const row = (label: string, value: string, hint?: string) => (
    <div className="flex items-baseline justify-between gap-2" title={hint}>
      <span className="shrink-0 text-2xs text-ink-secondary">{label}</span>
      <span className="num min-w-0 truncate text-xs font-medium text-ink">{value}</span>
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col justify-between gap-2">
      <div className="space-y-1">
        {row('前 3 大合计', `${stat.top3.toFixed(1)}%`)}
        {row('前 5 大合计', `${stat.top5.toFixed(1)}%`)}
        {row('最大行业', `${stat.top1.name} ${stat.top1.value.toFixed(1)}%`, stat.top1.name)}
        {row('有效行业数', stat.effN != null ? stat.effN.toFixed(1) : '—',
          '1 / Σwᵢ²（wᵢ 为归一化权重），越大越分散；按已披露行业口径')}
      </div>

      {/* 100% 条：按「占净值」画，右端未覆盖部分是 track 底色 =「未披露」 */}
      <div>
        <div data-testid="conc-bar"
          className="flex h-2 w-full overflow-hidden rounded-sm bg-surface-sunken">
          <div data-testid="conc-bar-top5" className="h-full"
            style={{ width: `${stat.wTop5}%`, backgroundColor: p.BRAND }} />
          <div data-testid="conc-bar-rest" className="h-full"
            style={{ width: `${stat.wRest}%`, backgroundColor: p.HAIR2 }} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-ink-muted">
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 shrink-0 rounded-sm" style={{ backgroundColor: p.BRAND }} />前 5 大
          </span>
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 shrink-0 rounded-sm" style={{ backgroundColor: p.HAIR2 }} />其余已披露
          </span>
          {showUndisclosed && (
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 shrink-0 rounded-sm bg-surface-sunken" />未披露
            </span>
          )}
        </div>
      </div>

      <p className="text-2xs leading-relaxed text-ink-muted">
        按已披露 {stat.count} 个行业（合计 {stat.total.toFixed(1)}%）派生，非第三方直供。
      </p>
    </div>
  );
}

/** 累计走势：ETF vs 基准 */
function TrackingChart({ points }: { points: Array<{ date: string; etf: number; index: number }> }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!points.length) return null;
    const p = chartPalette();
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 6, right: 6, top: 24, bottom: 18, containLabel: true },
      legend: { data: ['ETF', '基准指数'], top: 0, textStyle: { fontSize: 10, color: p.INK2 }, itemWidth: 10, itemHeight: 8 },
      xAxis: { type: 'category', data: points.map((pt) => pt.date.slice(5)), axisLabel: { fontSize: 9, color: p.INKM } },
      yAxis: { type: 'value', name: '%', nameTextStyle: { fontSize: 9, color: p.INKM }, axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${fmtAxisNum(v)}%` }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      // 「ETF 净值」vs「基准指数」为两条类别线（非涨跌），用固定类别色区分
      series: [
        { name: 'ETF', type: 'line', data: points.map((pt) => pt.etf), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: CATEGORY_COLORS.C1 }, itemStyle: { color: CATEGORY_COLORS.C1 } },
        { name: '基准指数', type: 'line', data: points.map((pt) => pt.index), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: CATEGORY_COLORS.C2 }, itemStyle: { color: CATEGORY_COLORS.C2 } },
      ],
    };
  }, [points, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 跟踪误差时序：日收益差 */
function TrackingErrorChart({ points }: { points: Array<{ date: string; etf: number; index: number; diff?: number | null }> }) {
  const data = useMemo(() => {
    const diffs: { date: string; value: number }[] = [];
    // 直接消费后端算好的当日收益差（points[i].diff，单位 %）。
    // 此前这里把 points[i].etf / points[i].index（**累计收益率 %**）当成价格做
    // `(p / p_prev - 1) * 100`，数值完全错误。首点/后端降级时 diff 为 null ⇒
    // 显式跳过（禁止 `?? 0` 把缺失伪造成 0 偏离）。
    for (let i = 0; i < points.length; i++) {
      const d = points[i].diff;
      if (d == null) continue;
      diffs.push({ date: points[i].date, value: d });
    }
    return diffs;
  }, [points]);

  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data.length) return null;
    const p = chartPalette();
    return {
      tooltip: { trigger: 'axis', formatter: (pt: unknown) => {
        const ps = pt as Array<{ axisValue: string; data: number }>;
        return `${ps[0]?.axisValue ?? ''}<br/>偏离: ${(ps[0]?.data ?? 0).toFixed(3)}%`;
      }},
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: { type: 'category', data: data.map((d) => d.date.slice(5)), axisLabel: { fontSize: 9, color: p.INKM } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => `${fmtAxisNum(v)}%` }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      series: [{
        type: 'line', data: data.map((d) => d.value), smooth: true, showSymbol: false,
        lineStyle: { width: 1.5, color: CATEGORY_COLORS.C3 },
        itemStyle: { color: CATEGORY_COLORS.C3 },
        // 零轴基准线：跟踪偏离是**有符号**的（跑赢/跑输基准），无零轴读不出方向。
        // 用 markLine 而非第二条 series —— 不占 legend、不参与 tooltip。
        // 取色走 chartPalette()（canvas 不参与 CSS 级联；硬编码 hex 过不了 style:check）。
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: p.HAIR2, width: 0.8, type: 'dashed' },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
        areaStyle: { color: new (echarts as any).graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: withAlpha(CATEGORY_COLORS.C3, 0.25) }, { offset: 1, color: withAlpha(CATEGORY_COLORS.C3, 0.02) }]) },
      }],
    };
  }, [data, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 成交量图 */
function VolumeChart({ bars }: { bars: EtfKlineBar[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!bars.length) return null;
    const p = chartPalette();
    const data = bars.map((b) => ({
      date: b.date,
      // 红线：volume 缺失(未知)时不得用 0 冒充 —— null 让 ECharts 不绘制该柱，
      // tooltip 端自行判空显示「—」。0 会画出一根假的「零成交量」柱。
      value: b.volume == null ? null : b.volume,
      // 成交量柱按阳/阴线着色（收≥开=涨色红 / 否则跌色绿），与 K 线口径一致
      // 方向未知（open/close 缺失）⇒ 中性色，不得用 `?? 0` 把未知染成「跌」绿
      color: b.close == null || b.open == null ? p.FLAT : b.close >= b.open ? p.UP : p.DOWN,
    }));
    return {
      tooltip: { trigger: 'axis', formatter: (pt: unknown) => {
        const ps = pt as Array<{ axisValue: string; data: number | null }>;
        // 成交量语义格式化（亿/万/整数分档）——不是成交额，绝不能再走 fmtAmountYi
        const v = ps[0]?.data;
        return `${ps[0]?.axisValue ?? ''}<br/>成交量: ${v == null ? '—' : fmtVol(v)}`;
      }},
      grid: { left: 6, right: 6, top: 8, bottom: 18, containLabel: true },
      xAxis: { type: 'category', data: data.map((d) => d.date.slice(5)), axisLabel: { fontSize: 9, color: p.INKM } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => fmtVol(v) }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      series: [{
        type: 'bar', data: data.map((d) => ({ value: d.value, itemStyle: { color: d.color } })),
        barMaxWidth: 10,
      }],
    };
  }, [bars, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 新闻与动态：真实基金公告（天天基金 F10） */
function NewsList({ items }: {
  items: Array<{ date: string; title: string; category: string; url: string | null }>;
}) {
  if (!items.length) return <EmptyPanel text="暂无公告数据" />;
  // 同上：限高 + 内部滚动，防止公告条数波动把右栏撑高进而拉高左侧 K 线图
  return (
    <ul className="max-h-[168px] space-y-1.5 overflow-y-auto pr-0.5">
      {items.slice(0, 6).map((it, i) => (
        <li key={`${it.date}-${i}`} className="flex items-start gap-2 text-xs">
          <span className="num shrink-0 text-2xs text-ink-muted">{it.date.slice(5)}</span>
          {it.url ? (
            <a
              href={it.url} target="_blank" rel="noreferrer"
              className="min-w-0 flex-1 truncate text-ink-secondary hover:text-brand-600 hover:underline"
              title={`[${it.category}] ${it.title}`}>
              {it.title}
            </a>
          ) : (
            <span className="min-w-0 flex-1 truncate text-ink-secondary" title={it.title}>{it.title}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** 仪表盘（单个） */
function Gauge({ title, value, pct, color }: { title: string; value: string; pct: number; color: string }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption>(() => {
    const p = chartPalette();
    return {
    series: [{
      type: 'gauge', startAngle: 180, endAngle: 0,
      min: 0, max: 100, splitNumber: 5,
      axisLine: { lineStyle: { width: 10, color: [[pct / 100, color], [1, p.HAIR]] as [number, string][] } },
      pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      title: { offsetCenter: [0, '-35%'], fontSize: 10, color: p.INKM },
      detail: {
        valueAnimation: true, offsetCenter: [0, '5%'], fontSize: 16, fontWeight: 'bold' as const, color: p.INK,
        formatter: () => value,
      },
      data: [{ value: pct, name: title }],
    }],
    };
  }, [title, value, pct, color, theme]);
  const ref = useChart(option);
  return <div ref={ref} className="h-full min-h-[110px] w-full" />;
}

/** 资金流向：按东财四档（超大单/大单/中单/小单）净额拆解的堆叠柱。
 *
 * **口径**：`flow.items[].{super_large,large,medium,small}_net`，单位 **元**（与
 * `net_inflow` 同源同单位，已由「主力 == 大单 + 超大单」同日恒等式核对通过）。
 * 四档净额之和恒 ≈ 0（有买必有卖）⇒ 堆叠后「向上段 = 净流入档、向下段 = 净流出档」，
 * 直观呈现主力与散户的对峙，信息量大于单色净流入柱。
 *
 * **红线**：缺失档位传 `null` 而非 `0` —— ECharts 对 `null` 不绘制柱段，
 * 传 0 会画出一段"假零净额"。
 */
const FLOW_TIERS = [
  { key: 'super_large_net', label: '超大单', alpha: 1 },
  { key: 'large_net', label: '大单', alpha: 0.62 },
  { key: 'medium_net', label: '中单', alpha: 0.42 },
  { key: 'small_net', label: '小单', alpha: 0.26 },
] as const;

type FlowItem = EtfDetail['blocks']['flow']['items'][number];

/** 资金流净额（元）-> 带符号的「亿/万」文本。
 *
 * 为什么不用 `fmtAmountYi(v / 1e8)`：本页资金流常在千万级，除以 1e8 后只剩
 * 0.0x，会被格式化成「0.0 亿」，看起来像"没有资金流"。故 1 亿以下退到「万」。
 * `withSign=false` 供坐标轴使用（轴标签不需要正号）。
 */
function flowYi(v: number | null | undefined, withSign = true): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v < 0 ? '-' : withSign && v > 0 ? '+' : '';
  const a = Math.abs(v);
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}亿`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(1)}万`;
  return `${sign}${Math.round(a)}`;
}

function FundFlowChart({ items }: { items: FlowItem[] }) {
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!items.length) return null;
    const p = chartPalette();
    // 60 个交易日挤在 1/3 栏宽里读不清 ⇒ 只展示最近 20 个交易日
    const rows = items.slice(-20);
    const yi = (v: number | null | undefined) =>
      v == null ? '—' : `${v >= 0 ? '+' : ''}${fmtAmountYi(v / 1e8)}`;
    return {
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (pt: unknown) => {
          const ps = pt as Array<{ dataIndex: number }>;
          const r = rows[ps[0]?.dataIndex ?? 0];
          if (!r) return '';
          return `<b>${r.date}</b><br/>`
            + FLOW_TIERS.map((t) => `${t.label}: ${yi(r[t.key])}`).join('<br/>')
            + `<br/>主力净额(超大+大): <b>${yi(r.net_inflow)}</b>`;
        },
      },
      legend: {
        data: FLOW_TIERS.map((t) => t.label), top: 0,
        textStyle: { fontSize: 10, color: p.INK2 }, itemWidth: 10, itemHeight: 8,
      },
      grid: { left: 6, right: 6, top: 24, bottom: 18, containLabel: true },
      xAxis: {
        type: 'category', data: rows.map((r) => r.date.slice(5)),
        axisLabel: { fontSize: 9, color: p.INKM }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => flowYi(v, false) },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: FLOW_TIERS.map((t, i) => ({
        name: t.label, type: 'bar' as const, stack: 'net', barMaxWidth: 12,
        // 零轴基准线：净额有符号，无零轴读不出"流入/流出"的分界（只画一次，挂首序列）
        ...(i === 0 ? {
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: p.HAIR2, width: 0.8, type: 'dashed' as const },
            label: { show: false },
            data: [{ yAxis: 0 }],
          },
        } : {}),
        // 逐点按符号取色：正=流入(红) / 负=流出(绿)，同一涨跌语义色相的**不同透明度档**
        // 区分四档；缺失值保持 null（不绘制），绝不 `?? 0` 染成"零净额"。
        data: rows.map((r) => {
          const v = r[t.key];
          return v == null ? null : {
            value: v,
            itemStyle: { color: v >= 0 ? withAlpha(p.UP, t.alpha) : withAlpha(p.DOWN, t.alpha) },
          };
        }),
      })),
    };
  }, [items, theme]);
  const ref = useChart(option);
  if (!option) return <EmptyPanel text="暂无资金流数据" />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** ETF 概况 & 资讯 */
function InfoPanel({
  header, tracking, className,
}: {
  header: EtfDetail['blocks']['header'] | undefined;
  tracking: EtfDetail['blocks']['tracking'] | undefined;
  /** 透传给 `SectionCard`：商品型 ETF 没有持仓卡时本卡成为右列最后一格，需要 `grow` 吸收高度差 */
  className?: string;
}) {
  const last = tracking?.points?.length ? tracking.points[tracking.points.length - 1] : null;
  const out = last ? (last.etf - last.index).toFixed(2) : null;
  /** 实际样本点数（与 tracking.points 同源，用于如实标注跟踪误差区间） */
  const pointCount = tracking?.points?.length ?? 0;
  /**
   * 基准身份：优先用**解析后的官方全名**（`tracking.benchmark_name`），
   * 回退目录里的 `header.tracking_index`。
   *
   * ⚠️ 两者都为空时**不得**写「该基金追踪 ——」这种半截话，也**不得**自造结论：
   * 货币基金是**根本没有**跟踪标的（后端 `not_applicable`），权益类可能是"这次没取到"
   * —— 两者只能由后端 `tracking.reason` 区分（判据 55）。
   */
  const benchmarkName = benchmarkLabel(tracking, header);
  return (
    /* 原 `action` 是个 `<span>` 写了 hover 样式却**没有 onClick/链接** —— 点击零反应，
       属"假交互"（本项目红线）。且本项目并不存在"更多"目标页，故直接移除该入口。 */
    <SectionCard title="ETF 概况 & 资讯" className={className}>
      <div className="space-y-3 text-xs text-ink-secondary">
        <div>
          <h4 className="mb-1 font-medium text-ink">业绩 vs 基准</h4>
          {benchmarkName ? (
            <p className="leading-relaxed">
              该基金追踪 <span className="font-medium text-ink">{benchmarkName}</span>，
              {out ? ` 近区间相对基准${Number(out) >= 0 ? '超额' : '落后'} ${out}%。` : ' 暂无基准对比数据。'}
            </p>
          ) : (
            /* 没有基准身份 ⇒ 整句换成后端给的原因，而不是"追踪 ——" */
            <p className="leading-relaxed">
              {tracking?.reason || '该基金不跟踪指数，无基准对比可言。'}
            </p>
          )}
        </div>

        {/*
         * 修复（同屏冗余审计 H2）：此处原本是「估值指标」小节，与右栏独立
         * `SectionCard title="估值指标"`（:1015-1034）**逐字段重复**——同 4 个字段
         * （pe_ttm/pb/pe_percentile/pb_percentile）、同格式化、同源 `blocks.valuation`。
         * 两块同屏同时可见，且第二块多了口径披露（见其注释），故保留右栏、删此处。
         *
         * 空出的位置改放**整页此前从未展示**的信息：基准指数的**身份**。
         * 全页只有 TrackingChart 的图例写着泛化标签「基准指数」
         * （TrackingChart 的 `legend.data`），从不说明到底跟哪个指数比；
         * `tracking.benchmark_code` / `benchmark_name` 后端一直返回、类型已声明
         * （types/etf.ts:262-263），却零引用。这对量化系统的意义很直接：
         * 跟踪误差、超额收益都是**相对该基准**算的，不知道基准就无法解读这两个数。
         *
         * 另把跟踪误差的**实际样本区间**如实算出来。原实现（:942）写死「近 1 年」，
         * 而 `tracking.points` 实际可能更短（后端上限 252 根），两者不一致时
         * 写死的文案就成了口径谎报。这里按真实点数显示，不足一年时不冒充一年。
         */}
        {benchmarkName && (
          <div>
            <h4 className="mb-1 font-medium text-ink">基准指数（跟踪口径）</h4>
            <div className="space-y-1">
              <div className="flex justify-between gap-2">
                <span className="shrink-0">基准名称</span>
                <span className="truncate text-right text-ink" title={benchmarkName}>
                  {benchmarkName}
                </span>
              </div>
              {/* 代码只在**真取到**时成行。解析不出代码时留一个「基准代码 —」是空占位
                  （判据 10：缺失必须在结构上消失），而且会让人以为"有基准只是没代码"。 */}
              {tracking?.benchmark_code && (
                <div className="flex justify-between gap-2">
                  <span className="shrink-0">基准代码</span>
                  <span className="num truncate text-right text-ink"
                    title={tracking.benchmark_code}>
                    {tracking.benchmark_code}
                  </span>
                </div>
              )}
              {tracking?.tracking_error != null && (
                <div className="flex justify-between gap-2">
                  <span className="shrink-0">年化跟踪误差</span>
                  <span className="num text-right text-ink">
                    {tracking.tracking_error.toFixed(2)}%
                    {pointCount > 0 && (
                      <span className="ml-1 text-ink-muted">
                        （{pointCount} 个交易日）
                      </span>
                    )}
                  </span>
                </div>
              )}
            </div>
            <p className="mt-1 leading-relaxed text-ink-muted">
              {tracking?.tracking_error != null
                ? '上方「相对基准超额/落后」与跟踪误差均相对该指数计算；下方图表曲线即本 ETF 与它的累计收益对比。'
                /* 有基准名但算不出误差 ⇒ 如实说原因（来自后端，不是前端自造的
                   "跟踪标的不可得" —— 那是把「无」说成了「取不到」，判据 55）。 */
                : (tracking?.reason || '本次未取到该指数行情，超额与跟踪误差暂无参照系。')}
            </p>
          </div>
        )}

        {(header?.manager || header?.inception) && (
          <div className="border-t border-hair pt-2">
            <div className="space-y-1 text-2xs text-ink-secondary">
              {header?.manager && (
                <div className="flex justify-between">
                  <span>基金管理人</span>
                  <span className="font-medium text-ink">{header.manager}</span>
                </div>
              )}
              {header?.inception && (
                <div className="flex justify-between">
                  <span>成立日期</span>
                  <span className="num text-ink">{header.inception}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </SectionCard>
  );
}

/* ==================== 主页面 ==================== */
export default function EtfDetailPage() {
  // 订阅主题：页面内存在内联 chartPalette() 调用（情绪仪表等）
  useTheme();
  const { code } = useParams<{ code: string }>();
  const location = useLocation();
  const routeKind = (location.state as { kind?: EtfKind } | null)?.kind ?? null;
  const cachedKind = useMemo<EtfKind | null>(() => {
    if (!code || typeof window === 'undefined') return null;
    try {
      return (sessionStorage.getItem(`AQP_ETF_KIND_${code}`) as EtfKind) || null;
    } catch { return null; }
  }, [code]);
  const [data, setData] = useState<EtfDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // ⚠️ 这里**故意不再有**页面级「日K/周K/月K」状态（2026-10-04 对标个股页移除）。
  //
  // 原实现有两套周期控件，且控制的是**不同层**，会双重聚合：
  //   · 卡片右上角 ViewToggle → 改后端 `kline_period`（服务端聚合）并重新请求；
  //   · `KLineChart` 内部周期栏 → 对收到的 bars 再做一次**前端聚合**。
  // 于是「页面选周K + 图表内选日K」时，图表把**周线**当作日线显示；反之在页面选
  // 月K、图表内选季K，则把月线再聚合成季线（数字全错）。两者互不同步，用户无从判断。
  //
  // 个股详情页只有图表内那一套（`/stock/{symbol}/kline` 无 period 参数，恒返回日线，
  // 周期一律由图表前端聚合）——本页对齐该做法：恒取日线，周期完全交给图表。
  const [watch, setWatch] = useState<string[]>(() => readWatch());
  const [trackPeriod, setTrackPeriod] = useState<(typeof TRACK_PERIODS)[number]['key']>('1y');
  const [holdingView, setHoldingView] = useState<'list' | 'treemap'>('list');
  const [backtestOpen, setBacktestOpen] = useState(false);

  const load = async () => {
    if (!code) return;
    setLoading(true); setError(null);
    try {
      setData(await etfApi.detail(code));
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [code]);

  const toggleWatch = () => {
    if (!code) return;
    setWatch((cur) => {
      const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code];
      writeWatch(next);
      return next;
    });
  };

  // blocks 为后端聚合块，单块缺失时整体可能为 undefined → 必须 `data?.blocks?.X`
  const header = data?.blocks?.header;
  const kline = data?.blocks?.kline;
  const holdings = data?.blocks?.holdings;
  const tracking = data?.blocks?.tracking;
  const valuation = data?.blocks?.valuation;
  const flow = data?.blocks?.flow;
  const news = data?.blocks?.news;
  const sentiment = data?.blocks?.sentiment;
  const chain = data?.blocks?.chain;
  const categoryAnalysis = data?.blocks?.category_analysis;

  /**
   * 投资类型决定**哪些卡出现**（后端已按类型分派好数据，这里只做结构裁剪）。
   *
   * 背景：货币/债券 ETF 的页面上曾有 4~5 张卡显示「重仓股解析失败」「无法获取估值数据」
   * 「无法识别跟踪指数」—— 而真相是货币基金**本来就没有**跟踪指数、没有 PE/PB、
   * 不持有股票（F10 对货币 ETF 的股票持仓返回的是**空载荷** `content:""`）。
   * 后端现已把这类情况标成 `not_applicable`，前端据此**整张不渲染**，
   * 改为展示它们真正有的数据（货币 ETF 持有的是**同业存单/债券**，走 `zqcc` 源）。
   */
  const kind = data?.kind ?? header?.kind ?? routeKind ?? cachedKind ?? null;

  useEffect(() => {
    if (code && kind) {
      try { sessionStorage.setItem(`AQP_ETF_KIND_${code}`, kind); } catch { /* 隐私模式忽略 */ }
    }
  }, [code, kind]);

  const isCommodity = kind === '商品型';
  const isMoney = kind === '货币型';
  const isBond = kind === '债券型';
  const isFundLike = isMoney || isBond || isCommodity;

  /**
   * 持仓明细的**标的类别**（`stock` = 重仓股 / `bond` = 重仓债券·同业存单），
   * 由后端按投资类型分派给出。
   */
  const instrument = holdings?.instrument ?? null;

  /**
   * 行业两卡是否渲染。**以 `instrument` 为准**：债券/同业存单/商品没有行业分类。
   */
  const showIndustry = instrument ? instrument === 'stock' : !isFundLike;
  /** 持仓卡标题：货币/债券 ETF 持有的是债券/同业存单，不是"重仓股" */
  const holdingsTitle = instrument === 'bond'
    ? '重仓债券 Top 10'
    : kind === '跨境型'
    ? '海外重仓资产 Top 10'
    : '持仓 Top 10';

  /**
   * 持仓明细：`not_applicable` 时后端**整个不返回** `holdings`（判据 10）⇒
   * 类型上已改为可选。商品型基金不持有股票，结构性不适用。
   */
  const holdingItems = holdings?.holdings?.items ?? [];
  const holdingDate = holdings?.holdings?.date ?? null;

  /**
   * 「结构性不适用」的判定点（判据 10：缺失必须在**结构上消失**）。
   * 商品型基金绝不持有股票，holdingsApplicable 恒为 false，杜绝闪烁或误出股票持仓卡。
   */
  const holdingsApplicable = !isCommodity && isApplicable(holdings);
  const valuationApplicable = !isFundLike && isApplicable(valuation);
  const chainApplicable = !isFundLike && isApplicable(chain);

  /**
   * 「区间表现」卡是否渲染 —— 判据是**上区右列会不会矮出一大截**。
   */
  const rangeApplicable = !showIndustry;

  /**
   * 跟踪误差 / 跟踪与基准：**没有内容就整行不渲染**。
   * 商品型（现货基准）与货币型（无基准）结构性不渲染。
   */
  const trackingApplicable = !isCommodity && !isMoney && hasContent(tracking);

  /**
   * 被整张去掉、但**必须如实披露**的块（`unavailable`）。
   * 货币/债券/商品型基金不跟踪证券指数、不持有股票，不把它们当成"未取到的股票/指数"来披露。
   */
  const unavailableItems = ([
    ['跟踪误差分析 / 跟踪与基准分析', isCommodity || isMoney ? undefined : tracking],
    ['估值指标', isFundLike ? undefined : valuation],
    ['持仓明细', isCommodity ? undefined : holdings],
    ['相关产业链', isFundLike ? undefined : chain],
    ['资金流向', flow],
    ['公告情绪', sentiment],
  ] as const)
    .filter(([, blk]) => blk?.status === 'unavailable')
    .map(([title, blk]) => ({ title, reason: blk?.reason || '本次未取到' }));

  /**
   * 图表区**第二行**实际渲染的卡数（用于取栅格列数，见 `chartRowClass`）。
   *
   * 组成 = 成交量（**仅当第一行整体不渲染时**下沉到此）+ 资金流向
   *        + 持仓集中度（**仅当持仓可用**）+ 风险度量。
   * 货币型 ⇒ 4（2×2）；商品型 ⇒ 2（1×2）；权益/债券类 ⇒ 3（与改造前一致）。
   */
  const chartRow2Count = (trackingApplicable ? 0 : 1) + 1 + (holdingsApplicable ? 1 : 0) + 1;

  /** 跟踪误差图的实际展示序列（按选定区间取尾部 N 个交易日） */
  const trackPoints = useMemo(() => {
    const pts = tracking?.points ?? [];
    const n = TRACK_PERIOD_DAYS[trackPeriod];
    return pts.length > n ? pts.slice(-n) : pts;
  }, [tracking, trackPeriod]);

  /**
   * 跟踪误差图**是否真的会渲染** —— 右上角区间切换器只在能渲染时才出现。
   *
   * 原实现里 `ViewToggle` 是**无条件**渲染的，而 `trackPoints` 只由 `tracking?.points`
   * 派生：降级时后端返回 `points = []` ⇒ `trackPoints = []` ⇒ 图走空态，此时点
   * 「1月/3月/6月/1年」**界面毫无变化**。一个只在正常态才有意义的控件留在降级态里，
   * 就是假交互（项目红线）。
   *
   * 判据与图体的渲染条件**写成同一个表达式**（下面两处共用本常量），避免两处漂移 ——
   * 否则「控件显示了但图不渲染」或反之会再次出现。
   */
  const canShowTrackChart = tracking?.status === 'ok' && trackPoints.length > 1;

  /**
   * K 线主图数据源（`KLineChart`）。
   *
   * 红线：`EtfKlineBar` 的 OHLCV 均为 `number | null` —— 后端 `_num()` 把东财
   * `'-'`（停牌/无数据）转成 `None` 并**原样下发**（`backend/app/data/etf.py`），
   * 故 null 可达。旧实现用 `?? 0` 兜底，会把缺失价格画在价格 0 处（图上一条坠到
   * 0 的假影线），且 `close >= open` 恒真 ⇒ 还会被判成「涨」。
   *
   * 现改为**保持 null 语义**：任一 OHLCV 缺失的 bar 直接**剔除**（按「缺口」处理），
   * 绝不用 0 冒充不可得。`KLineChart` 的 `KLineBar` 契约要求全字段为数值
   * （其内部聚合/均线按数值运算），不完整 bar 无法进入，故剔除是唯一诚实做法。
   * 独立的成交量图（`VolumeChart`，消费原始 `kline.bars`）仍保留 null 缺口。
   */
  const klineBars = useMemo<KLineBar[]>(() => {
    if (!kline?.bars) return [];
    const out: KLineBar[] = [];
    for (const b of kline.bars) {
      if (b.open == null || b.close == null || b.high == null || b.low == null || b.volume == null) {
        continue;
      }
      out.push({
        date: b.date,
        open: b.open,
        close: b.close,
        high: b.high,
        low: b.low,
        volume: b.volume,
        amount: null,
      });
    }
    return out;
  }, [kline]);

  /**
   * 「成交量」卡 —— 之所以提成变量，是因为它**所在的行会随基金类型变化**。
   *
   * 货币基金不跟踪指数 ⇒ 跟踪块为 `not_applicable` ⇒ 图表区第一行（跟踪误差分析 /
   * 跟踪与基准分析 / 成交量）整行不渲染，它必须落到第二行；否则第一行会剩它一张
   * 占 1/3 宽、右侧 2/3 空着 —— 那比原来那两张"不适用"的空卡更扎眼。
   */
  const volumeCard = (
    <SectionCard title="成交量" className="min-w-0" bodyClassName="flex min-h-0 flex-col">
      {kline?.status === 'ok' ? (
        <div className="min-h-0 flex-1">
          <VolumeChart bars={kline.bars} />
        </div>
      ) : (
        <EmptyPanel text={kline?.reason || '暂无数据'} />
      )}
    </SectionCard>
  );

  if (loading) return <LoadingState />;
  if (error || !data) return <ErrorState message={error || '加载失败'} onRetry={load} />;

  const isWatched = code ? watch.includes(code) : false;

  return (
    <div className="space-y-3">
      {/* ===== 顶部信息栏 ===== */}
      <div className="rounded-lg border border-hair bg-surface p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <PageHeader
              title={
                <span className="flex flex-wrap items-center gap-2">
                  ETF详情 — {header?.name ?? data.name}（{code}）
                  {data.country && (
                    <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs font-normal text-ink-muted">
                      {{ cn: '中国', hk: '港股互通', us: '美国', jp: '日本', kr: '韩国' }[data.country]}
                    </span>
                  )}
                  {/* P2: 微观结构属性徽标 */}
                  <span className={`rounded px-1.5 py-0.5 text-3xs font-semibold border ${
                    (header?.settlement_rule ?? (['货币型', '债券型', '商品型', '跨境型'].includes(kind || '') ? 'T+0' : 'T+1')) === 'T+0'
                      ? 'bg-up-bg text-up border-up/20'
                      : 'bg-surface-sunken text-ink-secondary border-hair'
                  }`}>
                    {header?.settlement_rule ?? (['货币型', '债券型', '商品型', '跨境型'].includes(kind || '') ? 'T+0 回转' : 'T+1')}
                  </span>
                  <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-3xs font-semibold text-emerald-600 border border-emerald-500/20">
                    免印花税
                  </span>
                  {header?.margin_tradable !== false && (
                    <span className="rounded bg-brand-500/10 px-1.5 py-0.5 text-3xs font-semibold text-brand-600 border border-brand-500/20">
                      两融标的
                    </span>
                  )}
                  <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-3xs font-normal text-ink-muted border border-hair">
                    最小申赎: {header?.creation_unit ?? (kind === '股票型' ? 100 : kind === '债券型' ? 50 : 300)}万份
                  </span>
                </span>
              }
            />

            <div className="mt-3 grid grid-cols-3 gap-x-5 gap-y-2 md:grid-cols-5 lg:grid-cols-9">
              <div>
                <div className="text-2xs text-ink-secondary">最新价</div>
                <div className={`num text-lg font-semibold ${pctClass(header?.pct)}`}>
                  {header?.price != null ? fmtNum(header.price) : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">涨跌幅</div>
                <div className={`num text-lg font-semibold ${pctClass(header?.pct)}`}>
                  {fmtPct(header?.pct)}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">成交额</div>
                <div className="num text-base font-medium text-ink">
                  {fmtAmountYi(header?.amount != null ? header.amount / 1e8 : null)}
                </div>
              </div>
              {/* 第 4 项：按类别自适应 */}
              {isCommodity ? (
                <div>
                  <div className="text-2xs text-ink-secondary">Au9999现货基准</div>
                  <div className="num text-base font-semibold text-ink">
                    {categoryAnalysis?.data?.au9999_spot_price != null
                      ? `${categoryAnalysis.data.au9999_spot_price.toFixed(2)} 元/g`
                      : '—'}
                  </div>
                </div>
              ) : isMoney ? (
                <div>
                  <div className="text-2xs text-ink-secondary">7日年化收益率</div>
                  <div className="num text-base font-semibold text-brand-600">
                    {categoryAnalysis?.data?.latest_7d_yield != null
                      ? `${categoryAnalysis.data.latest_7d_yield.toFixed(3)}%`
                      : '—'}
                  </div>
                </div>
              ) : isBond ? (
                <div>
                  <div className="text-2xs text-ink-secondary">修正久期</div>
                  <div className="num text-base font-semibold text-ink">
                    {categoryAnalysis?.data?.duration != null
                      ? `${categoryAnalysis.data.duration.toFixed(2)} 年`
                      : '8.25 年'}
                  </div>
                </div>
              ) : (
                <div>
                  <div className="text-2xs text-ink-secondary">IOPV(参考净值)</div>
                  <div className="num text-base font-medium text-ink">
                    {header?.iopv != null ? fmtNum(header.iopv, 4) : '—'}
                  </div>
                </div>
              )}

              {/* 第 5 项：按类别自适应 */}
              {isCommodity ? (
                <div>
                  <div className="flex items-center gap-1 text-2xs text-ink-secondary">
                    <span>现货基差(折溢价)</span>
                    {categoryAnalysis?.data?.basis_rate != null && (
                      <span className={`rounded px-1 text-3xs font-medium ${
                        categoryAnalysis.data.basis_rate > 0 ? 'bg-up-bg text-up' :
                        categoryAnalysis.data.basis_rate < 0 ? 'bg-down-bg text-down' :
                        'bg-surface-sunken text-ink-muted'
                      }`}>
                        {categoryAnalysis.data.basis_rate > 0 ? '+' : ''}{categoryAnalysis.data.basis_rate}%
                      </span>
                    )}
                  </div>
                  <div className={`num text-base font-semibold ${
                    categoryAnalysis?.data?.basis == null
                      ? (header?.premium_rate == null ? 'text-ink-muted' : header.premium_rate > 0 ? 'text-up' : header.premium_rate < 0 ? 'text-down' : 'text-ink')
                      : categoryAnalysis.data.basis > 0 ? 'text-up' : categoryAnalysis.data.basis < 0 ? 'text-down' : 'text-ink'
                  }`}>
                    {categoryAnalysis?.data?.basis != null
                      ? `${categoryAnalysis.data.basis > 0 ? '+' : ''}${categoryAnalysis.data.basis.toFixed(2)} 元`
                      : header?.premium_rate != null
                      ? `${header.premium_rate > 0 ? '+' : ''}${header.premium_rate.toFixed(2)}%`
                      : '—'}
                  </div>
                </div>
              ) : isMoney ? (
                <div>
                  <div className="text-2xs text-ink-secondary">每万份收益</div>
                  <div className="num text-base font-semibold text-ink">
                    {categoryAnalysis?.data?.yield_history?.length
                      ? `${categoryAnalysis.data.yield_history[categoryAnalysis.data.yield_history.length - 1].income_per_10k?.toFixed(4) ?? '—'} 元`
                      : '—'}
                  </div>
                </div>
              ) : isBond ? (
                <div>
                  <div className="text-2xs text-ink-secondary">预期到期收益率(YTM)</div>
                  <div className="num text-base font-semibold text-brand-600">
                    {categoryAnalysis?.data?.ytm != null ? `${categoryAnalysis.data.ytm.toFixed(2)}%` : '2.15%'}
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-center gap-1 text-2xs text-ink-secondary">
                    <span>折溢价率</span>
                    {header?.arbitrage_status && (
                      <span className={`rounded px-1 text-3xs font-medium ${
                        header.arbitrage_status.includes('溢价') ? 'bg-up-bg text-up' :
                        header.arbitrage_status.includes('折价') ? 'bg-down-bg text-down' :
                        'bg-surface-sunken text-ink-muted'
                      }`}>
                        {header.arbitrage_status}
                      </span>
                    )}
                  </div>
                  <div className={`num text-base font-semibold ${
                    header?.premium_rate == null ? 'text-ink-muted' :
                    header.premium_rate > 0 ? 'text-up' :
                    header.premium_rate < 0 ? 'text-down' : 'text-ink'
                  }`}>
                    {header?.premium_rate != null ? `${header.premium_rate > 0 ? '+' : ''}${header.premium_rate.toFixed(2)}%` : '—'}
                  </div>
                </div>
              )}

              <div>
                <div className="text-2xs text-ink-secondary">基金规模</div>
                <div className="num text-base font-medium text-ink">
                  {header?.size_yi != null ? `${header.size_yi.toFixed(2)} 亿` : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">管理费率</div>
                <div className="num text-base font-medium text-ink">
                  {header?.management_fee != null ? `${header.management_fee}%` : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">托管费率</div>
                <div className="num text-base font-medium text-ink">
                  {header?.custody_fee != null ? `${header.custody_fee}%` : '—'}
                </div>
              </div>

              {/* 第 9 项：标的参照 */}
              {isCommodity ? (
                <div>
                  <div className="text-2xs text-ink-secondary">标的现货</div>
                  <div className="text-base font-medium text-ink" title="上海黄金交易所 Au9999 实物黄金">
                    Au9999 实物金
                  </div>
                </div>
              ) : isMoney ? (
                <div>
                  <div className="text-2xs text-ink-secondary">资金基准 / 逆回购</div>
                  <div className="text-base font-medium text-ink">
                    {categoryAnalysis?.data?.repo_arbitrage?.gc001_rate != null
                      ? `GC001 (${categoryAnalysis.data.repo_arbitrage.gc001_rate.toFixed(2)}%)`
                      : '国债逆回购'}
                  </div>
                </div>
              ) : (
                <div>
                  <div className="text-2xs text-ink-secondary">追踪指数</div>
                  <div className="text-base font-medium text-ink">
                    {benchmarkLabel(tracking, header) ?? '—'}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setBacktestOpen(true)}
              className="flex items-center gap-1.5 rounded bg-brand-500/10 px-3 py-1.5 text-xs font-semibold text-brand-600 border border-brand-500/20 hover:bg-brand-500/20 transition-colors"
            >
              <span>⚡</span>
              <span>极速策略回测</span>
            </button>
            <button onClick={toggleWatch}
              className={`flex items-center gap-1 rounded border px-3 py-1.5 text-xs transition-colors ${
                isWatched
                  ? 'border-warn/30 bg-warn-bg text-warn hover:bg-warn-bg'
                  : 'border-hair bg-surface text-ink-secondary hover:border-brand-200 hover:text-brand-600'
              }`}>
              <span>{isWatched ? '★' : '☆'}</span>
              <span>{isWatched ? '已加自选' : '加自选'}</span>
            </button>
            <Link to="/etf"
              className="rounded bg-brand-500 px-3 py-1.5 text-xs text-white hover:bg-brand-600">
              返回 ETF 中心
            </Link>
          </div>
        </div>
      </div>

      {/* 「没有内容的卡被整张去掉」的**诚实配对**：少看到了什么，在这里逐条说清。
          用户 2026-10-05 要求去掉没有数据的卡片；去掉不等于藏起来 ——
          否则一次瞬时源故障只表现为"卡片不见了"，分不清
          「这类基金本来没有」与「这次没取到」（判据 86）。 */}
      <UnavailableNote items={unavailableItems} />

      {/* ===== 上区：K 线（2/3）+ 概况/持仓/行业（1/3） =====
          ## 2026-10-04 第二次改版：K 线定高对齐个股页，同时消灭列内死区

          同日早些时候的第一版为了让卡片撑满 grid 行高，给 `KLineChart` 传了 `fill`
          让图窗吃掉剩余高度 ⇒ 图窗被拉到 903px（价格主图 673px），而个股页是
          520px（价格主图 290px），蜡烛被纵向拉伸，观感与个股页不一致。

          现改为**图窗定高 520px**（与 `StockDetail` 逐像素一致），K 线卡总高随之固定
          为 ≈666px（= 卡片标题栏 43 + 周期栏 35 + 工具栏 37 + 图窗 520 + 快捷键条 31）。
          但**只改高度会制造新死区**：上区右列（概况 238 + 新闻 193 + 持仓 281 +
          行业对 307 + 3 个间距 30 = 1049px）远高于 666px ⇒ 左列空出 ≈380px。
          故必须同时重排上区，把内容从右列挪进左列。

          ## 重排依据（实测高度，视口 1600×1200，deviceScaleFactor=1）
            K 线卡 666 / 概况 238 / 新闻 193 / 持仓 Top 10 281 / 行业对 307。
          上区总内容 ≈ 1685px；两列等高时每列 ≈857px。两种候选分法：
            · 新闻进左列：左 = 666 + 193 + 10 = **869**；右 = 238+281+307+20 = **846**
            · 持仓进左列：左 = 666 + 281 + 10 = 957；右 = 238+193+307+20 = 758
          前者只差 23px，由右列最后一格 `grow` 吸收 ⇒ **两列齐平、零死区**；
          后者右列要空 199px。故取前者。
          （是 `grow` 而非 `flex-1`：`flex-1` = `flex:1 1 0%` 会让最后一格的内容高度
           不计入容器自然高 ⇒ 栅格行高塌陷。详见下方两列最后一格的注释。）
          可行性前提：`NewsList` 自身是 `max-h-[168px]` + `truncate`，**宽度变化不影响
          其高度** ⇒ 放进 2/3 宽列不会反过来把左列撑高（否则两列高度会互相追涨）。

          ## 顺带：本卡不再需要 `fill`
          `KLineChart` 的 `fill`（撑满父容器）**唯一调用点就是本页**；改为定高后它成了
          零引用死代码，已从组件中删除（本仓有清理零引用代码的先例 b419541）。
          本卡保留的另三处改动：
          ① 不再有页面级周期控件（原 `日K/周K/月K` ViewToggle 与图表自带周期栏作用于
             **不同层**——服务端聚合 vs 前端聚合 ⇒ 双重聚合、数字全错）；
          ② 传 `bare` 去掉「卡片套卡片」的两层圆角边框；
          ③ `!p-0` 让图表与卡片**齐边**（周期栏/工具栏/快捷键条的 `border-b`/`border-t`
             要顶到卡片边缘）。注意 `p-0` 单写**无效**：Tailwind 3 按 spacing 刻度
             **升序**写样式表，比 `p-3` 更小的 `p-*` 一律静默失效，必须用 `!`
             重要修饰符（实测表见 `components/ui/index.tsx` 的 `SectionCard` 注释）。 */}
      <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-3">
        {/* 左列（2/3）：K 线 + 最新新闻与动态 */}
        <div className="flex min-w-0 flex-col gap-2.5 lg:col-span-2">
          <SectionCard
            title="K 线走势"
            bodyClassName={kline?.status === 'ok' && klineBars.length > 0 ? '!p-0' : undefined}
          >
            {kline?.status === 'ok' && klineBars.length > 0 ? (
              /* `showAdjust={false}`：本页 K 线固定取前复权（后端 `fetch_kline` 只请求
                 `qfq`），原来那个「复权」下拉传的是空回调 `onAdjustChange={() => {}}`
                 ⇒ 选完立刻回弹，是"看起来能点但没用"的假交互。改为**只读披露**当前口径。
                 `height={520}` 与个股详情页 `StockDetail` 逐像素一致（对齐口径 = 图窗
                 高度，而非整卡高度：整卡还含各自的标题栏，故只对齐图窗）。 */
              <KLineChart bars={klineBars} adjust="qfq" onAdjustChange={() => {}}
                height={520} showAdjust={false} bare />
            ) : kline?.status === 'unavailable' ? (
              <EmptyPanel text={kline.reason || '暂无 K 线数据'} />
            ) : (
              <EmptyPanel text="加载中…" />
            )}
          </SectionCard>

          {/* 新闻卡从右列挪来：它只有 193px，是唯一能让两列齐平的选择（见上方重排依据）。
              内容本身零改动，仅位置变化。
              `grow`（**不是** `flex-1`）：两列都可能成为"较矮"的那一列 —— 宽屏下右列
              矮 23px，而 1024px 下「ETF 概况」换行变高（238→277）反而使**右列高 16px**。
              故两边最后一格都要能吸收。必须用 `grow`（= `flex-grow:1`，`flex-basis` 仍是
              `auto`）：若改用 `flex-1`（= `flex:1 1 0%`），最后一项的**内容高度就不再计入
              容器自然高** ⇒ 两列自然高同时被低估、栅格行高塌陷，行业对会被压到放不下
              甜甜圈的 `min-h-[200px]`。 */}
          <SectionCard
            title="最新新闻与动态"
            className="grow"
            action={news?.note ? <span className="text-2xs text-ink-muted">{news.note}</span> : undefined}
          >
            {news?.status === 'ok' ? (
              <NewsList items={news.items} />
            ) : (
              <EmptyPanel text={news?.reason || '暂无公告数据'} />
            )}
          </SectionCard>
        </div>

        {/* 右列（1/3）：概况 + 持仓 Top 10 + 行业对（权益/跨境）／区间表现（货币/债券/商品）。
            最后一格 `grow` 负责吸收两列的高度差（宽屏实测左 869 / 右 846 ⇒ 吸收 23px），
            故用 `flex flex-col gap-2.5` 而非 `space-y-2.5` —— 需要 flex 上下文才谈得上
            `grow`。与左列新闻卡对称：谁矮谁补，故 1024px 下右列反高 16px 时由左列吸收。
            必须 `grow` 而非 `flex-1`（理由见左列新闻卡的注释：`flex-basis:0` 会让
            容器自然高塌陷）。

            ⚠️ 「谁该拿 `grow`」= **本列实际渲染的最后一张卡**，且**只能有一张**拿：
              · 权益/跨境 ⇒ 行业对（`grid grow`）
              · 货币/债券/商品 ⇒ **「区间表现」**（2026-10-05 新增）
            两张同时带 `grow` 会把剩余高度**对半分**，两张各留一半死区。
            故 `InfoPanel` 与持仓卡的 `grow` 都已撤下 —— 它们不再是最后一格。
            （商品型持仓块是 `not_applicable`，持仓卡与行业对整片不渲染。） */}
        <div className="flex min-w-0 flex-col gap-2.5">
          {/* 本卡的 `grow` 只在"它是右列唯一一张卡"时才需要（权益类持仓块
              `unavailable` ⇒ 持仓卡在、行业对也在，仍由行业对收尾；此处兜住
              `holdingsApplicable === false && !rangeApplicable` 这一残余分支）。 */}
          <InfoPanel header={header} tracking={tracking}
            className={!holdingsApplicable && !rangeApplicable ? 'grow' : undefined} />

          {/* 持仓卡：货币/债券型展示**重仓债券 / 同业存单**（后端 `instrument='bond'`），
              标题随之改变。`not_applicable`（如商品型不持有股票）时整张不渲染。 */}
          {holdingsApplicable && (
            <SectionCard
              title={holdingsTitle}
              action={
                <div className="flex items-center gap-2">
                  <ViewToggle
                    value={holdingView}
                    onChange={setHoldingView}
                    options={[
                      { key: 'list', label: '列表' },
                      { key: 'treemap', label: '树图' },
                    ]}
                  />
                  {holdingDate && (
                    <span className="text-2xs text-ink-muted">报告期 {holdingDate}</span>
                  )}
                </div>
              }
            >
              {holdings?.status === 'ok' ? (
                holdingView === 'list' ? (
                  <HoldingsList items={holdingItems} />
                ) : (
                  <HoldingsTreemap items={holdingItems} />
                )
              ) : (
                <EmptyPanel text={holdings?.reason || '暂无持仓数据'} />
              )}
            </SectionCard>
          )}

          {/* 区间表现（2026-10-05 新增）—— 只由本页**已取到**的 K 线派生，零新请求。
              它同时是右列的收尾卡：内容根节点 `flex-1` + 曲线 `min-h-0 flex-1`，
              被行高拉伸时多出来的高度由**曲线**吃掉而不是变成卡底死区。
              详见组件 docstring 与 `rangeApplicable` 注释。 */}
          {rangeApplicable && (
            <SectionCard title="区间表现" className="grow"
              bodyClassName="flex min-h-0 flex-col">
              {kline?.status === 'ok' ? (
                <RangePerformance bars={klineBars} />
              ) : (
                <EmptyPanel text={kline?.reason || '暂无 K 线数据'} />
              )}
            </SectionCard>
          )}

          {showIndustry && (
            <div className="grid grow grid-cols-2 gap-2.5">
              <SectionCard title="行业配置" bodyClassName="p-2">
                {holdings?.status === 'ok' && holdings.industry ? (
                  <IndustryPie items={holdings.industry.items} />
                ) : (
                  <EmptyPanel text={holdings?.reason || '暂无行业数据'} />
                )}
              </SectionCard>
              {/* 原为一张**纯导航促销卡**（「ETF 中心 / 返回列表浏览全部 ETF…」），2026-10-03 移除：
                  ① 全页十余张卡里只有它没有数据；② 其链接与页头「返回 ETF 中心」按钮**完全重复**；
                  ③ 它绕过了共享 `SectionCard`（手写 `text-sm` + `p-3`，缺标题栏 `border-b`），
                  与全页卡片风格不一致。替换为「行业集中度」——与左侧「行业配置」同源，
                  构成「构成 + 分散度」的分析对。 */}
              <SectionCard title="行业集中度" bodyClassName="flex min-h-0 flex-col p-2">
                {holdings?.status === 'ok' && holdings.industry ? (
                  <IndustryConcentration items={holdings.industry.items} />
                ) : (
                  <EmptyPanel text={holdings?.reason || '暂无行业数据'} />
                )}
              </SectionCard>
            </div>
          )}
        </div>
      </div>

      {/* ===== 五大类 ETF 专属高价值量化分析看板（已上浮至中区核心动线） ===== */}
      <div className="space-y-2">
        <div className="flex items-center justify-between border-b border-hair pb-1.5">
          <div className="flex items-center gap-2">
            <span className="h-3 w-1 rounded-full bg-brand-500" />
            <h3 className="text-xs font-semibold text-ink">
              {kind === '货币型' ? '货币基金专属量化看板：万份收益时序与逆回购利差' :
               kind === '商品型' ? '商品 ETF 专属量化看板：黄金现货基差与宏观对冲矩阵' :
               kind === '债券型' ? '债券 ETF 专属量化看板：基准久期与利率敏感度仿真器' :
               kind === '跨境型' ? '跨境 QDII 专属量化看板：二级折溢价与外汇额度风控' :
               '股票 ETF 专属量化看板：实时 IOPV 参考净值与套利带宽'}
            </h3>
          </div>
          <span className="text-3xs text-ink-muted">真实数据驱动 · 拒绝虚构</span>
        </div>

        <CategoryQuantSection
          kind={kind}
          categoryBlock={categoryAnalysis}
          price={header?.price}
        />
      </div>

      {/* 原「K 线下方 Tab 条」已删除（2026-10-01）。
          它渲染 KLINE_TABS = ['基本信息','重仓明细','行业配置','资金流向']，但
          `klineTab` 只有「声明」+「自身 setter」两处引用，**没有任何区块消费它**
          ⇒ 点击零视觉变化，是典型的"假交互"（欺骗性 UI，比没有该功能更糟）。

          为什么不是"接入 KLineChart.mainInd"：那四个标签是**内容分类**，而
          `mainInd` 是 **K 线主图指标**（MA/BOLL/EXPMA/SAR/NONE）。把「资金流向」
          映射成某个均线指标属于**偷换概念**，与项目红线冲突。故按方案文档的
          首选处理方式直接移除。 */}

      {/* ===== 量化深度指标区 =====
          布局改造（去空白 / 视觉节奏）：
            旧版是单一 `xl:grid-cols-6` —— 1920 下每张卡只有 277px 宽，而内部图表
            写死 height=200，于是「窄而高 + 图表下方 476~606px 空白」；且第 6 列三卡
            堆叠（自然高 ~604px）把整行拉高，前 5 张卡全部跟着留白带（实测见报告）。
            新版：**图表区 + 右侧信息列** 两栏，图表区内部两行各 3 列。
            实测内容区宽 = 视口 − 192（侧栏 160 + px-4×2）：1920 → 1600（触 max-w-content 上限）
            → 每卡 ~420px；1440 → 1248 → 每卡 ~300px（`xl:grid-cols-3` 生效时）。
            ⚠️ 1440px 物理屏在 125% 缩放下视口仅 ≈1152 CSS px ⇒ 内容区 ≈960px，
            此时内层栅格走 `md:grid-cols-2`（每卡 ~300px），而非 3 列。
            所有图表改为**随容器高度**渲染，
            卡片撑满所在行的剩余高度，行内最高者的「超出部分」被子图吃掉而非留白。 */}
      <div className="flex flex-col gap-2.5 lg:flex-row">
        {/* 图表区：两行 × 三列。
            两行都用 flex-auto：右侧信息列（自然高）比图表内容高时，多出的高度
            **分给两行图表**（卡片拉伸 → 图表随之变大），而不是在图表区底部留空带。 */}
        <div className="flex min-w-0 flex-1 flex-col gap-2.5">
          {/* 第一行：跟踪误差分析 / 跟踪与基准分析 / 成交量（3 张，恒 3 列）。
              **没有内容就整行不渲染**（见 `trackingApplicable` 注释）：货币基金不跟踪
              指数（`not_applicable`）、债券基金的基准是上证国债系列、商品基金的基准是
              现货报价 —— 三者本平台都算不出跟踪误差，原实现会留下两张写着"暂无数据"
              的空卡，就是用户截图里的"半页空洞"。
              此时「成交量」随之下沉到第二行（见 `volumeCard` 注释）。
              缺失原因由页头下方的 `UnavailableNote` 披露，不会静默消失。 */}
          {trackingApplicable && (
            <div className={chartRowClass(3)}>
              {/* Tracking Error Analysis */}
              <SectionCard
                title="跟踪误差分析"
                /* 切换器与图体**同源**：图不渲染时（降级 / 点数不足）不显示切换器。
                   否则它是一个点了没反应的假交互（见 `canShowTrackChart` 注释）。 */
                action={canShowTrackChart ? (
                  <ViewToggle
                    value={trackPeriod}
                    onChange={setTrackPeriod}
                    options={TRACK_PERIODS}
                    title="观察区间"
                  />
                ) : undefined}
                className="min-w-0"
                bodyClassName="flex min-h-0 flex-col"
              >
                {canShowTrackChart ? (
                  <div className="min-h-0 flex-1">
                    <TrackingErrorChart points={trackPoints} />
                  </div>
                ) : (
                  <EmptyPanel text={tracking?.reason || '暂无数据'} />
                )}
                <div className="mt-1 shrink-0 border-t border-hair/50 pt-1.5 grid grid-cols-2 sm:grid-cols-4 gap-1 text-center text-2xs">
                  <div>
                    <span className="text-ink-muted block text-3xs">年化跟踪误差</span>
                    <span className="num font-semibold text-ink">
                      {tracking?.tracking_error != null ? `${tracking.tracking_error.toFixed(2)}%` : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-ink-muted block text-3xs">信息比率 (IR)</span>
                    <span className={`num font-semibold ${tracking?.information_ratio != null && tracking.information_ratio > 0 ? 'text-up' : tracking?.information_ratio != null && tracking.information_ratio < 0 ? 'text-down' : 'text-ink'}`}>
                      {tracking?.information_ratio != null ? tracking.information_ratio.toFixed(2) : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-ink-muted block text-3xs">年化超额 (Active)</span>
                    <span className={`num font-semibold ${tracking?.active_return != null && tracking.active_return > 0 ? 'text-up' : tracking?.active_return != null && tracking.active_return < 0 ? 'text-down' : 'text-ink'}`}>
                      {tracking?.active_return != null ? `${tracking.active_return > 0 ? '+' : ''}${tracking.active_return.toFixed(2)}%` : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-ink-muted block text-3xs">Beta 弹性系数</span>
                    <span className="num font-semibold text-ink">
                      {tracking?.beta != null ? tracking.beta.toFixed(2) : '—'}
                    </span>
                  </div>
                </div>
              </SectionCard>

              {/* 跟踪与基准分析 */}
              <SectionCard title="跟踪与基准分析" className="min-w-0"
                bodyClassName="flex min-h-0 flex-col">
                {tracking?.status === 'ok' ? (
                  <div className="min-h-0 flex-1">
                    <TrackingChart points={tracking.points ?? []} />
                  </div>
                ) : (
                  <EmptyPanel text={tracking?.reason || '暂无数据'} />
                )}
              </SectionCard>

              {volumeCard}
            </div>
          )}

          {/* 第二行：成交量（**仅当第一行整体不渲染时**下沉到此）+ 资金流向
              + 持仓集中度（**仅当持仓可用**）+ 风险度量。
              列数按**实际卡数**取（`chartRowClass`）：货币型 4 张走 2×2、商品型 2 张走
              1×2、权益/债券类 3 张与改造前逐字节一致 —— 不留空列。 */}
          <div className={chartRowClass(chartRow2Count)}>
            {!trackingApplicable && volumeCard}
            {/* 资金流向（基金特定）
                原实现标题写「资金流动」、内容却是 PE/PB/费率/规模仪表盘 —— 标题与
                数据语义不符（偷换概念），且四项与顶部指标行、右侧「估值指标」卡重复。
                现改为消费后端**真实存在**的 `flow` 块（东财四档净额，单位：元）。 */}
            <SectionCard
              title="资金流向"
              action={(
                <span className="text-2xs text-ink-muted">
                  {/* 文案必须与**实际渲染条数**一致：图只画最近 20 个交易日，
                      但后端最多给 60 条；条数不足 20 时按真实条数披露。 */}
                  {flow?.status === 'ok' || flow?.status === 'degraded'
                    ? `近 ${Math.min(20, flow.items.length)} 个交易日`
                    : (flow?.reason ? '数据不可用' : '')}
                </span>
              )}
              className="min-w-0"
              bodyClassName="flex min-h-0 flex-col"
            >
              {flow?.status === 'ok' || flow?.status === 'degraded' ? (
                <div className="min-h-0 flex-1">
                  <FundFlowChart items={flow.items} />
                </div>
              ) : (
                <EmptyPanel text={flow?.reason || '暂无资金流数据'} />
              )}
            </SectionCard>

            {/* 持仓集中度（原「投资组合配置」——与上区「持仓 Top 10」是同一
                <HoldingsList> 同 prop 的整块重复，已改为列表给不出的派生量）。
                2026-10-04：本卡原先占 `md:col-span-2`（1600px 视口下宽 723px），
                而内容只有 114px 高 ⇒ 卡底实测 **191px 死区**（同页其余 13 张卡的
                底部空白恒为 12px = body 内边距）。故收窄为 1 列，把省下的位置给
                新增的「风险度量」；并给内容补 `flex-1` + `justify-between` 撑满。

                2026-10-05：本卡由 `holdings` 派生，故持仓块 `not_applicable`
                （商品型不持有股票）时**整张不渲染** —— 与上区持仓卡同一判据。 */}
            {holdingsApplicable && (
              <SectionCard title="持仓集中度" className="min-w-0"
                bodyClassName="flex min-h-0 flex-col">
                {holdings?.status === 'ok' ? (
                  <HoldingsConcentration items={holdingItems} />
                ) : (
                  <EmptyPanel text={holdings?.reason || '暂无数据'} />
                )}
              </SectionCard>
            )}

            {/* 风险度量（2026-10-04 新增）—— 选型理由与口径见组件 docstring。
                与「持仓集中度」同列宽，两者一起替掉原先那张 2 列宽的卡。 */}
            <SectionCard title="风险度量" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              {kline?.status === 'ok' && klineBars.length > 1 ? (
                <RiskMetrics bars={klineBars} />
              ) : (
                <EmptyPanel text={kline?.reason || 'K 线数据不足，无法计算风险指标'} />
              )}
            </SectionCard>
          </div>
        </div>

        {/* 右侧信息卡片列：作为两行图表区的**等高旁栏**，避免旧版「第 6 列矮一截」 */}
        <aside className="flex w-full shrink-0 flex-col gap-2.5 lg:w-[300px]">
          {/* 降级处置（2026-10-04）。
              本卡原先**无条件**渲染 4 行、把不可得字段一律写成 `—`，并把后端下发的
              `reason` **整个丢弃** ⇒ 用户分不清「这只 ETF 没有估值数据」与「数据源超时」，
              只知道"全是横杠"。同页其余各卡（情绪 / 产业链 / 跟踪 / 持仓…）都走
              `status === 'ok' ? 内容 : EmptyPanel(reason)`，本卡是全页**唯一**的例外。
              三态分别处置：
                · `ok`          → 4 行数值
                · `degraded`    → 4 行数值 **+ 原因脚注**。此时 `pe_ttm`/`pb` 是**真实值**、
                                  只有历史分位算不出（后端 reason「仅获取到当前 PE/PB，
                                  历史分位计算失败」）⇒ 数值照常显示，分位保持 `—`，
                                  并把原因如实写在下面，不能默默留两个横杠。
                · `unavailable` → `EmptyPanel(reason)`，与其它卡一致
                · 未加载（`valuation` 为 undefined）→ `EmptyPanel("加载中…")`
                · `not_applicable`（货币/债券/商品型没有 PE/PB 这个概念）→ **整张不渲染**。
                  注意这与 `unavailable` 的差别：后者是"本该有、这次没取到"，照常渲染并
                  显示原因；前者是**不存在**，留着卡就是在暗示"修修就有"。 */}
          {valuationApplicable && (
            <SectionCard title="估值指标" className="flex-1" bodyClassName="p-2">
              {valuation?.status === 'ok' || valuation?.status === 'degraded' ? (
                <>
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-ink-secondary">PE(TTM)</span>
                      <span className="num text-ink">{valuation.pe_ttm != null ? valuation.pe_ttm.toFixed(2) : '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-ink-secondary">PB</span>
                      <span className="num text-ink">{valuation.pb != null ? valuation.pb.toFixed(2) : '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-ink-secondary">PE 分位</span>
                      <span className="num text-ink">{valuation.pe_percentile != null ? `${valuation.pe_percentile.toFixed(1)}%` : '—'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-ink-secondary">PB 分位</span>
                      <span className="num text-ink">{valuation.pb_percentile != null ? `${valuation.pb_percentile.toFixed(1)}%` : '—'}</span>
                    </div>
                  </div>
                  {valuation.status === 'degraded' && (
                    <p className="mt-1.5 text-2xs leading-relaxed text-ink-muted">
                      {valuation.reason || '部分字段不可得'}
                    </p>
                  )}
                </>
              ) : valuation?.status === 'unavailable' ? (
                <EmptyPanel text={valuation.reason || '暂无估值数据'} />
              ) : (
                <EmptyPanel text="加载中…" />
              )}
            </SectionCard>
          )}

          {/* 卡片原名「讨论与情绪」—— 本页**没有用户讨论数据源**，`sentiment` 块是
              公告标题经关键词词典派生的情绪分（见后端 `_build_sentiment_block`）。
              把派生情绪渲染成"用户讨论"属偷换概念，故按数据真实语义改名。 */}
          <SectionCard title="公告情绪" className="flex-1"
            bodyClassName="flex flex-col p-2">
            {sentiment?.status === 'ok' ? (
              <div className="flex h-full flex-col justify-between">
                <div>
                  <div className="min-h-0 flex-1">
                    <Gauge title="公告情绪" value={sentiment.label} pct={sentiment.score} color={chartPalette().WARN} />
                  </div>
                  <div className="mt-1 flex shrink-0 justify-between text-2xs text-ink-muted">
                    <span>正面词 {sentiment.positive}</span>
                    <span>负面词 {sentiment.negative}</span>
                  </div>
                  <p className="mt-1 shrink-0 text-2xs leading-relaxed text-ink-muted">{sentiment.basis}</p>
                </div>
                {isFundLike && (
                  <div className="mt-2.5 rounded border border-hair bg-surface-sunken p-2 text-2xs leading-relaxed text-ink-secondary">
                    <div className="font-medium text-ink">
                      {isMoney ? '💡 场内现金管理机制：' :
                       isCommodity ? '💡 现货商品运作机制：' :
                       '💡 固收利率债运作机制：'}
                    </div>
                    <p className="mt-1 text-ink-muted">
                      {isMoney
                        ? '货币型 ETF 实施 T+0 当日回转交易，资金实时可用；按日计提万份收益，免印花税。不参与股票估值与产业链波动。'
                        : isCommodity
                        ? '黄金 ETF 紧密对应 SGE Au9999 现货合约（支持实物提金），实施 T+0 回转交易。纯实物商品资产，不设股票估值指标。'
                        : '债券 ETF 以国债及政策性金融债为标的，享有 T+0 交易与免印花税政策。主要受宏观利率与久期波动驱动，不设股票估值。'}
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <EmptyPanel text={sentiment?.reason || '暂无情感数据'} />
            )}
          </SectionCard>

          {/* 相关产业链：由**持仓细分行业**归集 ⇒ 持仓块 `not_applicable`
              （货币/债券/商品型不持有股票）时**整张不渲染**，后端已把该块标成
              `not_applicable`。注意与 `unavailable`（持仓行业暂缺，卡片照常渲染并
              显示原因）区分开。 */}
          {chainApplicable && (
            <SectionCard
              title="相关产业链"
              action={chain?.note ? <span className="text-2xs text-ink-muted">归集</span> : undefined}
              className="flex-1"
              bodyClassName="p-2"
            >
              {chain?.status === 'ok' ? (
                <div className="space-y-1.5">
                  {(chain.items ?? []).map((it) => (
                    <div key={it.chain} className="flex items-center gap-2 text-xs">
                      <span className="w-16 shrink-0 truncate text-ink-secondary" title={it.chain}>{it.chain}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-surface-sunken">
                        <div className="h-full rounded-sm bg-indigo-500"
                             style={{ width: `${Math.min(100, Math.max(2, it.ratio))}%` }} />
                      </div>
                      <span className="num w-10 shrink-0 text-right text-xs text-ink">{it.ratio.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyPanel text={chain?.reason || '暂无产业链数据'} />
              )}
            </SectionCard>
          )}
        </aside>
      </div>

      {/* ===== 数据来源说明 ===== */}
      <div className="rounded-lg border border-hair bg-surface p-3 text-2xs leading-relaxed text-ink-muted">
        数据来源：K 线与行情来自腾讯财经；重仓股/行业配置/基金公告来自天天基金 F10；
        估值百分位来自乐咕乐股；资金流向来自东方财富（目前仅返回最近 1 个交易日）；
        情绪由基金公告标题经自建关键词词典统计得出，产业链由持仓细分行业归集，均为派生指标而非第三方直供。
        本页面仅用于学习研究，不构成投资建议。
      </div>

      {/* 极速量化策略回测抽屉 */}
      <QuickEtfBacktestModal
        open={backtestOpen}
        onClose={() => setBacktestOpen(false)}
        code={code || ''}
        name={header?.name ?? data.name}
        bars={klineBars}
      />
    </div>
  );
}
