import { useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { opsApi, type QualityRadarResult } from '@/api/production';
import { PanelEmpty } from '@/components/ui';

interface Props {
  dataset?: string;
  onDatasetChange?: (dataset: string) => void;
}

export default function QualityRadarCard({ dataset = 'daily_bar', onDatasetChange }: Props) {
  const [data, setData] = useState<QualityRadarResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  const loadRadar = (targetDataset: string) => {
    setLoading(true);
    setError(null);
    opsApi.qualityRadar(targetDataset)
      .then((res) => setData(res))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '加载五维质量雷达失败');
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadRadar(dataset);
  }, [dataset]);

  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.dimensions?.length) return null;
    const p = chartPalette();

    const indicator = data.dimensions.map((d) => ({
      name: d.name.split(' ')[0],
      max: 100,
    }));
    const scores = data.dimensions.map((d) => d.score);
    const benchmarks = data.dimensions.map((d) => d.benchmark);

    return {
      tooltip: {
        trigger: 'item',
      },
      legend: {
        data: ['当前综合实测分', '工业级基准分'],
        top: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
      },
      radar: {
        indicator,
        radius: '68%',
        center: ['50%', '55%'],
        axisName: {
          color: p.INK,
          fontSize: 10,
          fontWeight: 600,
        },
        splitLine: {
          lineStyle: { color: p.HAIR },
        },
        splitArea: {
          show: true,
          areaStyle: {
            color: [withAlpha(p.CARD, 0.4), withAlpha(p.CARD, 0.1)],
          },
        },
        axisLine: {
          lineStyle: { color: p.HAIR },
        },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: scores,
              name: '当前综合实测分',
              symbol: 'circle',
              symbolSize: 4,
              lineStyle: { width: 2, color: p.BRAND },
              itemStyle: { color: p.BRAND },
              areaStyle: { color: withAlpha(p.BRAND, 0.25) },
            },
            {
              value: benchmarks,
              name: '工业级基准分',
              symbol: 'none',
              lineStyle: { width: 1.5, type: 'dashed', color: p.INKM },
              itemStyle: { color: p.INKM },
            },
          ],
        },
      ],
    };
  }, [data, theme]);

  const chartRef = useChart(chartOption);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">
            五维数据质量健康雷达 (Institutional DQ Radar)
          </h2>
          {data && (
            <span
              className={`rounded px-2 py-0.5 text-2xs font-bold ${
                data.grade === 'AAA'
                  ? 'bg-success-bg text-success'
                  : data.grade === 'AA'
                  ? 'bg-brand-50 text-brand-600'
                  : 'bg-warn-bg text-warn'
              }`}
            >
              评级: {data.grade} · {data.composite_score} / 100
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {onDatasetChange && (
            <select
              value={dataset}
              onChange={(e) => onDatasetChange(e.target.value)}
              className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink outline-none"
            >
              <option value="daily_bar">daily_bar (原始日线)</option>
              <option value="daily_bar_hfq">daily_bar_hfq (后复权)</option>
              <option value="daily_bar_qfq">daily_bar_qfq (前复权)</option>
            </select>
          )}
          <button
            onClick={() => loadRadar(dataset)}
            disabled={loading}
            className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
          >
            {loading ? '测算中…' : '🔄 重新评估'}
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {loading ? (
          <div className="flex min-h-[220px] items-center justify-center text-xs text-ink-muted">
            <span className="animate-pulse">执行五维工业级质量综合加权扫描…</span>
          </div>
        ) : error ? (
          <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
        ) : !data ? (
          <PanelEmpty text="暂无雷达数据" minH="min-h-[200px]" />
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_1.3fr]">
            {/* 左侧：雷达图 */}
            <div className="flex flex-col items-center justify-center rounded-lg border border-hair bg-surface-alt/40 p-2">
              <div ref={chartRef} className="h-64 w-full" />
              <div className="text-2xs text-ink-muted text-center mt-1">
                综合评分: <b className="text-ink">{data.composite_score}</b> 分 · 工业基准线: 95.0 分
              </div>
            </div>

            {/* 右侧：五维明细列表 */}
            <div className="space-y-2">
              <div className="text-xs font-semibold text-ink">分项维度质检指标穿透 (Dimension Metrics)</div>
              <div className="grid grid-cols-1 gap-1.5">
                {data.dimensions.map((dim) => (
                  <div
                    key={dim.key}
                    className="flex items-center justify-between rounded border border-hair bg-surface-alt px-3 py-2 text-xs"
                  >
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-ink">{dim.name}</span>
                        <span className="rounded bg-surface-sunken px-1.5 py-0.2 font-mono text-3xs text-ink-secondary">
                          权重 {(dim.weight * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div className="text-3xs text-ink-muted">{dim.detail}</div>
                    </div>
                    <div className="text-right">
                      <div className="num font-bold text-ink">
                        <span
                          className={
                            dim.score >= dim.benchmark
                              ? 'text-success'
                              : dim.score >= 90
                              ? 'text-warn'
                              : 'text-danger'
                          }
                        >
                          {dim.score.toFixed(1)}
                        </span>
                        <span className="text-3xs text-ink-muted"> / 100</span>
                      </div>
                      <span className="text-3xs text-ink-muted">基准: {dim.benchmark.toFixed(1)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="rounded bg-surface-sunken p-2 text-2xs text-ink-secondary">
                {data.summary}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
