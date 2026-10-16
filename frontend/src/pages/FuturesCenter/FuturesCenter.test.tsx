import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FuturesCenter from './index';
import { futuresApi } from '@/api/futures';

vi.mock('@/api/futures', () => ({
  futuresApi: {
    contracts: vi.fn(),
    basis: vi.fn(),
    termStructure: vi.fn(),
    calendarSpread: vi.fn(),
    kline: vi.fn(),
    hedgeCalc: vi.fn(),
    cot: vi.fn(),
    interCommodity: vi.fn(),
    rollYieldMatrix: vi.fn(),
    basisHistory: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('FuturesCenter Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(futuresApi.calendarSpread).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指',
      near_contract: 'IF当月',
      far_contract: 'IF当季',
      count: 2,
      mean_spread: 12.0,
      std_spread: 4.5,
      current_spread: 10.5,
      current_z_score: -0.33,
      arbitrage_signal: 'neutral',
      series: [
        { date: '2026-10-08', near_price: 3950, far_price: 3940, spread: 10, spread_pct: 0.25, z_score: -0.4 },
        { date: '2026-10-09', near_price: 3955, far_price: 3944.5, spread: 10.5, spread_pct: 0.26, z_score: -0.33 },
      ],
    });

    vi.mocked(futuresApi.contracts).mockResolvedValue({
      as_of: '2026-10-09 15:00:00',
      degraded: false,
      count: 2,
      items: [
        {
          symbol: 'IF主力',
          prefix: 'IF',
          contract_code: 'IF2610',
          name: '沪深300股指期货',
          exchange: 'CFFEX',
          category: 'index',
          multiplier: 300,
          tick_size: 0.2,
          margin_rate: 0.12,
          margin_per_lot: 142200,
          close: 3950.0,
          change_pct: 0.45,
          volume: 125000,
          open_interest: 218000,
          spot_price: 3962.0,
          basis: 12.0,
          basis_rate: 0.3,
          annualized_basis_rate: 7.3,
          days_to_expiry: 7,
          basis_percentile: 88,
          percentile_regime: 'extreme_backwardation',
        },
        {
          symbol: 'AU主力',
          prefix: 'AU',
          contract_code: 'AU2612',
          name: '沪金主力',
          exchange: 'SHFE',
          category: 'precious',
          multiplier: 1000,
          tick_size: 0.02,
          margin_rate: 0.1,
          margin_per_lot: 68050,
          close: 680.5,
          change_pct: 1.12,
          volume: 245000,
          open_interest: 182000,
          spot_price: 682.0,
          basis: 1.5,
          basis_rate: 0.22,
          annualized_basis_rate: 5.35,
          days_to_expiry: 65,
          basis_percentile: 45,
          percentile_regime: 'neutral',
        },
      ],
    } as any);

    vi.mocked(futuresApi.termStructure).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      spot_price: 3962.0,
      structure_type: 'Backwardation (反向/贴水)',
      contracts: [
        {
          label: '当月合约',
          contract_code: 'IF2610',
          days_to_expiry: 7,
          price: 3950.0,
          spot_price: 3962.0,
          basis: 12.0,
          basis_rate: 0.3,
          annualized_basis_rate: 7.3,
        },
      ],
    } as any);

    vi.mocked(futuresApi.hedgeCalc).mockResolvedValue({
      symbol: 'IF',
      name: '沪深300股指期货',
      portfolio_value: 10000000,
      futures_price: 3950,
      multiplier: 300,
      contract_value_per_lot: 1185000,
      margin_rate: 0.12,
      beta: 1.0,
      required_contracts: 8,
      total_notional_value: 9480000,
      total_margin_required: 1137600,
      hedge_ratio_pct: 94.8,
    } as any);

    vi.mocked(futuresApi.cot).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      as_of: '2026-10-09',
      count: 2,
      records: [
        {
          rank: 1,
          member_name: '中信期货',
          long_volume: 35000,
          long_change: 1200,
          short_volume: 31000,
          short_change: -800,
          net_position: 4000,
          net_change: 2000,
        },
      ],
      summary: {
        total_long: 35000,
        total_short: 31000,
        long_change_total: 1200,
        short_change_total: -800,
        net_position_total: 4000,
        long_short_ratio: 1.129,
        top_bull_member: '中信期货',
        top_bear_member: '国泰君安',
      },
    } as any);

    vi.mocked(futuresApi.rollYieldMatrix).mockResolvedValue({
      as_of: '2026-10-09',
      total_count: 2,
      top_backwardation: [
        {
          prefix: 'IF',
          name: '沪深300股指',
          exchange: 'CFFEX',
          category: 'index',
          near_price: 3950,
          far_price: 3930,
          near_dte: 7,
          far_dte: 97,
          roll_yield_pct: 8.2,
          structure_type: 'Backwardation (贴水)',
          action_hint: '做多吃贴水',
        },
      ],
      top_contango: [],
      all_items: [],
    } as any);

    vi.mocked(futuresApi.interCommodity).mockResolvedValue({
      pair_key: 'AU_AG',
      name: '金银比 (AU / AG)',
      asset1_name: '沪金主力',
      asset2_name: '沪银主力',
      type: 'ratio',
      current_value: 85.5,
      mean_value: 84.0,
      std_value: 1.2,
      current_z_score: 1.25,
      arbitrage_signal: 'neutral',
      half_life_stats: {
        half_life_days: 12.5,
        is_stationary: true,
        lambda_param: -0.055,
        reversion_speed: 'moderate',
      },
      count: 2,
      series: [],
    } as any);

    vi.mocked(futuresApi.basisHistory).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      current_basis: 12.0,
      percentile_stats: {
        percentile: 88,
        regime: 'extreme_backwardation',
        historical_min: -15,
        historical_max: 35,
        historical_mean: 8,
      },
      count: 2,
      series: [],
    } as any);
  });

  it('renders contracts overview and statistics correctly', async () => {
    render(
      <MemoryRouter>
        <FuturesCenter />
      </MemoryRouter>
    );

    expect(screen.getByText(/期货中心/)).toBeInTheDocument();

    await waitFor(() => {
      expect(futuresApi.contracts).toHaveBeenCalled();
      expect(futuresApi.termStructure).toHaveBeenCalledWith('IF');
    });

    expect(screen.getByText('沪深300股指期货')).toBeInTheDocument();
    expect(screen.getByText('沪金主力')).toBeInTheDocument();
    expect(screen.getByText('Backwardation (反向/贴水)')).toBeInTheDocument();
  });

  it('provides clickable symbol links to navigate to Futures Analysis', async () => {
    render(
      <MemoryRouter>
        <FuturesCenter />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('沪深300股指期货')).toBeInTheDocument();
    });

    // 检查品种代码链接
    const ifLinks = screen.getAllByRole('link').filter((l) => l.getAttribute('href') === '/futures/analysis?symbol=IF');
    expect(ifLinks.length).toBeGreaterThan(0);

    const auLink = screen.getAllByRole('link').find((l) => l.getAttribute('href') === '/futures/analysis?symbol=AU');
    expect(auLink).toBeDefined();
  });

  it('supports row selection master-detail sync and switching detail tabs', async () => {
    render(
      <MemoryRouter>
        <FuturesCenter />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('沪金主力')).toBeInTheDocument();
    });

    // 点击“历史分位” Tab
    const basisTabBtn = screen.getByText('📉 历史分位');
    fireEvent.click(basisTabBtn);

    await waitFor(() => {
      expect(futuresApi.basisHistory).toHaveBeenCalled();
    });
  });

  it('supports opening and using hedge calculator modal', async () => {
    render(
      <MemoryRouter>
        <FuturesCenter />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('沪深300股指期货')).toBeInTheDocument();
    });

    const hedgeBtn = screen.getByText('🛡️ 组合套保与对冲测算');
    fireEvent.click(hedgeBtn);

    await waitFor(() => {
      expect(screen.getByText('股指期货对冲与套期保值计算器')).toBeInTheDocument();
      expect(futuresApi.hedgeCalc).toHaveBeenCalled();
    });

    expect(screen.getByText(/建议做空合约手数/)).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
  });
});
