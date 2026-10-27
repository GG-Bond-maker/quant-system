import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import OrderDesk from './index';
import { deskApi } from '@/api/production';
import { marketApi } from '@/api/market';

vi.mock('@/api/production', () => ({
  deskApi: {
    accounts: vi.fn(),
    createAccount: vi.fn(),
    compareAccounts: vi.fn(),
    getConditionalOrders: vi.fn(), getGridBots: vi.fn(),
    createConditionalOrder: vi.fn(), createGridBot: vi.fn(), evaluateBots: vi.fn(),
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

vi.mock('@/components/charts/StockIntradayChart', () => ({ default: () => <div>分时图</div> }));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('OrderDesk P0 交易执行安全加固', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();

    vi.mocked(deskApi.getConditionalOrders).mockResolvedValue([]);
    vi.mocked(deskApi.getGridBots).mockResolvedValue([]);
    vi.mocked(deskApi.accounts).mockResolvedValue([{ account_id: 'paper_default', initial_cash: 1000000 }, { account_id: 'strategy_a', initial_cash: 1000000 }]);
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
  it('切换独立账户后下单与查询只使用选中账户', async () => {
    vi.mocked(deskApi.placeOrder).mockResolvedValue({ ok: true, order_id: 1 });
    render(<OrderDesk />);
    await screen.findByRole('option', { name: 'strategy_a' });
    fireEvent.change(screen.getByLabelText('策略账户'), { target: { value: 'strategy_a' } });
    await waitFor(() => expect(deskApi.account).toHaveBeenLastCalledWith('strategy_a'));
    fireEvent.click(screen.getByRole('button', { name: '提交母单' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认提交报单' }));
    await waitFor(() => expect(deskApi.placeOrder).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'strategy_a', client_order_id: expect.any(String) })));
    expect(deskApi.orders).toHaveBeenLastCalledWith(200, 'strategy_a');
  });

  it('网络失败后重试保留幂等键，成功后的新意图使用新键', async () => {
    vi.mocked(deskApi.placeOrder).mockRejectedValueOnce(new Error('timeout')).mockResolvedValue({ ok: true, order_id: 1 });
    render(<OrderDesk />);
    await screen.findByRole('button', { name: '提交母单' });
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByRole('button', { name: '提交母单' }));
      fireEvent.click(await screen.findByRole('button', { name: '确认提交报单' }));
      await waitFor(() => expect(deskApi.placeOrder).toHaveBeenCalledTimes(i + 1));
      await waitFor(() => expect(screen.getByRole('button', { name: '提交母单' })).not.toBeDisabled());
    }
    const calls = vi.mocked(deskApi.placeOrder).mock.calls;
    expect(calls[0][0].client_order_id).toBeTruthy();
    expect(calls[1][0].client_order_id).toBe(calls[0][0].client_order_id);
    expect(calls[2][0].client_order_id).not.toBe(calls[1][0].client_order_id);
  });

  it('盘口缺失时对手价不得使用模拟报价报单', async () => {
    vi.mocked(marketApi.quotes).mockResolvedValue({ quotes: [] } as any);
    render(<OrderDesk />);
    fireEvent.click(await screen.findByRole('button', { name: '对手价 (卖一)' }));
    fireEvent.click(screen.getByRole('button', { name: '提交母单' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认提交报单' }));
    await screen.findByText('请输入有效限价，或等待真实对手报价');
    expect(deskApi.placeOrder).not.toHaveBeenCalled();
  });

  it('切换方向清除旧限价，避免不同交易意图沿用价格', async () => {
    render(<OrderDesk />);
    fireEvent.click(await screen.findByRole('button', { name: '指定限价' }));
    fireEvent.change(screen.getByPlaceholderText('输入指定限价'), { target: { value: '18' } });
    fireEvent.keyDown(window, { key: 'S', shiftKey: true });
    fireEvent.click(screen.getByRole('button', { name: '指定限价' }));
    expect(screen.getByPlaceholderText('输入指定限价')).not.toHaveValue(18);
  });

  it('批量部分成功后只重试未确认订单并保留来源与幂等键', async () => {
    sessionStorage.setItem('aqp:desk:batch_import', JSON.stringify({ source: '回测', createdAt: new Date().toISOString(), orders: ['000001.SZ', '600519.SH'].map((symbol, i) => ({ symbol, side: 'buy', order_amount: 10000, algo: 'market', split_days: 1, participation_cap: 0.05, signal_id: `signal-${i}`, model_version: 'm1', strategy_version: 's1', backtest_id: '7', run_id: 'r1' })) }));
    vi.mocked(deskApi.placeOrder).mockResolvedValueOnce({ ok: true, order_id: 1 }).mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce({ ok: true, order_id: 2 });
    render(<OrderDesk />);
    for (let i = 0; i < 2; i++) {
      fireEvent.click(await screen.findByRole('button', { name: /一键批量报单/ }));
      fireEvent.click(await screen.findByRole('button', { name: '确认一键批量报单' }));
      await waitFor(() => expect(deskApi.placeOrder).toHaveBeenCalledTimes(i === 0 ? 2 : 3));
      await waitFor(() => expect(screen.queryByText(/正在批量报单/)).toBeNull());
    }
    const calls = vi.mocked(deskApi.placeOrder).mock.calls;
    expect(calls[1][0]).toMatchObject({ signal_id: 'signal-1', model_version: 'm1', backtest_id: '7', run_id: 'r1', account_id: 'paper_default' });
    expect(calls[2][0].client_order_id).toBe(calls[1][0].client_order_id);
    expect(sessionStorage.getItem('aqp:desk:batch_import')).toBeNull();
  });

  it('估值缺失时页面保持未知且禁止母单提交', async () => {
    const snapshot = await vi.mocked(deskApi.account)();
    vi.mocked(deskApi.account).mockResolvedValue({ ...snapshot, equity: null, market_value: null });
    render(<OrderDesk />);
    await waitFor(() => expect(screen.getByRole('button', { name: '提交母单' })).toBeDisabled());
    expect(screen.queryByText('1,000,000 元')).toBeNull();
  });

  it('持仓缺行情时保留股数、披露估值缺失并禁用金额清仓', async () => {
    const snapshot = await vi.mocked(deskApi.account)();
    vi.mocked(deskApi.account).mockResolvedValue({ ...snapshot, equity: null, market_value: null, positions: { '600519.SH': { qty: 123, available_qty: 123, cost_price: 1800, last_price: null, market_value: null, pnl: null, mark_date: null } } } as any);
    render(<OrderDesk />);
    expect(await screen.findByText('123')).toBeInTheDocument();
    expect(screen.getAllByText('估值不可用').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '市价清仓' })).toBeDisabled();
  });

  it('创建独立账户后切换新账户，初始资金只发送创建请求', async () => {
    vi.mocked(deskApi.createAccount).mockResolvedValue({ account_id: 'new_strategy', initial_cash: 2000000 });
    render(<OrderDesk />);
    await screen.findByRole('option', { name: 'strategy_a' });
    vi.mocked(deskApi.accounts).mockResolvedValue([{ account_id: 'paper_default', initial_cash: 1000000 }, { account_id: 'new_strategy', initial_cash: 2000000 }]);
    fireEvent.change(screen.getByLabelText('新账户 ID'), { target: { value: 'new_strategy' } });
    fireEvent.change(screen.getByLabelText('初始资金'), { target: { value: '2000000' } });
    fireEvent.click(screen.getByRole('button', { name: '创建独立账户' }));
    await waitFor(() => expect(screen.getByLabelText('策略账户')).toHaveValue('new_strategy'));
    expect(deskApi.createAccount).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'new_strategy', initial_cash: 2000000 }));
    await waitFor(() => expect(deskApi.account).toHaveBeenLastCalledWith('new_strategy'));
  });

  it('共同日期样本不足时比较显示不可用，不补零收益', async () => {
    vi.mocked(deskApi.compareAccounts).mockResolvedValue({ start: null, end: null, benchmark: '000300.SH', comparable: false, reason: '共同净值样本不足两个交易日', basis: '共同交易日', items: [{ account_id: 'strategy_a', initial_cash: 1000000, strategy_version: 's1', period_return: null, benchmark_return: null, excess_return: null, total_fees: 0, n_fills: 0, lineage: [] }] });
    render(<OrderDesk />);
    fireEvent.click(await screen.findByLabelText('对照 strategy_a'));
    fireEvent.click(screen.getByRole('button', { name: '共同日期收益比较' }));
    const label = await screen.findByText('strategy_a / s1');
    expect(within(label.closest('tr')!).getAllByText('不可用')).toHaveLength(3);
    expect(deskApi.compareAccounts).toHaveBeenCalledWith(['strategy_a'], '000300.SH');
  });

  it('条件单、网格与全撤都携带当前账户', async () => {
    vi.mocked(deskApi.createConditionalOrder).mockResolvedValue({} as any);
    vi.mocked(deskApi.createGridBot).mockResolvedValue({} as any);
    vi.mocked(deskApi.cancelAllOrders).mockResolvedValue({ ok: true, cancelled_count: 1 });
    render(<OrderDesk />);
    await screen.findByRole('option', { name: 'strategy_a' });
    fireEvent.change(screen.getByLabelText('策略账户'), { target: { value: 'strategy_a' } });
    await waitFor(() => expect(deskApi.account).toHaveBeenLastCalledWith('strategy_a'));
    fireEvent.click(screen.getByRole('button', { name: /智能条件单与网格/ }));
    fireEvent.click(screen.getByRole('button', { name: /创建条件单/ }));
    await waitFor(() => expect(deskApi.createConditionalOrder).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'strategy_a' })));
    fireEvent.click(screen.getByRole('button', { name: /启动网格机器人/ }));
    await waitFor(() => expect(deskApi.createGridBot).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'strategy_a' })));
    fireEvent.click(screen.getByRole('button', { name: /一键撤销挂单/ }));
    fireEvent.click(await screen.findByRole('button', { name: '确认全部撤销' }));
    await waitFor(() => expect(deskApi.cancelAllOrders).toHaveBeenCalledWith('strategy_a'));
    expect(deskApi.getConditionalOrders).toHaveBeenLastCalledWith(undefined, 'strategy_a');
    expect(deskApi.getGridBots).toHaveBeenLastCalledWith(undefined, 'strategy_a');
    expect(deskApi.tca).toHaveBeenLastCalledWith(60, 'strategy_a');
  });

});
