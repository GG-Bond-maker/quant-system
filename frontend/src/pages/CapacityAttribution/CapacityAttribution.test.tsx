import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import CapacityAttribution from './index';
import { deskApi, type AttributionResult, type CapacityCurveResult, type PortfolioBottleneckResult } from '@/api/production';
import PortfolioBottleneckCard from './PortfolioBottleneckCard';
import StockContributionCard from './StockContributionCard';
import SectorActiveTiltCard from './SectorActiveTiltCard';
import BarraRiskDecompositionCard from './BarraRiskDecompositionCard';
import CumulativeAttributionChart from './CumulativeAttributionChart';

// Mock deskApi
vi.mock('@/api/production', async () => {
  const actual = await vi.importActual<typeof import('@/api/production')>('@/api/production');
  return {
    ...actual,
    deskApi: {
      ...actual.deskApi,
      capacity: vi.fn(),
      capacityCurve: vi.fn(),
      portfolioCapacity: vi.fn(),
      attribution: vi.fn(),
      account: vi.fn(),
    },
  };
});

// Mock echarts & useChart
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('CapacityAttribution Upgraded Workbench', () => {
  const mockCapacity = {
    aum_threshold: 120000000,
    aum_yi: 1.2,
    formula: 'ADV中位数 × 参与率上限 × 持仓数 ÷ 调仓换手比例',
  };

  const mockCurve: CapacityCurveResult = {
    has_data: true,
    adv_median_yi: 1.5,
    holdings: 20,
    rebalance_per_year: 12,
    gross_return_pct: 15.0,
    gross_vol_pct: 20.0,
    optimal_aum_yi: 1.85,
    optimal_aum_wan: 18500,
    optimal_sharpe: 1.42,
    max_profit_wan: 2500,
    breakeven_aum_yi: 4.5,
    curve: [
      {
        aum_yi: 0.5,
        aum_wan: 5000,
        participation_pct: 1.0,
        impact_bps: 10.0,
        total_cost_pct: 1.2,
        net_return_pct: 13.8,
        net_sharpe: 1.6,
        alpha_profit_wan: 690,
      },
    ],
    horizon_frontiers: [
      { horizon_days: 1, desc: '1 日变现建仓', capacity_yi: 0.8 },
      { horizon_days: 3, desc: '3 日变现建仓', capacity_yi: 2.4 },
      { horizon_days: 5, desc: '5 日变现建仓', capacity_yi: 4.0 },
    ],
  };

  const mockBottleneck: PortfolioBottleneckResult = {
    has_data: true,
    target_aum_yi: 0.5,
    participation_cap_pct: 10.0,
    liquidation_days_target: 3,
    bottleneck_symbol: '300750.SZ',
    bottleneck_name: '宁德时代',
    bottleneck_capacity_yi: 0.85,
    weighted_dtl: 1.8,
    holdings_count: 2,
    holdings: [
      {
        symbol: '300750.SZ',
        name: '宁德时代',
        industry: '电力设备',
        weight: 0.5,
        position_val_wan: 2500,
        adv_wan: 8000,
        days_to_liquidate: 3.1,
        capacity_limit_yi: 0.85,
        stress_level: 'high',
      },
      {
        symbol: '000001.SZ',
        name: '平安银行',
        industry: '银行',
        weight: 0.5,
        position_val_wan: 2500,
        adv_wan: 25000,
        days_to_liquidate: 1.0,
        capacity_limit_yi: 2.5,
        stress_level: 'low',
      },
    ],
  };

  const mockAttr: AttributionResult = {
    window_days: 120,
    n_obs: 120,
    portfolio_return: 0.185,
    benchmark_return: 0.082,
    excess_return: 0.103,
    brinson: {
      sectors: [
        {
          industry: '电力设备',
          portfolio_weight: 0.5,
          benchmark_weight: 0.2,
          active_weight: 0.3,
          portfolio_return: 0.22,
          benchmark_return: 0.10,
          allocation: 0.025,
          selection: 0.040,
          interaction: 0.015,
          total: 0.080,
        },
      ],
      summary: {
        portfolio_return: 0.185,
        benchmark_return: 0.082,
        excess_return: 0.103,
        allocation: 0.035,
        selection: 0.050,
        interaction: 0.018,
        residual_alpha: 0.0,
      },
    },
    multi_period_brinson: {
      sectors: [
        {
          industry: '电力设备',
          portfolio_weight: 0.5,
          benchmark_weight: 0.2,
          active_weight: 0.3,
          allocation: 0.026,
          selection: 0.041,
          interaction: 0.016,
          total: 0.083,
        },
      ],
      summary: {
        portfolio_return: 0.185,
        benchmark_return: 0.082,
        excess_return: 0.103,
        allocation: 0.036,
        selection: 0.049,
        interaction: 0.018,
        residual_alpha: 0.0,
      },
      timeseries: [
        {
          date: '2024-01-02',
          portfolio_cum_ret: 0.01,
          benchmark_cum_ret: 0.005,
          excess_cum_ret: 0.005,
          cum_allocation: 0.002,
          cum_selection: 0.002,
          cum_interaction: 0.001,
        },
        {
          date: '2024-06-30',
          portfolio_cum_ret: 0.185,
          benchmark_cum_ret: 0.082,
          excess_cum_ret: 0.103,
          cum_allocation: 0.036,
          cum_selection: 0.049,
          cum_interaction: 0.018,
        },
      ],
    },
    stock_contributions: {
      top_contributors: [
        {
          symbol: '300750.SZ',
          name: '宁德时代',
          industry: '电力设备',
          weight: 0.5,
          cum_return: 0.25,
          pnl_contrib_pct: 12.5,
          excess_contrib_bps: 840,
        },
      ],
      bottom_drags: [
        {
          symbol: '000001.SZ',
          name: '平安银行',
          industry: '银行',
          weight: 0.5,
          cum_return: 0.12,
          pnl_contrib_pct: 6.0,
          excess_contrib_bps: 190,
        },
      ],
      all_holdings: [
        {
          symbol: '300750.SZ',
          name: '宁德时代',
          industry: '电力设备',
          weight: 0.5,
          cum_return: 0.25,
          pnl_contrib_pct: 12.5,
          excess_contrib_bps: 840,
        },
      ],
    },
    liquidity_bottleneck: mockBottleneck,
    style: {
      n_days: 120,
      betas: { Momentum: 0.85, Volatility: -0.22 },
      contributions: { Momentum: 0.045, Volatility: -0.012 },
      alpha_annualized: 0.062,
      alpha_t_stat: 2.45,
      alpha_p_value: 0.016,
      alpha_significance: '**',
      r_squared: 0.72,
      factor_stats: {
        Momentum: {
          beta: 0.85,
          std_error: 0.12,
          t_stat: 7.08,
          p_value: 0.0001,
          significance: '***',
          annualized_contrib: 0.045,
        },
      },
      risk_decomposition: {
        tracking_error_pct: 8.5,
        factor_risk_pct: 68.0,
        specific_risk_pct: 32.0,
        annual_factor_vol_pct: 7.0,
        annual_specific_vol_pct: 4.8,
      },
    },
    benchmark_desc: '本地 universe 等权',
    benchmark_policy: {
      type: 'universe_equal',
      weight_basis: 'equal_weight_all_available_universe_symbols',
      symbol_count: 5208,
      as_of: '2024-06-30',
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(deskApi.capacity).mockResolvedValue(mockCapacity);
    vi.mocked(deskApi.capacityCurve).mockResolvedValue(mockCurve);
    vi.mocked(deskApi.portfolioCapacity).mockResolvedValue(mockBottleneck);
    vi.mocked(deskApi.attribution).mockResolvedValue(mockAttr);
    vi.mocked(deskApi.account).mockResolvedValue({
      initial_cash: 1000000,
      cash: 200000,
      equity: 1000000,
      market_value: 800000,
      total_fees: 100,
      total_impact_cost: 50,
      n_fills: 2,
      max_drawdown: 0.05,
      sharpe: 1.5,
      annualized_return: 0.15,
      n_active_days: 30,
      nav_series: [],
      integrity_warnings: [],
      positions: {
        '600519.SH': { qty: 200, cost_price: 1800, last_price: 1850, market_value: 370000, pnl: 10000 },
        '300750.SZ': { qty: 1000, cost_price: 200, last_price: 210, market_value: 210000, pnl: 10000 },
      },
    });
  });

  it('初始渲染页面并展示三大分析维度 Tab', async () => {
    render(
      <MemoryRouter>
        <CapacityAttribution />
      </MemoryRouter>,
    );

    expect(screen.getByText('策略容量与业绩归因中心')).toBeInTheDocument();
    expect(screen.getByText(/策略容量与流动性短板/)).toBeInTheDocument();
    expect(screen.getByText(/Brinson 行业多期业绩归因/)).toBeInTheDocument();
    expect(screen.getByText(/个股贡献与 Barra 风格风险分解/)).toBeInTheDocument();

    await waitFor(() => {
      expect(deskApi.attribution).toHaveBeenCalled();
      expect(deskApi.capacity).toHaveBeenCalled();
    });
  });

  it('PortfolioBottleneckCard 能够正确渲染锁喉资产警报和变现天数', () => {
    render(
      <PortfolioBottleneckCard
        data={mockBottleneck}
        targetAum={5e7}
        participationCap={0.10}
        liquidationDaysTarget={3}
        onAumChange={vi.fn()}
        onCapChange={vi.fn()}
        onDaysChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/流动性锁喉短板诊断/)).toBeInTheDocument();
    expect(screen.getAllByText('300750.SZ').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('0.85 亿元').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('高压力')).toBeInTheDocument();
    expect(screen.getByText('充沛')).toBeInTheDocument();
  });

  it('StockContributionCard 能够正确渲染 Top 5 赢家与全部持仓切换', () => {
    render(
      <StockContributionCard
        topContributors={mockAttr.stock_contributions?.top_contributors}
        bottomDrags={mockAttr.stock_contributions?.bottom_drags}
        allHoldings={mockAttr.stock_contributions?.all_holdings}
      />,
    );

    expect(screen.getByText(/Top 5 正向选股超额赢家/)).toBeInTheDocument();
    expect(screen.getAllByText('+840 bps').length).toBeGreaterThanOrEqual(1);

    // 切换到全部持仓
    fireEvent.click(screen.getByText(/全部持仓明细/));
    expect(screen.getByText('电力设备')).toBeInTheDocument();
  });

  it('SectorActiveTiltCard 能够正确切换多期与单期模式并显示零残差', () => {
    render(<SectorActiveTiltCard attr={mockAttr} />);

    expect(screen.getByText(/Carino \/ Menchero 连乘平滑模型/)).toBeInTheDocument();
    expect(screen.getByText(/零残差精确闭合/)).toBeInTheDocument();

    // 切换单期
    fireEvent.click(screen.getByText('单期近似'));
    expect(screen.getByText('经典单期 Brinson-Fachler')).toBeInTheDocument();
  });

  it('BarraRiskDecompositionCard 能够正确展示跟踪误差方差分解与 t 检验显著性', () => {
    render(<BarraRiskDecompositionCard style={mockAttr.style} />);

    expect(screen.getByText(/Barra 主动跟踪误差风险结构分解/)).toBeInTheDocument();
    expect(screen.getByText('68%')).toBeInTheDocument();
    expect(screen.getByText('32%')).toBeInTheDocument();
    expect(screen.getByText('***')).toBeInTheDocument();
    expect(screen.getByText('Momentum')).toBeInTheDocument();
  });

  it('CumulativeAttributionChart 能够正确挂载', () => {
    const { container } = render(
      <CumulativeAttributionChart
        timeseries={mockAttr.multi_period_brinson?.timeseries ?? []}
      />,
    );

    expect(container.querySelector('div')).toBeInTheDocument();
    expect(screen.getByText(/共 2 个交易日追踪/)).toBeInTheDocument();
  });

  it('一键导入模拟盘持仓能够成功解析真实市值权重', async () => {
    render(
      <MemoryRouter>
        <CapacityAttribution />
      </MemoryRouter>,
    );

    // 等待初次归因完成，解除 busy 禁用状态
    await waitFor(() => {
      expect(deskApi.attribution).toHaveBeenCalled();
      const btn = screen.getByText('📥 导入模拟盘持仓');
      expect(btn).not.toBeDisabled();
    });

    const importBtn = screen.getByText('📥 导入模拟盘持仓');
    fireEvent.click(importBtn);

    await waitFor(() => {
      expect(deskApi.account).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText(/成功导入模拟盘 2 只持仓标的/)).toBeInTheDocument();
    });
  });
});
