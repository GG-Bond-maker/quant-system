import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EtfBasketModal from './EtfBasketModal';
import type { EtfItem } from '@/types/etf';
import type { PortfolioBacktestResult } from '@/types/portfolio';

const h = vi.hoisted(() => ({
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: h.setOption,
    dispose: h.dispose,
    resize: h.resize,
  }),
}));

const portfolioMock = vi.hoisted(() => ({
  backtest: vi.fn(),
}));

vi.mock('@/api/portfolio', () => ({
  portfolioApi: portfolioMock,
}));

const mockEtfs: EtfItem[] = [
  {
    code: '510300',
    name: '沪深300ETF',
    country: 'cn',
    exchange: 'SH',
    type: '股票型',
    board: '宽基ETF',
    tracking_index: '000300.SH',
    manager: '华泰柏瑞',
    inception: '2012-05-04',
    price: 3.98,
    pct: 0.5,
    amount: 35e8,
    size_yi: 3000,
    quote_status: 'ok',
    overseas: null,
  },
  {
    code: '511010',
    name: '国债ETF',
    country: 'cn',
    exchange: 'SH',
    type: '债券型',
    board: '债券型',
    tracking_index: '000012.SH',
    manager: '国泰基金',
    inception: '2013-03-05',
    price: 130.5,
    pct: 0.02,
    amount: 5e8,
    size_yi: 150,
    quote_status: 'ok',
    overseas: null,
  },
  {
    code: '518880',
    name: '黄金ETF',
    country: 'cn',
    exchange: 'SH',
    type: '商品型',
    board: '商品型',
    tracking_index: 'AU9999',
    manager: '华安基金',
    inception: '2013-07-18',
    price: 5.62,
    pct: 0.8,
    amount: 12e8,
    size_yi: 200,
    quote_status: 'ok',
    overseas: null,
  },
];

const mockBacktestResult: PortfolioBacktestResult = {
  status: 'success',
  start_date: '2025-10-01',
  end_date: '2026-10-01',
  initial_cash: 1000000,
  rebalance: 'M',
  benchmark: '000300.SH',
  assets: [
    { code: '510300', type: 'etf', weight: 0.2 },
    { code: '511010', type: 'etf', weight: 0.5 },
    { code: '518880', type: 'etf', weight: 0.3 },
  ],
  trading_days: 242,
  metrics: {
    total_return: 18.5,
    cagr: 16.2,
    max_drawdown: 6.8,
    volatility: 8.5,
    sharpe: 1.65,
    calmar: 2.38,
    alpha: 8.2,
    beta: 0.35,
    risk_free: 2.0,
  },
  nav_curve: [
    { date: '2025-10-08', nav: 1.0, benchmark: 1.0 },
    { date: '2026-10-01', nav: 1.185, benchmark: 1.05 },
  ],
  drawdown_curve: [],
  annual_returns: [],
  holdings_drift: [],
};

describe('EtfBasketModal 多资产 ETF 篮子配置与风险平价回测', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    portfolioMock.backtest.mockResolvedValue(mockBacktestResult);
  });

  it('成功渲染已选标的、资产权重建议，并触发组合回测引擎', async () => {
    const onClose = vi.fn();
    render(<EtfBasketModal selectedEtfs={mockEtfs} onClose={onClose} />);

    expect(screen.getByText('多资产 ETF 篮子配置与风险平价回测')).toBeInTheDocument();
    expect(screen.getByText('已选 3 只标的')).toBeInTheDocument();
    expect(screen.getByText('沪深300ETF')).toBeInTheDocument();
    expect(screen.getByText('国债ETF')).toBeInTheDocument();
    expect(screen.getByText('黄金ETF')).toBeInTheDocument();

    await waitFor(() => {
      expect(portfolioMock.backtest).toHaveBeenCalledTimes(1);
    });

    // 验证回测指标 KPI 渲染
    expect(await screen.findByText('+16.20%')).toBeInTheDocument(); // 年化收益 (CAGR)
    expect(screen.getByText('-6.80%')).toBeInTheDocument(); // 最大回撤
    expect(screen.getByText('1.65')).toBeInTheDocument(); // 夏普比率
    expect(screen.getByText('+8.50%')).toBeInTheDocument(); // 年化波动率
  });

  it('切换配置模型至等权重 (1/N) 重新计算并触发回测', async () => {
    const onClose = vi.fn();
    render(<EtfBasketModal selectedEtfs={mockEtfs} onClose={onClose} />);

    await waitFor(() => expect(portfolioMock.backtest).toHaveBeenCalledTimes(1));

    const eqBtn = screen.getByRole('button', { name: '等权重 (1/N)' });
    fireEvent.click(eqBtn);

    await waitFor(() => {
      expect(portfolioMock.backtest).toHaveBeenCalledTimes(2);
    });
  });

  it('点击关闭按钮触发 onClose 回调', () => {
    const onClose = vi.fn();
    render(<EtfBasketModal selectedEtfs={mockEtfs} onClose={onClose} />);

    const closeBtn = screen.getByRole('button', { name: '✕' });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
