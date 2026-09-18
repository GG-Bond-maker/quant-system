/**
 * ETF表现 折线图：多条 ETF 序列的累计涨跌幅（或净值）对比。
 *
 * 序列不可用时（日韩本土标的行情源不可达）该条不画入图中，
 * 并在图下方单独列出，避免用 0 值伪造曲线。
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import { PanelEmpty } from '@/components/ui';
import type { EtfPerformance } from '@/types/etf';

const COLORS = ['#2563EB', '#DC2626', '#16A34A', '#F59E0B', '#06B6D4', '#A855F7', '#EC4899', '#64748B'];

/**
 * height 仅作为最小高度兜底：容器在 flex/grid 里会被拉伸，图表跟随容器高度铺满。
 */
export default function PerformanceChart({ data, height = 260 }: {
  data: EtfPerformance | null; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

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
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}${isPct ? '%' : ''}` },
      grid: { left: 8, right: 12, top: 30, bottom: 4, containLabel: true },
      legend: { top: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
      xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 9 } },
      yAxis: {
        type: 'value', axisLabel: { fontSize: 9, formatter: isPct ? '{value}%' : '{value}' },
        splitLine: { lineStyle: { color: '#F1F5F9' } },
      },
      series: okSeries.map((s, i) => ({
        name: s.name,
        type: 'line' as const,
        data: s.points.map((p) => p.value),
        smooth: true,
        symbol: 'none' as const,
        lineStyle: { width: 1.6, color: COLORS[i % COLORS.length] },
        itemStyle: { color: COLORS[i % COLORS.length] },
      })),
    };
  }, [okSeries, data]);

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
        <p className="mt-1 text-2xs text-ink-muted">
          以下标的暂无行情数据：{deadSeries.map((s) => `${s.code} ${s.name}`).join('、')}
        </p>
      )}
    </div>
  );
}
