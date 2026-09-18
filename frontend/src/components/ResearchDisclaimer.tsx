import type { ReactNode } from 'react';

/**
 * 面向模型选股与回测结果的统一研究参考声明。
 *
 * 所有可能影响投资决策的展示复用此组件，避免页面间口径强度不一致。
 */
export type ResearchDisclaimerKind = 'model' | 'backtest';

const DISCLAIMER_TEXT: Record<ResearchDisclaimerKind, ReactNode> = {
  model: (
    <>
      模型输出基于历史数据，预测窗口为未来 5 个交易日；仅供研究参考，不构成投资建议或收益承诺。
    </>
  ),
  backtest: (
    <>
      回测结果基于历史数据及设定参数，受数据口径和样本期影响；历史表现不代表未来收益，仅供研究参考，不构成投资建议。
    </>
  ),
};

/** 渲染统一的模型或回测研究参考声明。 */
export default function ResearchDisclaimer({
  kind,
  className = '',
}: {
  kind: ResearchDisclaimerKind;
  className?: string;
}) {
  return (
    <p className={`text-2xs leading-relaxed text-ink-muted ${className}`.trim()}>
      {DISCLAIMER_TEXT[kind]}
    </p>
  );
}
