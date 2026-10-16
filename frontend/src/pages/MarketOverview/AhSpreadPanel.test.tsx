import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AhSpreadPanel from './AhSpreadPanel';
import type { AhSpreadResult } from '@/types/stock';

const MOCK_DATA: AhSpreadResult = {
  total: 3,
  hkd_cny_fx: 0.915,
  as_of: '2026-10-09 14:30:00',
  source: 'tencent',
  summary: {
    total_pairs: 3,
    valid_pairs: 3,
    avg_premium_rate: 65.5,
    max_premium_stock: '浙江世宝',
    min_premium_stock: '招商银行',
  },
  items: [
    {
      name: '浙江世宝',
      industry: '汽车零部件',
      a_symbol: '002703.SZ',
      h_symbol: '01057.HK',
      a_price: 13.5,
      h_price: 3.5,
      h_price_cny: 3.20,
      a_pct: 2.5,
      h_pct: -1.2,
      a_amount_yi: 4.5,
      h_amount_yi: 0.8,
      ah_premium_rate: 321.88,
      ha_discount_rate: -76.30,
      ah_ratio: 4.22,
      arbitrage_signal: 'a_extreme_premium',
      signal_desc: 'A股极度溢价(>100%)',
    },
    {
      name: '中信证券',
      industry: '非银金融',
      a_symbol: '600030.SH',
      h_symbol: '06030.HK',
      a_price: 28.5,
      h_price: 22.0,
      h_price_cny: 20.13,
      a_pct: 1.1,
      h_pct: 0.5,
      a_amount_yi: 35.2,
      h_amount_yi: 8.5,
      ah_premium_rate: 41.58,
      ha_discount_rate: -29.37,
      ah_ratio: 1.42,
      arbitrage_signal: 'a_moderate_premium',
      signal_desc: 'A股适度溢价(30%~100%)',
    },
    {
      name: '招商银行',
      industry: '银行',
      a_symbol: '600036.SH',
      h_symbol: '03968.HK',
      a_price: 36.0,
      h_price: 38.0,
      h_price_cny: 34.77,
      a_pct: -0.2,
      h_pct: 0.8,
      a_amount_yi: 20.1,
      h_amount_yi: 12.3,
      ah_premium_rate: 3.54,
      ha_discount_rate: -3.42,
      ah_ratio: 1.04,
      arbitrage_signal: 'ah_near_parity',
      signal_desc: 'AH接近平价(-5%~30%)',
    },
  ],
};

describe('AhSpreadPanel Component', () => {
  it('renders AH spread summary and data table with correct metrics', () => {
    render(
      <MemoryRouter>
        <AhSpreadPanel data={MOCK_DATA} loading={false} />
      </MemoryRouter>
    );

    // Title and Southbound badge
    expect(screen.getByText('AH 股溢价与跨市场套利看板')).toBeInTheDocument();
    expect(screen.getByText('港股通对标')).toBeInTheDocument();
    expect(screen.getByText(/汇率参考: 1 HKD = 0.9150 CNY/)).toBeInTheDocument();

    // Summary KPI metrics
    expect(screen.getByText('+65.5%')).toBeInTheDocument();
    expect(screen.getAllByText('浙江世宝').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('招商银行').length).toBeGreaterThanOrEqual(1);

    // Table rows
    expect(screen.getByText('中信证券')).toBeInTheDocument();
    expect(screen.getByText('A股极度溢价(>100%)')).toBeInTheDocument();
    expect(screen.getByText('A股适度溢价(30%~100%)')).toBeInTheDocument();
    expect(screen.getByText('AH接近平价(-5%~30%)')).toBeInTheDocument();

    // Prices and currencies
    expect(screen.getByText('¥13.50')).toBeInTheDocument();
    expect(screen.getByText('HK$3.50')).toBeInTheDocument();
  });

  it('filters rows by arbitrage signal correctly', () => {
    render(
      <MemoryRouter>
        <AhSpreadPanel data={MOCK_DATA} loading={false} />
      </MemoryRouter>
    );

    // Click "极度溢价 (>100%)" filter button
    const extremeBtn = screen.getByRole('button', { name: /极度溢价/ });
    fireEvent.click(extremeBtn);

    const tbody = screen.getByTestId('ah-spread-tbody');
    // Only 浙江世宝 should remain in table
    expect(within(tbody).getByText('浙江世宝')).toBeInTheDocument();
    expect(within(tbody).queryByText('中信证券')).not.toBeInTheDocument();
    expect(within(tbody).queryByText('招商银行')).not.toBeInTheDocument();
  });

  it('filters rows by keyword search', () => {
    render(
      <MemoryRouter>
        <AhSpreadPanel data={MOCK_DATA} loading={false} />
      </MemoryRouter>
    );

    const searchInput = screen.getByPlaceholderText('搜索标的 / 代码 / 行业');
    fireEvent.change(searchInput, { target: { value: '银行' } });

    const tbody = screen.getByTestId('ah-spread-tbody');
    expect(within(tbody).getByText('招商银行')).toBeInTheDocument();
    expect(within(tbody).queryByText('浙江世宝')).not.toBeInTheDocument();
    expect(within(tbody).queryByText('中信证券')).not.toBeInTheDocument();
  });

  it('displays degraded warning when source is local_cache', () => {
    const degradedData: AhSpreadResult = {
      ...MOCK_DATA,
      source: 'local_cache',
    };

    render(
      <MemoryRouter>
        <AhSpreadPanel data={degradedData} loading={false} />
      </MemoryRouter>
    );

    expect(screen.getByText(/实时外盘行情源不可达，已如实回退本地截面数据/)).toBeInTheDocument();
  });

  it('triggers onRefresh callback when refresh button clicked', () => {
    const onRefresh = vi.fn();
    render(
      <MemoryRouter>
        <AhSpreadPanel data={MOCK_DATA} loading={false} onRefresh={onRefresh} />
      </MemoryRouter>
    );

    const refreshBtn = screen.getByRole('button', { name: '刷新' });
    fireEvent.click(refreshBtn);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
