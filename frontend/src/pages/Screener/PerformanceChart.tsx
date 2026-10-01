/**
 * 股票表现折线图（对齐 ETF 中心 PerformanceChart 模板）：
 * Alpha 榜 Top 5 标的近 1 年（前复权）收盘价的归一化对比。
 *
 * 归一化口径（真实数据，不造曲线）：
 * - 涨跌幅：以所选区间首日收盘为基准，(close/base - 1) × 100
 * - 净值：以所选区间首日收盘为基准 100，close/base × 100
 * 数据由父组件逐只调用 /stock/{symbol}/kline（qfq）取得后传入；
 * 行情缺失的序列不画入图中，在图下方单独列出。
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { PanelEmpty } from '@/components/ui';

// 多条对比线为**类别色**（区分是哪只标的，非涨跌），沿用 CATEGORY_COLORS.SEQ 固定色相
const COLORS = CATEGORY_COLORS.SEQ;

export type PerfMetric = 'pct' | 'price';
export type PerfPeriod = '1m' | '3m' | '6m' | '1y';

export const PERF_PERIODS: ReadonlyArray<{ key: PerfPeriod; label: string }> = [
  { key: '1m', label: '近1月' },
  { key: '3m', label: '近3月' },
  { key: '6m', label: '近6月' },
  { key: '1y', label: '近1年' },
];

export interface PerfSeries {
  symbol: string;
  name: string;
  /** 区间内按日期升序的收盘价序列（YYYY-MM-DD） */
  points: Array<{ date: string; close: number }>;
}

/** 周期对应的天数窗口（自然日，用于裁剪序列起点） */
const PERIOD_DAYS: Record<PerfPeriod, number> = { '1m': 31, '3m': 92, '6m': 183, '1y': 366 };

export default function PerformanceChart({ series, metric, period, height = 260 }: {
  series: PerfSeries[] | null;
  metric: PerfMetric;
  period: PerfPeriod;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  const okSeries = useMemo(
    () => (series ?? []).filter((s) => s.points.length >= 2),
    [series],
  );
  const deadSeries = useMemo(
    () => (series ?? []).filter((s) => s.points.length < 2),
    [series],
  );

  /** 按周期裁剪 + 归一化 */
  const normalized = useMemo(() => {
    if (!okSeries.length) return null;
    const cutoff = new Date(Date.now() - PERIOD_DAYS[period] * 86400_000).getTime();
    const cut = okSeries.map((s) => {
      const pts = s.points.filter((p) => new Date(p.date).getTime() >= cutoff);
      return { ...s, pts: pts.length >= 2 ? pts : s.points.slice(-2) };
    }).filter((s) => {
      // base 必须为正（停牌/复牌/除权导致首日 close 为 0 或负的序列直接丢弃）
      const base = s.pts[0]?.close;
      return Number.isFinite(base) && (base as number) > 0;
    });
    return cut.map((s) => {
      const base = s.pts[0].close as number;
      return {
        name: s.name,
        dates: s.pts.map((p) => p.date),
        values: s.pts.map((p) =>
          metric === 'pct' ? +(((p.close / base) - 1) * 100).toFixed(2) : +((p.close / base) * 100).toFixed(2)),
      };
    });
  }, [okSeries, metric, period, theme]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!normalized || !normalized.length) return null;
    const dates = normalized[0].dates;
    const isPct = metric === 'pct';
    const p = chartPalette();
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}${isPct ? '%' : ''}` },
      grid: { left: 8, right: 12, top: 30, bottom: 4, containLabel: true },
      legend: { top: 0, textStyle: { fontSize: 10, color: p.INK2 }, itemWidth: 12, itemHeight: 8 },
      xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 9, color: p.INKM } },
      yAxis: {
        type: 'value', axisLabel: { fontSize: 9, color: p.INKM, formatter: isPct ? '{value}%' : '{value}' },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: normalized.map((s, i) => ({
        name: s.name,
        type: 'line' as const,
        data: s.values,
        smooth: true,
        symbol: 'none' as const,
        lineStyle: { width: 1.6, color: COLORS[i % COLORS.length] },
        itemStyle: { color: COLORS[i % COLORS.length] },
      })),
    };
  }, [normalized, metric, theme]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [option]);

  if (!option) {
    return (
      <div style={{ minHeight: height }} className="h-full">
        <PanelEmpty text="暂无可绘制的行情序列" minH="h-full" />
      </div>
    );
  }
  return (
    <div className="flex h-full min-w-0 flex-col">
      <div ref={ref} style={{ minHeight: height }} className="w-full flex-1" />
      {deadSeries.length > 0 && (
        <p className="mt-1 text-2xs text-ink-muted">
          以下标的暂无行情数据：{deadSeries.map((s) => `${s.symbol} ${s.name}`).join('、')}
        </p>
      )}
    </div>
  );
}
