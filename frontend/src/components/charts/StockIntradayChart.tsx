/**
 * 真实 1 分钟分时走势图（StockIntradayChart）——对标 TSP/同花顺标准。
 *
 * 功能特性：
 * 1. 上窗格（价格走势）：
 *    - 白/主色价格折线（现价）
 *    - 黄色成交均价线（VWAP）
 *    - 昨收价平盘参考虚线（0% 居中对称基准）
 *    - 双 Y 轴：左轴绝对价格，右轴相对昨收涨跌幅百分比（严格零轴居中对称）
 * 2. 下窗格（分时成交量）：
 *    - 分钟成交量柱状图（红涨绿跌，与前一分钟或昨收对齐）
 * 3. 真实数据原则（红线）：
 *    - 100% 取自 /api/v1/stock/{symbol}/intraday 真实实时接口；
 *    - 严禁任何 Math.sin / 伪造曲线或假数据注入；
 *    - 休市或网络失败时，诚实呈现空状态或重试按钮；
 * 4. 主题自适应：色相取自 chartPalette() 与 CSS 变量。
 */
import { useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { stockApi } from '@/api/stock';
import type { IntradayData, IntradayRow } from '@/types/stock';
import { fmtVol } from '@/utils/format';

interface Props {
  symbol: string;
  days?: number;
  height?: number;
  className?: string;
}

export default function StockIntradayChart({
  symbol,
  days = 1,
  height = 420,
  className = '',
}: Props) {
  const theme = useTheme();
  const [data, setData] = useState<IntradayData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTrends = async () => {
    if (!symbol) return;
    try {
      setLoading(true);
      setError(null);
      const res = await stockApi.intraday(symbol, days);
      setData(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '获取分时数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchTrends();
    // 盘中 15s 轮询自动刷新
    const timer = setInterval(() => {
      void fetchTrends();
    }, 15000);
    return () => clearInterval(timer);
  }, [symbol, days]);

  const rows: IntradayRow[] = data?.rows ?? [];
  const prevClose = data?.prev_close ?? 0;

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!rows.length || prevClose <= 0) return null;
    const p = chartPalette();

    const times = rows.map((r) => r.time);
    const prices = rows.map((r) => r.price);
    const vwaps = rows.map((r) => r.vwap);
    const volumes = rows.map((r) => r.volume);

    // 计算价格波动范围并以 prevClose 为中心对称，保证左右轴 0% 居中
    let maxDiff = 0;
    for (const pr of prices) {
      const diff = Math.abs(pr - prevClose);
      if (diff > maxDiff) maxDiff = diff;
    }
    // 最小保留 0.8% 视距，避免极端横盘时轴挤压
    maxDiff = Math.max(maxDiff, prevClose * 0.008);
    const yMin = +(prevClose - maxDiff).toFixed(2);
    const yMax = +(prevClose + maxDiff).toFixed(2);
    const pctSpan = ((maxDiff / prevClose) * 100);

    const isLatestUp = (prices[prices.length - 1] ?? prevClose) >= prevClose;
    const lineColor = isLatestUp ? p.UP : p.DOWN;

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: unknown) => {
          const arr = params as Array<{ dataIndex: number }>;
          const idx = arr[0]?.dataIndex ?? 0;
          const it = rows[idx];
          if (!it) return '';
          const diffPct = prevClose > 0 ? ((it.price / prevClose - 1) * 100) : 0;
          const isUp = diffPct >= 0;
          return `
            <div class="text-xs font-semibold mb-1">${it.time}</div>
            <div class="space-y-0.5 text-2xs font-mono">
              <div class="flex justify-between gap-4">
                <span class="text-ink-muted">分时价格:</span>
                <b class="${isUp ? 'text-up' : 'text-down'}">￥${it.price.toFixed(2)} (${isUp ? '+' : ''}${diffPct.toFixed(2)}%)</b>
              </div>
              <div class="flex justify-between gap-4">
                <span class="text-ink-muted">分时均价:</span>
                <b class="text-amber-500">￥${it.vwap.toFixed(2)}</b>
              </div>
              <div class="flex justify-between gap-4">
                <span class="text-ink-muted">分钟成交:</span>
                <span>${fmtVol(it.volume * 100)}股 (${(it.amount / 10000).toFixed(1)}万)</span>
              </div>
            </div>
          `;
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 45, right: 48, top: 22, height: '62%' },
        { left: 45, right: 48, top: '75%', height: '18%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: times,
          axisLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            interval: Math.max(1, Math.floor(times.length / 5)),
            formatter: (v: string) => (v.length > 11 ? v.slice(11, 16) : v),
          },
          splitLine: { show: true, lineStyle: { color: p.SUNKEN, type: 'dashed' } },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: times,
          axisLine: { lineStyle: { color: p.SUNKEN } },
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          type: 'value',
          min: yMin,
          max: yMax,
          splitNumber: 4,
          axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(2) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        {
          type: 'value',
          min: -pctSpan,
          max: pctSpan,
          splitNumber: 4,
          axisLabel: {
            fontSize: 9,
            color: p.INKM,
            formatter: (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`,
          },
          splitLine: { show: false },
        },
        {
          gridIndex: 1,
          type: 'value',
          splitNumber: 2,
          axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => fmtVol(v * 100) },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
      ],
      series: [
        {
          name: '分时价格',
          type: 'line',
          data: prices,
          showSymbol: false,
          lineStyle: { width: 1.5, color: lineColor },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(lineColor, 0.25) },
              { offset: 1, color: withAlpha(lineColor, 0.02) },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              {
                yAxis: prevClose,
                lineStyle: { color: p.INKM, type: 'dashed', width: 1 },
                label: { show: true, position: 'start', formatter: '昨收' },
              },
            ],
          },
        },
        {
          name: '分时均价 (VWAP)',
          type: 'line',
          data: vwaps,
          showSymbol: false,
          lineStyle: { width: 1.2, color: p.WARN }, // 经典黄色均价线 (跟随语义警告色)
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 2,
          data: volumes.map((v, i) => {
            const isUp = (prices[i] ?? prevClose) >= (i > 0 ? (prices[i - 1] ?? prevClose) : prevClose);
            return {
              value: v,
              itemStyle: { color: isUp ? p.UP : p.DOWN },
            };
          }),
        },
      ],
    };
  }, [rows, prevClose, theme]);

  const chartRef = useChart(option);

  return (
    <div className={`relative flex flex-col rounded-lg border border-hair bg-surface p-3 ${className}`}>
      {/* 顶栏信息与状态指示 */}
      <div className="flex items-center justify-between border-b border-hair pb-2 text-2xs">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-ink">
            {days > 1 ? `${days}日连续分时` : '当日 1 分钟分时'}
          </span>
          {data?.prev_close ? (
            <span className="text-ink-muted">
              昨收基准: <b className="font-mono text-ink">￥{data.prev_close.toFixed(2)}</b>
            </span>
          ) : null}
          {rows.length > 0 && (
            <span className="text-ink-muted">
              最新: <b className="font-mono text-ink">￥{rows[rows.length - 1].price.toFixed(2)}</b>
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data?.source && (
            <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-mono text-ink-muted border border-hair">
              来源: {data.source}
            </span>
          )}
          <button
            type="button"
            onClick={() => void fetchTrends()}
            className="rounded px-2 py-0.5 text-3xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {/* 图表主区域 */}
      <div className="relative mt-2" style={{ height }}>
        {loading && !data && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-ink-muted">
            分时数据拉取中…
          </div>
        )}

        {error && !data && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-xs text-danger">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => void fetchTrends()}
              className="rounded bg-surface-alt px-3 py-1 text-2xs text-ink hover:bg-surface-sunken"
            >
              重新加载
            </button>
          </div>
        )}

        {!loading && (!data || !rows.length || data.status === 'unavailable') && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-xs text-ink-muted">
            <span className="font-medium">暂无盘中分时数据</span>
            <span className="text-2xs text-ink-muted/80">当前可能处于休市时段或数据源暂未提供</span>
          </div>
        )}

        <div ref={chartRef} className="h-full w-full" />
      </div>
    </div>
  );
}
