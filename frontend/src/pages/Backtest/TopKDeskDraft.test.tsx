import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import TopKPanel from './TopKPanel';
import { backtestApi } from '@/api/backtest';
vi.mock('@/api/backtest', () => ({ backtestApi: { runTopK: vi.fn() } }));
vi.mock('@/api/datacenter', () => ({ datacenterApi: { datasets: vi.fn().mockResolvedValue({ items: [] }) } }));
vi.mock('@/utils/useChart', () => ({ useChart: () => ({ current: null }) }));
beforeEach(() => sessionStorage.clear());
it('回测来源写入可审核草稿，缺少匹配信号的持仓不编造来源', async () => {
  vi.mocked(backtestApi.runTopK).mockResolvedValue({
    start: '2026-01-01', end: '2026-02-01', model_version: 'm1', backtest_id: '41', run_id: 'bt-run', strategy_version: 's1',
    signal_sources: [{ symbol: '000001.SZ', date: '2026-01-30', model_version: 'm1', signal_id: 'sig1', run_id: 'sig-run', feature_version: 'v3', label_horizon: 5 }],
    holdings: [{ date: '2026-02-01', holdings: { '000001.SZ': 0.5, '600519.SH': 0.5 } }],
    metrics: { annual_return: 0, max_drawdown: 0, annual_vol: 0, win_rate: 0 }, equity_curve: [], drawdown_curve: [], annual_returns: {}, trades: [], friction_costs: {}, rejected_trades: {},
  } as any);
  render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><TopKPanel /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: '运行 Top-K 回测' }));
  fireEvent.click(await screen.findByRole('button', { name: '审核交易台草稿' }));
  await waitFor(() => expect(sessionStorage.getItem('aqp:desk:batch_import')).toBeTruthy());
  const draft = JSON.parse(sessionStorage.getItem('aqp:desk:batch_import')!);
  expect(draft.orders).toHaveLength(1);
  expect(draft.orders[0]).toMatchObject({ symbol: '000001.SZ', backtest_id: '41', strategy_version: 's1', model_version: 'm1', signal_id: 'sig1', run_id: 'sig-run' });
  expect(draft.orders[0].client_order_id).toBeTruthy();
});
