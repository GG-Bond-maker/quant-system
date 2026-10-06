import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import EfficientFrontierCard from './EfficientFrontierCard';
import { portfolioApi } from '@/api/portfolio';

vi.mock('@/api/portfolio', () => ({
  portfolioApi: {
    efficientFrontier: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('EfficientFrontierCard', () => {
  const dummyAssets = [
    { code: '600519', weight: 0.5 },
    { code: '000001', weight: 0.5 },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('挂载时正常调用 efficientFrontier 接口并展示最优组合指标', async () => {
    vi.mocked(portfolioApi.efficientFrontier).mockResolvedValueOnce({
      frontier: [
        { vol: 0.15, ret: 0.12, sharpe: 0.67, weights: { '600519': 0.6, '000001': 0.4 } },
        { vol: 0.20, ret: 0.22, sharpe: 1.00, weights: { '600519': 0.7, '000001': 0.3 } },
      ],
      assets: [
        { symbol: '600519', vol: 0.22, ret: 0.25, sharpe: 1.05 },
        { symbol: '000001', vol: 0.18, ret: 0.10, sharpe: 0.44 },
      ],
      min_vol: { vol: 0.15, ret: 0.12, sharpe: 0.67, weights: { '600519': 0.6, '000001': 0.4 } },
      max_sharpe: { vol: 0.20, ret: 0.22, sharpe: 1.00, weights: { '600519': 0.7, '000001': 0.3 } },
      current: { vol: 0.19, ret: 0.17, sharpe: 0.79, weights: { '600519': 0.5, '000001': 0.5 } },
      risk_free_rate: 0.02,
    });

    render(<EfficientFrontierCard assets={dummyAssets} />);

    expect(screen.getByText('马科维茨有效前沿与资本资产定价 (Markowitz MPT)')).toBeInTheDocument();

    await waitFor(() => {
      expect(portfolioApi.efficientFrontier).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText('🎯 最大夏普切线组合 (Max Sharpe)')).toBeInTheDocument();
      expect(screen.getByText('🛡️ 全局最小方差组合 (Min Vol)')).toBeInTheDocument();
      expect(screen.getByText('1.00')).toBeInTheDocument();
    });
  });

  it('点击应用切线最优权重按钮触发 onApplyWeights 回调', async () => {
    const onApplyWeights = vi.fn();
    vi.mocked(portfolioApi.efficientFrontier).mockResolvedValueOnce({
      frontier: [],
      assets: [],
      min_vol: null,
      max_sharpe: { vol: 0.20, ret: 0.22, sharpe: 1.00, weights: { '600519': 0.7, '000001': 0.3 } },
      current: null,
      risk_free_rate: 0.02,
    });

    render(<EfficientFrontierCard assets={dummyAssets} onApplyWeights={onApplyWeights} />);

    await waitFor(() => {
      expect(screen.getByText('应用切线最优权重')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('应用切线最优权重'));

    expect(onApplyWeights).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ code: '600519', weight: 0.7 }),
        expect.objectContaining({ code: '000001', weight: 0.3 }),
      ]),
    );
  });
});
