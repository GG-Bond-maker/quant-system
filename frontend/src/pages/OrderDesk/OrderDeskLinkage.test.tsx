import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import OrderDesk from './index';
import { deskApi } from '@/api/production';

vi.mock('@/api/production', () => ({
  deskApi: {
    account: vi.fn(),
    orders: vi.fn(),
    killSwitch: vi.fn(),
    exclusionList: vi.fn(),
    tca: vi.fn(),
    placeOrder: vi.fn(),
    runFills: vi.fn(),
    setKillSwitch: vi.fn(),
    toggleExclusion: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('OrderDesk 投研联动闭环', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();

    vi.mocked(deskApi.account).mockResolvedValue({
      initial_cash: 1000000,
      cash: 800000,
      market_value: 200000,
      equity: 1000000,
      positions: {},
      n_fills: 0,
      total_fees: 0,
      total_impact_cost: 0,
      n_active_days: 1,
      max_drawdown: 0,
      sharpe: 0,
      annualized_return: 0,
      nav_series: [],
      integrity_warnings: [],
    });
    vi.mocked(deskApi.orders).mockResolvedValue({
      items: [],
      total: 0,
      returned: 0,
      limit: 200,
      truncated: false,
    });
    vi.mocked(deskApi.killSwitch).mockResolvedValue({
      kill_switch: false,
      pending_orders: 0,
    });
    vi.mocked(deskApi.exclusionList).mockResolvedValue([]);
    vi.mocked(deskApi.tca).mockResolvedValue(null as unknown as any);
  });

  it('读取凸优化导入的待执行草稿并支持一键批量下单', async () => {
    sessionStorage.setItem(
      'aqp:desk:batch_import',
      JSON.stringify({
        source: '投资组合凸优化 (风险平价)',
        orders: [
          {
            symbol: '600519.SH',
            side: 'buy',
            order_amount: 400000,
            algo: 'vwap',
            split_days: 5,
            participation_cap: 0.05,
            target_weight: 40.0,
          },
          {
            symbol: '000001.SZ',
            side: 'buy',
            order_amount: 600000,
            algo: 'vwap',
            split_days: 5,
            participation_cap: 0.05,
            target_weight: 60.0,
          },
        ],
        createdAt: new Date().toISOString(),
      }),
    );

    vi.mocked(deskApi.placeOrder).mockResolvedValue({
      ok: true,
      order_id: 101,
      decision_price: 1800,
    });

    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByText(/投研联动闭环：待执行调仓母单草稿/)).toBeInTheDocument();
      expect(screen.getByText('600519.SH')).toBeInTheDocument();
      expect(screen.getByText('000001.SZ')).toBeInTheDocument();
    });

    const batchBtn = screen.getByRole('button', { name: /一键批量报单/ });
    fireEvent.click(batchBtn);

    await waitFor(() => {
      expect(deskApi.placeOrder).toHaveBeenCalledTimes(2);
      expect(screen.getByText(/组合批量报单成功！已提交全部 2 笔母单/)).toBeInTheDocument();
    });

    expect(sessionStorage.getItem('aqp:desk:batch_import')).toBeNull();
  });

  it('控制舱仪表盘正确渲染动态权益、现金与风控盾牌', async () => {
    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByText('动态权益 (NAV)')).toBeInTheDocument();
      expect(screen.getByText('可用现金头寸')).toBeInTheDocument();
      expect(screen.getByText('风控响应级别')).toBeInTheDocument();
      expect(screen.getByText('L0 平稳运行中')).toBeInTheDocument();
    });
  });

  it('支持 Tab 切换至量化风控中心与 TCA 交易成本归因', async () => {
    vi.mocked(deskApi.tca).mockResolvedValue({
      has_data: true,
      total_orders: 5,
      total_fills: 12,
      summary: {
        total_volume: 500000,
        total_fee: 150,
        fee_bps: 3.0,
        avg_impact_bps: 4.5,
        delay_bps: 1.2,
        avg_basis_bps: 5.7,
        total_shortfall_bps: 8.7,
        waterfall: [
          { name: '规费与佣金 (Fee)', value: 3.0 },
          { name: '时机延迟 (Delay)', value: 1.2 },
          { name: '冲击成本 (Impact)', value: 4.5 },
          { name: '总实现落差 (Total IS)', value: 8.7 },
        ],
      },
      algo_breakdown: [
        { algo: 'vwap', fill_count: 8, volume: 350000, avg_impact_bps: 3.8, avg_basis_bps: 4.2 },
      ],
      recent_executions: [],
    });

    render(<OrderDesk />);

    // 切换至量化风控中心
    const riskTab = screen.getByRole('button', { name: /量化风控中心/ });
    fireEvent.click(riskTab);

    await waitFor(() => {
      expect(screen.getByText(/三级阶梯量化风控应急控制中心/)).toBeInTheDocument();
      expect(screen.getByText(/事前量化风控限额矩阵/)).toBeInTheDocument();
      expect(screen.getByText(/单标的集中度上限/)).toBeInTheDocument();
      expect(screen.getByText(/合规禁买池管理/)).toBeInTheDocument();
    });

    // 切换至 TCA 成本归因
    const tcaTab = screen.getByRole('button', { name: /TCA 交易成本与归因/ });
    fireEvent.click(tcaTab);

    await waitFor(() => {
      expect(screen.getByText(/Perold 总执行落差/)).toBeInTheDocument();
      expect(screen.getByText(/算法分批执行质量对比/)).toBeInTheDocument();
      expect(screen.getByText('vwap')).toBeInTheDocument();
    });
  });
});

