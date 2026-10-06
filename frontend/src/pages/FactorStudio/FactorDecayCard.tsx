/**
 * 因子 IC 衰减与换手半衰期分析看板（Factor Decay & Turnover Half-Life Card）。
 *
 * 理论支持：
 * - 评估 Alpha 信号随滞后期（Lag 1~20 期）的预测力衰减曲线；
 * - 测算信号衰减半衰期（Half-Life），给出最佳换手与调仓周期建议；
 * - 对接真实后端接口：`POST /api/v1/research/factor-decay`。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { ApiError } from '@/api/client';
import { researchApi, type FactorDecayResponse } from '@/api/research';
import type { CustomFactor } from '@/api/production';

const PRESET_FACTORS = [
  'ret_5',
  'ret_20',
  'vol_20',
  'rsi_14',
  'skew_ret_20',
  'ma_gap_20',
  'v_rank_20',
];

export interface FactorDecayCardProps {
  customFactors?: CustomFactor[] | null;
  initialFactor?: string;
}

export default function FactorDecayCard({
  customFactors,
  initialFactor = 'ret_5',
}: FactorDecayCardProps) {
  const theme = useTheme();
  const [selectedFactor, setSelectedFactor] = useState<string>(initialFactor);
  const [neutralizeSize, setNeutralizeSize] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [decayData, setDecayData] = useState<FactorDecayResponse | null>(null);

  const decayTask = useAbortableTask();

  const factorOptions = useMemo(() => {
    const list = [...PRESET_FACTORS];
    if (customFactors) {
      customFactors.forEach((cf) => {
        if (!list.includes(cf.name)) list.push(cf.name);
      });
    }
    return list;
  }, [customFactors]);

  const runDecayAnalysis = useCallback(
    async (factor: string, neutralize: boolean) => {
      if (!factor.trim()) return;
      const ctrl = decayTask.begin();
      setLoading(true);
      setError(null);
      try {
        const res = await researchApi.factorDecay(
          {
            factor: factor.trim(),
            lags: [1, 2, 3, 5, 10, 15, 20],
            neutralize_size: neutralize,
          },
          { signal: ctrl.signal },
        );
        if (ctrl.signal.aborted) return;
        setDecayData(res);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : '测算因子衰减失败');
      } finally {
        if (decayTask.finish(ctrl)) setLoading(false);
      }
    },
    [decayTask],
  );

  // 初始加载一次默认因子
  useEffect(() => {
    void runDecayAnalysis(selectedFactor, neutralizeSize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 衰减柱状与折线图
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!decayData?.lags?.length) return null;
    const p = chartPalette();
    const lags = decayData.lags;
    const xData = lags.map((l) => `T+${l.lag}`);
    const icData = lags.map((l) => (l.mean_ic != null ? Number(l.mean_ic.toFixed(4)) : 0));
    const icirData = lags.map((l) => (l.icir != null ? Number(l.icir.toFixed(3)) : 0));

    const halfLifeTarget =
      icData[0] != null && Math.abs(icData[0]) > 1e-6
        ? Number((icData[0] * 0.5).toFixed(4))
        : null;

    return {
      grid: { left: 44, right: 44, top: 28, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{
            seriesName: string;
            name: string;
            value: number;
          }>;
          if (!items?.length) return '';
          const name = items[0].name;
          const lagNum = parseInt(name.replace('T+', ''), 10);
          const row = lags.find((l) => l.lag === lagNum);
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>滞后周期: ${name} (${lagNum}个交易日)</b><br/>
              Rank IC: <b class="num" style="color:${(row?.mean_ic ?? 0) >= 0 ? p.UP : p.DOWN}">${row?.mean_ic ?? '—'}</b><br/>
              ICIR: <b class="num" style="color:${p.BRAND}">${row?.icir ?? '—'}</b><br/>
              t 统计量: <b class="num">${row?.t_stat ?? '—'}</b><br/>
              IC>0 占比: <b class="num">${row?.positive_ratio != null ? `${(row.positive_ratio * 100).toFixed(1)}%` : '—'}</b>
            </div>
          `;
        },
      },
      legend: {
        data: ['Rank IC 均值', 'ICIR 稳定性'],
        top: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      xAxis: {
        type: 'category',
        data: xData,
        axisLabel: { fontSize: 9, color: p.INKM },
        axisLine: { lineStyle: { color: p.HAIR } },
      },
      yAxis: [
        {
          type: 'value',
          name: 'Rank IC',
          nameTextStyle: { fontSize: 8, color: p.INKM },
          axisLabel: {
            fontSize: 8,
            color: p.INKM,
            formatter: (v: number) => v.toFixed(3),
          },
          splitLine: { lineStyle: { color: p.SUNKEN } },
        },
        {
          type: 'value',
          name: 'ICIR',
          nameTextStyle: { fontSize: 8, color: p.INKM },
          axisLabel: {
            fontSize: 8,
            color: p.INKM,
            formatter: (v: number) => v.toFixed(2),
          },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: 'Rank IC 均值',
          type: 'bar',
          barMaxWidth: 24,
          data: icData.map((v) => ({
            value: v,
            itemStyle: {
              color: v >= 0 ? withAlpha(p.UP, 0.75) : withAlpha(p.DOWN, 0.75),
              borderRadius: [2, 2, 0, 0],
            },
          })),
          markLine:
            halfLifeTarget != null
              ? {
                  silent: true,
                  symbol: 'none',
                  data: [
                    {
                      yAxis: halfLifeTarget,
                      lineStyle: { type: 'dashed', color: p.WARN, width: 1.2 },
                      label: {
                        formatter: `50% 半衰阈值 (${halfLifeTarget})`,
                        fontSize: 8,
                        position: 'middle',
                        color: p.WARN,
                      },
                    },
                  ],
                }
              : undefined,
        },
        {
          name: 'ICIR 稳定性',
          type: 'line',
          yAxisIndex: 1,
          data: icirData,
          showSymbol: true,
          symbolSize: 5,
          lineStyle: { width: 1.6, color: p.BRAND },
          itemStyle: { color: p.BRAND },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [decayData, theme]);

  const chartRef = useChart(option);

  // 关键衍生统计指标
  const firstIc = decayData?.lags?.[0]?.mean_ic;
  const lastIc = decayData?.lags?.[decayData.lags.length - 1]?.mean_ic;
  const retentionPct =
    firstIc && lastIc && Math.abs(firstIc) > 1e-6
      ? (lastIc / firstIc) * 100
      : null;

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-3">
      {/* 头部工具栏 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink">
            因子 IC 衰减与换手半衰期分析
          </span>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            Lag 1~20 期前向收益 Rank IC 衰减测算
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* 因子选择下拉框 */}
          <select
            value={selectedFactor}
            onChange={(e) => {
              const val = e.target.value;
              setSelectedFactor(val);
              void runDecayAnalysis(val, neutralizeSize);
            }}
            className="rounded border border-hair bg-surface-alt px-2 py-1 text-2xs font-mono text-ink outline-none focus:border-brand-300"
          >
            {factorOptions.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>

          {/* 市值中性化 Switch */}
          <label className="flex items-center gap-1.5 cursor-pointer text-2xs text-ink-secondary">
            <input
              type="checkbox"
              checked={neutralizeSize}
              onChange={(e) => {
                const val = e.target.checked;
                setNeutralizeSize(val);
                void runDecayAnalysis(selectedFactor, val);
              }}
              className="rounded border-hair accent-brand-500"
            />
            <span>市值中性化</span>
          </label>

          <button
            onClick={() => void runDecayAnalysis(selectedFactor, neutralizeSize)}
            disabled={loading}
            className="rounded border border-hair px-2.5 py-1 text-2xs text-ink-secondary hover:bg-surface-alt disabled:opacity-50"
          >
            {loading ? '测算中…' : '刷新测算'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded bg-danger-bg px-3 py-2 text-2xs text-danger">
          {error}
        </div>
      )}

      {/* 核心指标卡 */}
      {decayData && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-2xs">
          <div className="rounded bg-surface-alt p-2 border border-hair">
            <div className="text-ink-muted">信号半衰期 (Half-Life)</div>
            <div className="mt-0.5">
              <b className="num text-xs font-semibold text-brand-600">
                {decayData.half_life_lag != null
                  ? `${decayData.half_life_lag} 交易日`
                  : '未衰减过半 (>20日)'}
              </b>
            </div>
          </div>
          <div className="rounded bg-surface-alt p-2 border border-hair">
            <div className="text-ink-muted">建议调仓/持有周期</div>
            <div className="mt-0.5">
              <b className="num text-xs font-semibold text-ink">
                {decayData.suggested_holding_period}
              </b>
            </div>
          </div>
          <div className="rounded bg-surface-alt p-2 border border-hair">
            <div className="text-ink-muted">初始预测力 (Lag 1 IC)</div>
            <div className="mt-0.5">
              <b
                className={`num text-xs font-semibold ${
                  (firstIc ?? 0) >= 0 ? 'text-danger' : 'text-success'
                }`}
              >
                {firstIc != null ? firstIc.toFixed(4) : '—'}
              </b>
            </div>
          </div>
          <div className="rounded bg-surface-alt p-2 border border-hair">
            <div className="text-ink-muted">20日衰减留存率</div>
            <div className="mt-0.5">
              <b className="num text-xs font-semibold text-ink-secondary">
                {retentionPct != null ? `${retentionPct.toFixed(1)}%` : '—'}
              </b>
            </div>
          </div>
        </div>
      )}

      {/* ECharts 衰减曲线 */}
      <div className="space-y-1">
        <div ref={chartRef} className="h-48 w-full" />
        <p className="text-3xs text-ink-muted px-1">
          注：柱状高度表示该因子在滞后 1~20 期前向收益率上的截面 Rank IC 均值；虚线标记 50% 预测力半衰分界点；ICIR 折线反映多期预测的信噪比稳定性。
        </p>
      </div>

      {/* 逐期明细表 */}
      {decayData?.lags && decayData.lags.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-hair text-ink-muted">
                <th className="py-1 text-left font-medium">滞后天数</th>
                <th className="py-1 text-right font-medium">Rank IC 均值</th>
                <th className="py-1 text-right font-medium">ICIR 稳定性</th>
                <th className="py-1 text-right font-medium">t 检验统计量</th>
                <th className="py-1 text-right font-medium">正 IC 交易日占比</th>
                <th className="py-1 text-right font-medium">有效截面数</th>
              </tr>
            </thead>
            <tbody>
              {decayData.lags.map((row) => (
                <tr key={row.lag} className="border-b border-hair/50 font-mono">
                  <td className="py-1 text-left text-ink">T+{row.lag}</td>
                  <td
                    className={`num py-1 text-right font-medium ${
                      (row.mean_ic ?? 0) >= 0 ? 'text-danger' : 'text-success'
                    }`}
                  >
                    {row.mean_ic != null ? row.mean_ic.toFixed(4) : '—'}
                  </td>
                  <td className="num py-1 text-right text-brand-600">
                    {row.icir != null ? row.icir.toFixed(3) : '—'}
                  </td>
                  <td className="num py-1 text-right text-ink-secondary">
                    {row.t_stat != null ? row.t_stat.toFixed(2) : '—'}
                  </td>
                  <td className="num py-1 text-right text-ink">
                    {row.positive_ratio != null
                      ? `${(row.positive_ratio * 100).toFixed(1)}%`
                      : '—'}
                  </td>
                  <td className="num py-1 text-right text-ink-muted">
                    {row.n_days ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
