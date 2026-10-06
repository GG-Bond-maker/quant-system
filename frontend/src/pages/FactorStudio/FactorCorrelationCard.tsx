/**
 * 因子正交与相关性矩阵看板（Factor Correlation & Orthogonality Matrix）。
 *
 * 对接真实后端接口：`POST /api/v1/research/factor-corr`
 * 理论支持：
 * - 截面 Spearman 秩相关系数矩阵时序均值；
 * - 多重共线性诊断（Variance Inflation Factor 预警，|ρ| >= 0.7 红牌高亮）；
 * - 因子正交纯化联动（Gram-Schmidt / 截面多元回归残差剥离）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { ApiError } from '@/api/client';
import { researchApi } from '@/api/research';
import type { CustomFactor } from '@/api/production';

const PRESET_GROUPS: Array<{
  category: string;
  items: Array<{ key: string; label: string }>;
}> = [
  {
    category: '动量与反转',
    items: [
      { key: 'ret_5', label: '5日收益率' },
      { key: 'ret_20', label: '20日收益率' },
      { key: 'rsi_14', label: '14日RSI' },
    ],
  },
  {
    category: '波动与风险',
    items: [
      { key: 'vol_20', label: '20日波动率' },
      { key: 'vol_60', label: '60日波动率' },
      { key: 'skew_ret_20', label: '20日偏度' },
    ],
  },
  {
    category: '量价与趋势',
    items: [
      { key: 'ma_gap_20', label: '20日均线差' },
      { key: 'v_rank_20', label: '20日成交分位' },
      { key: 'turn_20', label: '20日换手率' },
    ],
  },
];

const WINDOW_PRESETS = [
  { value: 30, label: '30日 (近1月)' },
  { value: 60, label: '60日 (近1季)' },
  { value: 120, label: '120日 (近半年)' },
  { value: 250, label: '250日 (近1年)' },
];

export interface FactorCorrelationCardProps {
  customFactors?: CustomFactor[] | null;
  onSelectForOrthogonalize?: (factorExprOrName: string) => void;
}

export default function FactorCorrelationCard({
  customFactors,
  onSelectForOrthogonalize,
}: FactorCorrelationCardProps) {
  const theme = useTheme();
  const [selectedFactors, setSelectedFactors] = useState<string[]>([
    'ret_5',
    'vol_20',
    'rsi_14',
    'skew_ret_20',
    'ma_gap_20',
    'v_rank_20',
  ]);
  const [windowDays, setWindowDays] = useState<number>(60);
  const [corrResult, setCorrResult] = useState<
    Awaited<ReturnType<typeof researchApi.factorCorr>> | null
  >(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  const corrTask = useAbortableTask();

  // 执行相关性矩阵计算（真实截面 Spearman 计算）
  const runCorr = useCallback(
    async (factorsToRun = selectedFactors, days = windowDays) => {
      if (factorsToRun.length < 2) {
        setErr('请至少选择 2 个因子进行相关性分析');
        return;
      }
      if (factorsToRun.length > 8) {
        setErr('至多同时选择 8 个因子进行相关性分析');
        return;
      }
      const ctrl = corrTask.begin();
      setLoading(true);
      setErr(null);
      try {
        const res = await researchApi.factorCorr(
          { factors: factorsToRun, window_days: days },
          { signal: ctrl.signal },
        );
        if (ctrl.signal.aborted) return;
        setCorrResult(res);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setErr(e instanceof ApiError ? e.message : '相关性矩阵计算失败');
      } finally {
        if (corrTask.finish(ctrl)) setLoading(false);
      }
    },
    [selectedFactors, windowDays, corrTask],
  );

  // 初始加载一次
  useEffect(() => {
    void runCorr();
  }, [runCorr]);

  const toggleFactor = (key: string) => {
    setSelectedFactors((prev) => {
      if (prev.includes(key)) {
        if (prev.length <= 2) {
          setErr('因子数量不得少于 2 个');
          return prev;
        }
        setErr(null);
        return prev.filter((f) => f !== key);
      }
      if (prev.length >= 8) {
        setErr('至多同时选择 8 个因子（保持热力图可读性）');
        return prev;
      }
      setErr(null);
      return [...prev, key];
    });
  };

  // ECharts Heatmap 配置项
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!corrResult || !corrResult.factors.length) return null;
    const p = chartPalette();
    const factors = corrResult.factors;
    const matrix = corrResult.matrix;
    const data: Array<[number, number, number]> = [];

    for (let i = 0; i < factors.length; i++) {
      for (let j = 0; j < factors.length; j++) {
        // ECharts 热力图坐标：[xIdx, yIdx, value]
        // y 轴逆序放置让左上角对齐传统矩阵
        data.push([j, factors.length - 1 - i, matrix[i]?.[j] ?? 0]);
      }
    }

    return {
      grid: { left: 88, right: 60, top: 12, bottom: 44 },
      xAxis: {
        type: 'category',
        data: factors,
        axisLabel: {
          fontSize: 10,
          rotate: 35,
          color: p.INK2,
          fontFamily: 'monospace',
        },
      },
      yAxis: {
        type: 'category',
        data: [...factors].reverse(),
        axisLabel: {
          fontSize: 10,
          color: p.INK2,
          fontFamily: 'monospace',
        },
      },
      visualMap: {
        min: -1,
        max: 1,
        calculable: false,
        orient: 'vertical',
        right: 4,
        top: 'center',
        itemHeight: 90,
        inRange: {
          // 发散色谱：负相关偏绿 (p.DOWN)，0为卡片底色 (p.CARD)，正相关偏红 (p.UP)
          color: [p.DOWN, p.CARD, p.UP],
        },
        textStyle: { fontSize: 9, color: p.INKM },
      },
      tooltip: {
        formatter: (param: unknown) => {
          const item = param as { data: [number, number, number] };
          if (!item?.data) return '';
          const xIdx = item.data[0];
          const yIdx = factors.length - 1 - item.data[1];
          const f1 = factors[xIdx];
          const f2 = factors[yIdx];
          const rho = item.data[2];
          const isHigh = Math.abs(rho) >= 0.7 && xIdx !== yIdx;
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>${f2}</b> × <b>${f1}</b><br/>
              Spearman 秩相关系数 ρ = <b class="num" style="color:${rho >= 0 ? p.UP : p.DOWN}">${rho.toFixed(3)}</b>
              ${isHigh ? '<br/><span style="color:' + p.DANGER + '">⚠️ |ρ| ≥ 0.7 高度共线</span>' : ''}
            </div>
          `;
        },
      },
      series: [
        {
          type: 'heatmap',
          data,
          label: {
            show: true,
            fontSize: 9,
            formatter: (param: unknown) => {
              const item = param as { data: [number, number, number] };
              const v = item?.data?.[2];
              if (v == null) return '';
              return v.toFixed(2);
            },
            color: p.INK,
          },
          itemStyle: {
            borderColor: p.CARD,
            borderWidth: 1.5,
          },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [corrResult, theme]);

  const chartRef = useChart(option);

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-3">
      {/* 头部标题与操作 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-ink">
            因子正交与相关性矩阵看板
          </h3>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            真实截面 Spearman
          </span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={windowDays}
            onChange={(e) => {
              const d = Number(e.target.value);
              setWindowDays(d);
              void runCorr(selectedFactors, d);
            }}
            className="rounded border border-hair bg-surface px-2 py-1 text-2xs text-ink outline-none focus:border-brand-300"
          >
            {WINDOW_PRESETS.map((wp) => (
              <option key={wp.value} value={wp.value}>
                {wp.label}
              </option>
            ))}
          </select>
          <button
            onClick={() => void runCorr()}
            disabled={loading}
            className="rounded bg-brand-500 px-3 py-1 text-2xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {loading ? '计算中…' : '一键刷新矩阵'}
          </button>
        </div>
      </div>

      {err && (
        <div className="rounded-md bg-danger-bg px-3 py-2 text-2xs text-danger">
          {err}
        </div>
      )}

      {/* 因子选择器 (标签切换) */}
      <div className="space-y-2 rounded-md bg-surface-alt p-2 text-2xs">
        <div className="flex items-center justify-between text-ink-secondary font-medium">
          <span>
            已选因子 ({selectedFactors.length}/8 · 至少选择 2 项):
          </span>
          <span className="font-mono text-ink-muted">
            {selectedFactors.join(', ')}
          </span>
        </div>

        {/* 预置分类因子勾选 */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {PRESET_GROUPS.map((grp) => (
            <div key={grp.category} className="space-y-1">
              <span className="text-3xs font-semibold text-ink-muted uppercase">
                {grp.category}
              </span>
              <div className="flex flex-wrap gap-1">
                {grp.items.map((it) => {
                  const active = selectedFactors.includes(it.key);
                  return (
                    <button
                      key={it.key}
                      onClick={() => toggleFactor(it.key)}
                      className={`rounded px-1.5 py-0.5 font-mono text-2xs border transition-colors ${
                        active
                          ? 'border-brand-500 bg-brand-500/10 text-brand-600 font-medium'
                          : 'border-hair bg-surface text-ink-secondary hover:border-hair2'
                      }`}
                      title={`${it.label} (${it.key})`}
                    >
                      {it.key}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* 用户自定义因子池 */}
        {customFactors && customFactors.length > 0 && (
          <div className="pt-1 border-t border-hair/60">
            <span className="text-3xs font-semibold text-ink-muted uppercase">
              自定义因子库 (已登记)
            </span>
            <div className="mt-1 flex flex-wrap gap-1">
              {customFactors.map((cf) => {
                const active = selectedFactors.includes(cf.name);
                return (
                  <button
                    key={cf.id}
                    onClick={() => toggleFactor(cf.name)}
                    className={`rounded px-1.5 py-0.5 font-mono text-2xs border transition-colors ${
                      active
                        ? 'border-brand-500 bg-brand-500/10 text-brand-600 font-medium'
                        : 'border-hair bg-surface text-ink-secondary hover:border-hair2'
                    }`}
                    title={cf.expression}
                  >
                    ★ {cf.name}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* 热力图与多重共线性预警看板 */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-start">
        {/* 热力图渲染区 */}
        <div className="lg:col-span-8 rounded-lg border border-hair bg-surface p-2">
          <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1">
            <span className="font-medium">
              Spearman 秩相关系数矩阵 (近 {corrResult?.window_days ?? windowDays} 交易日)
            </span>
            <span className="text-ink-muted text-3xs">
              正相关偏红 · 负相关偏绿 · 对角线恒为 1.0
            </span>
          </div>
          <div ref={chartRef} className="h-64 w-full" />
        </div>

        {/* 右侧：多重共线性诊断面板 */}
        <div className="lg:col-span-4 space-y-2 rounded-lg border border-hair bg-surface-alt p-3 text-2xs">
          <div className="flex items-center justify-between border-b border-hair pb-1.5">
            <span className="font-semibold text-ink">共线性诊断与正交建议</span>
            {corrResult?.high_corr_pairs && corrResult.high_corr_pairs.length > 0 ? (
              <span className="rounded bg-danger-bg px-1.5 py-0.5 text-3xs font-semibold text-danger">
                共线预警
              </span>
            ) : (
              <span className="rounded bg-success-bg px-1.5 py-0.5 text-3xs font-semibold text-success">
                低共线性
              </span>
            )}
          </div>

          {corrResult?.high_corr_pairs && corrResult.high_corr_pairs.length > 0 ? (
            <div className="space-y-1.5">
              <div className="text-danger font-medium text-2xs">
                ⚠️ 检测到 {corrResult.high_corr_pairs.length} 对高相关因子 (|ρ| ≥ 0.7)：
              </div>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {corrResult.high_corr_pairs.map((pair) => (
                  <div
                    key={`${pair.a}-${pair.b}`}
                    className="flex items-center justify-between rounded bg-surface p-1.5 border border-hair"
                  >
                    <div className="font-mono text-ink">
                      <span>{pair.a}</span>
                      <span className="text-ink-muted mx-1">×</span>
                      <span>{pair.b}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="num font-semibold text-danger">
                        {pair.r.toFixed(3)}
                      </span>
                      {onSelectForOrthogonalize && (
                        <button
                          onClick={() => onSelectForOrthogonalize(pair.a)}
                          className="rounded border border-brand-500/40 px-1 py-0.2 text-3xs text-brand-500 hover:bg-brand-500/10"
                          title="将此因子填入下方进行正交纯化分析"
                        >
                          纯化
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-3xs text-ink-muted leading-relaxed">
                在多因子选股模型中，强相关因子会导致线性回归方差膨胀（VIF 升高）并破坏特征权重稳定性。建议对冗余因子进行 Gram-Schmidt 正交化或残差回归纯化。
              </p>
            </div>
          ) : (
            <div className="space-y-1 py-2">
              <div className="text-success font-medium">
                ✓ 候选池因子间无 |ρ| ≥ 0.7 显著共线性
              </div>
              <p className="text-3xs text-ink-muted leading-relaxed">
                当前因子组合各维度信息重叠度较低，具备良好的正交纯度，适合直接输入多因子加权评分或机器学习模型。
              </p>
            </div>
          )}

          {/* 纯化快速跳转指引 */}
          <div className="pt-2 border-t border-hair text-3xs text-ink-secondary">
            <span>💡 提示：点击高相关对旁的「纯化」可自动代入因子实验室正交纯化器，剥离基准风格因子的相关性。</span>
          </div>
        </div>
      </div>
    </div>
  );
}
