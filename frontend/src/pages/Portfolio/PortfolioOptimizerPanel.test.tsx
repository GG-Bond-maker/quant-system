import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PortfolioOptimizerPanel from './PortfolioOptimizerPanel';
import { researchApi } from '@/api/research';
import type { PortfolioAsset } from '@/types/portfolio';

// Mock researchApi
vi.mock('@/api/research', () => ({
  researchApi: {
    optimize: vi.fn(),
  },
}));

// Mock echarts & useChart
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('PortfolioOptimizerPanel', () => {
  const mockAssets: PortfolioAsset[] = [
    { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.5 },
    { code: '000001', name: '平安银行', type: 'stock', weight: 0.5 },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('初始正确渲染并支持选择优化模型与求解', async () => {
    vi.mocked(researchApi.optimize).mockResolvedValueOnce({
      method: 'risk_parity',
      symbols: ['600519.SH', '000001.SZ'],
      prev_weights: { '600519.SH': 0.5, '000001.SZ': 0.5 },
      weights: { '600519.SH': 0.38, '000001.SZ': 0.62 },
      exposure_before: { Size: 0.5, Beta: 0.8 },
      exposure_after: { Size: 0.2, Beta: 0.6 },
      style_factor_map: { Size: 'ln_market_cap', Beta: 'beta_60d' },
      n_obs: 120,
      weight_cap_info: {
        cap: 0,
        n_assets: 2,
        feasible: true,
        cap_enforced: true,
        invested_ratio: 1.0,
        max_weight: 0.62,
        max_invested_ratio: 1.0,
        min_feasible_cap: 0.5,
        note: '无单股上限约束，全额配置',
      },
    });

    render(<PortfolioOptimizerPanel currentAssets={mockAssets} />);

    expect(screen.getByText('投资组合凸优化一键求解器')).toBeInTheDocument();
    expect(screen.getByText('风险平价 (Risk Parity)')).toBeInTheDocument();

    const solveBtn = screen.getByText('一键求解最优权重');
    fireEvent.click(solveBtn);

    await waitFor(() => {
      expect(researchApi.optimize).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText('权重调仓与偏离对比')).toBeInTheDocument();
      expect(screen.getByText('600519.SH')).toBeInTheDocument();
      expect(screen.getByText('38.00%')).toBeInTheDocument();
      expect(screen.getByText('62.00%')).toBeInTheDocument();
    });
  });

  it('点击“应用此最优权重到左侧组合回测”能正确触发回调', async () => {
    const onApply = vi.fn();
    vi.mocked(researchApi.optimize).mockResolvedValueOnce({
      method: 'max_div',
      symbols: ['600519.SH', '000001.SZ'],
      prev_weights: { '600519.SH': 0.5, '000001.SZ': 0.5 },
      weights: { '600519.SH': 0.45, '000001.SZ': 0.55 },
      exposure_before: { Size: 0.5 },
      exposure_after: { Size: 0.4 },
      style_factor_map: { Size: 'ln_market_cap' },
      n_obs: 120,
    });

    render(
      <PortfolioOptimizerPanel
        currentAssets={mockAssets}
        onApplyWeights={onApply}
      />,
    );

    const solveBtn = screen.getByText('一键求解最优权重');
    fireEvent.click(solveBtn);

    await waitFor(() => {
      expect(screen.getByText(/应用此最优权重到左侧组合回测/)).toBeInTheDocument();
    });

    const applyBtn = screen.getByText(/应用此最优权重到左侧组合回测/);
    fireEvent.click(applyBtn);

    expect(onApply).toHaveBeenCalledWith([
      { code: '600519', name: '贵州茅台', type: 'stock', weight: 0.45 },
      { code: '000001', name: '平安银行', type: 'stock', weight: 0.55 },
    ]);
  });
});
