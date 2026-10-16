/**
 * 投资组合凸优化一键求解器（Portfolio Convex Optimizer Panel）。
 *
 * 对接真实后端接口：`POST /api/v1/research/optimize`
 * 理论支持：
 * - 风险平价 (Risk Parity, ERC)：欧拉齐次风险分解均衡化；
 * - 最大分散度 (Maximum Diversification)：Choueifaty 分散化比率求解；
 * - 最小方差 (Ledoit-Wolf 收缩 + 随机矩阵理论 RMT 降噪二次规划求解)；
 * - 波动率倒数加权 (Inverse Volatility)；
 * - Barra 风格因子截面暴露穿透评估（Size, Beta, Momentum, Volatility, Value）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { useAbortableTask } from '@/hooks/useAbortableTask';
import { ApiError } from '@/api/client';
import { researchApi } from '@/api/research';
import type { PortfolioAsset } from '@/types/portfolio';
import { fmtPct } from '@/utils/format';
import EfficientFrontierCard from './EfficientFrontierCard';

export type OptimizerMethod = 'risk_parity' | 'max_div' | 'mvo' | 'inverse_vol' | 'hrp' | 'black_litterman';

const METHOD_OPTIONS: Array<{
  key: OptimizerMethod;
  name: string;
  tag: string;
  desc: string;
}> = [
  {
    key: 'risk_parity',
    name: '风险平价 (Risk Parity)',
    tag: 'ERC 欧拉分解',
    desc: '每个资产对组合的总风险贡献平权，防止高波动单标的集中穿透。',
  },
  {
    key: 'max_div',
    name: '最大分散度 (Max Div)',
    tag: 'Choueifaty 比率',
    desc: '最大化各资产波动率加权和与组合波动率之比，提升有效分散资产数。',
  },
  {
    key: 'mvo',
    name: '最小方差 (Min Variance)',
    tag: 'LW 收缩 + RMT',
    desc: '高维协方差 Ledoit-Wolf 压缩与特征值降噪，求解总波动率最小组合。',
  },
  {
    key: 'inverse_vol',
    name: '波动率倒数加权 (Inv Vol)',
    tag: '单资产风险启发式',
    desc: '权重正比于资产历史波动率倒数（忽略相关性时的快速风险分散解）。',
  },
  {
    key: 'hrp',
    name: '分层风险平价 (HRP)',
    tag: '树状分层聚类',
    desc: '基于准对角化层次聚类与递归二分，无需协方差矩阵求逆，避免病态震荡。',
  },
  {
    key: 'black_litterman',
    name: 'Black-Litterman 贝叶斯',
    tag: '市场均衡 + 贝叶斯后验',
    desc: '反向工程隐含均衡收益率与贝叶斯先验更新，避免传统 Markowitz 极端配置缺陷。',
  },
];

const WEIGHT_CAP_OPTIONS = [
  { value: 0.0, label: '不设上限 (无单股约束)' },
  { value: 0.2, label: '20% 上限' },
  { value: 0.25, label: '25% 上限' },
  { value: 0.3, label: '30% 上限' },
  { value: 0.4, label: '40% 上限' },
];

const COV_WINDOW_OPTIONS = [
  { value: 60, label: '60 交易日 (近1季)' },
  { value: 120, label: '120 交易日 (近半年，推荐)' },
  { value: 250, label: '250 交易日 (近1年)' },
];

export interface PortfolioOptimizerPanelProps {
  /** 当前主回测面板里的资产列表 */
  currentAssets: PortfolioAsset[];
  /** 优化完成点击“应用到回测”时的回调 */
  onApplyWeights?: (newAssets: PortfolioAsset[]) => void;
  /** 初始是否收起折叠（默认 false） */
  initialCollapsed?: boolean;
}

