import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { opsApi, type FeatureDriftResult } from '@/api/production';
import { ApiError } from '@/api/client';
import { SectionCard } from '@/components/ui';

export default function FeatureDriftPanel() {
  const [factor, setFactor] = useState('ret_20');
  const [data, setData] = useState<FeatureDriftResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const theme = useTheme();
  const p = useMemo(() => chartPalette(), [theme]);

  const loadDrift = useCallback(async (selectedFactor: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await opsApi.featureDrift({
        factor: selectedFactor,
        baseline_year: 2025,
        target_year: 2026,
      });
      setData(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '获取特征协变量漂移失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDrift(factor);
  }, [factor, loadDrift]);

  // ECharts 双柱分布对比图
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.bins || data.bins.length === 0) return null;

    const categories = data.bins.map((b) => `Decile ${b.bin}`);
    const expectedData = data.bins.map((b) => b.expected_pct);
    const actualData = data.bins.map((b) => b.actual_pct);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const items = params as Array<{ seriesName: string; value: number; dataIndex: number }>;
          if (!items || !items.length) return '';
          const idx = items[0].dataIndex;
          const bin = data.bins[idx];
          return `<b>分箱 Decile ${bin.bin}</b><br/>` +
                 `区间: [${bin.lower}, ${bin.upper})<br/>` +
                 `基准期 (2025): ${bin.expected_pct}%<br/>` +
                 `目标期 (2026): ${bin.actual_pct}%`;
        },
      },
      legend: {
        data: ['基准期分布 (2025)', '评估期分布 (2026)'],
        textStyle: { color: p.INK2, fontSize: 11 },
        top: 0,
      },
      grid: {
        top: 36,
        bottom: 24,
        left: 45,
        right: 15,
      },
      xAxis: {
        type: 'category',
        data: categories,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '占比 (%)',
        nameTextStyle: { color: p.INKM, fontSize: 10 },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      series: [
        {
          name: '基准期分布 (2025)',
          type: 'bar',
          data: expectedData,
          itemStyle: { color: p.BRAND },
        },
        {
          name: '评估期分布 (2026)',
          type: 'bar',
          data: actualData,
          itemStyle: {
            color: data.drift_level === 'severe' ? p.DOWN : (data.drift_level === 'moderate' ? p.WARN : p.UP),
          },
        },
      ],
    };
  }, [data, p]);

  const chartRef = useChart(chartOption);

  const driftBadge = useMemo(() => {
    if (!data) return null;
    switch (data.drift_level) {
      case 'stable':
        return <span className="rounded bg-success-bg px-2 py-0.5 text-xs font-semibold text-success">稳定无漂移 (PSI &lt; 0.1)</span>;
      case 'moderate':
        return <span className="rounded bg-warn-bg px-2 py-0.5 text-xs font-semibold text-warn">轻度漂移 (0.1 ≤ PSI &lt; 0.25)</span>;
      case 'severe':
        return <span className="rounded bg-danger-bg px-2 py-0.5 text-xs font-semibold text-danger">严重协变量偏移 (PSI ≥ 0.25)</span>;
      default:
        return <span className="rounded bg-surface-sunken px-2 py-0.5 text-xs text-ink-muted">待检验</span>;
    }
  }, [data]);

  return (
    <SectionCard
      title="特征协变量漂移监控（Covariate Shift · PSI & KS 稳定性检验）"
      action={
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-muted">检测因子：</span>
          <select
            value={factor}
            onChange={(e) => setFactor(e.target.value)}
            className="rounded border border-hair bg-surface px-2 py-1 text-xs font-mono outline-none focus:border-brand-300"
          >
            {(data?.available_factors?.length ? data.available_factors : ['ret_20', 'ret_10', 'ret_5', 'overnight_gap', 'ma_gap_20', 'vol_20']).map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {error && (
        <div className="rounded-md border border-danger/30 bg-danger-bg p-2 text-2xs text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-xs text-ink-muted">正在计算截面特征分箱与稳定性统计量…</div>
      ) : data ? (
        <div className="space-y-3">
          {/* 统计指标卡 */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">群体稳定性指标 (PSI)</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.psi.toFixed(4)}
              </div>
              <div className="mt-0.5">{driftBadge}</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">双样本 KS 统计量</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.ks_statistic.toFixed(4)}
              </div>
              <div className="text-2xs text-ink-muted">p-value: {data.ks_pvalue < 1e-4 ? '< 1e-4' : data.ks_pvalue.toFixed(4)}</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">基准样本量 (2025)</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.baseline_samples.toLocaleString()}
              </div>
              <div className="text-2xs text-ink-muted">十等分截面基准分箱</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">评估样本量 (2026)</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.target_samples.toLocaleString()}
              </div>
              <div className="text-2xs text-ink-muted">最新实盘截面样本池</div>
            </div>
          </div>

          {/* ECharts 图表 */}
          <div className="rounded-md border border-hair bg-surface p-2">
            <div ref={chartRef} style={{ height: 220 }} className="w-full" />
          </div>

          <div className="text-2xs text-ink-muted">
            {'ⓘ 量化风控准则：群体稳定性指标 PSI = ∑ (P_actual - P_expected) × ln(P_actual / P_expected)。当 PSI ≥ 0.25 时表明因子分布出现显著协变量漂移，系统将在特征工程入库阶段自动拦截，防范无效模型推理。'}
          </div>
        </div>
      ) : null}
    </SectionCard>
  );
}
