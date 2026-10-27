import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { PredictionUncertainty } from './PredictionUncertainty';
import type { MLPredictResult } from '@/types/stock';
it('展示模型目标概率、真实期限和覆盖率区间', () => {
 render(<PredictionUncertainty prediction={{ horizon: 7, confidence: 0.63, confidence_basis: '独立样本校准', prediction_interval: { lower: -0.04, upper: 0.08, coverage_level: 0.9 }, signal_id: 'sig-7', return_basis: '超额收益' } as unknown as MLPredictResult} />);
 expect(screen.getByText('预测期限：7 个交易日')).toBeInTheDocument();
 expect(screen.getByText(/P\(模型目标 > 0\)：63%/)).toBeInTheDocument();
 expect(screen.getByText(/90% 覆盖区间：-4.00% 至 8.00%/)).toBeInTheDocument();
 expect(screen.getByText(/sig-7/)).toBeInTheDocument();
});
it('校准与区间缺失时明确不可用', () => {
 render(<PredictionUncertainty prediction={{ horizon: 5, confidence: null } as MLPredictResult} />);
 expect(screen.getByText(/校准概率不可用/)).toBeInTheDocument();
 expect(screen.getByText(/预测区间不可用/)).toBeInTheDocument();
});
