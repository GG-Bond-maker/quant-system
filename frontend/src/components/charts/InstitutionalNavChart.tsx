/**
 * 机构级累计净值基准对比图表组件（InstitutionalNavChart）
 *
 * ## 设计与量化标准（对标顶级量化对冲基金与学术研报口径）：
 * 1. 策略与基准双曲线对比，动态呈现年化复合收益率（CAGR）；
 * 2. 动态区间差分着色（Spread Shading）：
 *    - 策略 >= 基准：淡青色填充（跑赢区间）；
 *    - 策略 < 基准：淡红褐色填充（跑输区间）；
 * 3. 1.0 水平保本参考基线（Dashed Baseline）；
 * 4. 标题与样本属性自适应（全样本 vs 样本外 OOS，双边摩擦成本 bps）；
 * 5. 全面遵循项目样式闸门（无硬编码 hex，色彩与主题联动）。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, calcNavAnnualizedReturn, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

export interface NavPoint {
  date: string;
  strategy: number;
  benchmark: number | null;
}

export interface InstitutionalNavChartProps {
  nav: NavPoint[];
  title?: string;
  strategyName?: string;
  benchmarkName?: string;
  annualStrategy?: number | null;
  annualBenchmark?: number | null;
  sampleLabel?: string; // 如 "样本外"、"全样本"、"折外验证"
  costBps?: number; // 如 35 代表含双边成本 35bp
  loading?: boolean;
  height?: number | string;
  className?: string;
  showMarkLine?: boolean;
  signals?: Array<{ date: string; action: '买入' | '卖出'; symbol: string }>;
}

export default function InstitutionalNavChart({
  nav,
  title,
  strategyName = '策略',
  benchmarkName = '基准',
  annualStrategy,
  annualBenchmark,
  sampleLabel,
  costBps,
  loading = false,
  height = 340,
  className = '',
  showMarkLine = true,
  signals = [],
}: InstitutionalNavChartProps) {
  const theme = useTheme();

  // 1. 计算时间范围与动态规范标题
  const computedTitle = useMemo(() => {
    if (title) return title;
    if (!nav.length) return '累计净值走势';

    const startYear = nav[0].date.slice(0, 4);
    const endYear = nav[nav.length - 1].date.slice(0, 4);
    const yearRange = startYear === endYear ? startYear : `${startYear}–${endYear}`;
    const sample = sampleLabel ? `${sampleLabel} ` : '';
    const cost = costBps != null ? `，含双边成本 ${costBps}bp` : '';

    return `累计净值（${sample}${yearRange}${cost}）`;
  }, [title, nav, sampleLabel, costBps]);

  // 2. 年化收益计算与图例标签
  const { stratLegend, benchLegend, annStratPct, annBenchPct } = useMemo(() => {
    if (!nav.length) {
      return {
        stratLegend: strategyName,
        benchLegend: benchmarkName,
        annStratPct: null,
        annBenchPct: null,
      };
    }

    const stratValues = nav.map((d) => d.strategy);
    const benchValues = nav.map((d) => d.benchmark ?? d.strategy);

    const sAnn = annualStrategy ?? calcNavAnnualizedReturn(stratValues);
    const bAnn = annualBenchmark ?? calcNavAnnualizedReturn(benchValues);

    const sStr = sAnn != null ? ` (年化 ${(sAnn * 100).toFixed(2)}%)` : '';
    const bStr = bAnn != null ? ` (年化 ${(bAnn * 100).toFixed(2)}%)` : '';

    return {
      stratLegend: `${strategyName}${sStr}`,
      benchLegend: `${benchmarkName}${bStr}`,
      annStratPct: sAnn != null ? `${(sAnn * 100).toFixed(2)}%` : null,
      annBenchPct: bAnn != null ? `${(bAnn * 100).toFixed(2)}%` : null,
    };
  }, [nav, strategyName, benchmarkName, annualStrategy, annualBenchmark]);

  // 3. 构建 ECharts 配置项（双重差分堆叠）
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!nav.length) return null;
    const p = chartPalette();
    const dates = nav.map((d) => d.date);
    const stratData = nav.map((d) => d.strategy);
    const benchData = nav.map((d) => d.benchmark ?? d.strategy);

    // 差分序列计算
    const baseData: number[] = [];
    const outperformData: number[] = [];
    const underperformData: number[] = [];

    for (let i = 0; i < nav.length; i++) {
      const s = stratData[i];
      const b = benchData[i];
      const base = Math.min(s, b);
      baseData.push(base);

      if (s >= b) {
        outperformData.push(Number((s - b).toFixed(4)));
        underperformData.push(0);
      } else {
        outperformData.push(0);
        underperformData.push(Number((b - s).toFixed(4)));
      }
    }

    // 交易信号标记点 (MarkPoints)
    const signalPts = signals
      .slice(-16)
      .map((s) => {
        const i = dates.indexOf(s.date);
        return i >= 0 ? { coord: [s.date, stratData[i]], value: s.action, symbol: s.symbol } : null;
      })
      .filter((x): x is NonNullable<typeof x> => x != null);

    return {
      animation: false,
      tooltip: {
        trigger: 'axis',
        backgroundColor: p.TIP_BG,
        borderColor: p.TIP_BORDER,
        textStyle: { color: p.TIP_TEXT, fontSize: 11 },
        formatter: (params: unknown) => {
          const items = params as Array<{ seriesName: string; value: number; dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const pt = nav[idx];
          if (!pt) return '';
          const s = pt.strategy;
          const b = pt.benchmark ?? s;
          const diff = s - b;
          const excessPct = b > 0 ? ((s / b - 1) * 100).toFixed(2) : '0.00';
          const isOutperform = diff >= 0;

          return `
            <div style="font-size:11px;font-family:monospace;padding:2px">
              <b>交易日: ${pt.date}</b><br/>
              <span style="color:${p.STRATEGY_LINE};font-weight:600;">● ${strategyName}:</span> <b>${s.toFixed(3)}</b> (${((s - 1) * 100).toFixed(2)}%)<br/>
              <span style="color:${p.BENCHMARK_LINE};font-weight:600;">● ${benchmarkName}:</span> <b>${b.toFixed(3)}</b> (${((b - 1) * 100).toFixed(2)}%)<br/>
              <div style="margin-top:4px;border-top:1px solid ${p.HAIR};padding-top:2px;">
                相对状态: <b style="color:${isOutperform ? p.STRATEGY_LINE : p.BENCHMARK_LINE}">
                  ${isOutperform ? '跑赢' : '跑输'} (${diff >= 0 ? '+' : ''}${diff.toFixed(3)})
                </b><br/>
                累计超额: <b style="color:${Number(excessPct) >= 0 ? p.UP : p.DOWN}">
                  ${Number(excessPct) >= 0 ? '+' : ''}${excessPct}%
                </b>
              </div>
            </div>
          `;
        },
      },
      legend: {
        data: [stratLegend, benchLegend, '跑赢区间', '跑输区间'],
        left: 18,
        top: 8,
        orient: 'vertical',
        backgroundColor: withAlpha(p.CARD, 0.88),
        borderColor: p.HAIR,
        borderWidth: 1,
        borderRadius: 4,
        padding: [6, 10],
        itemWidth: 14,
        itemHeight: 10,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      grid: {
        left: 20,
        right: 20,
        top: 32,
        bottom: 46,
        containLabel: true,
      },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: {
          fontSize: 9,
          color: p.INKM,
          formatter: (v: string) => (v.length >= 4 ? v.slice(0, 4) : v),
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: p.HAIR } },
        splitLine: {
          show: true,
          lineStyle: { type: 'dotted', color: p.SUNKEN },
        },
      },
      yAxis: {
        type: 'value',
        name: '净值',
        nameTextStyle: { fontSize: 10, color: p.INK2, align: 'right', padding: [0, 4, 0, 0] },
        scale: true,
        axisLabel: { fontSize: 9, color: p.INKM, formatter: (v: number) => v.toFixed(1) },
        splitLine: {
          show: true,
          lineStyle: { type: 'dotted', color: p.SUNKEN },
        },
      },
      dataZoom: [
        { type: 'inside' },
        {
          type: 'slider',
          bottom: 4,
          height: 16,
          borderColor: p.HAIR,
          fillerColor: withAlpha(p.STRATEGY_LINE, 0.15),
          handleStyle: { color: p.STRATEGY_LINE },
        },
      ],
      series: [
        // 1. 跑赢堆叠基底（透明）
        {
          name: 'base_outperform',
          type: 'line',
          stack: 'stack_outperform',
          data: baseData,
          showSymbol: false,
          lineStyle: { opacity: 0 },
          silent: true,
          tooltip: { show: false },
        },
        // 2. 跑赢差分区域（淡青色）
        {
          name: '跑赢区间',
          type: 'line',
          stack: 'stack_outperform',
          data: outperformData,
          showSymbol: false,
          lineStyle: { opacity: 0 },
          areaStyle: { color: p.OUTPERFORM_FILL },
          itemStyle: { color: p.STRATEGY_LINE },
        },
        // 3. 跑输堆叠基底（透明）
        {
          name: 'base_underperform',
          type: 'line',
          stack: 'stack_underperform',
          data: baseData,
          showSymbol: false,
          lineStyle: { opacity: 0 },
          silent: true,
          tooltip: { show: false },
        },
        // 4. 跑输差分区域（淡红褐色）
        {
          name: '跑输区间',
          type: 'line',
          stack: 'stack_underperform',
          data: underperformData,
          showSymbol: false,
          lineStyle: { opacity: 0 },
          areaStyle: { color: p.UNDERPERFORM_FILL },
          itemStyle: { color: p.BENCHMARK_LINE },
        },
        // 5. 策略实际净值折线
        {
          name: stratLegend,
          type: 'line',
          data: stratData,
          showSymbol: false,
          lineStyle: { width: 1.9, color: p.STRATEGY_LINE },
          itemStyle: { color: p.STRATEGY_LINE },
          markLine: showMarkLine
            ? {
                silent: true,
                symbol: 'none',
                data: [
                  {
                    yAxis: 1.0,
                    lineStyle: { type: 'dashed', color: p.BASELINE, width: 1 },
                    label: {
                      show: true,
                      position: 'insideStartBottom',
                      formatter: '1.0 保本线',
                      fontSize: 9,
                      color: p.INKM,
                    },
                  },
                ],
              }
            : undefined,
          markPoint: signalPts.length
            ? {
                symbol: 'roundRect',
                symbolSize: [36, 16],
                label: {
                  show: true,
                  fontSize: 9,
                  color: p.ON_SOLID,
                  formatter: '{c}',
                },
                data: signalPts.map((pc) => ({
                  name: pc.value,
                  coord: pc.coord,
                  value: pc.value,
                  symbolOffset: pc.value === '买入' ? [0, 20] : [0, -20],
                  itemStyle: { color: pc.value === '买入' ? p.UP : p.DOWN },
                })),
              }
            : undefined,
        },
        // 6. 基准实际净值折线
        {
          name: benchLegend,
          type: 'line',
          data: benchData,
          showSymbol: false,
          lineStyle: { width: 1.7, color: p.BENCHMARK_LINE },
          itemStyle: { color: p.BENCHMARK_LINE },
        },
      ],
    };
  }, [
    nav,
    strategyName,
    benchmarkName,
    stratLegend,
    benchLegend,
    signals,
    showMarkLine,
    theme,
  ]);

  const chartRef = useChart(option);

  return (
    <div className={`flex flex-col rounded-lg border border-hair bg-surface ${className}`}>
      {/* 居中标题与关键口径标签 */}
      <div className="flex flex-col items-center justify-center border-b border-hair px-4 py-2 text-center">
        <div className="text-sm font-bold tracking-tight text-ink">{computedTitle}</div>
        <div className="mt-0.5 flex flex-wrap items-center justify-center gap-3 text-2xs text-ink-muted">
          <span>初始基准线: 1.0</span>
          {costBps != null && <span>摩擦成本: {costBps} bp</span>}
          {annStratPct && <span>策略年化: <span className="font-semibold text-ink">{annStratPct}</span></span>}
          {annBenchPct && <span>基准年化: <span className="font-semibold text-ink">{annBenchPct}</span></span>}
        </div>
      </div>

      <div className="p-3">
        {loading ? (
          <div className="animate-pulse rounded bg-surface-sunken" style={{ height }} />
        ) : nav.length ? (
          <div ref={chartRef} style={{ height }} className="w-full" />
        ) : (
          <div
            style={{ height }}
            className="flex items-center justify-center text-xs text-ink-muted"
          >
            暂无净值数据
          </div>
        )}
      </div>
    </div>
  );
}
