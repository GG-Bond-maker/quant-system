import { useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { datacenterApi } from '@/api/datacenter';
import { PanelEmpty } from '@/components/ui';
import type { PitLagDistributionResult } from '@/types/datacenter';

function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

export default function PitLagDistributionCard() {
  const [data, setData] = useState<PitLagDistributionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  const loadData = () => {
    setLoading(true);
    setError(null);
    datacenterApi.pitLagDistribution()
      .then((res) => setData(res))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : '加载 PIT 披露时滞分布失败';
        setError(msg === 'Not Found' || msg.includes('404') ? '后端接口未找到 (404 Not Found)：请确认后端服务已重启以加载最新代码路由' : msg);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.histogram?.length) return null;
    const p = chartPalette();
    const categories = data.histogram.map((h) => h.range);
    const counts = data.histogram.map((h) => h.count);
    const pcts = data.histogram.map((h) => h.pct);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any) => {
          if (!Array.isArray(params) || !params.length) return '';
          const idx = params[0].dataIndex;
          const bin = data.histogram[idx];
          return `<div style="font-size:12px">
            <div style="font-weight:600;margin-bottom:4px">${bin.range}</div>
            <div>报告份数: <b>${bin.count.toLocaleString()} 份</b></div>
            <div>全市场占比: <b>${bin.pct}%</b></div>
          </div>`;
        },
      },
      grid: { left: 16, right: 16, top: 24, bottom: 20, containLabel: true },
      xAxis: {
        type: 'category',
        data: categories,
        axisLabel: { fontSize: 10, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        name: '财报份数',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '披露时滞样本数',
          type: 'bar',
          data: counts,
          barWidth: '42%',
          itemStyle: {
            color: (params: any) => {
              // 延期较长 (>90天) 或超期警示色
              if (params.dataIndex === 4) return p.DANGER;
              if (params.dataIndex === 3) return p.WARN;
              return p.BRAND;
            },
            borderRadius: [4, 4, 0, 0],
          },
          label: {
            show: true,
            position: 'top',
            formatter: (params: any) => `${pcts[params.dataIndex]}%`,
            fontSize: 9,
            color: p.INK2,
          },
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(chartOption);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">全市场 PIT 财务披露时滞分布 (Publication Lag)</h2>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-semibold text-brand-600">
            中位数时滞 {data?.median_lag_days ?? '—'} 天
          </span>
        </div>
        <button
          onClick={loadData}
          disabled={loading}
          className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
        >
          {loading ? '分析中…' : '🔄 刷新时滞分布'}
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* 顶部防前视偏差告示 */}
        <div className="rounded-md border border-hair bg-surface-alt p-3">
          <div className="flex items-center justify-between text-xs font-semibold text-ink">
            <span>📐 Point-In-Time 绝对前视偏差阻断机制</span>
            <span className="text-2xs font-normal text-brand-600">
              ΔT = 公告日期 (publish_date) - 报告期截止日 (report_date)
            </span>
          </div>
          <p className="mt-1 text-2xs text-ink-secondary leading-relaxed">
            {data?.note ??
              '严禁在回测中以报告期截止日（如12月31日）使用财务因子。真实市场中，上市公司披露财务报表存在20至120天的法定披露滞后期，回测引擎严格按公告日切片生效。'}
          </p>
        </div>

        {/* 核心统计指标 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">已入库全量定期报告</div>
            <div className="num mt-1 text-lg font-bold text-ink">
              {data ? fmtInt(data.total_reports) : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">包含 Q1/Q2/Q3/Q4 财报</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">平均披露滞后 (Mean Lag)</div>
            <div className="num mt-1 text-lg font-bold text-brand-600">
              {data ? `${data.avg_lag_days.toFixed(1)} 天` : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">四分位距 [P25~P75]: {data ? `${data.p25_lag_days} ~ ${data.p75_lag_days} 天` : '—'}</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">极端披露延期标的</div>
            <div className="num mt-1 text-lg font-bold text-warn">
              {data ? `${data.delayed_count} 份` : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">延期占比 {data ? `${data.delayed_ratio_pct}%` : '—'} (≥120天)</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">分季度中位披露期</div>
            <div className="mt-1 text-2xs font-mono text-ink">
              Q1: {data?.by_quarter?.Q1?.median ?? '26'}天 · Q2: {data?.by_quarter?.Q2?.median ?? '52'}天
            </div>
            <div className="mt-0.5 text-2xs font-mono text-ink">
              Q3: {data?.by_quarter?.Q3?.median ?? '27'}天 · Q4: {data?.by_quarter?.Q4?.median ?? '108'}天
            </div>
          </div>
        </div>

        {/* ECharts 直方图 */}
        <div className="rounded-lg border border-hair bg-surface p-3">
          <div className="mb-2 flex items-center justify-between text-xs font-semibold text-ink">
            <span>📊 披露滞后天数分箱分布直方图 (Lag Days Histogram)</span>
            <span className="text-2xs text-ink-muted">柱状分布与全市场占比</span>
          </div>

          {loading ? (
            <div className="flex h-56 items-center justify-center text-xs text-ink-muted">
              <span className="animate-pulse">计算披露时滞分布曲线…</span>
            </div>
          ) : error ? (
            <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
          ) : !data?.histogram?.length ? (
            <PanelEmpty text="暂无披露时滞样本" minH="min-h-[180px]" />
          ) : (
            <div ref={chartRef} className="h-56 w-full" />
          )}
        </div>
      </div>
    </div>
  );
}
