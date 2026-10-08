import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PortfolioBacktest from './index';
import { portfolioApi } from '@/api/portfolio';
import type { PortfolioBacktestResult } from '@/types/portfolio';

// Mock portfolioApi
vi.mock('@/api/portfolio', () => ({
  portfolioApi: {
    backtest: vi.fn(),
    search: vi.fn(),
  },
}));

// Mock echarts & useChart
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

// Mock useAuthStore with researcher role
vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: 'researcher' } }),
}));

describe('PortfolioBacktest Workbench', () => {
  const mockResult: PortfolioBacktestResult = {
    status: 'ok',
    start_date: '2023-01-01',
    end_date: '2023-12-31',
    initial_cash: 1_000_000,
    rebalance: 'M',
    benchmark: '000300',
    assets: [
      { code: '600519', type: 'stock', weight: 0.3 },
      { code: '600036', type: 'stock', weight: 0.4 },
      { code: '300750', type: 'stock', weight: 0.3 },
    ],
    trading_days: 242,
    price_basis: {
      kind: 'platform',
      basis: 'qfq',
      raw_fallback_symbols: [],
      note: '全部标的为平台前复权（QFQ）口径',
    },
    metrics: {
      total_return: 0.25,
      cagr: 0.25,
      bm_cagr: 0.05,
      excess_cagr: 0.20,
      excess_total_return: 0.20,
      tracking_error: 0.08,
      information_ratio: 2.5,
      win_rate: 0.62,
      active_max_drawdown: 0.04,
      max_drawdown: 0.12,
      volatility: 0.18,
      sharpe: 1.45,
      calmar: 2.08,
      alpha: 0.15,
      beta: 0.85,
      risk_free: 0.02,
      sortino: 2.1,
      cvar_95: -0.025,
      cvar_99: -0.045,
      omega: 1.65,
      tail_risk_ratio: 1.15,
      annualized_turnover: 1.25,
      friction_drag_bps: 45.2,
    },
    nav_curve: [
      { date: '2023-01-03', nav: 1000000, benchmark: 1000000 },
      { date: '2023-12-29', nav: 1250000, benchmark: 1050000 },
    ],
    active_return_curve: [
      { date: '2023-01-03', excess_return: 0.0, active_nav: 1.0 },
      { date: '2023-12-29', excess_return: 0.20, active_nav: 1.19 },
    ],
    drawdown_curve: [
      { date: '2023-01-03', drawdown: 0.0 },
      { date: '2023-12-29', drawdown: -0.03 },
    ],
    annual_returns: [
      { year: '2023', portfolio: 0.25, benchmark: 0.05 },
    ],
    holdings_drift: [
      { date: '2023-01-03', weights: { '600519': 0.3, '600036': 0.4, '300750': 0.3 } },
      { date: '2023-12-29', weights: { '600519': 0.32, '600036': 0.38, '300750': 0.30 } },
    ],
    risk_budgeting: [
      { code: '600519', weight: 0.3, mrc: 0.05, rc: 0.015, rc_pct: 0.28, risk_contribution: 0.015, pct_risk_contribution: 28.0 },
      { code: '600036', weight: 0.4, mrc: 0.04, rc: 0.016, rc_pct: 0.35, risk_contribution: 0.016, pct_risk_contribution: 35.0 },
      { code: '300750', weight: 0.3, mrc: 0.07, rc: 0.021, rc_pct: 0.37, risk_contribution: 0.021, pct_risk_contribution: 37.0 },
    ],
    stress_tests: [
      {
        scenario: '2015_crash',
        name: '2015 杠杆牛市崩盘与股灾',
        period: '2015-06-12 ~ 2015-08-26',
        portfolio_return: null,
        benchmark_return: null,
        portfolio_max_dd: null,
        benchmark_max_dd: null,
        excess_return: null,
        status: 'out_of_range',
      },
      {
        scenario: '2024_microcap_liquidity',
        name: '2024 微盘股流动性踩踏与出清',
        period: '2024-01-02 ~ 2024-02-08',
        portfolio_return: -0.08,
        benchmark_return: -0.15,
        portfolio_max_dd: 0.09,
        benchmark_max_dd: 0.16,
        excess_return: 0.07,
        status: 'in_range',
      },
    ],
    monte_carlo: {
      fan: [
        { day: 0, q05: 1.0, q25: 1.0, q50: 1.0, q75: 1.0, q95: 1.0 },
        { day: 252, q05: 0.85, q25: 1.02, q50: 1.15, q75: 1.30, q95: 1.55 },
      ],
      summary: {
        prob_positive: 0.78,
        expected_return: 0.15,
        var_95: -0.15,
        cvar_95: -0.22,
      },
    },
    turnover_series: [
      {
        date: '2023-01-03',
        turnover_pct: 1.0,
        trade_volume: 1000000,
        commission: 250,
        stamp_duty: 0,
        slippage: 500,
      },
      {
        date: '2023-02-01',
        turnover_pct: 0.08,
        trade_volume: 80000,
        commission: 20,
        stamp_duty: 15,
        slippage: 40,
      },
    ],
    rebalance_log: [
      {
        date: '2023-01-03',
        weighting: 'user',
        fallback: false,
        weights: { '600519': 0.3, '600036': 0.4, '300750': 0.3 },
      },
    ],
    friction_costs: {
      commission: 270,
      stamp_duty: 15,
      slippage: 540,
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
  });

  it('初始正确渲染并支持发起回测', async () => {
    vi.mocked(portfolioApi.backtest).mockResolvedValueOnce(mockResult);

    render(
      <MemoryRouter>
        <PortfolioBacktest />
      </MemoryRouter>,
    );

    expect(screen.getByText('组合回测')).toBeInTheDocument();
    expect(screen.getByText('组合配置 & 回测参数')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('招商银行')).toBeInTheDocument();
    expect(screen.getByText('宁德时代')).toBeInTheDocument();
    expect(screen.getByText('配置资产后点击「开始回测」')).toBeInTheDocument();

    const startBtn = screen.getByText('开始回测');
    expect(startBtn).not.toBeDisabled();
    fireEvent.click(startBtn);

    await waitFor(() => {
      expect(portfolioApi.backtest).toHaveBeenCalled();
    });

    // 验证回测完成后的 3-Tab 体系与 Tab 1 机构 KPI
    await waitFor(() => {
      expect(screen.getByText('业绩与超额概览 (Overview & Alpha)')).toBeInTheDocument();
      expect(screen.getByText('风险预算与压力测试 (Risk & Stress)')).toBeInTheDocument();
      expect(screen.getByText('调仓流水与摩擦损耗 (Trades & Friction)')).toBeInTheDocument();
      expect(screen.getByText('信息比率 (IR)')).toBeInTheDocument();
      expect(screen.getByText('跟踪误差 (TE)')).toBeInTheDocument();
      expect(screen.getByText('年化超额 (Excess CAGR)')).toBeInTheDocument();
      expect(screen.getByText('超额最大回撤')).toBeInTheDocument();
      expect(screen.getByText('累计超额收益走势 (Active Alpha Curve)')).toBeInTheDocument();
    });
  });

  it('切换至 Tab 2 (Risk & Stress) 正确展示黑天鹅压力测试与蒙特卡洛', async () => {
    vi.mocked(portfolioApi.backtest).mockResolvedValueOnce(mockResult);

    render(
      <MemoryRouter>
        <PortfolioBacktest />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText('开始回测'));

    await waitFor(() => {
      expect(screen.getByText('风险预算与压力测试 (Risk & Stress)')).toBeInTheDocument();
    });

    // 点击切换至 Tab 2
    fireEvent.click(screen.getByText('风险预算与压力测试 (Risk & Stress)'));

    await waitFor(() => {
      expect(screen.getByText('历史黑天鹅极端情景压力测试 (Stress Testing)')).toBeInTheDocument();
      expect(screen.getByText('2024 微盘股流动性踩踏与出清')).toBeInTheDocument();
      expect(screen.getByText('综合风险度量与非对称尾部特征')).toBeInTheDocument();
      expect(screen.getByText('风险预算与欧拉齐次风险分解 (%RC 剪刀差分析)')).toBeInTheDocument();
      expect(screen.getByText('蒙特卡洛随机漫步预测扇形图 (Monte Carlo Bootstrap Fan Chart)')).toBeInTheDocument();
      expect(screen.getByText('未来 1 年正收益概率')).toBeInTheDocument();
    });
  });

  it('切换至 Tab 3 (Trades & Friction) 正确展示单期换手与摩擦归因', async () => {
    vi.mocked(portfolioApi.backtest).mockResolvedValueOnce(mockResult);

    render(
      <MemoryRouter>
        <PortfolioBacktest />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText('开始回测'));

    await waitFor(() => {
      expect(screen.getByText('调仓流水与摩擦损耗 (Trades & Friction)')).toBeInTheDocument();
    });

    // 点击切换至 Tab 3
    fireEvent.click(screen.getByText('调仓流水与摩擦损耗 (Trades & Friction)'));

    await waitFor(() => {
      expect(screen.getByText('年化换手率')).toBeInTheDocument();
      expect(screen.getByText('摩擦年化拖累 (Drag)')).toBeInTheDocument();
      expect(screen.getByText('单期调仓换手率与交易额时序分布')).toBeInTheDocument();
      expect(screen.getByText('持仓权重漂移时序堆叠图')).toBeInTheDocument();
      expect(screen.getByText('调仓决策审计日志 (Rebalance Decision Audit)')).toBeInTheDocument();
    });
  });
});
