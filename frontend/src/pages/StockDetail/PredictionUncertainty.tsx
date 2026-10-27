import type { MLPredictResult } from '@/types/stock';

export function PredictionUncertainty({ prediction }: { prediction: MLPredictResult }) {
  const interval = prediction.prediction_interval;
  const hasInterval = interval?.lower != null && interval.upper != null && interval.coverage_level != null;
  return <div className="mt-2 space-y-1 text-2xs text-ink-secondary">
    <p>预测期限：{prediction.horizon} 个交易日</p>
    <p title={prediction.confidence_basis}>P(模型目标 &gt; 0)：{prediction.confidence == null ? '校准概率不可用' : `${Math.round(prediction.confidence * 100)}%`}</p>
    <p>{hasInterval ? `${Math.round(interval.coverage_level! * 100)}% 覆盖区间：${(interval.lower! * 100).toFixed(2)}% 至 ${(interval.upper! * 100).toFixed(2)}%` : '预测区间不可用'}</p>
    <p>目标口径：{prediction.return_basis || '模型训练目标'}；该概率对应模型目标大于零。</p>
    {prediction.signal_id && <p className="break-all">信号：{prediction.signal_id} · {prediction.run_id}</p>}
  </div>;
}
