import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import OrderDesk from './index';
import { deskApi } from '@/api/production';
import { marketApi } from '@/api/market';

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
    screenCandidates: vi.fn(),
    addExclusion: vi.fn(),
    cancelAllOrders: vi.fn(),
    liquidateAll: vi.fn(),
    preflight: vi.fn(),
  },
}));

vi.mock('@/api/market', () => ({
  marketApi: {
    quotes: vi.fn(),
    orderbook: vi.fn(),
    kline: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('OrderDesk P0 交易执行安全加固', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();

    vi.mocked(deskApi.account).mockResolvedValue({
      initial_cash: 1000000,
      cash: 800000,
      market_value: 200000,
      equity: 1000000,
      positions: {
        '600519.SH': { qty: 100, market_value: 180000, cost_price: 1800, last_price: 1800, pnl: 0 },
      },
      n_fills: 1,
      total_fees: 15,
      total_impact_cost: 20,
      n_active_days: 1,
      max_drawdown: 0,
      sharpe: 0,
      annualized_return: 0,
      nav_series: [],
      integrity_warnings: [],
    });

    vi.mocked(deskApi.orders).mockResolvedValue({
      items: [
        {
          id: 99,
          symbol: '600519.SH',
          side: 'buy',
          order_amount: 100000,
          algo: 'vwap',
          status: 'PENDING',
          created_at: new Date().toISOString(),
          decision_price: 1800,
          filled_amount: 0,
          split_days: 1,
          reject_reason: null,
          fills: [],
        },
      ],
      total: 1,
      returned: 1,
      limit: 200,
      truncated: false,
    });

    vi.mocked(deskApi.killSwitch).mockResolvedValue({
      kill_switch: false,
      pending_orders: 1,
    });
    vi.mocked(deskApi.exclusionList).mockResolvedValue([]);
    vi.mocked(deskApi.tca).mockResolvedValue(null as unknown as any);
    vi.mocked(deskApi.preflight).mockResolvedValue({
      passed: true,
      reasons: [],
      warnings: [],
      metrics: {
        order_amount: 100000,
        current_cash: 800000,
        total_equity: 1000000,
        max_position_ratio: 0.1,
      },
    });
    vi.mocked(marketApi.quotes).mockResolvedValue({
      quotes: [
        {
          symbol: '000001.SZ',
          name: '平安银行',
          price: 11.5,
          change: 0.1,
          pct_change: 0.88,
          volume: 100000,
          amount: 1150000,
          bids: [{ price: 11.49, volume: 500 }],
          asks: [{ price: 11.51, volume: 600 }],
        },
      ],
    } as any);
  });

  it('单笔母单提交触发确认弹窗，确认后才向后端报单', async () => {
    vi.mocked(deskApi.placeOrder).mockResolvedValue({
      ok: true,
      order_id: 88001,
      decision_price: 11.5,
    });

    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '提交母单' })).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole('button', { name: '提交母单' });
    fireEvent.click(submitBtn);

    // 弹窗出现
    await waitFor(() => {
      expect(screen.getByText('母单委托前置确认')).toBeInTheDocument();
    });

    // 此时并未向接口直接提交
    expect(deskApi.placeOrder).not.toHaveBeenCalled();

    // 点击弹窗中的确认
    const confirmBtn = screen.getByRole('button', { name: '确认提交报单' });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(deskApi.placeOrder).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/母单.*88001.*已成功提交/)).toBeInTheDocument();
    });
  });

  it('勾选本次会话记住选择后，后续报单直接提交无需二次弹窗', async () => {
    vi.mocked(deskApi.placeOrder).mockResolvedValue({
      ok: true,
      order_id: 202,
      decision_price: 11.5,
    });

    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '提交母单' })).toBeInTheDocument();
    });

    // 第一次点击：弹窗拦截
    fireEvent.click(screen.getByRole('button', { name: '提交母单' }));
    await waitFor(() => {
      expect(screen.getByText('母单委托前置确认')).toBeInTheDocument();
    });

    // 勾选会话记住
    const checkbox = screen.getByRole('checkbox', { name: /本次登录会话记住选择/ });
    fireEvent.click(checkbox);

    // 确认第一次提交
    fireEvent.click(screen.getByRole('button', { name: '确认提交报单' }));
    await waitFor(() => {
      expect(deskApi.placeOrder).toHaveBeenCalledTimes(1);
    });

    // 第二次点击：应直接提交，不再弹出确认框
    fireEvent.click(screen.getByRole('button', { name: '提交母单' }));
    await waitFor(() => {
      expect(deskApi.placeOrder).toHaveBeenCalledTimes(2);
    });
  });

  it('Shift+X 快捷键在选中未持仓标的时被安全拦截，禁止误清仓其他资产', async () => {
    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('000001.SZ')).toBeInTheDocument();
    });

    // 当前表单代码是 000001.SZ，但持仓中只有 600519.SH
    fireEvent.keyDown(window, { key: 'X', shiftKey: true });

    // 应安全报错，绝不能去清空 600519.SH
    await waitFor(() => {
      expect(screen.getByText(/快捷键 \[Shift\+X\] 拦截：当前选中标的 000001.SZ 未在持仓列表中/)).toBeInTheDocument();
    });
    expect(deskApi.placeOrder).not.toHaveBeenCalled();
  });

  it('持仓列表点击市价清仓弹出二次阻断确认弹窗', async () => {
    vi.mocked(deskApi.placeOrder).mockResolvedValue({
      ok: true,
      order_id: 203,
      decision_price: 1800,
    });

    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByText('600519.SH')).toBeInTheDocument();
    });

    const quickLiquidateBtn = screen.getByRole('button', { name: '市价清仓' });
    fireEvent.click(quickLiquidateBtn);

    // 阻断弹窗展示
    await waitFor(() => {
      expect(screen.getByText('确认执行【单标的市价清仓】？')).toBeInTheDocument();
    });
    expect(deskApi.placeOrder).not.toHaveBeenCalled();

    // 确认清仓
    fireEvent.click(screen.getByRole('button', { name: '确认立即清仓' }));
    await waitFor(() => {
      expect(deskApi.placeOrder).toHaveBeenCalledTimes(1);
    });
  });

  it('一键撤单弹出确认弹窗后执行撤单', async () => {
    vi.mocked(deskApi.cancelAllOrders).mockResolvedValue({
      ok: true,
      cancelled_count: 1,
    });

    render(<OrderDesk />);

    await waitFor(() => {
      expect(screen.getByText(/一键撤销挂单/)).toBeInTheDocument();
    });

    const cancelAllBtn = screen.getByText(/一键撤销挂单/);
    fireEvent.click(cancelAllBtn);

    await waitFor(() => {
      expect(screen.getByText('确认撤销【全部在途挂单】？')).toBeInTheDocument();
    });
    expect(deskApi.cancelAllOrders).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认全部撤销' }));
    await waitFor(() => {
      expect(deskApi.cancelAllOrders).toHaveBeenCalledTimes(1);
    });
  });
});
