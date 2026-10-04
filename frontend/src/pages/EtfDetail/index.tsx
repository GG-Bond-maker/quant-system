/**
 * ETF 分析详情页（按设计稿重排）。
 *
 * 布局参考用户截图：
 *   1. 顶部：标题 + 关键指标行 + 自选/返回操作
 *   2. 上区：左侧 K 线 + 右侧资讯/新闻/持仓/行业
 *   3. 中区：（原「K 线下方 Tab 条」为假交互，已删除，见下方注释）
 *   4. 下区：跟踪误差分析 / 资金流动 / 跟踪与基准 / 投资组合配置 / 成交量
 *            + 右侧「估值指标 / 公告情绪 / 相关产业链」信息卡片
 *
 * 数据来自 /api/v1/etf/detail/{code}，所有块独立降级。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { useChart } from '@/utils/useChart';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

import { etfApi } from '@/api/etf';
import KLineChart from '@/components/charts/KLineChart';
import {
  ErrorState, LoadingState, PageHeader, PanelEmpty, SectionCard, ViewToggle,
} from '@/components/ui';
import type { EtfDetail, EtfIndustryItem, EtfKlineBar } from '@/types/etf';
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
 * 图的**实际高度**本就由外层容器（grid 拉伸 + `flex-1 min-h-0`）决定，各调用点的
 * `min-h-[Npx]` 类保留为兜底最小高度。 */

/* ==================== 通用占位 ==================== */
function EmptyPanel({ text = '暂无数据' }: { text?: string }) {
  return <PanelEmpty text={text} minH="min-h-[120px]" />;
}

/* ==================== 子组件 ==================== */

/** 前十大重仓股列表（带横向条）
 *
 * 加 `max-h` + 内部滚动：右栏是决定上区 grid 行高的那一列，列表再长也不能把行撑高
 * （行高变大 ⇒ 左侧 K 线图被拉得过高）。
 * `justify-between`：在「投资组合配置」这类**被行高拉伸**的卡里，行均匀铺满卡内
 * 高度（不留底部空带）；在自然高度的卡（上区「持仓 Top 10」）里 justify-between
 * 退化为普通排列，行为不变。 */
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
 * * **头部 10 只合计**：`Σ ratio`，与列表右列同源、可直接核对。
 * * **未披露比例**：`ratio` 是"占**净值**比例"，故全量应 ≈100%；明显小于 100
 *   说明后端只返回了部分持仓（源站仅前十大/分页）。此时**如实显示缺口**，
 *   绝不补满 —— 披露多少就是多少。
 * * **HHI**：`Σ wᵢ² × 10000`（标准口径，0~10000）。⚠️ 只在**已披露**持仓上算，
 *   未披露部分缺失时它是**下界**，不可与含全部持仓的行业口径直接比较。
 *   宁可写明口径，也不假装它是完整 HHI。
 */
