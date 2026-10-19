import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FuturesAnalysis from './index';
import { futuresApi } from '@/api/futures';

vi.mock('@/api/futures', () => ({
  futuresApi: {
    backtest: vi.fn(),
    instrumentProfile: vi.fn(),
    paramSensitivity: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('FuturesAnalysis Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(futuresApi.instrumentProfile).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      exchange: 'CFFEX',
      category: 'index',
      multiplier: 300,
      tick_size: 0.2,
      tick_value: 60,
      margin_rate: 0.12,
      margin_rate_pct: 12.0,
      fee_type: 'rate',
      fee_unit: 0.000023,
      underlying_code: '000300.SH',
      latest_price: 3950.0,
      contract_value: 1185000.0,
      margin_per_lot: 142200.0,
      volatility_stats: {
        hv20_pct: 16.5,
        atr14: 45.2,
        atr_pct: 1.14,
      },
      open_interest_stats: {
        latest_oi: 218000,
        oi_change: 3500,
        oi_change_pct: 1.63,
        structure: '增仓上涨 (多头主动上攻，上行动能较强)',
      },
      kline: [
        {
          date: '2026-01-01',
          open: 3920.0,
          high: 3960.0,
          low: 3910.0,
          close: 3950.0,
          volume: 120000,
          open_interest: 218000,
          ma5: 3940.0,
          ma20: 3910.0,
        },
      ],
    } as any);

    vi.mocked(futuresApi.paramSensitivity).mockResolvedValue({
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      strategy_type: 'dual_ma',
      grid: [
        { fast: 5, slow: 20, sharpe: 1.85, total_return_pct: 25.0, max_drawdown_pct: -6.8 },
      ],
      optimal_params: { fast: 5, slow: 20, sharpe: 1.85, total_return_pct: 25.0, max_drawdown_pct: -6.8 },
      total_combinations: 1,
    } as any);

    vi.mocked(futuresApi.backtest).mockResolvedValue({
      status: 'ok',
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      strategy_type: 'dual_ma',
      direction: 'both',
      leverage: 1.0,
      init_cash: 1000000,
      final_equity: 1250000,
      total_return_pct: 25.0,
      ann_return_pct: 26.5,
      annual_volatility_pct: 14.8,
      max_drawdown_pct: -6.8,
      current_drawdown_pct: -1.2,
      sharpe_ratio: 1.85,
      sortino_ratio: 2.15,
      calmar_ratio: 3.9,
      peak_margin_ratio: 18.5,
      benchmark_name: '标的买入持有 (IF)',
      benchmark_return_pct: 5.0,
      benchmark_ann_return_pct: 5.2,
      excess_return_pct: 20.0,
      beta: 0.15,
      alpha_annual_pct: 21.3,
      tracking_error_pct: 12.4,
      information_ratio: 1.72,
      var_95_pct: -1.65,
      cvar_95_pct: -2.48,
      margin_call_count: 0,
      liquidation_count: 0,
      total_trades: 22,
      profit_factor: 2.14,
      payoff_ratio: 1.85,
      t_statistic: 2.45,
      p_value: 0.018,
      psr: 96.5,
      dsr: 89.2,
      max_win_contrib_pct: 14.2,
      is_sample_sufficient: true,
      cost_stress_matrix: {
        grid: [
          { slippage_ticks: 0, fee_multiplier: 1.0, sharpe: 2.1, total_return_pct: 28.0, max_drawdown_pct: -6.0 },
          { slippage_ticks: 1.0, fee_multiplier: 1.0, sharpe: 1.85, total_return_pct: 25.0, max_drawdown_pct: -6.8 },
          { slippage_ticks: 2.0, fee_multiplier: 2.0, sharpe: 1.2, total_return_pct: 16.0, max_drawdown_pct: -8.5 },
          { slippage_ticks: 5.0, fee_multiplier: 5.0, sharpe: -0.4, total_return_pct: -4.5, max_drawdown_pct: -15.2 },
        ],
        breakeven_slippage_ticks: 3.8,
        tested_slippages: [0, 1.0, 2.0, 5.0],
        tested_fee_multipliers: [0.5, 1.0, 2.0, 5.0],
      },
      long_stats: {
        trades: 12,
        wins: 8,
        win_rate: 66.7,
        total_pnl: 160000,
      },
      short_stats: {
        trades: 10,
        wins: 7,
        win_rate: 70.0,
        total_pnl: 90000,
      },
      overall_win_rate: 68.2,
      dates: ['2026-01-01', '2026-01-02'],
      equity_curve: [1000000, 1250000],
      benchmark_curve: [1000000, 1050000],
      drawdown_curve: [0, -1.2],
      margin_used_curve: [140000, 142000],
      margin_ratio_curve: [14.0, 11.36],
      positions: [1, -1],
      trades: [
        {
          id: 'T-001',
          direction: 'LONG',
          entry_date: '2026-01-01',
          entry_price: 3920.0,
          exit_date: '2026-01-02',
          exit_price: 3950.0,
          lots: 1,
          fee: 28.0,
          pnl: 8972.0,
          pnl_pct: 0.77,
        },
      ],
      top_drawdowns: [
        {
          peak_date: '2026-01-01',
          peak_value: 1200000,
          trough_date: '2026-01-02',
          trough_value: 1120000,
          recovery_date: '2026-01-03',
          max_drawdown_pct: -6.8,
          drawdown_days: 5,
          recovery_days: 8,
          total_duration_days: 13,
          is_recovered: true,
        },
      ],
    } as any);
  });

  it('renders backtest results, drawdown from peak table, and short metrics correctly', async () => {
    render(
      <MemoryRouter>
        <FuturesAnalysis />
      </MemoryRouter>
    );

    expect(screen.getByText(/期货分析/)).toBeInTheDocument();
    expect(screen.getByText('返回期货中心')).toBeInTheDocument();

    await waitFor(() => {
      expect(futuresApi.backtest).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: 'IF',
          strategy_type: 'dual_ma',
          direction: 'both',
        })
      );
    });

    // 检查核心绩效与做空指标
    expect(screen.getByText(/卡尔玛比率/)).toBeInTheDocument();
    expect(screen.getAllByText(/年化夏普比率/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('1.85').length).toBeGreaterThan(0);
    expect(screen.getByText(/做空交易分项绩效/)).toBeInTheDocument();
    expect(screen.getAllByText(/70%/).length).toBeGreaterThan(0); // 空头胜率

    // 检查统计严谨性与样本显著性
    expect(screen.getByText(/统计学显著性与样本容量检验合格/)).toBeInTheDocument();

    // 检查基准对比与阿尔法业绩归因表
    expect(screen.getByText(/业绩归因对标表/)).toBeInTheDocument();
    expect(screen.getByText(/Jensen's α/)).toBeInTheDocument();

    // 检查尾部风险与极端流动性监控
    expect(screen.getByText(/95% 日度在险价值/)).toBeInTheDocument();
    expect(screen.getByText(/强平穿仓风控预警/)).toBeInTheDocument();

    // 检查从区间高点计算的回测表
    expect(screen.getByRole('heading', { name: /从区间高点计算的回测表/ })).toBeInTheDocument();
    expect(screen.getByText('已完全修复')).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
  });

  it('supports changing strategy type and re-running backtest', async () => {
    render(
      <MemoryRouter>
        <FuturesAnalysis />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(futuresApi.backtest).toHaveBeenCalledTimes(1);
    });

    const runBtn = screen.getByText('🚀 运行期货回测');
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(futuresApi.backtest).toHaveBeenCalledTimes(2);
    });
  });

  it('supports switching tabs to profile, margin, blotter, and sensitivity', async () => {
    render(
      <MemoryRouter>
        <FuturesAnalysis />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(futuresApi.backtest).toHaveBeenCalled();
    });

    // 切换到标的画像 Tab
    const profileTab = screen.getByText(/标的画像与量价K线/);
    fireEvent.click(profileTab);
    expect(screen.getByText(/合约乘数 \/ 计价单位/)).toBeInTheDocument();
    expect(screen.getByText(/HV20 年化历史波动率/)).toBeInTheDocument();

    // 切换到保证金风控 Tab
    const marginTab = screen.getByText(/保证金与风控/);
    fireEvent.click(marginTab);
    expect(screen.getByText(/逐日保证金占用走势与杠杆风控预警/)).toBeInTheDocument();
    expect(screen.getByText(/历史峰值保证金率/)).toBeInTheDocument();

    // 切换到逐笔交易流水 Tab
    const blotterTab = screen.getByText(/逐笔交易流水/);
    fireEvent.click(blotterTab);
    expect(screen.getByText(/逐笔开平仓交易流水表/)).toBeInTheDocument();
    expect(screen.getByText('T-001')).toBeInTheDocument();

    // 切换到参数寻优 Tab
    const sensTab = screen.getByText(/参数敏感性寻优/);
    fireEvent.click(sensTab);
    expect(screen.getByText(/最优均线参数推荐/)).toBeInTheDocument();
    expect(screen.getByText(/摩擦成本压力测试矩阵/)).toBeInTheDocument();
    expect(screen.getByText(/盈亏平衡临界滑点/)).toBeInTheDocument();
  });

  it('supports switching K-line range buttons and maintains selected range on continuous toggle', async () => {
    render(
      <MemoryRouter>
        <FuturesAnalysis />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(futuresApi.instrumentProfile).toHaveBeenCalledWith('IF', 250, true);
    });

    // 切换到标的画像 Tab
    const profileTab = screen.getByText(/标的画像与量价K线/);
    fireEvent.click(profileTab);

    // 默认高亮 250日(1年)
    const btn250 = screen.getByText('250日(1年)');
    expect(btn250).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 点击 60日
    const btn60 = screen.getByText('60日');
    fireEvent.click(btn60);

    await waitFor(() => {
      expect(futuresApi.instrumentProfile).toHaveBeenCalledWith('IF', 60, true);
    });
    expect(btn60).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 点击 全历史 (2500)
    const btnAll = screen.getByText('全历史');
    fireEvent.click(btnAll);

    await waitFor(() => {
      expect(futuresApi.instrumentProfile).toHaveBeenCalledWith('IF', 2500, true);
    });
    expect(btnAll).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 切换复权勾选，应保留当前选中的 2500 日全历史范围
    const continuousCheckbox = screen.getByRole('checkbox');
    fireEvent.click(continuousCheckbox);

    await waitFor(() => {
      expect(futuresApi.instrumentProfile).toHaveBeenCalledWith('IF', 2500, false);
    });
    expect(btnAll).toHaveClass('bg-card text-primary shadow-sm font-semibold');
  });

  it('displays small-sample bias alert when trades < 20 or sample insufficient', async () => {
    vi.mocked(futuresApi.backtest).mockResolvedValueOnce({
      status: 'ok',
      symbol_prefix: 'IF',
      name: '沪深300股指期货',
      strategy_type: 'dual_ma',
      direction: 'both',
      leverage: 1.0,
      init_cash: 1000000,
      final_equity: 1827000,
      total_return_pct: 82.7,
      ann_return_pct: 88.5,
      annual_volatility_pct: 12.0,
      max_drawdown_pct: -7.5,
      current_drawdown_pct: -0.5,
      sharpe_ratio: 7.93,
      sortino_ratio: 9.15,
      calmar_ratio: 10.95,
      peak_margin_ratio: 14.5,
      total_trades: 4,
      is_sample_sufficient: false,
      long_stats: { trades: 2, wins: 1, win_rate: 50.0, total_pnl: 660000 },
      short_stats: { trades: 2, wins: 1, win_rate: 50.0, total_pnl: 167000 },
      overall_win_rate: 50.0,
      dates: ['2026-01-01', '2026-01-02'],
      equity_curve: [1000000, 1827000],
      benchmark_curve: [1000000, 1020000],
      drawdown_curve: [0, -0.5],
      margin_used_curve: [140000, 142000],
      margin_ratio_curve: [14.0, 7.77],
      positions: [1, -1],
      trades: [],
      top_drawdowns: [],
    } as any);

    render(
      <MemoryRouter>
        <FuturesAnalysis />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(futuresApi.backtest).toHaveBeenCalled();
    });

    expect(screen.getByText(/交易样本容量过小预警/)).toBeInTheDocument();
    expect(screen.getByText(/N = 4 笔/)).toBeInTheDocument();
  });
});


