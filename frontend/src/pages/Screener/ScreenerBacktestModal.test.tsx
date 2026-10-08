import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import ScreenerBacktestModal from './ScreenerBacktestModal';
import { portfolioApi } from '@/api/portfolio';
import type { PortfolioBacktestResult } from '@/types/portfolio';

vi.mock('@/api/portfolio', () => ({
  portfolioApi: {
    backtest: vi.fn(),
  },
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
  graphic: {
    LinearGradient: vi.fn(),
  },
}));

const MOCK_BACKTEST_RESULT: PortfolioBacktestResult = {
  status: 'ok',
  start_date: '2025-09-30',
  end_date: '2026-09-30',
  initial_cash: 1_000_000,
  rebalance: 'M',
  benchmark: '000300.SH',
  assets: [
    { code: '600519.SH', type: 'stock', weight: 0.5 },
    { code: '000858.SZ', type: 'stock', weight: 0.5 },
  ],
  trading_days: 242,
  metrics: {
    total_return: 0.185,
    cagr: 0.185,
    max_drawdown: 0.082,
    volatility: 0.15,
    sharpe: 1.25,
    calmar: 2.25,
    alpha: 0.065,
    beta: 0.82,
    risk_free: 0.02,
    sortino: 1.6,
  },
  nav_curve: [
    { date: '2025-09-30', nav: 1.0, benchmark: 1.0 },
    { date: '2026-09-30', nav: 1.185, benchmark: 1.05 },
  ],
  drawdown_curve: [
    { date: '2025-09-30', drawdown: 0 },
    { date: '2026-09-30', drawdown: -0.05 },
  ],
  annual_returns: [],
  holdings_drift: [],
  price_basis: {
    kind: 'qfq',
    basis: 'qfq',
    raw_fallback_symbols: [],
    note: 'ok',
  },
};

describe('ScreenerBacktestModal 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('成功调用 portfolioApi.backtest 并渲染真实组合回测绩效指标', async () => {
    vi.mocked(portfolioApi.backtest).mockResolvedValue(MOCK_BACKTEST_RESULT);

    render(
      <ScreenerBacktestModal
        open={true}
        onClose={vi.fn()}
        symbols={['600519.SH', '000858.SZ']}
        asOfDate="2026-09-30"
      />,
    );

    expect(screen.getByText('选股组合截面调仓历史回测')).toBeDefined();
    expect(screen.getByText('600519.SH')).toBeDefined();
    expect(screen.getByText('000858.SZ')).toBeDefined();

    // 点击执行回测
    fireEvent.click(screen.getByText('执行组合回测'));

    await waitFor(() => {
      expect(portfolioApi.backtest).toHaveBeenCalledTimes(1);
      expect(screen.getAllByText('+18.50%').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('1.25')).toBeDefined();
      expect(screen.getByText('+8.20%')).toBeDefined();
    });
  });
});
