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
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';

import { etfApi } from '@/api/etf';
import KLineChart from '@/components/charts/KLineChart';
import {
  ErrorState, LoadingState, PageHeader, PanelEmpty, SectionCard, ViewToggle,
} from '@/components/ui';
import type { EtfDetail, EtfKlineBar } from '@/types/etf';
import { fmtAmountYi, fmtNum, fmtPct, fmtVol, pctClass } from '@/utils/format';

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

/**
 * 通用 ECharts 图容器。
 *
 * **本次改动（详情页去空白）**：图表的实际像素高度由**外层容器**决定（grid 拉伸 +
 * `flex-1 min-h-0`），容器不再写死 `style={{height: N}}`。因此这里补上
 * `ResizeObserver`：容器尺寸变化（窗口 resize / 行高被同行最高的卡片拉高）时
 * 主动 `chart.resize()`，让画布而不是留白带 shoulder 掉多余的高度。
 *
 * `minHeight` 退化为**兜底最小高度**（容器高度未解算出来的首帧），不再是渲染目标。
 */
function useChart(option: echarts.EChartsOption | null, minHeight: number) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !option) return;
    const host = ref.current;
    const chart = echarts.init(host);
    chart.setOption(option, true);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    // 行高由 grid 行内最高的兄弟卡片决定；容器一变高就必须跟上，否则图下方留白带
    const ro = new ResizeObserver(onResize);
    ro.observe(host);
    return () => {
      window.removeEventListener('resize', onResize);
      ro.disconnect();
      chart.dispose();
    };
  }, [option, minHeight]);
  return { ref };
}

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

