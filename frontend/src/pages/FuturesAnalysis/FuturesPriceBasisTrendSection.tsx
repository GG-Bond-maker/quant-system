/**
 * 期货分析 · 价格折线与期现基差双图。
 *
 * 价格图直接复用画像接口返回的真实收盘价序列；基差图使用独立接口，
 * 只在真实现货/指数序列可用时绘图。商品期货没有可信现货映射时明确显示不可用，
 * 不使用期货近远月价差或合成数据替代。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { futuresApi } from '@/api/futures';
import { FuturesDataUnavailable } from '@/components/FuturesDataUnavailable';
import { fmtAxisNum, fmtNum, fmtPct } from '@/utils/format';
import type {
  BasisHistoryResult,
  FuturesKlineWithIndicator,
} from '@/types/futures';

interface Props {
  symbol: string;
  name?: string;
  bars: FuturesKlineWithIndicator[];
  activeRange?: number;
  continuous?: boolean;
}

const REGIME_LABELS: Record<string, string> = {
  extreme_backwardation: '极度贴水',
  moderate_backwardation: '温和贴水',
  extreme_contango: '极度升水',
  moderate_contango: '温和升水',
  neutral: '中性区间',
};

const dispDate = (date: string) => date.replaceAll('-', '/');
const shortDate = (date: string) => date.slice(5);

function regimeLabel(regime: string | undefined): string {
  return regime ? REGIME_LABELS[regime] ?? regime : '—';
}

export function FuturesPriceBasisTrendSection({
  symbol,
  name,
  bars,
  activeRange = 250,
  continuous = true,
}: Props) {
  const theme = useTheme();
  const [basisData, setBasisData] = useState<BasisHistoryResult | null>(null);
  const [basisLoading, setBasisLoading] = useState(true);
  const [basisError, setBasisError] = useState<string | null>(null);

  const fullHistory = activeRange === 0;
  const rangeDays = fullHistory
    ? null
    : Math.max(30, Math.min(5000, Math.round(activeRange || 250)));
  const priceBars = useMemo(() => {
    if (!bars.length) return [];
    return rangeDays === null || rangeDays >= bars.length ? bars : bars.slice(-rangeDays);
  }, [bars, rangeDays]);

  useEffect(() => {
    let cancelled = false;
    setBasisLoading(true);
    setBasisError(null);
    setBasisData(null);

    futuresApi.basisHistory(symbol, rangeDays ?? 0)
      .then((result) => {
        if (!cancelled) setBasisData(result);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setBasisError(error instanceof Error ? error.message : '获取历史基差失败');
        }
      })
      .finally(() => {
        if (!cancelled) setBasisLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbol, rangeDays]);

  const priceOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!priceBars.length) return null;
    const p = chartPalette();
    const dates = priceBars.map((bar) => dispDate(bar.date));
    const closes = priceBars.map((bar) => bar.close);
    const priceLabel = continuous ? '后复权连续收盘价' : '主力收盘价';

    return {
      animationDuration: 320,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex?: number }>;
          const index = items[0]?.dataIndex ?? priceBars.length - 1;
          const row = priceBars[index];
          if (!row) return '';
          const previous = index > 0 ? priceBars[index - 1] : row;
          const change = row.close - previous.close;
          const changePct = previous.close > 0 ? (change / previous.close) * 100 : 0;
          return `<div style="font-size:11px;line-height:1.7">
            <b>${dispDate(row.date)}</b><br/>
            <span style="color:${p.INKM}">${priceLabel}</span>: <b>${fmtNum(row.close)}</b><br/>
            <span style="color:${p.INKM}">日涨跌</span>: <b style="color:${change >= 0 ? p.UP : p.DOWN}">${change >= 0 ? '+' : ''}${fmtNum(change)} (${fmtPct(changePct)})</b>
          </div>`;
        },
      },
      grid: { left: 48, right: 18, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisTick: { show: false },
        axisLabel: {
          color: p.INKM,
          fontSize: 9,
          hideOverlap: true,
          formatter: (value: string) => shortDate(value),
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        axisLabel: {
          color: p.INKM,
          fontSize: 9,
          formatter: (value: number) => fmtAxisNum(value, 2),
        },
      },
      series: [
        {
          name: priceLabel,
          type: 'line',
          data: closes,
          smooth: false,
          showSymbol: false,
          lineStyle: { color: p.BRAND, width: 2 },
          itemStyle: { color: p.BRAND },
          areaStyle: { color: withAlpha(p.BRAND, 0.12) },
        },
      ],
    };
  }, [continuous, priceBars, theme]);

  const basisSeries = basisData?.series ?? [];
  const basisOption = useMemo<echarts.EChartsOption | null>(() => {
    if (basisData?.available === false || !basisSeries.length) return null;
    const p = chartPalette();
    const dates = basisSeries.map((point) => dispDate(point.date));
    const rates = basisSeries.map((point) => point.basis_rate);
    const rateMin = Math.min(0, ...rates);
    const rateMax = Math.max(0, ...rates);
    const ratePad = Math.max((rateMax - rateMin) * 0.12, 0.05);

    return {
      animationDuration: 320,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex?: number }>;
          const index = items[0]?.dataIndex ?? basisSeries.length - 1;
          const point = basisSeries[index];
          if (!point) return '';
          const color = point.basis >= 0 ? p.UP : p.DOWN;
          return `<div style="font-size:11px;line-height:1.7">
            <b>${dispDate(point.date)}</b><br/>
            <span style="color:${p.INKM}">现货价格</span>: <b>${fmtNum(point.spot_price)}</b><br/>
            <span style="color:${p.INKM}">期货价格</span>: <b>${fmtNum(point.futures_price)}</b><br/>
            <span style="color:${p.INKM}">基差 (现货-期货)</span>: <b style="color:${color}">${fmtNum(point.basis)} 点</b><br/>
            <span style="color:${p.INKM}">基差率</span>: <b style="color:${color}">${fmtPct(point.basis_rate)}</b>
          </div>`;
        },
      },
      grid: { left: 48, right: 18, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: p.HAIR } },
        axisTick: { show: false },
        axisLabel: {
          color: p.INKM,
          fontSize: 9,
          hideOverlap: true,
          formatter: (value: string) => shortDate(value),
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        min: rateMin - ratePad,
        max: rateMax + ratePad,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        axisLabel: {
          color: p.INKM,
          fontSize: 9,
          formatter: (value: number) => fmtPct(value, 2),
        },
      },
      series: [
        {
          name: '基差率',
          type: 'line',
          data: rates,
          smooth: false,
          showSymbol: false,
          lineStyle: { color: p.BRAND, width: 2 },
          itemStyle: { color: p.BRAND },
          areaStyle: { color: withAlpha(p.BRAND, 0.12) },
          markLine: {
            silent: true,
            symbol: 'none',
            label: {
              color: p.INKM,
              fontSize: 9,
              formatter: '0 轴',
              position: 'insideEndTop',
            },
            lineStyle: { color: p.INKM, type: 'dashed', width: 1 },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    };
  }, [basisData?.available, basisSeries, theme]);

  const priceRef = useChart(priceOption);
  const basisRef = useChart(basisOption);
  const stats = basisData?.percentile_stats;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-ink">价格与基差趋势</div>
          <div className="mt-0.5 text-2xs text-ink-muted">
            {name ? `${name} · ` : ''}
            收盘价与期现基差对照 · 基差正值=贴水，负值=升水
          </div>
        </div>
        <span className="rounded-full border border-hair bg-surface-alt px-2 py-0.5 text-2xs text-ink-muted">
          {fullHistory ? '区间 全历史' : `区间 ${rangeDays} 交易日`}
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-surface p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between border-b border-hair pb-2">
            <span className="text-xs font-semibold text-ink">主力收盘价折线</span>
            <span className="text-2xs text-ink-muted">
              {continuous ? '后复权连续' : '原始主力'} · {priceBars.length} 点
            </span>
          </div>
          {priceBars.length ? (
            <div
              ref={priceRef}
              className="h-[280px] w-full"
              data-testid="futures-price-line-chart"
            />
          ) : (
            <FuturesDataUnavailable
              title="暂无价格序列"
              reason="当前品种没有可绘制的真实收盘价序列。"
            />
          )}
        </div>

        <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-surface p-3 shadow-sm">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
            <span className="text-xs font-semibold text-ink">折价 / 升水（基差）</span>
            {basisData?.available === true && (
              <span className="text-2xs text-ink-muted">
                原始主力基差率 · {basisData.input_start_date ?? '—'} ~ {basisData.input_end_date ?? '—'} · {basisSeries.length} 点
              </span>
            )}
          </div>

          {basisLoading && (
            <div className="flex h-[280px] items-center justify-center text-xs text-ink-muted animate-pulse">
              正在拉取真实现货/指数序列与历史基差...
            </div>
          )}

          {!basisLoading && basisError && (
            <div className="flex h-[280px] items-center justify-center px-4 text-center text-xs text-warn">
              {basisError}
            </div>
          )}

          {!basisLoading && !basisError && basisData?.available === false && (
            <FuturesDataUnavailable
              title="历史基差暂不可用"
              reason={basisData.reason ?? '缺少真实现货序列，无法计算历史基差'}
            />
          )}

          {!basisLoading && !basisError && basisData && basisData.available !== false && (
            <>
              <div className="mb-2 grid grid-cols-3 gap-2 text-2xs">
                <div className="rounded border border-hair bg-surface-alt p-1.5">
                  <span className="block text-ink-muted">当前基差</span>
                  <span className="font-mono font-semibold text-ink">
                    {fmtNum(basisData?.current_basis)} 点
                  </span>
                </div>
                <div className="rounded border border-hair bg-surface-alt p-1.5">
                  <span className="block text-ink-muted">历史分位</span>
                  <span className="font-mono font-semibold text-ink">
                    {stats ? `${stats.percentile}%` : '—'}
                  </span>
                </div>
                <div className="rounded border border-hair bg-surface-alt p-1.5">
                  <span className="block text-ink-muted">当前状态</span>
                  <span className="font-semibold text-ink">
                    {regimeLabel(stats?.regime)}
                  </span>
                </div>
              </div>
              {basisSeries.length ? (
                <div
                  ref={basisRef}
                  className="h-[220px] w-full"
                  data-testid="futures-basis-trend-chart"
                />
              ) : (
                <FuturesDataUnavailable
                  title="暂无可绘制基差"
                  reason="现货与期货没有足够的重叠交易日。"
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
