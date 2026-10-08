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

export const FACTOR_WEIGHT_PRESETS: Array<{
  name: string;
  desc: string;
  weights: FactorWeights;
}> = [
  { name: '默认推荐', desc: '经典多因子配比', weights: DEFAULT_FACTOR_WEIGHTS },
  { name: 'Max-ICIR', desc: '信息比率与正交加权', weights: { predict: 40, quality: 30, momentum: 15, turnover: 15 } },
  { name: '动量进攻', desc: '趋势与量能主升', weights: { predict: 20, momentum: 50, turnover: 20, quality: 10 } },
  { name: '稳健低波', desc: '高质地防御抗跌', weights: { predict: 20, quality: 60, momentum: 10, turnover: 10 } },
  { name: '等权基准', desc: '四因子等权', weights: { predict: 25, momentum: 25, turnover: 25, quality: 25 } },
];

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

  const isPresetActive = (pw: FactorWeights) =>
    pw.predict === weights.predict &&
    pw.momentum === weights.momentum &&
    pw.turnover === weights.turnover &&
    pw.quality === weights.quality;

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
        <div className="mt-2.5 space-y-2 border-t border-hair pt-2">
          {/* 预设策略快捷工具栏 */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-2xs text-ink-muted">优化预设:</span>
            {FACTOR_WEIGHT_PRESETS.map((p) => {
              const active = isPresetActive(p.weights);
              return (
                <button
                  key={p.name}
                  type="button"
                  title={p.desc}
                  onClick={() => onChange(p.weights)}
                  className={`rounded px-2 py-0.5 text-2xs transition-colors ${
                    active
                      ? 'bg-brand-500 text-white font-medium'
                      : 'border border-hair bg-surface-alt text-ink-secondary hover:text-ink hover:bg-slate-100'
                  }`}
                >
                  {p.name}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
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
        </div>
      )}
    </div>
  );
}
