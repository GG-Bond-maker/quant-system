import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import FactorDecayCard from './FactorDecayCard';
import { researchApi } from '@/api/research';

vi.mock('@/api/research', () => ({
  researchApi: {
    factorDecay: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('FactorDecayCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('挂载时正常调用 factorDecay 并渲染半衰期与多期 Rank IC 指标', async () => {
    vi.mocked(researchApi.factorDecay).mockResolvedValueOnce({
      factor: 'ret_5',
      neutralize_size: false,
      lags: [
        { lag: 1, mean_ic: 0.052, icir: 0.65, t_stat: 3.2, positive_ratio: 0.62, n_days: 60 },
        { lag: 2, mean_ic: 0.041, icir: 0.52, t_stat: 2.8, positive_ratio: 0.58, n_days: 60 },
        { lag: 5, mean_ic: 0.025, icir: 0.31, t_stat: 1.9, positive_ratio: 0.53, n_days: 60 },
        { lag: 10, mean_ic: 0.012, icir: 0.15, t_stat: 1.1, positive_ratio: 0.51, n_days: 60 },
        { lag: 20, mean_ic: 0.005, icir: 0.06, t_stat: 0.4, positive_ratio: 0.49, n_days: 60 },
      ],
      half_life_lag: 5,
      suggested_holding_period: '5个交易日',
    });

    render(<FactorDecayCard />);

    expect(screen.getByText('因子 IC 衰减与换手半衰期分析')).toBeInTheDocument();

    await waitFor(() => {
      expect(researchApi.factorDecay).toHaveBeenCalledWith(
        expect.objectContaining({ factor: 'ret_5', lags: [1, 2, 3, 5, 10, 15, 20], neutralize_size: false }),
        expect.anything(),
      );
    });

    await waitFor(() => {
      expect(screen.getByText('5 交易日')).toBeInTheDocument();
      expect(screen.getByText('5个交易日')).toBeInTheDocument();
      expect(screen.getAllByText('0.0520').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/T\+1/).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('勾选市值中性化后触发重新测算', async () => {
    vi.mocked(researchApi.factorDecay).mockResolvedValue({
      factor: 'ret_5',
      neutralize_size: true,
      lags: [
        { lag: 1, mean_ic: 0.045, icir: 0.58, t_stat: 2.9, positive_ratio: 0.60, n_days: 60 },
      ],
      half_life_lag: 3,
      suggested_holding_period: '3个交易日',
    });

    render(<FactorDecayCard />);

    const checkbox = screen.getByLabelText('市值中性化');
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(researchApi.factorDecay).toHaveBeenCalledWith(
        expect.objectContaining({ neutralize_size: true }),
        expect.anything(),
      );
    });
  });
});
