/**
 * 逐日保证金占用走势与杠杆风控预警卡片 (Margin Utilization & Risk Control)。
 *
 * 包含：
 * 1. 逐日保证金占用金额与保证金占用比例 (Margin / Equity)；
 * 2. 70% 追保预警线与 90% 强平风险线；
 * 3. 峰值保证金率与账户抗风险抗穿仓能力诊断。
 */
import { useMemo } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { SectionCard } from '@/components/ui';
import { fmtNum, fmtPct } from '@/utils/format';

interface Props {
  dates: string[];
  marginUsedCurve: number[];
  marginRatioCurve?: number[];
  equityCurve: number[];
  peakMarginRatio?: number;
}

export function MarginUtilizationCard({
  dates = [],
  marginUsedCurve = [],
  marginRatioCurve = [],
  equityCurve = [],
  peakMarginRatio = 0,
}: Props) {
  const theme = useTheme();

  // 最新与统计指标
  const latestMargin = marginUsedCurve.length > 0 ? marginUsedCurve[marginUsedCurve.length - 1] : 0;
  const latestEquity = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1] : 0;
  const latestRatio =
    marginRatioCurve && marginRatioCurve.length > 0
      ? marginRatioCurve[marginRatioCurve.length - 1]
      : latestEquity > 0
      ? (latestMargin / latestEquity) * 100
      : 0;

  const peakRatio = peakMarginRatio || (marginRatioCurve.length > 0 ? Math.max(...marginRatioCurve) : 0);

  // 风控状态等级
  const riskStatus = useMemo(() => {
    if (peakRatio >= 90) return { label: '高危强平风险', color: 'text-rose-500', bg: 'bg-rose-500/10' };
    if (peakRatio >= 70) return { label: '触及追保警戒', color: 'text-amber-500', bg: 'bg-amber-500/10' };
    if (peakRatio >= 50) return { label: '适度占用区间', color: 'text-blue-500', bg: 'bg-blue-500/10' };
    return { label: '充裕安全区间', color: 'text-emerald-500', bg: 'bg-emerald-500/10' };
  }, [peakRatio]);

  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!dates.length) return null;
    const p = chartPalette();

    return {
      grid: { left: 60, right: 60, top: 32, bottom: 28 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: p.TIP_BG,
        borderColor: p.TIP_BORDER,
        textStyle: { color: p.TIP_TEXT },
        formatter: (params: any) => {
          if (!Array.isArray(params)) return '';
          let tip = `<div style="font-size:11px; font-weight:600; margin-bottom:4px;">${params[0]?.axisValue}</div>`;
          params.forEach((item: any) => {
            const isRatio = item.seriesName.includes('率') || item.seriesName.includes('比例');
            const valStr = isRatio ? `${Number(item.value).toFixed(2)}%` : `${fmtNum(item.value, 0)} 元`;
            tip += `<div style="display:flex; justify-content:space-between; gap:16px; font-size:11px;">
              <span>${item.marker} ${item.seriesName}</span>
              <span style="font-family:monospace; font-weight:600;">${valStr}</span>
            </div>`;
          });
          return tip;
        },
      },
      legend: {
        top: 2,
        right: 12,
        textStyle: { color: p.INK2, fontSize: 11 },
        data: ['保证金占用金额', '账户总净值', '保证金占用比例'],
      },
      xAxis: {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: 'value',
          name: '资金规模 (元)',
          nameTextStyle: { color: p.INKM, fontSize: 10 },
          scale: true,
          axisLine: { lineStyle: { color: p.HAIR } },
          axisLabel: {
            color: p.INKM,
            fontSize: 10,
            formatter: (v: number) => (v >= 10000 ? `${(v / 10000).toFixed(0)}万` : String(v)),
          },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        },
        {
          type: 'value',
          name: '占用率 (%)',
          nameTextStyle: { color: p.WARN, fontSize: 10 },
          min: 0,
          max: 100,
          axisLine: { lineStyle: { color: p.WARN } },
          axisLabel: { color: p.WARN, fontSize: 10, formatter: '{value}%' },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '保证金占用金额',
          type: 'line',
          yAxisIndex: 0,
          data: marginUsedCurve,
          smooth: true,
          showSymbol: false,
          areaStyle: {
            color: withAlpha(p.INFO, 0.25),
          },
          lineStyle: { width: 1.8, color: p.INFO },
        },
        {
          name: '账户总净值',
          type: 'line',
          yAxisIndex: 0,
          data: equityCurve,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, type: 'dashed', color: p.INKM },
        },
        {
          name: '保证金占用比例',
          type: 'line',
          yAxisIndex: 1,
          data: marginRatioCurve,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: p.WARN },
          markLine: {
            silent: true,
            symbol: 'none',
            label: {
              position: 'insideEndTop',
              formatter: '{b}',
              fontSize: 10,
            },
            data: [
              {
                yAxis: 70,
                name: '70% 追保警戒线',
                lineStyle: { color: p.WARN, type: 'dashed', width: 1.5 },
              },
              {
                yAxis: 90,
                name: '90% 强平线',
                lineStyle: { color: p.DANGER, type: 'dashed', width: 1.5 },
              },
            ],
          },
        },
      ],
    };
  }, [dates, marginUsedCurve, equityCurve, marginRatioCurve, theme]);

  const chartRef = useChart(chartOption);

  return (
    <SectionCard
      title="逐日保证金占用走势与杠杆风控预警"
      action={
        <span className="text-2xs text-text-muted">
          监控日级别保证金/净值占用比例，规避极端行情保证金不足触发强平风险
        </span>
      }
    >
      <div className="space-y-4">
        {/* 指标卡 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">最新保证金占用</div>
            <div className="mt-1 font-mono text-base font-semibold text-text">
              {fmtNum(latestMargin, 0)} <span className="text-xs font-normal text-text-muted">元</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">当前保证金占用率</div>
            <div className="mt-1 font-mono text-base font-semibold text-text">
              {fmtPct(latestRatio)}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">历史峰值保证金率</div>
            <div
              className={`mt-1 font-mono text-base font-semibold ${
                peakRatio >= 70 ? 'text-rose-500' : 'text-amber-500'
              }`}
            >
              {fmtPct(peakRatio)}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card p-3">
            <div className="text-xs text-text-muted">风控状态诊断</div>
            <div className="mt-1 flex items-center">
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${riskStatus.bg} ${riskStatus.color}`}>
                {riskStatus.label}
              </span>
            </div>
          </div>
        </div>

        {/* 警戒提示 */}
        {peakRatio >= 70 && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-500">
            ⚠️ 提示：回测区间内最大保证金占用达 {fmtPct(peakRatio)}，已突破 70%
            追保警戒水位。实盘运行建议适度调低杠杆倍数或加大备付金缓冲区，防止流动性极端挤压。
          </div>
        )}

        {/* 图表 */}
        <div className="rounded-lg border border-border bg-card p-2">
          <div ref={chartRef} style={{ width: '100%', height: '340px' }} />
        </div>
      </div>
    </SectionCard>
  );
}
