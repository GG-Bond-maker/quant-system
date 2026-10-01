/**
 * ETF表现 折线图：多条 ETF 序列的累计涨跌幅（或净值）对比。
 *
 * 序列不可用时（日韩本土标的行情源不可达）该条不画入图中，
 * 并在图下方单独列出，避免用 0 值伪造曲线。
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { PanelEmpty } from '@/components/ui';
import type { EtfPerformance } from '@/types/etf';

// 多条对比线为**类别色**（区分是哪只 ETF，非涨跌），沿用 CATEGORY_COLORS.SEQ 固定色相
const COLORS = CATEGORY_COLORS.SEQ;

/**
 * height 仅作为最小高度兜底：容器在 flex/grid 里会被拉伸，图表跟随容器高度铺满。
 */
export default function PerformanceChart({ data, height = 260 }: {
  data: EtfPerformance | null; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  /**
   * 「纵轴是否被截断」的披露标志。
   *
   * 用 ref 而不是 state：`option` 的 useMemo 里已经算出该布尔值，
   * 若改用 state 会触发一次额外渲染（且首帧渲染必然拿到旧值）。
   * ref 在当前渲染的 useMemo 执行后即被赋值，而 JSX 的求值发生在**其后**
   * ⇒ 同一帧内读到的一定是本帧算出的值，不会滞后。
   */
  const clipFlagRef = useRef(false);

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
     *   配合零轴 `markLine` + tooltip 真实值仍可读出量级。
     * - 剔除后若区间退化（数据几乎全等），回退到真实极值，避免 span=0 把轴压成一条线。
     */
    /** 每条序列的振幅（max-min）；同时保留其全局极值用于兜底 */
    const perSeries = okSeries.map((s) => {
      const vs = s.points.map((pt) => pt.value).filter((v) => Number.isFinite(v));
      if (!vs.length) return { amp: 0, min: 0, max: 0 };
      const mn = Math.min(...vs), mx = Math.max(...vs);
      return { amp: mx - mn, min: mn, max: mx };
    });
    const allMin = perSeries.length ? Math.min(...perSeries.map((x) => x.min)) : 0;
    const allMax = perSeries.length ? Math.max(...perSeries.map((x) => x.max)) : 0;

    /**
     * 判定"谁是离群序列"：用**中位振幅**做基准，只剔除显著超出者。
     *   `amp > medianAmp * OUTLIER_RATIO * 3`
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
     * 剔除离群序列（仅当**确为离群**）：
     * - 至少保留 **2** 条（不能因为剔离群把图剔空）；
     * - 上限 **2** 条（剔太多会让纵轴失去代表性）。
     */
    let kept = perSeries;
    if (perSeries.length >= 3 && medianAmp > 0) {
      const outliers = perSeries.filter((x) => isOutlier(x.amp));
      if (outliers.length > 0) {
        const maxDrop = Math.min(2, perSeries.length - 2);
        // 按振幅降序剔最"撑轴"的，最多 maxDrop 条
        const dropSet = new Set(
          [...perSeries].sort((a, b) => b.amp - a.amp)
            .filter((x) => isOutlier(x.amp))
            .slice(0, maxDrop),
        );
        kept = perSeries.filter((x) => !dropSet.has(x));
      }
    }
    const baseMin = kept.length ? Math.min(...kept.map((x) => x.min)) : allMin;
    const baseMax = kept.length ? Math.max(...kept.map((x) => x.max)) : allMax;
    /** 纵轴留白比：剔除离群后再留 8%，让线不贴边 */
    const PAD = 0.08;
    const span = baseMax - baseMin || 1;
    // 涨跌幅口径：纵轴**必须包含 0**（否则「跑赢基准」的视觉基准丢失）
    const yMin = isPct ? Math.min(0, baseMin) - span * PAD : baseMin - span * PAD;
    const yMax = isPct ? Math.max(0, baseMax) + span * PAD : baseMax + span * PAD;
    /**
     * 是否需要披露"纵轴已截断"。
     * ⚠️ 这个值由 useMemo **顺带算出并缓存**（`clipFlagRef`），供下方 JSX 读取 ——
     * 不在 JSX 里重算：重算需要再次遍历全部序列，而 useMemo 里已经算过了，
     * 重算既浪费又可能与绘图用的量程**不一致**（那会导致"披露了却没截断"的假告警）。
     */
    clipFlagRef.current = allMax > yMax || allMin < yMin;
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
        axisLabel: { fontSize: 9, color: p.INKM, margin: 6,
          formatter: isPct ? '{value}%' : '{value}' },
        splitLine: { lineStyle: { color: p.SUNKEN } },
        // 涨跌幅：y=0 加一条淡基准线，替代"靠空网格猜零点"
        ...(isPct ? {
          axisLine: { show: false },
        } : {}),
      },
      series: okSeries.map((s, i) => ({
        name: s.name,
        type: 'line' as const,
        data: s.points.map((pt) => pt.value),
        smooth: true,
        symbol: 'none' as const,
        lineStyle: { width: 1.6, color: COLORS[i % COLORS.length] },
        itemStyle: { color: COLORS[i % COLORS.length] },
        // 涨跌幅口径下画 0% 基准线；**用 markLine 而非第二条 series**，不占 legend
        ...(isPct && i === 0 ? {
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: p.INKM, width: 0.8, type: 'dashed' as const, opacity: 0.55 },
            label: { show: false },
            data: [{ yAxis: 0 }],
          },
        } : {}),
      })),
    };
  }, [okSeries, data, theme]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    // **必须 notMerge**：`setOption` 默认是 merge 语义，新 option 的 series 按**下标**
    // 与旧 series 合并，新数组更短时多出来的旧 series **不会被删除**。
    // 而本图 series 数量是会变的：① 热门榜只取前 5 只 A 股，榜里 A 股少于 5 只时会变短；
    // ② `okSeries` 会剔除 `status !== 'ok'` 或 `points.length < 2` 的序列（次新 ETF 的
    // K 线可能不足 2 根）。两者任一发生，merge 就会把上一次的曲线**残留**在图上，
    // 表现为"切了周期/切了排序，但图上还留着上一份数据的线"。故用 setOption(option, true)
    // 每次全量替换，legend / xAxis（交易日范围）/ series 都按新数据重建。
    chart.setOption(option, true);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [option]);

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
  return (
    <div className="flex h-full min-w-0 flex-col">
      <div ref={ref} style={{ minHeight: height }} className="w-full flex-1" />
      {deadSeries.length > 0 && (
        // 单个标的一行、可换行；不用 `、` 连成一长句（5 只标的会撑成两行且难扫读）
        <p className="mt-0.5 text-2xs leading-snug text-ink-muted">
          以下标的暂无行情数据：{deadSeries.map((s) => `${s.code} ${s.name}`).join(' · ')}
        </p>
      )}
      {clipFlagRef.current && (
        // 🔴 截断必须**如实披露**，否则用户会以为"图就是全部数据"。
        // 这是"展示优化"与"伪装数据"的分界线：我们改了纵轴量程，但明确说出来。
        <p className="mt-0.5 text-2xs leading-snug text-ink-muted">
          纵轴已忽略个别振幅显著偏大的标的（仅影响纵轴量程，不改数据）；真实值见悬停提示
        </p>
      )}
    </div>
  );
}
