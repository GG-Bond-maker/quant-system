import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StockList from './StockList';
import { screenerApi } from '@/api/screener';

vi.mock('@/api/screener', () => ({
  screenerApi: {
    stocks: vi.fn(),
  },
}));

vi.mock('@/stores/useWatchlistStore', () => ({
  DEFAULT_GROUP: 'default',
  useWatchlistStore: () => ({
    addTo: vi.fn(),
    removeFrom: vi.fn(),
    contains: () => false,
  }),
}));

describe('StockList Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders and supports changing page size dynamically', async () => {
    const mockData = {
      items: [
        {
          symbol: '000001.SZ',
          name: '平安银行',
          industry: '银行',
          board: 'main',
          close: 12.34,
          pct: 1.5,
          total_cap_yi: 2000.0,
          float_cap_yi: 1800.0,
          amount_yi: 15.2,
        },
      ],
      total: 100,
      options: {
        industries: ['银行', '非银金融'],
      },
      trade_date: '2026-10-08',
      degraded: false,
      basis_desc: '实时行情',
    };

    vi.mocked(screenerApi.stocks).mockResolvedValue(mockData as any);

    render(
      <MemoryRouter>
        <StockList board="all" />
      </MemoryRouter>
    );

    // Initial query should request default page_size: 20
    await waitFor(() => {
      expect(screenerApi.stocks).toHaveBeenCalledWith(
        expect.objectContaining({
          board: 'all',
          page: 1,
          page_size: 20,
        })
      );
    });

    // Check page size selector exists
    const pageSizeSelect = screen.getByLabelText('每页条数');
    expect(pageSizeSelect).toBeInTheDocument();
    expect((pageSizeSelect as HTMLSelectElement).value).toBe('20');

    // Switch page size to 50
    fireEvent.change(pageSizeSelect, { target: { value: '50' } });

    await waitFor(() => {
      expect(screenerApi.stocks).toHaveBeenCalledWith(
        expect.objectContaining({
          board: 'all',
          page: 1,
          page_size: 50,
        })
      );
    });
  });

  it('respects parent market prop and renders southbound stock attributes', async () => {
    const mockHkData = {
      items: [
        {
          symbol: '00700.HK',
          name: '腾讯控股',
          industry: '资讯科技',
          board: 'hk_main',
          close: 380.4,
          pct: 2.1,
          total_cap_yi: 35000.0,
          float_cap_yi: 35000.0,
          amount_yi: 85.0,
          currency: 'HKD',
          is_southbound: true,
          lot_size: 100,
          market: 'hk',
        },
      ],
      total: 40,
      options: {
        industries: ['资讯科技', '金融业'],
      },
      trade_date: '2026-10-08',
      degraded: false,
      basis_desc: '实时行情',
    };

    vi.mocked(screenerApi.stocks).mockResolvedValue(mockHkData as any);

    render(
      <MemoryRouter>
        <StockList board="all" market="southbound" />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screenerApi.stocks).toHaveBeenCalledWith(
        expect.objectContaining({
          market: 'southbound',
        })
      );
    });

    expect(screen.getByText('腾讯控股')).toBeInTheDocument();
    expect(screen.getByTitle('港股通标的')).toBeInTheDocument();
    expect(screen.getByText('HKD')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('HK$')).toBeInTheDocument();
  });
});
