/**
 * 选股中心 · 模型截面分层单调性与多空走势 (Quintile Spread Analysis)。
 *
 * 核心量化价值（对标主流量化机构与 Barra/JoinQuant 研报标准）：
 * - 将历史截面预测按 Score 严格等分为 Q1~Q5 五档（Q1 = 头部 20% 最强预测，Q5 = 尾部 20% 最弱预测）；
 * - 纯向量化核算各分档每日等权收益与累计净值；
 * - 核算 Long-Short 对冲走势 (Q1 - Q5) 与单调性得分 (Monotonicity Score)；
 * - 杜绝数据造假与假象：完全基于本地 predictions 分区与真实行情截面日线。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { screenerApi } from '@/api/screener';
import type { QuintileSpreadsResult } from '@/types/p1';
import { fmtPct } from '@/utils/format';

interface Props {
  board?: string;
  height?: number;
}

type ViewMode = 'all' | 'quintile' | 'spread';

const DAY_OPTIONS = [
  { label: '近15日', value: 15 },
  { label: '近30日', value: 30 },
  { label: '近60日', value: 60 },
] as const;

export default function QuintileSpreadsCard({ board = 'all', height = 240 }: Props) {
  const [days, setDays] = useState<number>(30);
  const [viewMode, setViewMode] = useState<ViewMode>('all');
  const [data, setData] = useState<QuintileSpreadsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    screenerApi.quintileSpreads({ days, board })
      .then((res) => {
        if (!cancelled) {
          setData(res);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '获取分层单调性数据失败');
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [days, board]);

  const metrics = data?.metrics;

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data || !data.dates || data.dates.length < 2) return null;
    const p = chartPalette();
    const dates = data.dates;

    // 分层颜色定义：Q1 (最强/绿/蓝) -> Q5 (最弱/紫/红)
    const seriesList: echarts.SeriesOption[] = [];

    if (viewMode === 'all' || viewMode === 'quintile') {
      seriesList.push(
        {
          name: 'Q1 (Top 20%)',
          type: 'line',
          data: data.q1,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 2, color: p.SUCCESS },
          itemStyle: { color: p.SUCCESS },
        },
        {
          name: 'Q2 (20%~40%)',
          type: 'line',
          data: data.q2,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.5, color: p.INFO },
          itemStyle: { color: p.INFO },
        },
        {
          name: 'Q3 (40%~60%)',
          type: 'line',
          data: data.q3,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.2, color: p.FLAT, type: 'dashed' },
          itemStyle: { color: p.FLAT },
        },
        {
          name: 'Q4 (60%~80%)',
          type: 'line',
          data: data.q4,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.5, color: p.WARN },
          itemStyle: { color: p.WARN },
        },
        {
          name: 'Q5 (Bottom 20%)',
          type: 'line',
          data: data.q5,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.8, color: p.DANGER },
          itemStyle: { color: p.DANGER },
        },
      );
    }

    if (viewMode === 'all' || viewMode === 'spread') {
      seriesList.push({
        name: 'Long-Short 对冲净值',
        type: 'line',
        data: data.long_short,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2.2, color: p.BRAND },
        itemStyle: { color: p.BRAND },
        areaStyle: viewMode === 'spread' ? {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: withAlpha(p.BRAND, 0.25) },
              { offset: 1, color: withAlpha(p.BRAND, 0.02) },
            ],
          },
        } : undefined,
      });
    }

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line' },
        formatter: (params: any) => {
          if (!Array.isArray(params) || params.length === 0) return '';
          const d = params[0].axisValue;
          let tip = `<div style="font-size:11px;min-width:130px;"><b>${d}</b><br/>`;
          params.forEach((it) => {
            const val = typeof it.value === 'number' ? it.value.toFixed(4) : '—';
            const retPct = typeof it.value === 'number' ? ((it.value - 1) * 100).toFixed(2) + '%' : '';
            tip += `<div style="display:flex;justify-content:space-between;margin:2px 0;">
              <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${it.color};margin-right:4px;"></span>${it.seriesName}</span>
              <b>${val} (${retPct})</b>
            </div>`;
          });
          tip += '</div>';
          return tip;
        },
      },
      legend: {
        top: 0,
        textStyle: { fontSize: 10, color: p.INK2 },
        itemWidth: 12,
        itemHeight: 6,
      },
      grid: { left: 8, right: 14, top: 32, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLabel: { fontSize: 9, color: p.INKM },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          fontSize: 9,
          color: p.INKM,
          formatter: (v: number) => v.toFixed(2),
        },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: seriesList,
    };
  }, [data, viewMode, theme]);

  const chartRef = useChart(option);

  // 单调性评价标签
  const monoScore = metrics?.monotonicity_score ?? 0;
  const monoBadge = useMemo(() => {
    if (!metrics) return null;
    if (monoScore >= 75) return { text: '强单调性 ★★★', cls: 'bg-up/10 text-up' };
    if (monoScore >= 50) return { text: '中度单调 ★★☆', cls: 'bg-brand-500/10 text-brand-600' };
    return { text: '弱单调/震荡 ★☆☆', cls: 'bg-warn-bg text-warn' };
  }, [monoScore, metrics]);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部标题与控制栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair px-3 py-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-ink">模型分层单调性与多空走势</h3>
          <span
            title="将截面预测按 Score 均分 Q1~Q5 五档，检验高预测组是否单调跑赢低预测组。Q1=头部20%多头，Q5=尾部20%空头。"
            className="inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-hair2 text-[9px] text-ink-muted"
          >
            ⓘ
          </span>
          {monoBadge && (
            <span className={`rounded px-1.5 py-0.5 text-2xs font-medium ${monoBadge.cls}`}>
              单调得分 {monoScore.toFixed(0)}分 · {monoBadge.text}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {/* 视图模式切换 */}
          <div className="flex items-center rounded-md bg-surface-sunken p-0.5 text-2xs">
            <button
              type="button"
              onClick={() => setViewMode('all')}
              className={`rounded px-2 py-0.5 transition-colors ${
                viewMode === 'all'
                  ? 'bg-surface font-medium text-brand-600 shadow-sm'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              全览对比
            </button>
            <button
              type="button"
              onClick={() => setViewMode('quintile')}
              className={`rounded px-2 py-0.5 transition-colors ${
                viewMode === 'quintile'
                  ? 'bg-surface font-medium text-brand-600 shadow-sm'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              五档分层
            </button>
            <button
              type="button"
              onClick={() => setViewMode('spread')}
              className={`rounded px-2 py-0.5 transition-colors ${
                viewMode === 'spread'
                  ? 'bg-surface font-medium text-brand-600 shadow-sm'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              多空对冲
            </button>
          </div>

          {/* 回看窗口 */}
          <div className="flex items-center rounded-md bg-surface-sunken p-0.5 text-2xs">
            {DAY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setDays(opt.value)}
                className={`rounded px-2 py-0.5 transition-colors ${
                  days === opt.value
                    ? 'bg-surface font-medium text-brand-600 shadow-sm'
                    : 'text-ink-muted hover:text-ink-secondary'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 核心指标条 */}
      {metrics && (
        <div className="grid grid-cols-2 gap-2 border-b border-hair/60 bg-surface-alt/30 px-3 py-1.5 text-2xs sm:grid-cols-5">
          <div>
            <span className="text-ink-muted">多空累计收益: </span>
            <span className={`num font-semibold ${metrics.spread_total_ret >= 0 ? 'text-up' : 'text-down'}`}>
              {fmtPct(metrics.spread_total_ret)}
            </span>
          </div>
          <div>
            <span className="text-ink-muted">多空夏普: </span>
            <span className="num font-semibold text-ink">
              {metrics.spread_sharpe.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="text-ink-muted">多空最大回撤: </span>
            <span className="num font-semibold text-down">
              {fmtPct(metrics.spread_max_dd)}
            </span>
          </div>
          <div>
            <span className="text-ink-muted">Q1 多头收益: </span>
            <span className={`num font-semibold ${metrics.q1_total_ret >= 0 ? 'text-up' : 'text-down'}`}>
              {fmtPct(metrics.q1_total_ret)}
            </span>
          </div>
          <div>
            <span className="text-ink-muted">Q5 空头收益: </span>
            <span className={`num font-semibold ${metrics.q5_total_ret >= 0 ? 'text-up' : 'text-down'}`}>
              {fmtPct(metrics.q5_total_ret)}
            </span>
          </div>
        </div>
      )}

      {/* 图表展示区 */}
      <div className="flex min-h-0 flex-1 flex-col p-2">
        {loading && !data && (
          <div style={{ height }} className="flex items-center justify-center text-xs text-ink-muted">
            正在按截面核算五档单调性净值...
          </div>
        )}

        {error && (
          <div style={{ height }} className="flex items-center justify-center text-xs text-warn">
            {error}
          </div>
        )}

        {!loading && !error && (!option || !data?.dates.length) && (
          <div style={{ height }} className="flex items-center justify-center text-xs text-ink-muted">
            暂无充足截面预测期数以绘制分层走势（至少需 2 期交易日）
          </div>
        )}

        {option && (
          <div ref={chartRef} style={{ minHeight: height }} className="w-full flex-1" />
        )}

        <div className="mt-1 flex items-center justify-between text-2xs text-ink-muted">
          <span>Q1=Top 20% 多头 · Q5=Bottom 20% 空头 · 向量化日终截面调仓</span>
          {metrics && (
            <span>样本量: {metrics.sample_count} 个交易日截面</span>
          )}
        </div>
      </div>
    </div>
  );
}
