/**
 * ETF表现 折线图：多条 ETF 序列的累计涨跌幅（或净值）对比。
 *
 * 序列不可用时（日韩本土标的行情源不可达）该条不画入图中，
 * 并在图下方单独列出，避免用 0 值伪造曲线。
 *
 * ## 纵轴定轴：两种模式 + 「超限呈现」（2026-10-03）
 *
 * 背景：热门榜按涨跌幅取前 N 时，会混入**杠杆型**标的（如 SOXL 半导体3x：
 * 1 年 max ≈ +669%，其余宽基仅 ±30%）。若把这类离群序列纳入定轴，其余序列
 * 会被压进轴的极小一段，几乎不可读。故默认仍**按序列剔除离群后定轴**（见下方
 * v1/v2/v3 修复记录），但被剔除的序列**不隐藏、不改数**，而是：
 *   ① 画成**虚线 + 降透明度**（视觉上标记"这条跑到轴外了"）；
 *   ② 图内加**不被裁切**的 `graphic` 标注，点明其真实峰值「超出纵轴」；
 *   ③ 图下披露文案列出被剔标的名称与真实峰值，并给出**「显示全部」开关**。
 * 该开关把纵轴切回**全量极值**（超限呈现 → 完整呈现），供用户主动查看全貌。
 * ⚠️ 判定离群的算法（`OUTLIER_RATIO=3` / 中位振幅）**语义不变**，只新增"模式"
 * 与"呈现"，不改截断口径。
 */
import { useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { PanelEmpty } from '@/components/ui';
import { fmtAxisNum } from '@/utils/format';
import { useChart } from '@/utils/useChart';
import type { EtfPerformance } from '@/types/etf';

// 多条对比线为**类别色**（区分是哪只 ETF，非涨跌），沿用 CATEGORY_COLORS.SEQ 固定色相
const COLORS = CATEGORY_COLORS.SEQ;

/**
 * 峰值文案：pct 口径带 `%`（正值补 `+`），price 口径为纯数值。
 * 一律经 `fmtAxisNum` 去掉 IEEE754 浮点尾巴，**数值取自真实数据，严禁写死**。
 */
export function formatPeak(v: number, isPct: boolean): string {
  return isPct ? `${v > 0 ? '+' : ''}${fmtAxisNum(v)}%` : fmtAxisNum(v);
}

/**
 * 由 `option` 的 useMemo **顺带算出**、供 JSX 读取的「纵轴信息」。
 *
 * 用 ref 而不是 state：这些值在 useMemo 里已经算过，若改用 state 会触发一次
 * 额外渲染（且首帧渲染必然拿到旧值）。ref 在当前渲染的 useMemo 执行后即被赋值，
 * 而 JSX 的求值发生在**其后** ⇒ 同一帧内读到的一定是本帧算出的值，不会滞后。
 */
interface AxisInfo {
  /** 是否存在被判定为离群、且默认模式下会被剔除的序列（决定「显示全部」开关是否渲染） */
  hasOutlier: boolean;
  /** 当前选中模式下**确实被剔除**（画在轴外）的序列，含真实极值 */
  dropped: Array<{ name: string; code: string; min: number; max: number }>;
  /** 当前选中模式的纵轴是否无法覆盖全部真实极值（即存在被截断的数据） */
  clipped: boolean;
}

/**
 * height 仅作为最小高度兜底：容器在 flex/grid 里会被拉伸，图表跟随容器高度铺满。
 */
export default function PerformanceChart({ data, height = 260 }: {
  data: EtfPerformance | null; height?: number;
}) {
  const theme = useTheme();
  /**
   * 「显示全部标的」开关（默认 false = 现状）。
   *
   * false：纵轴按**剔除离群后**的量程（超限序列画在轴外，虚线 + 图内标注呈现）。
   * true ：纵轴切到**全量极值**（所有序列都在轴内，标注随之消失）。
   */
  const [showAll, setShowAll] = useState(false);

  /** 纵轴信息 ref（见 `AxisInfo` 说明；沿用旧 `clipFlagRef` 的"useMemo 顺带算出"理由） */
  const axisInfoRef = useRef<AxisInfo>({ hasOutlier: false, dropped: [], clipped: false });

  const okSeries = useMemo(
    () => (data?.series ?? []).filter((s) => s.status === 'ok' && s.points.length >= 2),
    [data],
  );
  const deadSeries = useMemo(
    () => (data?.series ?? []).filter((s) => !(s.status === 'ok' && s.points.length >= 2)),
    [data],
  );

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!okSeries.length) return null;
    // 以第一条序列的日期为准对齐 x 轴（各标的交易日基本一致）
    const dates = okSeries[0].points.map((p) => p.date);
    const isPct = (data?.metric ?? 'pct') === 'pct';
    const p = chartPalette();
    /**
     * 纵轴量程：按**序列**剔除离群后定轴。
     *
     * 🔴 2026-10-01 三次修复的记录（前两版都错，务必读完再改）：
     *
     * **v1（等比留白）**：`[vMin - span*8%, vMax + span*8%]`。
     *   无效 —— `vMax` 完全由最极端的那条序列决定，留白比例不改变"极值主导量程"。
     *   实测：1 条 +250% 曲线会把其余 4 条（±5%）压进轴的底部 **8.6%** 高度。
     *
     * **v2（对全部观测取 2%~98% 分位）**：**仍然无效，且更隐蔽**。
     *   原因：离群的是"**整条序列**"，它占全部观测的比例 = **1/N**。
     *   N=5（热门榜常取 5 只）⇒ 单条离群占 **20%** 的观测，远大于 2% 分位 ⇒ **根本切不掉**。
     *   实测：轴范围仍被撑到 2.22，低位序列只占 **9.0%**（与 v1 的 8.6% 几乎无差）。
     *   ⇒ **元教训：分位数只在"离群点是少数个别观测"时有效；
     *      当离群以"整条序列"为单位时，必须换判定维度。**
     *
     * **v3（当前，按序列判定离群）**：换维度 —— 先算每条序列自己的振幅，再按振幅排序，
     *   剔除振幅最大的少数序列后再取量程。这样剔的是"整条线"，与离群的实际形态一致。
     *
     * 设计要点：
     * - 「离群」判据用**振幅**（`max - min`）而非绝对值：一条从 0 涨到 250% 的线，
     *   和一条从 0 跌到 -250% 的线，对量程的破坏是等价的。
     * - 剔除比例取 **1/3 向下取整**（5 条 ⇒ 剔 1 条；3 条 ⇒ 剔 1 条；8 条 ⇒ 剔 2 条），
     *   上限 2 条：既能让主区间展开，又不至于把图"截得看不见真实跨度"。
     * - **被剔除的序列不隐藏、不改数**：它照常绘制，出界部分由 ECharts 画在轴顶，
     *   配合零轴 `markLine` + tooltip 真实值仍可读出量级；本版进一步把它画成
     *   **虚线 + 降透明度**并在图内加 `graphic` 标注，明确"超出纵轴"。
     * - 剔除后若区间退化（数据几乎全等），回退到真实极值，避免 span=0 把轴压成一条线。
     *
     * **两种模式**：`showAll=false`（默认）用「剔除离群」组；`showAll=true` 用**全量**组。
     * `span` 一律由**被选中**的那组算，故滚轮/开关切换时无需重算两遍。
     */
    /** 每条序列的振幅（max-min）+ 真实极值 + 标识（name/code，供披露与标注） */
    const perSeries = okSeries.map((s) => {
      const vs = s.points.map((pt) => pt.value).filter((v) => Number.isFinite(v));
      if (!vs.length) return { name: s.name, code: s.code, amp: 0, min: 0, max: 0 };
      const mn = Math.min(...vs), mx = Math.max(...vs);
      return { name: s.name, code: s.code, amp: mx - mn, min: mn, max: mx };
    });
    const allMin = perSeries.length ? Math.min(...perSeries.map((x) => x.min)) : 0;
    const allMax = perSeries.length ? Math.max(...perSeries.map((x) => x.max)) : 0;

    /**
     * 判定"谁是离群序列"：用**中位振幅**做基准，只剔除显著超出者。
     *   `amp > medianAmp * OUTLIER_RATIO`
     *
     * ⚠️ 为什么必须先做这个判定，而不是无条件剔振幅最大者（v3 首版踩的坑）：
     *   若 5 条序列振幅都相近，无条件剔掉 1 条会把轴范围**凭空收窄**，
     *   反而让**没被剔的那条线冲出轴外** —— 明明没有离群，却制造了"假截断"。
     *   实测：5 条全正常时，无条件剔 1 条得到轴 0.043，而真实最大 0.05 ⇒ 被迫披露截断，
     *   属于**自己制造的问题**。故必须"确实存在显著离群才剔"。
     *
     * 系数取 `*3` 的依据：振幅差 3 倍以内属于正常量级差异（同样都是宽基 ETF 的不同品种），
     * 超过 3 倍才视为"量级不同"（如科创/创业板类 vs 宽基）。倍数可调，但**不宜更小**，
     * 否则会把正常的强势品种误判为离群。
     */
    const amps = perSeries.map((x) => x.amp).sort((a, b) => a - b);
    const medianAmp = amps.length
      ? (amps.length % 2
        ? amps[(amps.length - 1) / 2]
        : (amps[amps.length / 2 - 1] + amps[amps.length / 2]) / 2)
      : 0;
    const OUTLIER_RATIO = 3;
    const isOutlier = (amp: number) => medianAmp > 0 && amp > medianAmp * OUTLIER_RATIO;

    /**
     * 选出「默认会被剔除」的离群序列（**与 showAll 无关**：它决定 `hasOutlier`
     * 与开关是否可见，故必须在两种模式下都算出来）：
     * - 至少保留 **2** 条（不能因为剔离群把图剔空）；
     * - 上限 **2** 条（剔太多会让纵轴失去代表性）。
     */
    let dropSet = new Set<typeof perSeries[number]>();
    if (perSeries.length >= 3 && medianAmp > 0) {
      const outliers = perSeries.filter((x) => isOutlier(x.amp));
      if (outliers.length > 0) {
        const maxDrop = Math.min(2, perSeries.length - 2);
        // 按振幅降序剔最"撑轴"的，最多 maxDrop 条
        dropSet = new Set(
          [...perSeries].sort((a, b) => b.amp - a.amp)
            .filter((x) => isOutlier(x.amp))
            .slice(0, maxDrop),
        );
      }
    }
    /** 是否存在离群（开关可见性；与当前模式无关，保证切到"显示全部"后开关仍在） */
    const hasOutlier = dropSet.size > 0;

    /**
     * 按 `showAll` **取一组**参与定轴：
     * - 默认（false）：保留项 = 全量 − 离群（剔除离群后定轴）；
     * - 显示全部（true）：保留项 = 全量。
     */
    const kept = showAll ? perSeries : perSeries.filter((x) => !dropSet.has(x));
    /** 当前模式下**确实被剔除**（画在轴外）的序列；「显示全部」时为空 */
    const droppedList = showAll ? [] : perSeries.filter((x) => dropSet.has(x));

    const baseMin = kept.length ? Math.min(...kept.map((x) => x.min)) : allMin;
    const baseMax = kept.length ? Math.max(...kept.map((x) => x.max)) : allMax;
    /** 纵轴留白比：定轴后再留 8%，让线不贴边 */
    const PAD = 0.08;
    const span = baseMax - baseMin || 1;
    // 涨跌幅口径：纵轴**必须包含 0**（否则「跑赢基准」的视觉基准丢失）
    const yMin = isPct ? Math.min(0, baseMin) - span * PAD : baseMin - span * PAD;
    const yMax = isPct ? Math.max(0, baseMax) + span * PAD : baseMax + span * PAD;

    /**
     * 是否需要披露"纵轴已截断"（存在画在轴外的真实极值）。
     * ⚠️ 由 useMemo **顺带算出并缓存**（`axisInfoRef`），供下方 JSX 读取 ——
     * 不在 JSX 里重算：重算需要再次遍历全部序列，而 useMemo 里已经算过了，
     * 重算既浪费又可能与绘图用的量程**不一致**（那会导致"披露了却没截断"的假告警）。
     */
    const clipped = allMax > yMax || allMin < yMin;
    axisInfoRef.current = {
      hasOutlier,
      dropped: droppedList.map((d) => ({ name: d.name, code: d.code, min: d.min, max: d.max })),
      clipped,
    };

    /**
     * 图内「超出纵轴」标注（ECharts `graphic`，**不受坐标轴裁剪**）。
     * 数值一律来自被剔除序列的真实峰值（`formatPeak`），pct 口径才带 `%`。
     * 仅在**确有画在轴外的序列**时出现（即默认模式）；切到"显示全部"后自然消失。
     */
    const droppedPeaks = droppedList.length
      ? `▲ ${droppedList.map((d) => `${d.code} 峰值 ${formatPeak(d.max, isPct)}`).join(' · ')}（超出纵轴）`
      : '';

    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}${isPct ? '%' : ''}` },
      // top 由 30 收到 22：legend 单行 10px 字号只需 ~14px 行高，30 会白留 16px
      // bottom 由 4 提到 6：给 x 轴末端的日期标签留出不被裁切的最小空间
      grid: { left: 4, right: 10, top: 22, bottom: 6, containLabel: true },
      legend: { top: 0, textStyle: { fontSize: 10, color: p.INK2 }, itemWidth: 12, itemHeight: 8 },
      xAxis: {
        type: 'category', data: dates, boundaryGap: false,
        axisLabel: { fontSize: 9, color: p.INKM, hideOverlap: true, margin: 6 },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'value', min: yMin, max: yMax,
        // 本轴 min/max 是**显式浮点**（按序列剔除离群后算出的 yMin/yMax），刻度步长不是整数，
        // 用字符串模板 `'{value}%'` 会把 `70.70239999999999%` 原样显示出来。
        // 故改函数式 formatter 并四舍五入（`fmtAxisNum`）。**不动纵轴定轴算法与截断披露逻辑**。
        axisLabel: { fontSize: 9, color: p.INKM, margin: 6,
          formatter: (v: number) => (isPct ? `${fmtAxisNum(v)}%` : fmtAxisNum(v)) },
        splitLine: { lineStyle: { color: p.SUNKEN } },
        // 涨跌幅：y=0 加一条淡基准线，替代"靠空网格猜零点"
        ...(isPct ? {
          axisLine: { show: false },
        } : {}),
      },
      // 图内标注：不被裁切、不参与 legend / tooltip（`silent`），仅陈述"某条峰值超轴"
      ...(droppedPeaks ? {
        graphic: {
          elements: [{
            type: 'text' as const, right: 8, top: 2, silent: true,
            style: { text: droppedPeaks, fontSize: 10, fill: p.WARN },
          }],
        },
      } : {}),
      series: okSeries.map((s, i) => {
        // 当前模式下被剔除者：虚线 + 降透明度，明确"这条画在轴外"
        const dropped = droppedList.includes(perSeries[i]);
        const color = COLORS[i % COLORS.length];
        return {
          name: s.name,
          type: 'line' as const,
          data: s.points.map((pt) => pt.value),
          smooth: true,
          symbol: 'none' as const,
          lineStyle: {
            width: dropped ? 1.2 : 1.6, color,
            type: dropped ? 'dashed' as const : 'solid' as const,
            opacity: dropped ? 0.5 : 1,
          },
          itemStyle: { color, opacity: dropped ? 0.5 : 1 },
          // 涨跌幅口径下画 0% 基准线；**用 markLine 而非第二条 series**，不占 legend
          ...(isPct && i === 0 ? {
            markLine: {
              silent: true, symbol: 'none',
              lineStyle: { color: p.INKM, width: 0.8, type: 'dashed' as const, opacity: 0.55 },
              label: { show: false },
              data: [{ yAxis: 0 }],
            },
          } : {}),
        };
      }),
    };
  }, [okSeries, data, theme, showAll]);

  // 🔴 G-5 + D-6（2026-10-02）：改用全项目统一的 `utils/useChart`。
  //
  // 原实现是 `useEffect(..., [option])` + `echarts.init()`：
  //   ① **实例抖动（G-5）**：option 一变就 `dispose()` + `init()` 重建全新实例，
  //      每次数据刷新都要付一遍实例构建 + canvas 重建的代价；
  //   ② **尺寸失跟（D-6）**：只监听 `window.resize`。而本图的容器高度来自
  //      grid/flex 拉伸（主区栅格 `flex-1` 分配自由空间），「窗口没变、容器却变高/变矮」
  //      的时机（数据加载完成、右栏内容变化、栅格从单列切双栏）**不会触发 window.resize**；
  //      而 zrender 的 canvas 是 `position:absolute`、像素尺寸在 `init` 时刻写死、不参与
  //      CSS 自适应 ⇒ 图表停留在旧尺寸被拉伸/压扁。
  //
  // `useChart` 一次解决两者：`init` 只在节点挂载时发生一次 + `ResizeObserver` 观察**容器本身**
  // + 复用实例调 `setOption(option, true)`。
  //
  // ⚠️ **notMerge 语义必须保留**（`useChart` 内部已用 `setOption(o, true)`）：
  // `setOption` 默认是 merge，新 option 的 series 按**下标**与旧 series 合并，新数组更短时
  // 多出来的旧 series **不会被删除**。而本图 series 数量是会变的：① 热门榜只取前 5 只 A 股，
  // 榜里 A 股少于 5 只时会变短；② `okSeries` 会剔除 `status !== 'ok'` 或 `points.length < 2`
  // 的序列（次新 ETF 的 K 线可能不足 2 根）。两者任一发生，merge 就会把上一次的曲线**残留**
  // 在图上，表现为"切了周期/切了排序，但图上还留着上一份数据的线"。
  const ref = useChart(option);

  if (!option) {
    // 降级时把后端给出的 reason 一并展示（冷路径超时/异常不再只见"空"）
    const reason = data?.data_freshness?.reason
      || (data?.series ?? []).find((s) => s.reason)?.reason;
    return (
      <div style={{ minHeight: height }} className="h-full">
        <PanelEmpty text={reason ? `暂无可绘制的行情序列：${reason}` : '暂无可绘制的行情序列'}
          minH="h-full" />
      </div>
    );
  }
  const info = axisInfoRef.current;
  const isPct = (data?.metric ?? 'pct') === 'pct';
  return (
    <div className="flex h-full min-w-0 flex-col">
      <div ref={ref} style={{ minHeight: height }} className="w-full flex-1" />
      {deadSeries.length > 0 && (
        // 单个标的一行、可换行；不用 `、` 连成一长句（5 只标的会撑成两行且难扫读）
        <p className="mt-0.5 text-2xs leading-snug text-ink-muted">
          以下标的暂无行情数据：{deadSeries.map((s) => `${s.code} ${s.name}`).join(' · ')}
        </p>
      )}
      {info.hasOutlier && (
        // 🔴 截断必须**如实披露**，否则用户会以为"图就是全部数据"。
        // 这是"展示优化"与"伪装数据"的分界线：我们改了纵轴量程，但明确说出
        // **被剔的是谁、真实峰值是多少**，并给出「显示全部」开关让用户主动看全貌。
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {info.dropped.length > 0 && (
            <p className="text-2xs leading-snug text-ink-muted">
              纵轴已忽略振幅显著偏大的标的：
              {info.dropped.map((d) => `${d.code} ${d.name} 峰值 ${formatPeak(d.max, isPct)}`).join(' · ')}
              （仅影响纵轴量程，不改数据；真实值见悬停提示）
            </p>
          )}
          <button type="button" onClick={() => setShowAll((v) => !v)}
            aria-pressed={showAll}
            className="shrink-0 rounded border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
            {showAll ? '仅显示常规区间' : '显示全部标的'}
          </button>
        </div>
      )}
    </div>
  );
}