/** 行业配置甜甜圈 */
function IndustryPie({ items }: { items: Array<{ industry: string; ratio: number | null }> }) {
  // 图例取色走 chartPalette()（canvas 不参与 CSS 级联）⇒ theme 必须进 useMemo 依赖
  const theme = useTheme();
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!items.length) return null;
    const p = chartPalette();
    const data = items
      .map((it) => ({ name: it.industry, value: it.ratio ?? 0 }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}%', textStyle: { fontSize: 11 } },
      // 该卡在 300px 侧栏内仅约 145px 宽，扇区外 label 必然互相压盖 ⇒ 改底部横排图例：
      // 名称截断到 5 字 + `scroll` 类型（8 类在窄卡里必然换行，plain 会被裁）。
      // 饼图半径/圆心相应上移，给图例让出约 30% 高度。
      legend: {
        show: true, type: 'scroll', orient: 'horizontal', bottom: 0, left: 'center',
        itemWidth: 8, itemHeight: 8, itemGap: 6,
        formatter: (n: string) => (n.length > 5 ? `${n.slice(0, 5)}…` : n),
        textStyle: { fontSize: 9, color: p.INK2 },
        itemStyle: { borderColor: p.CARD, borderWidth: 1 },
      },
      series: [{
        type: 'pie', radius: ['38%', '58%'], center: ['50%', '40%'],
        data,
        label: { show: false },
        labelLine: { show: false },
      }],
    };
  }, [items, theme]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel text="暂无行业数据" />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
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
      yAxis: { type: 'value', name: '%', nameTextStyle: { fontSize: 9, color: p.INKM }, axisLabel: { fontSize: 9, color: p.INKM, formatter: '{value}%' }, splitLine: { lineStyle: { color: p.SUNKEN } } },
      // 「ETF 净值」vs「基准指数」为两条类别线（非涨跌），用固定类别色区分
      series: [
        { name: 'ETF', type: 'line', data: points.map((pt) => pt.etf), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: CATEGORY_COLORS.C1 }, itemStyle: { color: CATEGORY_COLORS.C1 } },
        { name: '基准指数', type: 'line', data: points.map((pt) => pt.index), smooth: true, showSymbol: false, lineStyle: { width: 1.5, color: CATEGORY_COLORS.C2 }, itemStyle: { color: CATEGORY_COLORS.C2 } },
      ],
    };
  }, [points, theme]);
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** 跟踪误差时序：日收益差 */
function TrackingErrorChart({ points }: { points: Array<{ date: string; etf: number; index: number }> }) {
  const data = useMemo(() => {
    const diffs: { date: string; value: number }[] = [];
    for (let i = 1; i < points.length; i++) {
      const e = (points[i].etf / (points[i - 1].etf || 1) - 1) * 100;
      const b = (points[i].index / (points[i - 1].index || 1) - 1) * 100;
      diffs.push({ date: points[i].date, value: e - b });
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
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: '{value}%' }, splitLine: { lineStyle: { color: p.SUNKEN } } },
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
  const { ref } = useChart(option, 200);
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
  const { ref } = useChart(option, 200);
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
  const { ref } = useChart(option, 110);
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
  const { ref } = useChart(option, 200);
  if (!option) return <EmptyPanel text="暂无资金流数据" />;
  return <div ref={ref} className="h-full min-h-[200px] w-full" />;
}

/** ETF 概况 & 资讯 */
function InfoPanel({
  header, tracking, valuation,
}: {
  header: EtfDetail['blocks']['header'] | undefined;
  tracking: EtfDetail['blocks']['tracking'] | undefined;
  valuation: EtfDetail['blocks']['valuation'] | undefined;
}) {
  const last = tracking?.points?.length ? tracking.points[tracking.points.length - 1] : null;
  const out = last ? (last.etf - last.index).toFixed(2) : null;
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

        <div>
          <h4 className="mb-1 font-medium text-ink">估值指标</h4>
          <div className="space-y-1">
            <div className="flex justify-between">
              <span>PE(TTM)</span>
              <span className="num text-ink">{valuation?.pe_ttm != null ? valuation.pe_ttm.toFixed(2) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>市净率 PB</span>
              <span className="num text-ink">{valuation?.pb != null ? valuation.pb.toFixed(2) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>PE 历史分位</span>
              <span className="num text-ink">{valuation?.pe_percentile != null ? `${valuation.pe_percentile.toFixed(1)}%` : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>PB 历史分位</span>
              <span className="num text-ink">{valuation?.pb_percentile != null ? `${valuation.pb_percentile.toFixed(1)}%` : '—'}</span>
            </div>
          </div>
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
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('day');
  const [watch, setWatch] = useState<string[]>(() => readWatch());
  const [trackPeriod, setTrackPeriod] = useState<(typeof TRACK_PERIODS)[number]['key']>('1y');

  const load = async () => {
    if (!code) return;
    setLoading(true); setError(null);
    try {
      setData(await etfApi.detail(code, period));
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [code, period]);

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

  const klineBars = useMemo(() => {
    if (!kline?.bars) return [];
    return kline.bars.map((b) => ({
      date: b.date,
      open: b.open ?? 0,
      close: b.close ?? 0,
      high: b.high ?? 0,
      low: b.low ?? 0,
      volume: b.volume ?? 0,
      amount: null as number | null,
    }));
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
        {/* 左侧 K 线
            `flex` + SectionCard `w-full`：让卡片**撑满 grid 行高**（grid 默认
            items-stretch 只拉伸到列 div，卡卡片自身是 auto 高，之前因此在图表下方
            留下整列 ≈380px 的死区）。卡片撑满后 body 用 flex-1 min-h-0 把剩余高度
            交给 KLineChart（`fill`）→ 图窗实测高度随行高走，空白消失。 */}
        <div className="flex min-w-0 lg:col-span-2">
          <SectionCard
            title="K 线走势"
            action={(
              <ViewToggle
                value={period}
                onChange={setPeriod}
                options={[
                  { key: 'day', label: '日K' },
                  { key: 'week', label: '周K' },
                  { key: 'month', label: '月K' },
                ]}
                title="K 线周期"
              />
            )}
            className="w-full"
            bodyClassName="flex min-h-0 flex-col"
          >
            {kline?.status === 'ok' && klineBars.length > 0 ? (
              /* `showAdjust={false}`：本页 K 线固定取前复权（后端 `fetch_kline` 只请求
                 `qfq`），原来那个「复权」下拉传的是空回调 `onAdjustChange={() => {}}`
                 ⇒ 选完立刻回弹，是"看起来能点但没用"的假交互。改为**只读披露**当前口径。 */
              <KLineChart bars={klineBars} adjust="qfq" onAdjustChange={() => {}}
                height={460} fill showAdjust={false} />
            ) : kline?.status === 'unavailable' ? (
              <EmptyPanel text={kline.reason || '暂无 K 线数据'} />
            ) : (
              <EmptyPanel text="加载中…" />
            )}
          </SectionCard>
        </div>

        {/* 右侧资讯/持仓/行业：这一列的内容决定上区 grid 行高 */}
        <div className="min-w-0 space-y-2.5">
          <InfoPanel header={header} tracking={tracking} valuation={valuation} />

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
            <div className="flex flex-col justify-between rounded-lg border border-hair bg-surface p-3">
              <div>
                <div className="text-sm font-semibold text-ink">ETF 中心</div>
                <p className="mt-1 text-2xs leading-relaxed text-ink-secondary">
                  返回列表浏览全部 ETF、对比业绩与资金流向。
                </p>
              </div>
              <Link to="/etf" className="mt-2 text-center text-xs text-brand-600 hover:underline">
                返回 ETF 中心 →
              </Link>
            </div>
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

            {/* 投资组合配置 */}
            <SectionCard title="投资组合配置" className="min-w-0 md:col-span-2"
              bodyClassName="flex min-h-0 flex-col">
              {holdings?.status === 'ok' ? (
                <HoldingsList items={holdings.holdings.items} />
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
