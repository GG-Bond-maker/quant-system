import { useState } from 'react';

export interface FactorWeights {
  predict: number;   // 预测强度
  momentum: number;  // 动量趋势
  turnover: number;  // 换手活跃
  quality: number;   // 质地/波动
}

export const DEFAULT_FACTOR_WEIGHTS: FactorWeights = {
  predict: 35,
  momentum: 25,
  turnover: 20,
  quality: 20,
};

interface Props {
  weights: FactorWeights;
  onChange: (weights: FactorWeights) => void;
  enabled: boolean;
  onToggleEnabled: (enabled: boolean) => void;
}

export default function FactorWeightToolbar({
  weights,
  onChange,
  enabled,
  onToggleEnabled,
}: Props) {
  const [collapsed, setCollapsed] = useState(false);

  const updateWeight = (key: keyof FactorWeights, val: number) => {
    onChange({ ...weights, [key]: val });
  };

  const total = weights.predict + weights.momentum + weights.turnover + weights.quality;

  return (
    <div className="rounded-lg border border-hair bg-surface p-2.5 text-xs transition-all">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium text-ink">多因子打分卡与动态加权</span>
          <button
            type="button"
            onClick={() => onToggleEnabled(!enabled)}
            className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
              enabled
                ? 'bg-brand-500 text-white'
                : 'border border-hair bg-surface-alt text-ink-muted hover:text-ink'
            }`}
          >
            {enabled ? '加权排序生效中' : '未启用加权'}
          </button>
        </div>
        <div className="flex items-center gap-2 text-2xs">
          <span className="text-ink-muted">权重总和: {total}</span>
          <button
            type="button"
            onClick={() => onChange(DEFAULT_FACTOR_WEIGHTS)}
            className="text-brand-500 hover:text-brand-600"
          >
            重置
          </button>
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            className="text-ink-muted hover:text-ink"
          >
            {collapsed ? '展开 ▼' : '收起 ▲'}
          </button>
        </div>
      </div>

      {!collapsed && enabled && (
        <div className="mt-2.5 grid grid-cols-2 gap-3 border-t border-hair pt-2 sm:grid-cols-4">
          <div className="space-y-1">
            <div className="flex justify-between text-2xs text-ink-secondary">
              <span>模型预测 (Alpha)</span>
              <span className="font-mono">{weights.predict}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={weights.predict}
              onChange={(e) => updateWeight('predict', Number(e.target.value))}
              className="w-full accent-brand-500 cursor-pointer"
            />
          </div>

          <div className="space-y-1">
            <div className="flex justify-between text-2xs text-ink-secondary">
              <span>动量趋势 (Momentum)</span>
              <span className="font-mono">{weights.momentum}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={weights.momentum}
              onChange={(e) => updateWeight('momentum', Number(e.target.value))}
              className="w-full accent-brand-500 cursor-pointer"
            />
          </div>

          <div className="space-y-1">
            <div className="flex justify-between text-2xs text-ink-secondary">
              <span>换手流动性 (Turnover)</span>
              <span className="font-mono">{weights.turnover}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={weights.turnover}
              onChange={(e) => updateWeight('turnover', Number(e.target.value))}
              className="w-full accent-brand-500 cursor-pointer"
            />
          </div>

          <div className="space-y-1">
            <div className="flex justify-between text-2xs text-ink-secondary">
              <span>低波/质量 (Quality)</span>
              <span className="font-mono">{weights.quality}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={weights.quality}
              onChange={(e) => updateWeight('quality', Number(e.target.value))}
              className="w-full accent-brand-500 cursor-pointer"
            />
          </div>
        </div>
      )}
    </div>
  );
}
