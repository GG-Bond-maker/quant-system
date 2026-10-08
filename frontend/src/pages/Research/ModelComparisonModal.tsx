/**
 * MLOps 多实验版本 Head-to-Head 横向对比弹窗 (Model Comparison Modal)。
 *
 * 理论与工程背景：
 * 在量化模型选代中（Qlib / WorldQuant 体系），多版本横向对比是决定“是否晋升生产模型”的核心评审机制。
 * 本组件支持量化研究员勾选 2~4 个不同版本（如 Baseline、调参实验、新特征版本），
 * 针对样本内表现 (Valid Rank IC)、样本外泛化 (Test Rank IC)、过拟合裂口 (Overfitting Gap)、
 * 超参数差异与雷达图多维对垒，辅助决策冠军模型。
 */
import { useMemo } from 'react';
import * as echarts from '@/lib/echarts';
import { CATEGORY_COLORS, chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';

export interface ExperimentItem {
  model_name: string;
  version: string;
  status: string;
  is_production: number;
  feature_version: string;
  created_at: string;
  train_start: string | null;
  train_end: string | null;
  metrics: Record<string, number | null>;
  hyperparams: Record<string, number | null>;
  has_model_file: boolean;
}

export interface ModelComparisonModalProps {
  open: boolean;
  onClose: () => void;
  selectedExperiments: ExperimentItem[];
}

export default function ModelComparisonModal({
  open,
  onClose,
  selectedExperiments,
}: ModelComparisonModalProps) {
  const theme = useTheme();

  // 雷达图对比
  const radarOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!selectedExperiments.length) return null;
    const p = chartPalette();
    const colors = CATEGORY_COLORS.SEQ;

    const indicators = [
      { name: '验证集 IC', max: 0.15 },
      { name: '测试集 IC (OOS)', max: 0.15 },
      { name: '抗过拟合度', max: 1.0 },
      { name: 'ICIR 稳定性', max: 3.0 },
      { name: '超参保守度', max: 1.0 },
    ];

    const seriesData = selectedExperiments.map((exp, idx) => {
      const validIc = Math.max(0, exp.metrics.valid_rank_ic ?? 0);
      const testIc = Math.max(0, exp.metrics.test_rank_ic ?? 0);
      const gap = Math.abs(validIc - testIc);
      const antiOverfit = Math.max(0, 1 - gap * 10);
      const icir = Math.max(0, exp.metrics.icir ?? exp.metrics.valid_icir ?? 1.2);
      const lr = exp.hyperparams.learning_rate ?? 0.05;
      const conservatism = Math.max(0, 1 - lr * 5);

      return {
        name: `${exp.version}${exp.is_production ? ' (Prod)' : ''}`,
        value: [
          Number(validIc.toFixed(4)),
          Number(testIc.toFixed(4)),
          Number(antiOverfit.toFixed(2)),
          Number(icir.toFixed(2)),
          Number(conservatism.toFixed(2)),
        ],
        itemStyle: { color: colors[idx % colors.length] },
        lineStyle: { width: 2, color: colors[idx % colors.length] },
        areaStyle: { opacity: 0.15, color: colors[idx % colors.length] },
      };
    });

    return {
      tooltip: { trigger: 'item' },
      legend: {
        data: seriesData.map((s) => s.name),
        bottom: 0,
        textStyle: { fontSize: 9, color: p.INK2 },
      },
      radar: {
        indicator: indicators,
        radius: '62%',
        center: ['50%', '44%'],
        axisName: {
          color: p.INK2,
          fontSize: 9,
        },
        splitLine: { lineStyle: { color: p.SUNKEN } },
        splitArea: { show: false },
      },
      series: [
        {
          type: 'radar',
          data: seriesData,
        },
      ],
    };
  }, [selectedExperiments, theme]);

  const radarRef = useChart(radarOption);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-xl border border-hair bg-surface p-5 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部标题 */}
        <div className="flex items-center justify-between border-b border-hair pb-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-bold text-ink">
                ⚔️ MLOps 模型多版本 Head-to-Head 深度对垒
              </span>
              <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs font-semibold text-brand-700">
                已选 {selectedExperiments.length} 个实验版本
              </span>
            </div>
            <p className="mt-0.5 text-2xs text-ink-muted">
              横向比对验证集 vs 测试集泛化力、过拟合衰减差与超参数配置
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-alt hover:text-ink text-sm"
          >
            ✕
          </button>
        </div>

        {selectedExperiments.length < 2 ? (
          <div className="py-12 text-center text-xs text-ink-muted">
            请至少勾选 2 个实验模型版本以启动对垒比对
          </div>
        ) : (
          <div className="space-y-4">
            {/* 上半部分：多维雷达图与核心诊断 */}
            <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-stretch">
              <div className="md:col-span-6 rounded-lg border border-hair bg-surface-alt p-3">
                <div className="text-2xs font-semibold text-ink mb-1 text-center">
                  核心能力雷达分布对比
                </div>
                <div ref={radarRef} className="h-60 w-full" />
              </div>

              <div className="md:col-span-6 rounded-lg border border-hair bg-surface-alt p-3 flex flex-col justify-between">
                <div>
                  <div className="text-2xs font-semibold text-ink mb-2">
                    🏆 晋升建议与对垒结论
                  </div>
                  <div className="space-y-2 text-2xs">
                    {selectedExperiments.map((exp) => {
                      const valid = exp.metrics.valid_rank_ic ?? 0;
                      const test = exp.metrics.test_rank_ic ?? 0;
                      const gap = valid - test;
                      const isChampion =
                        test ===
                        Math.max(
                          ...selectedExperiments.map((e) => e.metrics.test_rank_ic ?? 0),
                        );

                      return (
                        <div
                          key={exp.version}
                          className="rounded border border-hair bg-surface p-2 space-y-1"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-mono font-bold text-ink">
                              {exp.version}
                              {exp.is_production ? ' (当前线上)' : ''}
                            </span>
                            {isChampion && (
                              <span className="rounded bg-success-bg px-1.5 py-0.2 text-3xs font-semibold text-success">
                                ★ 样本外最强
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 text-3xs text-ink-secondary">
                            <span>
                              Test IC:{' '}
                              <b
                                className={`num ${
                                  test >= 0 ? 'text-danger' : 'text-success'
                                }`}
                              >
                                {test.toFixed(4)}
                              </b>
                            </span>
                            <span>
                              过拟合裂口 (Gap):{' '}
                              <b
                                className={`num ${
                                  gap > 0.03 ? 'text-warn' : 'text-ink-muted'
                                }`}
                              >
                                {gap.toFixed(4)}
                              </b>
                            </span>
                            <span>
                              学习率:{' '}
                              <b className="num">
                                {exp.hyperparams.learning_rate ?? '—'}
                              </b>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <p className="mt-2 text-3xs text-ink-muted">
                  💡 <b>评审准则</b>：不唯单点高 IC 论，优先选择样本外测试集 Test Rank IC 高且过拟合裂口较窄、超参未在边界过激拟合的版本。
                </p>
              </div>
            </div>

            {/* 下半部分：详细横向指标对比表 */}
            <div className="overflow-x-auto rounded-lg border border-hair">
              <table className="w-full text-2xs">
                <thead>
                  <tr className="border-b border-hair bg-surface-alt text-ink-secondary">
                    <th className="py-2 px-3 text-left font-medium">版本 / 架构</th>
                    <th className="py-2 px-2 text-center font-medium">线上状态</th>
                    <th className="py-2 px-2 text-center font-medium">特征集</th>
                    <th className="py-2 px-2 text-right font-medium">Valid RankIC</th>
                    <th className="py-2 px-2 text-right font-medium">Test RankIC (OOS)</th>
                    <th className="py-2 px-2 text-right font-medium">过拟合衰减 (Gap)</th>
                    <th className="py-2 px-2 text-right font-medium">Learning Rate</th>
                    <th className="py-2 px-2 text-right font-medium">树颗数 / 迭代</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedExperiments.map((exp) => {
                    const valid = exp.metrics.valid_rank_ic ?? 0;
                    const test = exp.metrics.test_rank_ic ?? 0;
                    const gap = valid - test;

                    return (
                      <tr
                        key={exp.version}
                        className="border-b border-hair/50 hover:bg-surface-alt/50"
                      >
                        <td className="py-2 px-3 font-mono font-medium text-ink">
                          {exp.version}
                        </td>
                        <td className="py-2 px-2 text-center">
                          {exp.is_production ? (
                            <span className="rounded bg-brand-50 px-1.5 py-0.5 text-3xs font-semibold text-brand-700">
                              Production
                            </span>
                          ) : (
                            <span className="text-3xs text-ink-muted">Archived</span>
                          )}
                        </td>
                        <td className="py-2 px-2 text-center font-mono text-ink-secondary text-3xs">
                          {exp.feature_version}
                        </td>
                        <td className="num py-2 px-2 text-right text-ink">
                          {exp.metrics.valid_rank_ic != null
                            ? Number(exp.metrics.valid_rank_ic).toFixed(4)
                            : '—'}
                        </td>
                        <td className="num py-2 px-2 text-right font-semibold text-brand-600">
                          {exp.metrics.test_rank_ic != null
                            ? Number(exp.metrics.test_rank_ic).toFixed(4)
                            : '—'}
                        </td>
                        <td
                          className={`num py-2 px-2 text-right ${
                            gap > 0.03 ? 'text-warn font-medium' : 'text-ink-muted'
                          }`}
                        >
                          {gap.toFixed(4)}
                        </td>
                        <td className="num py-2 px-2 text-right text-ink-secondary">
                          {exp.hyperparams.learning_rate ?? '—'}
                        </td>
                        <td className="num py-2 px-2 text-right text-ink-secondary">
                          {exp.hyperparams.n_estimators ??
                            exp.hyperparams.num_iterations ??
                            '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="flex justify-end pt-1">
          <button
            onClick={onClose}
            className="rounded-md border border-hair px-4 py-1.5 text-xs text-ink hover:bg-surface-alt"
          >
            关闭对比
          </button>
        </div>
      </div>
    </div>
  );
}
