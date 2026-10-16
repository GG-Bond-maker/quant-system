import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Screener from './index';
import { screenerApi } from '@/api/screener';
import { marketApi } from '@/api/market';

vi.mock('@/api/screener', () => ({
  screenerApi: {
    screen: vi.fn(),
    statsSeries: vi.fn(),
    watchlist: vi.fn(),
    stocks: vi.fn(),
    quintileSpreads: vi.fn(),
  },
}));

vi.mock('@/api/market', () => ({
  marketApi: {
    overviewDaily: vi.fn(),
    indexKline: vi.fn(),
  },
}));

vi.mock('@/api/export', () => ({
  exportApi: {
    screener: vi.fn(),
  },
}));

vi.mock('@/stores/useWatchlistStore', () => ({
  DEFAULT_GROUP: 'default',
  useWatchlistStore: () => ({
    addTo: vi.fn(),
    removeFrom: vi.fn(),
    contains: () => false,
    groups: { default: [] },
  }),
}));

vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: () => ({
    user: { role: 'researcher' },
  }),
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('Screener Market Scope Linkage', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(marketApi.overviewDaily).mockResolvedValue({
      trade_date: '20261008',
      as_of: '2026-10-08 15:00:00',
    } as any);

    vi.mocked(marketApi.indexKline).mockResolvedValue({
      code: 'sh000001',
      name: '上证指数',
      bars: [{ date: '2026-10-08', close: 3200 }],
    } as any);

    vi.mocked(screenerApi.quintileSpreads).mockResolvedValue({
      points: [],
      monotonicity_score: 0.8,
      q1_accumulated: 0.05,
      q5_accumulated: -0.03,
      spread_accumulated: 0.08,
    } as any);

    vi.mocked(screenerApi.screen).mockResolvedValue({
      date: '2026-10-08',
      as_of: '2026-10-08',
      status: 'ok',
      strategy: 'alpha_basic_v1',
      top_k: 20,
      board: 'all',
      count: 1,
      items: [
        {
          symbol: '600519.SH',
          name: '贵州茅台',
          industry: '白酒',
          close: 1750.0,
          pct: 1.2,
          score: 0.0085,
          turnover: 1.8,
          signal_strength: 'strong',
        },
      ],
      stats: {
        today: {
          total: 1,
          pool_size: 5200,
          win_rate: 1.0,
          avg_pct: 1.2,
          avg_score: 0.0085,
          strong_signal: 1,
          industry_count: 1,
        },
      },
      coverage: { available: 1, total: 1, ratio: 1.0 },
    } as any);

    vi.mocked(screenerApi.statsSeries).mockResolvedValue({
      drawable: ['avg_score'],
      metrics: {},
    } as any);

    vi.mocked(screenerApi.stocks).mockResolvedValue({
      items: [],
      total: 0,
      options: { industries: [] },
    } as any);
  });

  it('renders top market scope toggle and switches between A-share and HK Southbound', async () => {
    render(
      <MemoryRouter>
        <Screener />
      </MemoryRouter>
    );

    // Initial load: calls screener with market="cn"
    await waitFor(() => {
      expect(screenerApi.screen).toHaveBeenCalledWith(
        expect.objectContaining({
          market: 'cn',
          board: 'all',
        }),
        false
      );
    });

    // Check header toggle buttons
    const cnBtn = screen.getByRole('button', { name: /A股市场/i });
    const hkBtn = screen.getByRole('button', { name: /港股通/i });
    expect(cnBtn).toBeInTheDocument();
    expect(hkBtn).toBeInTheDocument();

    // Verify A-share boards are present
    expect(screen.getByRole('button', { name: '主板' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创业/科创' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '北交所' })).toBeInTheDocument();

    // Mock HK response
    vi.mocked(screenerApi.screen).mockResolvedValueOnce({
      date: '2026-10-08',
      as_of: '2026-10-08',
      status: 'ok',
      strategy: 'hk_ah_arbitrage',
      top_k: 20,
      board: 'all',
      count: 1,
      items: [
        {
          symbol: '00700.HK',
          name: '腾讯控股',
          industry: '资讯科技',
          close: 380.0,
          pct: 2.5,
          score: 0.35,
          turnover: 3.2,
          signal_strength: 'strong',
          is_southbound: true,
          currency: 'HKD',
          lot_size: 100,
        },
      ],
      stats: {
        today: {
          total: 1,
          pool_size: 40,
          win_rate: 1.0,
          avg_pct: 2.5,
          avg_score: 0.35,
          strong_signal: 1,
          industry_count: 1,
        },
      },
      coverage: { available: 1, total: 1, ratio: 1.0 },
    } as any);

    // Click to switch to HK Southbound
    fireEvent.click(hkBtn);

    // Wait for screen call with market="southbound"
    await waitFor(() => {
      expect(screenerApi.screen).toHaveBeenCalledWith(
        expect.objectContaining({
          market: 'southbound',
          board: 'all',
        }),
        false
      );
    });

    // Verify HK boards are now displayed
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '恒生科技' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '港股主板' })).toBeInTheDocument();
    });

    // Verify HK scope subtitle
    expect(screen.getByText(/AH 折价套利 \/ 港股通动量量化/i)).toBeInTheDocument();
  });
});