function HoldingsConcentration({ items }: {
  items: Array<{ code: string; name: string; ratio: number | null; mv_yi: number | null }>;
}) {
  if (!items.length) return <EmptyPanel />;
  const top = items.slice(0, 10);
  const ratios = top.map((i) => i.ratio).filter((r): r is number => r != null);
  if (!ratios.length) {
    return <EmptyPanel text="持仓明细缺少占净值比例，无法计算集中度" />;
  }
  const sumTop = ratios.reduce((a, b) => a + b, 0);
  const hhi = ratios.reduce((a, b) => a + (b / 100) ** 2, 0) * 10000;
  const undisclosed = 100 - sumTop;
  const withRatio = items.filter((i) => i.ratio != null).length;

  return (
    <div className="flex min-h-0 flex-col gap-2 text-xs">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <div className="flex justify-between">
          <span className="text-ink-secondary">披露持仓数</span>
          <span className="num text-ink">{withRatio} 只</span>
        </div>
        <div className="flex justify-between">
          <span className="text-ink-secondary">头部 10 只合计</span>
          <span className="num text-ink">{sumTop.toFixed(2)}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-ink-secondary">未披露比例</span>
          <span className={`num ${undisclosed > 0.01 ? 'text-ink' : 'text-ink-muted'}`}>
            {undisclosed > 0.01 ? `${undisclosed.toFixed(2)}%` : '0%（已满）'}
          </span>
        </div>
        <div className="flex justify-between"
          title="Herfindahl-Hirschman 指数：Σ(占比²)×10000，仅按上面已披露持仓计算">
          <span className="text-ink-secondary">HHI（已披露口径）</span>
          <span className="num text-ink">{hhi.toFixed(0)}</span>
        </div>
      </div>

      {/* 只画**已披露**部分：留白本身就是"这里有没拿到的数据"的可视信号。 */}
      <div className="mt-0.5">
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
        HHI 按**已披露**持仓计算；后端未返回的持仓未计入，故它是集中度的下界，
        不宜与含全部持仓的行业口径直接比较。
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
  header, tracking,
}: {
  header: EtfDetail['blocks']['header'] | undefined;
  tracking: EtfDetail['blocks']['tracking'] | undefined;
}) {
  const last = tracking?.points?.length ? tracking.points[tracking.points.length - 1] : null;
  const out = last ? (last.etf - last.index).toFixed(2) : null;
  /** 实际样本点数（与 tracking.points 同源，用于如实标注跟踪误差区间） */
  const pointCount = tracking?.points?.length ?? 0;
  return (
    /* 原 `action` 是个 `<span>` 写了 hover 样式却**没有 onClick/链接** —— 点击零反应，
       属"假交互"（本项目红线）。且本项目并不存在"更多"目标页，故直接移除该入口。 */
    <SectionCard title="ETF 概况 & 资讯">
      <div className="space-y-3 text-xs text-ink-secondary">
        <div>
          <h4 className="mb-1 font-medium text-ink">业绩 vs 基准</h4>
          <p className="leading-relaxed">
            该基金追踪 <span className="font-medium text-ink">{header?.tracking_index ?? '—'}</span>，
            {out ? ` 近区间相对基准${Number(out) >= 0 ? '超额' : '落后'} ${out}%。` : ' 暂无基准对比数据。'}
          </p>
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
        <div>
          <h4 className="mb-1 font-medium text-ink">基准指数（跟踪口径）</h4>
          <div className="space-y-1">
            <div className="flex justify-between gap-2">
              <span className="shrink-0">基准名称</span>
              <span className="truncate text-right text-ink" title={tracking?.benchmark_name || undefined}>
                {tracking?.benchmark_name || '—'}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="shrink-0">基准代码</span>
              <span className="num truncate text-right text-ink" title={tracking?.benchmark_code || undefined}>
                {tracking?.benchmark_code || '—'}
              </span>
            </div>
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
          {tracking?.benchmark_name ? (
            <p className="mt-1 leading-relaxed text-ink-muted">
              上方「相对基准超额/落后」与跟踪误差均相对该指数计算；下方图表曲线即本 ETF 与它的累计收益对比。
            </p>
          ) : (
            <p className="mt-1 leading-relaxed text-ink-muted">
              后端未返回基准身份（该 ETF 的跟踪标的不可得），故超额与跟踪误差无可解读的参照系。
            </p>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

/* ==================== 主页面 ==================== */
export default function EtfDetailPage() {
  // 订阅主题：页面内存在内联 chartPalette() 调用（情绪仪表等）
  useTheme();
  const { code } = useParams<{ code: string }>();
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

  /** 跟踪误差图的实际展示序列（按选定区间取尾部 N 个交易日） */
  const trackPoints = useMemo(() => {
    const pts = tracking?.points ?? [];
    const n = TRACK_PERIOD_DAYS[trackPeriod];
    return pts.length > n ? pts.slice(-n) : pts;
  }, [tracking, trackPeriod]);

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
                      {{ cn: '中国', us: '美国', jp: '日本', kr: '韩国' }[data.country]}
                    </span>
                  )}
                </span>
              }
            />

            <div className="mt-3 grid grid-cols-3 gap-x-5 gap-y-2 md:grid-cols-4 lg:grid-cols-7">
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
              <div>
                <div className="text-2xs text-ink-secondary">基金规模</div>
                <div className="num text-base font-medium text-ink">
                  {header?.size_yi != null ? `${header.size_yi.toFixed(2)} 亿` : '—'}
                </div>
              </div>
              <div>
                <div className="text-2xs text-ink-secondary">追踪指数</div>
                <div className="text-base font-medium text-ink">{header?.tracking_index ?? '—'}</div>
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
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

      {/* ===== 核心图表区 ===== */}
      <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-3">
        {/* 左侧 K 线 */}
        {/* 左侧 K 线 —— 2026-10-04 对标个股详情页重做，三处改动：
            ① **不再有页面级周期控件**：原卡片右上角有个 `日K/周K/月K` ViewToggle，
               与图表内部自带的 `日K/周K/月K/季K/年K` 周期栏**同时存在且互不同步**，
               且控制的是**不同层**（前者改后端 `kline_period` 重新请求 = 服务端聚合；
               后者对收到的 bars 再做一次前端聚合）⇒ 会双重聚合、数字全错。
               个股详情页只有图表内那一套（`/stock/{symbol}/kline` 无 period 参数），
               本页已对齐：页面恒取日线，周期完全交给图表。故此处 `action` 整体删除。
            ② **去掉「卡片套卡片」**：`KLineChart` 根节点自带
               `rounded-lg border border-hair bg-surface`，再嵌进 `SectionCard` 就成了
               两层圆角边框相套（实测外卡片 930×1049 / 内图表 904×978），与个股页
               的单层外观不一致 ⇒ 传 `bare` 让图表只出内容，边框底色交给外层卡片。
            ③ **`!p-0`**：`bare` 后图表内容要与卡片**齐边**（周期栏/工具栏/快捷键条的
               `border-b`/`border-t` 要顶到卡片边缘），但 `SectionCard` body 硬编码 `p-3`。
               注意 `p-0` 单写**无效**：Tailwind 3 的 padding 工具类按样式表顺序排列，
               `.p-3` 生成在 `.p-0` 之后，同属性同优先级下**后者胜出**（已实测
               `class="p-3 p-2"` 计算值 = 12px）⇒ 必须用 `!` 重要修饰符。
               仅图表路径用 `!p-0`；降级态（`PanelEmpty`）保持原有 `p-3`，避免文案贴边。

            `flex` + SectionCard `w-full`：让卡片**撑满 grid 行高**（grid 默认
            items-stretch 只拉伸到列 div，卡片自身是 auto 高，之前因此在图表下方
            留下整列 ≈380px 的死区）。卡片撑满后 body 用 flex-1 min-h-0 把剩余高度
            交给 KLineChart（`fill`）→ 图窗实测高度随行高走，空白消失。 */}
        <div className="flex min-w-0 lg:col-span-2">
          <SectionCard
            title="K 线走势"
            className="w-full"
            bodyClassName={kline?.status === 'ok' && klineBars.length > 0
              ? 'flex min-h-0 flex-col !p-0'
              : 'flex min-h-0 flex-col'}
          >
            {kline?.status === 'ok' && klineBars.length > 0 ? (
              /* `showAdjust={false}`：本页 K 线固定取前复权（后端 `fetch_kline` 只请求
                 `qfq`），原来那个「复权」下拉传的是空回调 `onAdjustChange={() => {}}`
                 ⇒ 选完立刻回弹，是"看起来能点但没用"的假交互。改为**只读披露**当前口径。
                 `height={520}` 与个股详情页一致（`fill` 下该值只在首帧 ResizeObserver
                 测量前作兜底，取值贴近终值可少一次高度跳变）。 */
              <KLineChart bars={klineBars} adjust="qfq" onAdjustChange={() => {}}
                height={520} fill showAdjust={false} bare />
            ) : kline?.status === 'unavailable' ? (
              <EmptyPanel text={kline.reason || '暂无 K 线数据'} />
            ) : (
              <EmptyPanel text="加载中…" />
            )}
          </SectionCard>
        </div>

        {/* 右侧资讯/持仓/行业：这一列的内容决定上区 grid 行高 */}
        <div className="min-w-0 space-y-2.5">
          <InfoPanel header={header} tracking={tracking} />

          <SectionCard
            title="最新新闻与动态"
            action={news?.note ? <span className="text-2xs text-ink-muted">{news.note}</span> : undefined}
          >
            {news?.status === 'ok' ? (
              <NewsList items={news.items} />
            ) : (
              <EmptyPanel text={news?.reason || '暂无公告数据'} />
            )}
          </SectionCard>

          <SectionCard
            title="持仓 Top 10"
            action={holdings?.holdings?.date ? (
              <span className="text-2xs text-ink-muted">报告期 {holdings.holdings.date}</span>
            ) : undefined}
          >
            {holdings?.status === 'ok' ? (
              <HoldingsList items={holdings.holdings.items} />
            ) : (
              <EmptyPanel text={holdings?.reason || '暂无持仓数据'} />
            )}
          </SectionCard>

          <div className="grid grid-cols-2 gap-2.5">
            <SectionCard title="行业配置" bodyClassName="p-2">
              {holdings?.status === 'ok' ? (
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
              {holdings?.status === 'ok' ? (
                <IndustryConcentration items={holdings.industry.items} />
              ) : (
                <EmptyPanel text={holdings?.reason || '暂无行业数据'} />
              )}
            </SectionCard>
          </div>
        </div>
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
          <div className="grid flex-auto grid-cols-1 gap-2.5 md:grid-cols-2 xl:grid-cols-3">
            {/* Tracking Error Analysis */}
            <SectionCard
              title="跟踪误差分析"
              action={(
                <ViewToggle
                  value={trackPeriod}
                  onChange={setTrackPeriod}
                  options={TRACK_PERIODS}
                  title="观察区间"
                />
              )}
              className="min-w-0"
              bodyClassName="flex min-h-0 flex-col"
            >
              {tracking?.status === 'ok' && trackPoints.length > 1 ? (
                <div className="min-h-0 flex-1">
                  <TrackingErrorChart points={trackPoints} />
                </div>
              ) : (
                <EmptyPanel text={tracking?.reason || '暂无数据'} />
              )}
              {tracking?.tracking_error != null && (
                <p className="mt-1 shrink-0 text-center text-2xs text-ink-muted">
                  {/* 标注窗口：上方区间选择只裁图，本值恒为后端按完整窗口年化的结果 */}
                  年化跟踪误差 {tracking.tracking_error.toFixed(2)}%（近 1 年）
                </p>
              )}
            </SectionCard>

            {/* 跟踪与基准分析 */}
            <SectionCard title="跟踪与基准分析" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              {tracking?.status === 'ok' ? (
                <div className="min-h-0 flex-1">
                  <TrackingChart points={tracking.points} />
                </div>
              ) : (
                <EmptyPanel text={tracking?.reason || '暂无数据'} />
              )}
            </SectionCard>

            {/* Volume */}
            <SectionCard title="成交量" className="min-w-0"
              bodyClassName="flex min-h-0 flex-col">
              {kline?.status === 'ok' ? (
                <div className="min-h-0 flex-1">
                  <VolumeChart bars={kline.bars} />
                </div>
              ) : (
                <EmptyPanel text={kline?.reason || '暂无数据'} />
              )}
            </SectionCard>
          </div>

          {/* 第二行：资金流动仪表盘（1 份宽）+ 投资组合配置（2 份宽） */}
          <div className="grid flex-auto grid-cols-1 gap-2.5 md:grid-cols-2 xl:grid-cols-3">
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
                <HoldingsList> 同 prop 的整块重复，已改为列表给不出的派生量）。 */}
            <SectionCard title="持仓集中度" className="min-w-0 md:col-span-2"
              bodyClassName="flex min-h-0 flex-col">
              {holdings?.status === 'ok' ? (
                <HoldingsConcentration items={holdings.holdings.items} />
              ) : (
                <EmptyPanel text={holdings?.reason || '暂无数据'} />
              )}
            </SectionCard>
          </div>
        </div>

        {/* 右侧信息卡片列：作为两行图表区的**等高旁栏**，避免旧版「第 6 列矮一截」 */}
        <aside className="flex w-full shrink-0 flex-col gap-2.5 lg:w-[300px]">
          <SectionCard title="估值指标" className="flex-1" bodyClassName="p-2">
            <div className="space-y-1 text-xs">
              <div className="flex justify-between">
                <span className="text-ink-secondary">PE(TTM)</span>
                <span className="num text-ink">{valuation?.pe_ttm != null ? valuation.pe_ttm.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PB</span>
                <span className="num text-ink">{valuation?.pb != null ? valuation.pb.toFixed(2) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PE 分位</span>
                <span className="num text-ink">{valuation?.pe_percentile != null ? `${valuation.pe_percentile.toFixed(1)}%` : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-secondary">PB 分位</span>
                <span className="num text-ink">{valuation?.pb_percentile != null ? `${valuation.pb_percentile.toFixed(1)}%` : '—'}</span>
              </div>
            </div>
          </SectionCard>

          {/* 卡片原名「讨论与情绪」—— 本页**没有用户讨论数据源**，`sentiment` 块是
              公告标题经关键词词典派生的情绪分（见后端 `_build_sentiment_block`）。
              把派生情绪渲染成"用户讨论"属偷换概念，故按数据真实语义改名。 */}
          <SectionCard title="公告情绪" className="flex-1"
            bodyClassName="flex flex-col p-2">
            {sentiment?.status === 'ok' ? (
              <>
                <div className="min-h-0 flex-1">
                  <Gauge title="公告情绪" value={sentiment.label} pct={sentiment.score} color={chartPalette().WARN} />
                </div>
                <div className="mt-1 flex shrink-0 justify-between text-2xs text-ink-muted">
                  <span>正面词 {sentiment.positive}</span>
                  <span>负面词 {sentiment.negative}</span>
                </div>
                <p className="mt-1 shrink-0 text-2xs leading-relaxed text-ink-muted">{sentiment.basis}</p>
              </>
            ) : (
              <EmptyPanel text={sentiment?.reason || '暂无情感数据'} />
            )}
          </SectionCard>

          <SectionCard
            title="相关产业链"
            action={chain?.note ? <span className="text-2xs text-ink-muted">归集</span> : undefined}
            className="flex-1"
            bodyClassName="p-2"
          >
            {chain?.status === 'ok' ? (
              <div className="space-y-1.5">
                {chain.items.map((it) => (
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
        </aside>
      </div>

      {/* ===== 数据来源说明 ===== */}
      <div className="rounded-lg border border-hair bg-surface p-3 text-2xs leading-relaxed text-ink-muted">
        数据来源：K 线与行情来自腾讯财经；重仓股/行业配置/基金公告来自天天基金 F10；
        估值百分位来自乐咕乐股；资金流向来自东方财富（目前仅返回最近 1 个交易日）；
        情绪由基金公告标题经自建关键词词典统计得出，产业链由持仓细分行业归集，均为派生指标而非第三方直供。
        本页面仅用于学习研究，不构成投资建议。
      </div>
    </div>
  );
}
