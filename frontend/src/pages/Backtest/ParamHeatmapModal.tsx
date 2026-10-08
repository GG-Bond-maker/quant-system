/**
 * 2D 参数敏感性热力图弹窗（ParamHeatmapModal）。
 *
 * ## 设计思想与量化投研标准
 * 顶级量化研究（WorldQuant BRAIN / JoinQuant）对参数寻优的核心要求是：
 * **严防过拟合（Overfitting）与数据窥探偏误（Data Snooping Bias）**。
 *
 * 通过将短均线 (short_ma) 与长均线 (long_ma) 等超参数展开为 2D 夏普比率敏感性热力矩阵：
 * 1. 稳健高原 (Robust Plateau)：最优解周围参数夏普平稳过渡，策略具备高鲁棒性与参数容错率；
 * 2. 孤峰陷阱 (Overfit Peak)：仅单一孤立点夏普奇高，周边迅速衰退，大概率为过拟合噪音。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';

export interface ParamTrial {
  params: Record<string, number>;
  objective: number;
}

export interface ParamHeatmapModalProps {
  isOpen: boolean;
  onClose: () => void;
  trials?: ParamTrial[];
  bestParams?: Record<string, number>;
  method?: string;
}

export default function ParamHeatmapModal({
  isOpen,
  onClose,
  trials = [],
  bestParams,
  method = 'grid',
}: ParamHeatmapModalProps) {
  useTheme();

  // 提取 2D 坐标轴与热力矩阵数据
  const { xData, yData, heatmapData, xKey, yKey, stats, bestCoord } = useMemo(() => {
    if (!trials.length) {
      return {
        xData: [] as number[],
        yData: [] as number[],
        heatmapData: [] as Array<[number, number, number]>,
        xKey: 'short_ma',
        yKey: 'long_ma',
        stats: null,
        bestCoord: null as [number, number] | null,
      };
    }

    // 确定两个主轴参数名（优先短均线与长均线，其次前两个参数键）
    const sampleKeys = Object.keys(trials[0].params);
    let chosenX = sampleKeys.find((k) => k.toLowerCase().includes('short')) || sampleKeys[0] || 'x';
    let chosenY = sampleKeys.find((k) => k.toLowerCase().includes('long')) || sampleKeys[1] || 'y';
    if (chosenX === chosenY && sampleKeys.length > 1) {
      chosenY = sampleKeys.find((k) => k !== chosenX) || 'y';
    }

    const xVals = Array.from(new Set(trials.map((t) => t.params[chosenX]).filter((v): v is number => v != null))).sort((a, b) => a - b);
    const yVals = Array.from(new Set(trials.map((t) => t.params[chosenY]).filter((v): v is number => v != null))).sort((a, b) => a - b);

    const xMap = new Map<number, number>(xVals.map((v, i) => [v, i]));
    const yMap = new Map<number, number>(yVals.map((v, i) => [v, i]));

    const gridMap = new Map<string, number>();
    let maxObj = -Infinity;
    let minObj = Infinity;
    let sumObj = 0;
    const allObjectives: number[] = [];

    for (const t of trials) {
      const xv = t.params[chosenX];
      const yv = t.params[chosenY];
      if (xv == null || yv == null) continue;
      const key = `${xv}_${yv}`;
      // 保留当前单元格的最优目标值
      const prev = gridMap.get(key);
      const cur = t.objective;
      if (prev == null || cur > prev) {
        gridMap.set(key, cur);
      }
      if (cur > maxObj) maxObj = cur;
      if (cur < minObj) minObj = cur;
      sumObj += cur;
      allObjectives.push(cur);
    }

    const matrix: Array<[number, number, number]> = [];
    xVals.forEach((xv) => {
      const xi = xMap.get(xv)!;
      yVals.forEach((yv) => {
        const yi = yMap.get(yv)!;
        const val = gridMap.get(`${xv}_${yv}`);
        if (val != null) {
          matrix.push([xi, yi, Number(val.toFixed(2))]);
        }
      });
    });

    // 定位最优参数坐标
    let bestX = -1;
    let bestY = -1;
    if (bestParams && bestParams[chosenX] != null && bestParams[chosenY] != null) {
      bestX = xMap.get(bestParams[chosenX]) ?? -1;
      bestY = yMap.get(bestParams[chosenY]) ?? -1;
    }

    const avg = allObjectives.length ? sumObj / allObjectives.length : 0;
    const variance = allObjectives.length
      ? allObjectives.reduce((acc, v) => acc + Math.pow(v - avg, 2), 0) / allObjectives.length
      : 0;
    const stdDev = Math.sqrt(variance);

    // 粗略判断过拟合倾向：若最优值与均值差距超过 2.5 个标准差，提示孤峰偏误
    const isPeakOverfit = maxObj > avg + 2.5 * stdDev && allObjectives.length >= 6;

    return {
      xData: xVals,
      yData: yVals,
      heatmapData: matrix,
      xKey: chosenX,
      yKey: chosenY,
      stats: {
        maxObj: Number.isFinite(maxObj) ? maxObj : 0,
        minObj: Number.isFinite(minObj) ? minObj : 0,
        avg: Number.isFinite(avg) ? avg : 0,
        stdDev: Number.isFinite(stdDev) ? stdDev : 0,
        isPeakOverfit,
        totalTrials: trials.length,
      },
      bestCoord: bestX >= 0 && bestY >= 0 ? [bestX, bestY] as [number, number] : null,
    };
  }, [trials, bestParams]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!heatmapData.length || !xData.length || !yData.length) return null;
    const p = chartPalette();
    const minVal = Math.floor((stats?.minObj ?? 0) * 10) / 10;
    const maxVal = Math.ceil((stats?.maxObj ?? 1) * 10) / 10;

    return {
      title: {
        text: `2D 参数夏普热力图（${xKey} × ${yKey}）`,
        subtext: '色温越高代表夏普比率越高；观察是否形成高夏普高原',
        left: 12,
        top: 8,
        textStyle: { fontSize: 13, fontWeight: 'bold', color: p.INK },
        subtextStyle: { fontSize: 10, color: p.INKM },
      },
      tooltip: {
        position: 'top',
        formatter: (params: unknown) => {
          const item = params as { data: [number, number, number] };
          if (!item?.data) return '';
          const [xi, yi, val] = item.data;
          const isBest = bestCoord && bestCoord[0] === xi && bestCoord[1] === yi;
          return `
            <div style="font-size:11px;font-family:monospace;padding:2px 4px">
              <b>${xKey}:</b> ${xData[xi]}<br/>
              <b>${yKey}:</b> ${yData[yi]}<br/>
              <b>夏普比率 (Sharpe):</b> <span style="font-weight:bold;color:${val >= 0 ? p.UP : p.DOWN}">${val}</span>
              ${isBest ? `<br/><span style="color:${p.BRAND};font-weight:bold">★ 最优推荐参数</span>` : ''}
            </div>
          `;
        },
      },
      grid: {
        left: 54,
        right: 48,
        top: 60,
        bottom: 60,
      },
      xAxis: {
        type: 'category',
        data: xData.map(String),
        name: xKey,
        nameLocation: 'middle',
        nameGap: 24,
        nameTextStyle: { fontSize: 11, color: p.INK2 },
        splitArea: { show: true },
        axisLabel: { fontSize: 10, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: {
        type: 'category',
        data: yData.map(String),
        name: yKey,
        nameLocation: 'middle',
        nameGap: 36,
        nameTextStyle: { fontSize: 11, color: p.INK2 },
        splitArea: { show: true },
        axisLabel: { fontSize: 10, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      visualMap: {
        min: minVal,
        max: maxVal === minVal ? minVal + 1 : maxVal,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 8,
        inRange: {
          color: [p.SUNKEN, p.INFO, p.BRAND, p.UP],
        },
        textStyle: { fontSize: 10, color: p.INKM },
      },
      series: [
        {
          name: '夏普比率',
          type: 'heatmap',
          data: heatmapData,
          label: {
            show: heatmapData.length <= 64,
            fontSize: 9,
            color: p.INK,
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowColor: p.BRAND,
            },
          },
        },
      ],
    };
  }, [heatmapData, xData, yData, xKey, yKey, stats, bestCoord]);

  const chartRef = useChart(option);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-hair bg-surface shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-hair px-5 py-3.5">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold text-ink">参数敏感性 2D 热力分析</span>
            <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-mono font-medium text-brand-600">
              {method.toUpperCase()} 试验采样 ({stats?.totalTrials ?? 0} 组)
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-alt hover:text-ink"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto p-5 space-y-4">
          {trials.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-xs text-ink-muted">
              当前暂无寻优采样试验数据。请在参数配置中勾选「启用参数寻优」并重新运行回测。
            </div>
          ) : (
            <>
              {/* ECharts Heatmap */}
              <div className="rounded-lg border border-hair bg-surface-alt/50 p-2">
                <div ref={chartRef} className="h-96 w-full" />
              </div>

              {/* Robustness Diagnosis Card */}
              {stats && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-4 text-xs">
                  <div className="rounded-lg border border-hair bg-surface p-3">
                    <div className="text-2xs text-ink-muted">最优目标夏普 (Max)</div>
                    <div className="mt-1 num text-base font-semibold t-up">
                      {stats.maxObj.toFixed(2)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-hair bg-surface p-3">
                    <div className="text-2xs text-ink-muted">全域均值夏普 (Mean)</div>
                    <div className="mt-1 num text-base font-semibold text-ink">
                      {stats.avg.toFixed(2)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-hair bg-surface p-3">
                    <div className="text-2xs text-ink-muted">参数敏感度 (Std Dev)</div>
                    <div className="mt-1 num text-base font-semibold text-ink">
                      {stats.stdDev.toFixed(2)}
                    </div>
                  </div>
                  <div className={`rounded-lg border p-3 ${
                    stats.isPeakOverfit
                      ? 'border-danger-300 bg-danger-bg text-danger'
                      : 'border-success-300 bg-success-bg text-success'
                  }`}>
                    <div className="text-2xs font-medium">鲁棒性诊断</div>
                    <div className="mt-1 text-xs font-semibold">
                      {stats.isPeakOverfit ? '⚠ 疑似孤峰过拟合' : '✓ 稳健高原分布'}
                    </div>
                  </div>
                </div>
              )}

              {/* Academic & Engineering Explanation */}
              <div className="rounded-lg border border-hair bg-surface-alt p-3.5 text-2xs leading-relaxed text-ink-secondary">
                <div className="font-semibold text-ink mb-1">量化投研知识库：参数高原 vs 孤峰识别</div>
                <p>
                  • <b>稳健高原 (Robust Plateau)：</b>若最优参数（如 short_ma={bestParams?.short_ma ?? 5}, long_ma={bestParams?.long_ma ?? 20}）周边相邻网格的夏普比率变化平缓且普遍为正，说明该策略在市场微结构变化或波动率漂移时依然具备鲁棒盈利能力。
                </p>
                <p className="mt-1">
                  • <b>孤峰过拟合 (Overfit Peak)：</b>若最优参数表现极为突出，但相邻参数迅速转负或急剧滑坡，表明该组合高度依赖历史特定样本点的幸运匹配，实盘运行极易失效（Data Snooping Bias）。
                </p>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end border-t border-hair px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-brand-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
          >
            关闭分析
          </button>
        </div>
      </div>
    </div>
  );
}
