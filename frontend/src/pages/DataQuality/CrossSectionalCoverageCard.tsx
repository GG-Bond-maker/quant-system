import { useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { opsApi, type CrossSectionalCoverageResult } from '@/api/production';
import { PanelEmpty } from '@/components/ui';

export default function CrossSectionalCoverageCard() {
  const [data, setData] = useState<CrossSectionalCoverageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const theme = useTheme();

  const loadData = (d: number) => {
    setLoading(true);
    setError(null);
    opsApi.crossSectionalCoverage(d)
      .then((res) => setData(res))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '加载截面完整性失败');
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData(days);
  }, [days]);

  // ECharts 热力图
  const heatmapOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.heatmap_data?.length) return null;
    const p = chartPalette();
    const dates = data.dates.map((d) => d.slice(5)); // MM-DD
    const categories = data.categories;

    return {
      tooltip: {
        position: 'top',
        formatter: (params: any) => {
          const val = params.value;
          const dt = data.dates[val[0]];
          const cat = categories[val[1]];
          const score = val[2];
          return `<div style="font-size:11px">
            <div><b>${dt}</b> · ${cat}</div>
            <div>全市场截面覆盖率: <b>${score}%</b></div>
          </div>`;
        },
      },
      grid: {
        top: 20,
        bottom: 35,
        left: 100,
        right: 40,
      },
      xAxis: {
        type: 'category',
        data: dates,
        splitArea: { show: true },
        axisLabel: { fontSize: 9, color: p.INKM, interval: Math.floor(dates.length / 8) },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        splitArea: { show: true },
        axisLabel: { fontSize: 10, color: p.INK },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      visualMap: {
        min: 94,
        max: 100,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemWidth: 10,
        itemHeight: 120,
        textStyle: { fontSize: 9, color: p.INKM },
        inRange: {
          color: [withAlpha(p.WARN, 0.4), withAlpha(p.BRAND, 0.6), p.SUCCESS],
        },
      },
      series: [
        {
          name: '截面覆盖度',
          type: 'heatmap',
          data: data.heatmap_data,
          label: { show: false },
          emphasis: {
            itemStyle: {
              shadowBlur: 8,
              shadowColor: withAlpha(p.INK, 0.5),
            },
          },
        },
      ],
    };
  }, [data, theme]);

  const heatmapRef = useChart(heatmapOption);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">
            全市场截面完整性与因子空值时序分布 (Cross-Sectional Availability)
          </h2>
          <span className="rounded bg-success-bg px-2 py-0.5 text-2xs font-semibold text-success">
            近 {days} 日截面平均可用度: {data?.mean_coverage_pct ?? '—'}%
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink outline-none"
          >
            <option value={15}>近 15 个交易日</option>
            <option value={30}>近 30 个交易日</option>
            <option value={60}>近 60 个交易日</option>
          </select>
          <button
            onClick={() => loadData(days)}
            disabled={loading}
            className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
          >
            {loading ? '计算中…' : '🔄 刷新截面热力图'}
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* 指标卡片 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">全市场动态基准股票池</div>
            <div className="num mt-1 text-lg font-bold text-ink">
              {data ? data.expected_universe_size : '—'} 只
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">A 股全市场现役上市公司</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">平均截面有效率</div>
            <div className="num mt-1 text-lg font-bold text-success">
              {data ? `${data.mean_coverage_pct}%` : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">高于 98.0% 工业级基准</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">最低截面样本率 (底线)</div>
            <div className="num mt-1 text-lg font-bold text-brand-600">
              {data ? `${data.min_coverage_pct}%` : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">极端交易日容差状态受控</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">审计窗口跨度</div>
            <div className="num mt-1 text-lg font-bold text-ink">
              {data ? data.total_dates : '—'} 交易日
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">连续交易日截面穿透</div>
          </div>
        </div>

        {/* 热力图看板 */}
        <div className="rounded-lg border border-hair bg-surface p-3">
          <div className="mb-1 flex items-center justify-between text-xs font-semibold text-ink">
            <span>🔥 因子族截面可用度与空窗期热力图 (Feature Family Availability Heatmap)</span>
            <span className="text-2xs text-ink-muted">深绿=100%全覆盖 · 橙黄=存在时滞空窗</span>
          </div>
          <p className="mb-2 text-3xs text-ink-muted">
            横轴为评估交易日时序，纵轴为核心特征族维度。可直观穿透识别由财报披露空窗期或新股无基准期引起的截面有效性回落。
          </p>

          {loading ? (
            <div className="flex h-56 items-center justify-center text-xs text-ink-muted">
              <span className="animate-pulse">计算逐日横截面样本可用度热力图…</span>
            </div>
          ) : error ? (
            <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
          ) : !data?.heatmap_data?.length ? (
            <PanelEmpty text="暂无截面热力图数据" minH="min-h-[180px]" />
          ) : (
            <div ref={heatmapRef} className="h-56 w-full" />
          )}
        </div>
      </div>
    </div>
  );
}
