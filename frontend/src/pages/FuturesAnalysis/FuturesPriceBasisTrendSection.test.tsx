import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FuturesPriceBasisTrendSection } from './FuturesPriceBasisTrendSection';
import { futuresApi } from '@/api/futures';
import type { FuturesKlineWithIndicator } from '@/types/futures';

vi.mock('@/api/futures', () => ({
  futuresApi: {
    basisHistory: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

const bars: FuturesKlineWithIndicator[] = [
  {
    date: '2026-01-01',
    open: 3950,
    high: 3960,
    low: 3940,
    close: 3954,
    volume: 1000,
    open_interest: 2000,
    ma5: 3950,
    ma20: 3940,
  },
  {
    date: '2026-01-02',
    open: 3954,
    high: 3965,
    low: 3945,
    close: 3950,
    volume: 1100,
    open_interest: 2100,
    ma5: 3952,
    ma20: 3942,
  },
];

describe('FuturesPriceBasisTrendSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the price line and real basis trend together', async () => {
    vi.mocked(futuresApi.basisHistory).mockResolvedValue({
      available: true,
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      current_basis: 12,
      percentile_stats: {
        percentile: 65,
        regime: 'moderate_backwardation',
        historical_min: 8,
        historical_max: 12,
        historical_mean: 10,
      },
      series: [
        {
          date: '2026-01-01',
          spot_price: 3962,
          futures_price: 3954,
          basis: 8,
          basis_rate: 0.2019,
          annualized_basis_rate: null,
        },
        {
          date: '2026-01-02',
          spot_price: 3962,
          futures_price: 3950,
          basis: 12,
          basis_rate: 0.3029,
          annualized_basis_rate: null,
        },
      ],
    });

    render(
      <FuturesPriceBasisTrendSection
        symbol="IF"
        name="沪深300股指期货"
        bars={bars}
        activeRange={250}
      />
    );

    expect(screen.getByText('主力收盘价折线')).toBeInTheDocument();
    expect(screen.getByTestId('futures-price-line-chart')).toBeInTheDocument();
    expect(screen.getByText('区间 250 交易日')).toBeInTheDocument();

    await waitFor(() => {
      expect(futuresApi.basisHistory).toHaveBeenCalledWith('IF', 250);
      expect(screen.getByTestId('futures-basis-trend-chart')).toBeInTheDocument();
    });
    expect(screen.getByText('当前基差')).toBeInTheDocument();
    expect(screen.getByText('65%')).toBeInTheDocument();
    expect(screen.getByText('温和贴水')).toBeInTheDocument();
  });

  it('keeps the chart unavailable when the backend has no real spot series', async () => {
    vi.mocked(futuresApi.basisHistory).mockResolvedValue({
      available: false,
      reason: '缺少真实现货序列，无法计算历史基差',
    });

    render(
      <FuturesPriceBasisTrendSection
        symbol="CF"
        name="棉花"
        bars={bars}
        activeRange={60}
      />
    );

    await waitFor(() => {
      expect(futuresApi.basisHistory).toHaveBeenCalledWith('CF', 60);
      expect(screen.getByText('历史基差暂不可用')).toBeInTheDocument();
    });
    expect(screen.getByText('缺少真实现货序列，无法计算历史基差')).toBeInTheDocument();
    expect(screen.queryByTestId('futures-basis-trend-chart')).not.toBeInTheDocument();
  });

  it('requests the source full history when activeRange is zero', async () => {
    vi.mocked(futuresApi.basisHistory).mockResolvedValue({
      available: true,
      current_basis: 10,
      series: [
        {
          date: '2026-01-01',
          spot_price: 3960,
          futures_price: 3950,
          basis: 10,
          basis_rate: 0.2525,
          annualized_basis_rate: null,
        },
        {
          date: '2026-01-02',
          spot_price: 3965,
          futures_price: 3955,
          basis: 10,
          basis_rate: 0.2522,
          annualized_basis_rate: null,
        },
      ],
    });

    render(
      <FuturesPriceBasisTrendSection
        symbol="IF"
        bars={bars}
        activeRange={0}
      />
    );

    expect(screen.getByText('区间 全历史')).toBeInTheDocument();
    await waitFor(() => {
      expect(futuresApi.basisHistory).toHaveBeenCalledWith('IF', 0);
    });
  });
});
