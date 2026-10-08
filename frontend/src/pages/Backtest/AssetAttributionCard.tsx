/**
 * 多资产 PnL 盈亏贡献度分解与持仓结构卡片（AssetAttributionCard）。
 * 对标 Barra / JoinQuant 多资产投资组合分析体系。
 */
import { useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { fmtNum } from '@/utils/format';

export interface AssetAttributionCardProps {
  assetPnl?: Record<string, number>;
  holdings?: Array<{ date: string; holdings: Record<string, number> }>;
  equityCurve?: Array<{ date: string; nav: number; equity: number; cash: number }>;
  loading?: boolean;
  onExportUniverse?: (symbols: string[]) => void;
}

export default function AssetAttributionCard({
  assetPnl = {},
  holdings = [],
  equityCurve = [],
  loading = false,
  onExportUniverse,
}: AssetAttributionCardProps) {
  useTheme();
  const [exportedTip, setExportedTip] = useState<string | null>(null);

  // 整理各资产盈亏数据
  const { sortedItems, totalPnl, winCount, lossCount, topWinner, topLoser } = useMemo(() => {
    const entries = Object.entries(assetPnl);
    if (!entries.length) {
      return {
        sortedItems: [] as Array<{ symbol: string; pnl: number }>,
        totalPnl: 0,
        winCount: 0,
        lossCount: 0,
        topWinner: null as { symbol: string; pnl: number } | null,
        topLoser: null as { symbol: string; pnl: number } | null,
      };
    }

    const items = entries.map(([symbol, pnl]) => ({ symbol, pnl }));
    // 按盈亏降序排序
    items.sort((a, b) => b.pnl - a.pnl);

    let sum = 0;
    let win = 0;
    let loss = 0;
    for (const it of items) {
      sum += it.pnl;
      if (it.pnl > 0) win++;
      else if (it.pnl < 0) loss++;
    }

    return {
      sortedItems: items,
      totalPnl: sum,
      winCount: win,
      lossCount: loss,
      topWinner: items.length > 0 && items[0].pnl > 0 ? items[0] : null,
      topLoser: items.length > 0 && items[items.length - 1].pnl < 0 ? items[items.length - 1] : null,
    };
  }, [assetPnl]);

  // ECharts 水平柱状图呈现各资产 PnL 盈亏
  const barOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!sortedItems.length) return null;
    const p = chartPalette();
    // 最多展示前 15 只重点资产，过多时截断展示
    const displayItems = [...sortedItems].slice(0, 15).reverse();
    const categories = displayItems.map((it) => it.symbol.split('.')[0]);
    const values = displayItems.map((it) => it.pnl);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const items = params as Array<{ dataIndex: number }>;
          if (!items?.length) return '';
          const idx = items[0].dataIndex;
          const item = displayItems[idx];
          if (!item) return '';
          const isPos = item.pnl >= 0;
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px 4px">
              <b>标的代码:</b> ${item.symbol}<br/>
              <b>累计盈亏:</b> <span style="font-weight:bold;color:${isPos ? p.UP : p.DOWN}">
                ${isPos ? '+' : ''}${fmtNum(item.pnl)} 元
              </span><br/>
              <b>折算万元:</b> ${(item.pnl / 1e4).toFixed(2)} 万
            </div>
          `;
        },
      },
      grid: {
        left: 64,
        right: 24,
        top: 14,
        bottom: 24,
      },
      xAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 9,
          color: p.INKM,
          formatter: (v: number) => `${(v / 1e4).toFixed(0)}万`,
        },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { fontSize: 9, color: p.INK2 },
        axisLine: { lineStyle: { color: p.HAIR } },
        axisTick: { show: false },
      },
      series: [
        {
          name: 'PnL 贡献',
          type: 'bar',
          data: values.map((val) => ({
            value: val,
            itemStyle: {
              color: val >= 0 ? p.UP : p.DOWN,
              borderRadius: val >= 0 ? [0, 3, 3, 0] : [3, 0, 0, 3],
            },
          })),
        },
      ],
    };
  }, [sortedItems]);

  const barChartRef = useChart(barOption);

  // 一键导出所有参与资产至组合优化
  const handleExport = () => {
    const symbols = sortedItems.map((i) => i.symbol);
    if (!symbols.length) return;
    try {
      localStorage.setItem('aqp:optimizer_imported_universe', JSON.stringify(symbols));
      if (onExportUniverse) {
        onExportUniverse(symbols);
      }
      setExportedTip(`已成功导出 ${symbols.length} 只标的至组合优化工作台！可在「投资组合」中一键加载。`);
      setTimeout(() => setExportedTip(null), 4000);
    } catch {
      setExportedTip('导出失败，请重试');
    }
  };

  const hasData = sortedItems.length > 0;

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface">
      <div className="flex flex-wrap items-center justify-between border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink">多资产 PnL 盈亏贡献度分解</h2>
          <span className="rounded bg-surface-alt px-1.5 py-0.5 text-3xs font-mono text-ink-muted">
            Asset Attribution
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasData && (
            <button
              type="button"
              onClick={handleExport}
              className="rounded border border-brand-500/30 bg-brand-50/50 px-2 py-0.5 text-2xs font-medium text-brand-600 hover:bg-brand-50 transition-colors"
            >
              🎯 导出标的至组合优化
            </button>
          )}
        </div>
      </div>

      {exportedTip && (
        <div className="bg-success-bg border-b border-hair px-4 py-1.5 text-2xs text-success transition-all">
          {exportedTip}
        </div>
      )}

      <div className="p-3">
        {loading ? (
          <div className="animate-pulse space-y-2">
            <div className="h-10 rounded bg-surface-sunken" />
            <div className="h-48 rounded bg-surface-sunken" />
          </div>
        ) : !hasData ? (
          <div className="flex h-36 items-center justify-center text-xs text-ink-muted">
            单资产或暂无资产盈亏归因数据（回测多标的策略后展示）
          </div>
        ) : (
          <div className="space-y-3">
            {/* KPI Statistics */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
              <div className="rounded bg-surface-alt p-2 border border-hair">
                <div className="text-ink-muted">累计总 PnL</div>
                <div className={`mt-0.5 num text-xs font-semibold ${totalPnl >= 0 ? 't-up' : 't-down'}`}>
                  {totalPnl >= 0 ? '+' : ''}{(totalPnl / 1e4).toFixed(1)} 万元
                </div>
              </div>
              <div className="rounded bg-surface-alt p-2 border border-hair">
                <div className="text-ink-muted">盈利/亏损标的</div>
                <div className="mt-0.5 num text-xs font-semibold text-ink">
                  <span className="t-up">{winCount}</span> / <span className="t-down">{lossCount}</span> 只
                </div>
              </div>
              <div className="rounded bg-surface-alt p-2 border border-hair">
                <div className="text-ink-muted">最大正贡献标的</div>
                <div className="mt-0.5 num text-xs font-medium text-ink truncate">
                  {topWinner ? `${topWinner.symbol.split('.')[0]} (+${(topWinner.pnl / 1e4).toFixed(1)}万)` : '—'}
                </div>
              </div>
              <div className="rounded bg-surface-alt p-2 border border-hair">
                <div className="text-ink-muted">最大亏损拖累标的</div>
                <div className="mt-0.5 num text-xs font-medium text-ink truncate">
                  {topLoser ? `${topLoser.symbol.split('.')[0]} (${(topLoser.pnl / 1e4).toFixed(1)}万)` : '—'}
                </div>
              </div>
            </div>

            {/* PnL Horizontal Bar Chart */}
            <div className="rounded border border-hair bg-surface p-2">
              <div className="text-2xs font-semibold text-ink mb-1">标的 PnL 净盈亏排行 (元)</div>
              <div ref={barChartRef} className="h-52 w-full" />
            </div>

            {/* Holding and Equity Tail Overview */}
            {equityCurve.length > 0 && (
              <div className="text-3xs text-ink-muted flex items-center justify-between">
                <span>
                  最新持仓市值: {fmtNum(equityCurve[equityCurve.length - 1].equity)} 元 ·
                  可用现金: {fmtNum(equityCurve[equityCurve.length - 1].cash)} 元
                </span>
                {holdings.length > 0 && (
                  <span>当前持仓总数: {Object.keys(holdings[holdings.length - 1].holdings || {}).length} 只</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
