/**
 * 马科维茨有效前沿测算看板（Markowitz Efficient Frontier Card）。
 *
 * 理论支持：
 * - 现代资产组合理论 (MPT / Mean-Variance Optimization)；
 * - 基于 Ledoit-Wolf 收缩高维协方差与预期收益向量，闭式扫描风险厌恶系数 λ；
 * - 求解有效前沿双曲线、全局最小方差组合 (Min Vol) 与资本市场线切线组合 (Max Sharpe)；
 * - 对接真实后端接口：`POST /api/v1/portfolio/efficient-frontier`。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { ApiError } from '@/api/client';
import { portfolioApi } from '@/api/portfolio';
import type {
  PortfolioAsset,
  EfficientFrontierPoint,
  EfficientFrontierResult,
} from '@/types/portfolio';
import { fmtPct } from '@/utils/format';

export interface EfficientFrontierCardProps {
  assets: Array<{ code: string; weight: number }>;
  currentAssets?: PortfolioAsset[];
  onApplyWeights?: (newAssets: PortfolioAsset[]) => void;
  startDate?: string;
  endDate?: string;
}

export default function EfficientFrontierCard({
  assets,
  currentAssets = [],
  onApplyWeights,
  startDate = '2023-01-01',
  endDate = '2024-12-31',
}: EfficientFrontierCardProps) {
  const theme = useTheme();
  const [rfRate, setRfRate] = useState<number>(0.02);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [frontierData, setFrontierData] = useState<EfficientFrontierResult | null>(null);
  const [appliedMsg, setAppliedMsg] = useState<string | null>(null);
  const [activePoint, setActivePoint] = useState<EfficientFrontierPoint | null>(null);

  const frontierTask = useAbortableTask();

  const runFrontier = useCallback(async () => {
    if (assets.length < 2) {
      setError('有效前沿测算至少需要 2 只有效标的资产');
      return;
    }
    const ctrl = frontierTask.begin();
    setLoading(true);
    setError(null);
    setAppliedMsg(null);

    const payloadAssets: PortfolioAsset[] = assets.map((a) => {
      const matched = currentAssets.find(
        (ca) => ca.code.replace(/\.(SH|SZ)$/i, '') === a.code.replace(/\.(SH|SZ)$/i, ''),
      );
      return {
        code: a.code,
        name: matched?.name ?? a.code,
        type: matched?.type ?? 'stock',
        weight: a.weight,
      };
    });

    try {
      const res = await portfolioApi.efficientFrontier(
        {
          assets: payloadAssets,
          start_date: startDate,
          end_date: endDate,
          risk_free_rate: rfRate,
        },
        { signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      setFrontierData(res);
      if (res.max_sharpe) setActivePoint(res.max_sharpe);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(e instanceof ApiError ? e.message : '有效前沿求解失败');
    } finally {
      if (frontierTask.finish(ctrl)) setLoading(false);
    }
  }, [assets, currentAssets, startDate, endDate, rfRate, frontierTask]);

  // 初始加载一次
  useEffect(() => {
    if (assets.length >= 2) {
      void runFrontier();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleApply = (pt: EfficientFrontierPoint | null) => {
    if (!pt || !onApplyWeights) return;
    const newAssets: PortfolioAsset[] = Object.entries(pt.weights).map(([code, w]) => {
      const cleanCode = code.replace(/\.(SH|SZ)$/i, '');
      const matched = currentAssets.find(
        (ca) => ca.code.replace(/\.(SH|SZ)$/i, '') === cleanCode,
      );
      return {
        code: cleanCode,
        name: matched?.name ?? cleanCode,
        type: matched?.type ?? 'stock',
        weight: Number(w.toFixed(4)),
      };
    });
    onApplyWeights(newAssets);
    setAppliedMsg(`已应用所选配置权重（预期年化 ${(pt.ret * 100).toFixed(1)}%，Sharpe ${pt.sharpe.toFixed(2)}）`);
    setTimeout(() => setAppliedMsg(null), 4000);
  };

  // ECharts 有效前沿曲线与散点图
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!frontierData?.frontier?.length) return null;
    const p = chartPalette();
    const fd = frontierData;

    // 前沿曲线数据点: [vol%, ret%]
    const frontierPoints = fd.frontier.map((pt) => [
      Number((pt.vol * 100).toFixed(2)),
      Number((pt.ret * 100).toFixed(2)),
      pt.sharpe,
    ]);

    // 单资产散点: [vol%, ret%, symbol, sharpe]
    const assetPoints = fd.assets.map((a) => [
      Number((a.vol * 100).toFixed(2)),
      Number((a.ret * 100).toFixed(2)),
      a.symbol,
      a.sharpe,
    ]);

    // 切线最优点 (Max Sharpe)
    const maxSharpeCoord = fd.max_sharpe
      ? [Number((fd.max_sharpe.vol * 100).toFixed(2)), Number((fd.max_sharpe.ret * 100).toFixed(2))]
      : null;

    // 最小方差组合 (Min Vol)
    const minVolCoord = fd.min_vol
      ? [Number((fd.min_vol.vol * 100).toFixed(2)), Number((fd.min_vol.ret * 100).toFixed(2))]
      : null;

    // 当前组合点 (Current)
    const currentCoord = fd.current
      ? [Number((fd.current.vol * 100).toFixed(2)), Number((fd.current.ret * 100).toFixed(2))]
      : null;

    // 资本配置线 (CAL: 从无风险利率点到切线最优组合延伸)
    const calPoints: number[][] = [];
    if (maxSharpeCoord) {
      calPoints.push([0, Number((fd.risk_free_rate * 100).toFixed(2))]);
      // 延伸到 1.5 倍切线最优波动率
      const extVol = maxSharpeCoord[0] * 1.5;
      const slope = (maxSharpeCoord[1] - fd.risk_free_rate * 100) / (maxSharpeCoord[0] || 1);
      const extRet = fd.risk_free_rate * 100 + slope * extVol;
      calPoints.push([extVol, extRet]);
    }

    return {
      grid: { left: 52, right: 28, top: 28, bottom: 32 },
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const item = params as {
            seriesName: string;
            data: unknown;
          };
          const data = item.data;
          if (Array.isArray(data)) {
            const vol = data[0];
            const ret = data[1];
            const extra = data[2];
            return `
              <div style="font-size:11px;font-family:monospace">
                <b>${item.seriesName} ${typeof extra === 'string' ? `(${extra})` : ''}</b><br/>
                年化波动率: <b class="num">${vol}%</b><br/>
                预期年化收益: <b class="num" style="color:${ret >= 0 ? p.UP : p.DOWN}">${ret}%</b><br/>
                夏普比率: <b class="num" style="color:${p.BRAND}">${typeof extra === 'number' ? extra.toFixed(2) : ((ret - fd.risk_free_rate * 100) / (vol || 1)).toFixed(2)}</b>
              </div>
            `;
          }
          return '';
        },
      },
      legend: {
        data: ['有效前沿 (Efficient Frontier)', '单资产点', '切线最大夏普 (Max Sharpe)', '全局最小方差 (Min Vol)', '当前持仓组合'],
        top: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      xAxis: {
        type: 'value',
        name: '年化波动率 (%)',
        nameLocation: 'middle',
        nameGap: 22,
        nameTextStyle: { fontSize: 9, color: p.INKM },
        axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => `${v.toFixed(1)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      yAxis: {
        type: 'value',
        name: '预期年化收益 (%)',
        nameTextStyle: { fontSize: 9, color: p.INKM },
        axisLabel: { fontSize: 8, color: p.INKM, formatter: (v: number) => `${v.toFixed(1)}%` },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        // 1. 有效前沿平滑曲线
        {
          name: '有效前沿 (Efficient Frontier)',
          type: 'line',
          smooth: true,
          data: frontierPoints,
          lineStyle: { width: 2.5, color: p.BRAND },
          itemStyle: { color: p.BRAND },
          showSymbol: true,
          symbolSize: 4,
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: withAlpha(p.BRAND, 0.25) },
              { offset: 1, color: withAlpha(p.BRAND, 0.02) },
            ]),
          },
        },
        // 2. 资本市场切线 (CAL)
        ...(calPoints.length
          ? [
              {
                name: '资本市场线 (CAL)',
                type: 'line' as const,
                data: calPoints,
                lineStyle: { type: 'dashed' as const, width: 1.2, color: withAlpha(p.WARN, 0.8) },
                symbol: 'none',
              },
            ]
          : []),
        // 3. 各单资产散点
        {
          name: '单资产点',
          type: 'scatter',
          data: assetPoints,
          symbolSize: 8,
          itemStyle: { color: withAlpha(p.INK2, 0.7) },
          label: {
            show: true,
            position: 'top',
            formatter: (param: unknown) => {
              const d = (param as { data: unknown[] }).data;
              return String(d[2] || '');
            },
            fontSize: 8,
            color: p.INKM,
            fontFamily: 'monospace',
          },
        },
        // 4. 切线最大夏普组合
        ...(maxSharpeCoord
          ? [
              {
                name: '切线最大夏普 (Max Sharpe)',
                type: 'scatter' as const,
                data: [maxSharpeCoord],
                symbol: 'pin',
                symbolSize: 22,
                itemStyle: { color: p.UP },
                label: {
                  show: true,
                  formatter: 'Max Sharpe',
                  position: 'top' as const,
                  fontSize: 9,
                  fontWeight: 'bold' as const,
                  color: p.UP,
                },
              },
            ]
          : []),
        // 5. 全局最小方差组合
        ...(minVolCoord
          ? [
              {
                name: '全局最小方差 (Min Vol)',
                type: 'scatter' as const,
                data: [minVolCoord],
                symbol: 'diamond',
                symbolSize: 12,
                itemStyle: { color: p.DOWN },
                label: {
                  show: true,
                  formatter: 'Min Vol',
                  position: 'bottom' as const,
                  fontSize: 8,
                  color: p.DOWN,
                },
              },
            ]
          : []),
        // 6. 当前组合
        ...(currentCoord
          ? [
              {
                name: '当前持仓组合',
                type: 'scatter' as const,
                data: [currentCoord],
                symbol: 'circle',
                symbolSize: 14,
                itemStyle: { color: p.WARN, borderColor: p.CANVAS, borderWidth: 2 },
                label: {
                  show: true,
                  formatter: '当前组合',
                  position: 'right' as const,
                  fontSize: 9,
                  color: p.WARN,
                },
              },
            ]
          : []),
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [frontierData, theme]);

  const chartRef = useChart(option);

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-3">
      {/* 头部标题与操作 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">
            马科维茨有效前沿与资本资产定价 (Markowitz MPT)
          </span>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            Ledoit-Wolf 收缩 · 切线最优组合解析
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 text-2xs text-ink-secondary">
            <span>无风险利率 Rf:</span>
            <select
              value={rfRate}
              onChange={(e) => setRfRate(Number(e.target.value))}
              className="rounded border border-hair bg-surface-alt px-1.5 py-0.5 font-mono text-ink outline-none"
            >
              <option value={0.015}>1.5% (国债1Y)</option>
              <option value={0.02}>2.0% (基准2%)</option>
              <option value={0.025}>2.5% (国债10Y)</option>
              <option value={0.03}>3.0% (中性利率)</option>
            </select>
          </div>

          <button
            onClick={() => void runFrontier()}
            disabled={loading || assets.length < 2}
            className="rounded bg-brand-500 px-2.5 py-1 text-2xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            {loading ? '测算中…' : '重新测算有效前沿'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded bg-danger-bg px-3 py-2 text-2xs text-danger">
          {error}
        </div>
      )}

      {appliedMsg && (
        <div className="rounded bg-success-bg px-3 py-2 text-2xs text-success">
          {appliedMsg}
        </div>
      )}

      {/* 核心组合对比卡 */}
      {frontierData && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-2xs">
          {/* 最大夏普切线点 */}
          <div className="rounded-lg border border-brand-500/40 bg-surface-alt p-2.5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-ink">
                <span className="font-semibold text-brand-600">
                  🎯 最大夏普切线组合 (Max Sharpe)
                </span>
                <span className="num font-bold text-xs text-brand-600">
                  {frontierData.max_sharpe?.sharpe.toFixed(2)}
                </span>
              </div>
              <div className="mt-1 flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化收益:</span>
                <b className="num text-danger">
                  +{((frontierData.max_sharpe?.ret ?? 0) * 100).toFixed(1)}%
                </b>
              </div>
              <div className="flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化波动:</span>
                <b className="num text-ink">
                  {((frontierData.max_sharpe?.vol ?? 0) * 100).toFixed(1)}%
                </b>
              </div>
            </div>
            {onApplyWeights && (
              <button
                onClick={() => handleApply(frontierData.max_sharpe)}
                className="mt-2 w-full rounded bg-brand-500 py-1 text-3xs font-medium text-white hover:bg-brand-600 transition-colors"
              >
                应用切线最优权重
              </button>
            )}
          </div>

          {/* 全局最小方差点 */}
          <div className="rounded-lg border border-hair bg-surface-alt p-2.5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-ink">
                <span className="font-semibold text-ink-secondary">
                  🛡️ 全局最小方差组合 (Min Vol)
                </span>
                <span className="num font-bold text-xs text-ink">
                  {frontierData.min_vol?.sharpe.toFixed(2)}
                </span>
              </div>
              <div className="mt-1 flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化收益:</span>
                <b className="num text-danger">
                  +{((frontierData.min_vol?.ret ?? 0) * 100).toFixed(1)}%
                </b>
              </div>
              <div className="flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化波动:</span>
                <b className="num text-success">
                  {((frontierData.min_vol?.vol ?? 0) * 100).toFixed(1)}% (最低)
                </b>
              </div>
            </div>
            {onApplyWeights && (
              <button
                onClick={() => handleApply(frontierData.min_vol)}
                className="mt-2 w-full rounded border border-hair bg-surface py-1 text-3xs font-medium text-ink-secondary hover:bg-surface-alt transition-colors"
              >
                应用最小方差权重
              </button>
            )}
          </div>

          {/* 当前组合坐标 */}
          <div className="rounded-lg border border-hair bg-surface-alt p-2.5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between text-ink">
                <span className="font-semibold text-ink-secondary">
                  📍 当前持仓配置 (Current)
                </span>
                <span className="num font-bold text-xs text-ink">
                  {frontierData.current ? frontierData.current.sharpe.toFixed(2) : '—'}
                </span>
              </div>
              <div className="mt-1 flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化收益:</span>
                <b className="num text-ink">
                  {frontierData.current
                    ? `${((frontierData.current.ret) * 100).toFixed(1)}%`
                    : '—'}
                </b>
              </div>
              <div className="flex items-baseline justify-between text-3xs text-ink-muted">
                <span>预期年化波动:</span>
                <b className="num text-ink">
                  {frontierData.current
                    ? `${((frontierData.current.vol) * 100).toFixed(1)}%`
                    : '—'}
                </b>
              </div>
            </div>
            <div className="mt-2 text-3xs text-ink-muted text-center py-1">
              {frontierData.current && frontierData.max_sharpe
                ? `距切线前沿 Sharpe 差距: ${(
                    frontierData.max_sharpe.sharpe - frontierData.current.sharpe
                  ).toFixed(2)}`
                : '有效前沿基准对齐'}
            </div>
          </div>
        </div>
      )}

      {/* 有效前沿交互图表 */}
      <div className="space-y-1">
        <div ref={chartRef} className="h-64 w-full" />
        <p className="text-3xs text-ink-muted px-1">
          注：有效前沿曲线描绘了给定预期收益下的最小方差资产配置边界；虚线为切线资本市场配置线 (CAL)；红色图钉为夏普比率最大的市场组合；绿色菱形为全局风险最小组合。
        </p>
      </div>

      {/* 激活前沿点权重详情预览 */}
      {activePoint?.weights && (
        <div className="rounded bg-surface-alt p-2 text-2xs border border-hair space-y-1">
          <div className="flex items-center justify-between text-3xs text-ink-muted">
            <span className="font-semibold text-ink">
              所选前沿点配置权重明细 (年化收益 {(activePoint.ret * 100).toFixed(1)}% · 波动率 {(activePoint.vol * 100).toFixed(1)}% · Sharpe {activePoint.sharpe.toFixed(2)})
            </span>
            {onApplyWeights && (
              <button
                onClick={() => handleApply(activePoint)}
                className="text-brand-600 hover:underline font-medium"
              >
                应用此权重方案
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {Object.entries(activePoint.weights).map(([sym, w]) => (
              <span key={sym} className="rounded bg-surface px-1.5 py-0.5 font-mono text-3xs border border-hair">
                <span className="text-ink-secondary mr-1">{sym}:</span>
                <b className="text-brand-600">{fmtPct(w * 100)}</b>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