export default function PortfolioOptimizerPanel({
  currentAssets,
  onApplyWeights,
  initialCollapsed = false,
}: PortfolioOptimizerPanelProps) {
  const navigate = useNavigate();
  const theme = useTheme();

  const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);

  // 内部待优化的资产清单
  const [optAssets, setOptAssets] = useState<
    Array<{ code: string; weight: number }>
  >([]);
  const [method, setMethod] = useState<OptimizerMethod>('risk_parity');
  const [weightCap, setWeightCap] = useState<number>(0.0);
  const [covWindow, setCovWindow] = useState<number>(120);
  const [turnoverPenalty, setTurnoverPenalty] = useState<number>(0.0);

  const [loading, setLoading] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);
  const [appliedMsg, setAppliedMsg] = useState<string | null>(null);

  type OptimizeResponse = Awaited<ReturnType<typeof researchApi.optimize>>;
  const [optResult, setOptResult] = useState<OptimizeResponse | null>(null);

  const optimizeTask = useAbortableTask();

  // 从传入的 currentAssets 同步（若有）
  const importCurrentAssets = useCallback(() => {
    if (currentAssets && currentAssets.length >= 2) {
      const sum = currentAssets.reduce((acc, a) => acc + (a.weight || 0), 0);
      const normalized = currentAssets.map((a) => ({
        code: a.code,
        weight: sum > 0 ? (a.weight || 0) / sum : 1 / currentAssets.length,
      }));
      setOptAssets(normalized);
      setErr(null);
    } else {
      // 预置默认资产池（3 只高流动性标的）
      setOptAssets([
        { code: '600519', weight: 0.34 },
        { code: '600036', weight: 0.33 },
        { code: '300750', weight: 0.33 },
      ]);
    }
  }, [currentAssets]);

  useEffect(() => {
    importCurrentAssets();
  }, [importCurrentAssets]);

  // 求解凸优化
  const runOptimization = useCallback(async () => {
    if (optAssets.length < 2) {
      setErr('求解器要求至少 2 只有效标的资产');
      return;
    }
    if (optAssets.length > 20) {
      setErr('当前支持最多 20 只标的资产同时优化');
      return;
    }
    const ctrl = optimizeTask.begin();
    setLoading(true);
    setErr(null);
    setAppliedMsg(null);
    try {
      const res = await researchApi.optimize(
        {
          assets: optAssets,
          method,
          weight_cap: weightCap,
          turnover_penalty: turnoverPenalty,
          cov_window: covWindow,
        },
        { signal: ctrl.signal },
      );
      if (ctrl.signal.aborted) return;
      setOptResult(res);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setErr(e instanceof ApiError ? e.message : '凸优化求解失败');
    } finally {
      if (optimizeTask.finish(ctrl)) setLoading(false);
    }
  }, [optAssets, method, weightCap, turnoverPenalty, covWindow, optimizeTask]);

  // 最优权重分布环形图 (Donut Chart)
  const donutOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!optResult?.weights) return null;
    const p = chartPalette();
    const weightEntries = Object.entries(optResult.weights).sort(
      (a, b) => b[1] - a[1],
    );
    const totalAssets = weightEntries.length;

    const data = weightEntries.map(([code, w], idx) => ({
      name: code,
      value: Number((w * 100).toFixed(2)),
      itemStyle: {
        color: CATEGORY_COLORS.PIE[idx % CATEGORY_COLORS.PIE.length],
      },
    }));

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const item = params as { name: string; value: number; percent: number };
          return `
            <div style="font-size:11px;font-family:monospace">
              <b>${item.name}</b><br/>
              最优配置: <b class="num" style="color:${p.BRAND}">${item.value}%</b>
            </div>
          `;
        },
      },
      legend: {
        orient: 'vertical',
        right: 4,
        top: 'center',
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { fontSize: 9, color: p.INK2, fontFamily: 'monospace' },
      },
      series: [
        {
          name: '最优权重',
          type: 'pie',
          radius: ['45%', '72%'],
          center: ['38%', '50%'],
          avoidLabelOverlap: false,
          label: {
            show: false,
            position: 'center',
          },
          emphasis: {
            label: {
              show: true,
              fontSize: 10,
              fontWeight: 'bold',
              formatter: '{b}\n{c}%',
              color: p.INK,
            },
          },
          labelLine: { show: false },
          data,
        },
      ],
      title: {
        text: `${totalAssets} 只\n资产`,
        left: '37%',
        top: '42%',
        textAlign: 'center',
        textStyle: {
          fontSize: 10,
          fontWeight: 'normal',
          color: p.INKM,
          lineHeight: 13,
        },
      },
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [optResult, theme]);

  const donutRef = useChart(donutOption);

  // Barra 风格因子暴露对比柱状图 (优化前 vs 优化后)
  const exposureOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!optResult?.exposure_before || !optResult?.exposure_after) return null;
    const p = chartPalette();
    const before = optResult.exposure_before;
    const after = optResult.exposure_after;
    const styles = Object.keys(before);

    return {
      grid: { left: 32, right: 12, top: 24, bottom: 24 },
      legend: {
        data: ['优化前名义暴露', '优化后目标暴露'],
        top: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const items = params as Array<{
            seriesName: string;
            name: string;
            value: number | null;
          }>;
          if (!items || !items.length) return '';
          const name = items[0].name;
          const factorLabel =
            optResult.style_factor_map?.[name] || name;
          return `
            <div style="font-size:11px;">
              <b>Barra 风格: ${name} (${factorLabel})</b><br/>
              ${items
                .map(
                  (it) =>
                    `${it.seriesName}: <b class="num">${
                      it.value != null ? Number(it.value).toFixed(3) : '—'
                    } σ</b>`,
                )
                .join('<br/>')}
            </div>
          `;
        },
      },
      xAxis: {
        type: 'category',
        data: styles,
        axisLabel: { fontSize: 9, color: p.INK2 },
      },
      yAxis: {
        type: 'value',
        name: 'z-score (σ)',
        nameTextStyle: { fontSize: 8, color: p.INKM },
        axisLabel: { fontSize: 9, color: p.INKM },
        splitLine: { lineStyle: { color: p.SUNKEN } },
      },
      series: [
        {
          name: '优化前名义暴露',
          type: 'bar',
          data: styles.map((s) => before[s]),
          itemStyle: { color: withAlpha(p.BRAND, 0.4) },
          barWidth: 14,
        },
        {
          name: '优化后目标暴露',
          type: 'bar',
          data: styles.map((s) => after[s]),
          itemStyle: { color: p.BRAND },
          barWidth: 14,
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theme 触发色板重算
  }, [optResult, theme]);

  const exposureRef = useChart(exposureOption);

  // 应用优化权重到主回测列表
  const handleApplyToBacktest = () => {
    if (!optResult?.weights || !onApplyWeights) return;
    const newAssets: PortfolioAsset[] = Object.entries(optResult.weights).map(
      ([code, w]) => {
        // 尝试匹配已有的名称和类型
        const matched = currentAssets.find(
          (ca) =>
            ca.code.replace(/\.(SH|SZ)$/i, '') ===
            code.replace(/\.(SH|SZ)$/i, ''),
        );
        return {
          code: code.replace(/\.(SH|SZ)$/i, ''),
          name: matched?.name ?? code,
          type: matched?.type ?? 'stock',
          weight: Number(w.toFixed(4)),
        };
      },
    );
    onApplyWeights(newAssets);
    setAppliedMsg('已成功将最优权重方案应用至回测资产列表！');
    setTimeout(() => setAppliedMsg(null), 4000);
  };

  // 投研联动闭环：一键将优化求解后的权重推送到模拟盘 (OrderDesk) 批量执行
  const handlePushToDesk = () => {
    if (!optResult?.weights) return;
    const totalCapital = 1_000_000; // 模拟盘基准建仓资金 100 万元
    const orders = Object.entries(optResult.weights)
      .filter(([, w]) => w > 0.005)
      .map(([code, w]) => ({
        symbol: code,
        side: 'buy' as const,
        order_amount: Math.max(10_000, Math.round((totalCapital * w) / 1000) * 1000),
        algo: 'vwap' as const,
        split_days: 5,
        participation_cap: 0.05,
        target_weight: Number((w * 100).toFixed(2)),
      }));

    if (!orders.length) return;

    sessionStorage.setItem(
      'aqp:desk:batch_import',
      JSON.stringify({
        source: `投资组合凸优化 (${METHOD_OPTIONS.find((m) => m.key === method)?.name ?? method})`,
        method,
        orders,
        createdAt: new Date().toISOString(),
      }),
    );
    navigate('/desk?from=optimizer');
  };

  return (
    <div className="rounded-lg border border-hair bg-surface p-3 space-y-3">
      {/* 头部标题与模型选择 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="flex items-center gap-1.5 text-left text-xs font-semibold text-ink hover:text-brand-600"
          >
            <span className="text-3xs text-ink-muted">{isCollapsed ? '▶' : '▼'}</span>
            <span>投资组合凸优化一键求解器</span>
          </button>
          <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-medium text-brand-700">
            LW 收缩协方差 · 真实高维求解
          </span>
          <button
            type="button"
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="rounded border border-hair px-1.5 py-0.5 text-3xs text-ink-secondary hover:bg-surface-alt"
          >
            {isCollapsed ? '展开求解器' : '收起折叠'}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={importCurrentAssets}
            className="rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary hover:bg-surface-alt"
            title="将当前已输入的标的池同步到求解器"
          >
            同步当前组合
          </button>
          <button
            onClick={() => {
              if (isCollapsed) setIsCollapsed(false);
              void runOptimization();
            }}
            disabled={loading || optAssets.length < 2}
            className="rounded bg-brand-500 px-3 py-1 text-2xs font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {loading ? '求解中…' : '一键求解最优权重'}
          </button>
        </div>
      </div>

      {!isCollapsed && (
        <>
          {err && (
            <div className="rounded-md bg-danger-bg px-3 py-2 text-2xs text-danger">
              {err}
            </div>
          )}
          {appliedMsg && (
            <div className="rounded-md bg-success-bg px-3 py-2 text-2xs text-success">
              {appliedMsg}
            </div>
          )}

      {/* 优化模型单选卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-2">
        {METHOD_OPTIONS.map((m) => {
          const active = method === m.key;
          return (
            <button
              key={m.key}
              onClick={() => setMethod(m.key)}
              className={`rounded-lg border p-2 text-left transition-all ${
                active
                  ? 'border-brand-500 bg-brand-50/20 shadow-xs'
                  : 'border-hair bg-surface-alt hover:border-hair2'
              }`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-2xs font-semibold ${
                    active ? 'text-brand-600' : 'text-ink'
                  }`}
                >
                  {m.name}
                </span>
                <span className="rounded bg-surface px-1.5 py-0.2 text-3xs font-mono text-ink-muted border border-hair">
                  {m.tag}
                </span>
              </div>
              <p className="mt-1 text-3xs text-ink-muted leading-tight">
                {m.desc}
              </p>
            </button>
          );
        })}
      </div>

      {/* 参数微调区 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 rounded-md bg-surface-alt p-2 text-2xs">
        <div>
          <span className="text-3xs text-ink-muted">单股权重上限 (Weight Cap):</span>
          <select
            value={weightCap}
            onChange={(e) => setWeightCap(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-2 py-1 text-ink outline-none focus:border-brand-300"
          >
            {WEIGHT_CAP_OPTIONS.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="text-3xs text-ink-muted">协方差回看窗口:</span>
          <select
            value={covWindow}
            onChange={(e) => setCovWindow(Number(e.target.value))}
            className="mt-0.5 w-full rounded border border-hair bg-surface px-2 py-1 text-ink outline-none focus:border-brand-300"
          >
            {COV_WINDOW_OPTIONS.map((cw) => (
              <option key={cw.value} value={cw.value}>
                {cw.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="text-3xs text-ink-muted">
            调仓换手惩罚系数 (Turnover Penalty):
          </span>
          <div className="mt-0.5 flex items-center gap-1.5">
            <input
              type="number"
              step="0.5"
              min="0"
              max="10"
              value={turnoverPenalty}
              onChange={(e) => setTurnoverPenalty(Number(e.target.value))}
              className="w-20 rounded border border-hair bg-surface px-2 py-1 font-mono text-ink outline-none focus:border-brand-300"
            />
            <span className="text-3xs text-ink-muted">
              阻尼调仓频率，避免过度换手
            </span>
          </div>
        </div>
      </div>

      {/* 求解结果展示区 */}
      {optResult && (
        <div className="space-y-3 pt-1">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-stretch">
            {/* 最优权重环形图 */}
            <div className="lg:col-span-5 rounded-lg border border-hair bg-surface p-2 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1">
                  <span className="font-semibold text-ink">
                    最优权重分布 ({optResult.method})
                  </span>
                  <span className="num font-mono text-3xs text-ink-muted">
                    有效样本: {optResult.n_obs} 日
                  </span>
                </div>
                <div ref={donutRef} className="h-52 w-full" />
              </div>
              <div className="pt-2 border-t border-hair space-y-1.5">
                {onApplyWeights && (
                  <button
                    onClick={handleApplyToBacktest}
                    className="w-full rounded bg-brand-50 py-1.5 text-xs font-medium text-brand-600 hover:bg-brand-100 transition-colors"
                  >
                    🚀 应用此最优权重到左侧组合回测
                  </button>
                )}
                <button
                  onClick={handlePushToDesk}
                  className="w-full rounded bg-brand-500 py-1.5 text-xs font-medium text-white hover:bg-brand-600 transition-colors flex items-center justify-center gap-1 shadow-xs"
                >
                  <span>💼 一键推送至模拟盘执行 (OrderDesk)</span>
                </button>
              </div>
            </div>

            {/* 权重变动对比表格 */}
            <div className="lg:col-span-7 rounded-lg border border-hair bg-surface p-2 flex flex-col">
              <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1 border-b border-hair pb-1">
                <span className="font-semibold text-ink">权重调仓与偏离对比</span>
                <span className="text-3xs text-ink-muted">
                  原名义权重 → 优化求解目标权重
                </span>
              </div>
              <div className="overflow-x-auto flex-1 max-h-56">
                <table className="w-full text-2xs">
                  <thead>
                    <tr className="border-b border-hair text-ink-muted">
                      <th className="py-1 text-left font-medium">标的代码</th>
                      <th className="py-1 text-right font-medium">优化前权重</th>
                      <th className="py-1 text-right font-medium">最优权重</th>
                      <th className="py-1 text-right font-medium">调仓变化 (Δw)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {optResult.symbols.map((sym) => {
                      const prevW = optResult.prev_weights[sym] ?? 0;
                      const nextW = optResult.weights[sym] ?? 0;
                      const diff = nextW - prevW;
                      return (
                        <tr key={sym} className="border-b border-hair/50">
                          <td className="py-1 font-mono font-medium text-ink">
                            {sym}
                          </td>
                          <td className="num py-1 text-right text-ink-secondary">
                            {(prevW * 100).toFixed(2)}%
                          </td>
                          <td className="num py-1 text-right font-semibold text-brand-600">
                            {(nextW * 100).toFixed(2)}%
                          </td>
                          <td
                            className={`num py-1 text-right font-medium ${
                              Math.abs(diff) < 0.001
                                ? 'text-ink-muted'
                                : diff > 0
                                ? 'text-danger'
                                : 'text-success'
                            }`}
                          >
                            {fmtPct(diff * 100)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Barra 风格因子暴露对比 */}
          {optResult.exposure_before && optResult.exposure_after && (
            <div className="rounded-lg border border-hair bg-surface p-3">
              <div className="flex items-center justify-between text-2xs text-ink-secondary mb-1">
                <span className="font-semibold text-ink">
                  Barra 风格因子暴露穿透对比 (截面标准化 z-score)
                </span>
                <span className="text-3xs text-ink-muted">
                  检验优化是否导致在 Size、Beta、Momentum、Vol 等因子上被动漂移
                </span>
              </div>
              <div ref={exposureRef} className="h-44 w-full" />
            </div>
          )}

          {/* 约束生效与数学说明 */}
          {optResult.weight_cap_info && (
            <div className="rounded bg-surface-alt p-2 text-3xs text-ink-secondary space-y-1 border border-hair">
              <div className="flex items-center gap-1.5 font-medium text-ink">
                <span>📌 单标的上限约束诊断：</span>
                <span
                  className={
                    optResult.weight_cap_info.feasible
                      ? 'text-success'
                      : 'text-danger'
                  }
                >
                  {optResult.weight_cap_info.feasible ? '数学可行' : '不可行'}
                </span>
                <span>·</span>
                <span>{optResult.weight_cap_info.note}</span>
              </div>
              {optResult.expected_returns?.basis === 'unavailable' && (
                <div className="text-ink-muted">
                  ⚠️ 提示：{optResult.expected_returns.note}
                </div>
              )}
            </div>
          )}
        </div>
      )}

          {/* 马科维茨有效前沿测算看板 */}
          <EfficientFrontierCard
            assets={optAssets}
            currentAssets={currentAssets}
            onApplyWeights={onApplyWeights}
          />
        </>
      )}
    </div>
  );
}
