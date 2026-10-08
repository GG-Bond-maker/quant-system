import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { WatchlistRiskRadar } from './WatchlistRiskRadar';
import { watchlistApi } from '@/api/watchlist';
import type { WatchlistRiskResponse } from '@/types/watchlist';

vi.mock('@/api/watchlist', () => ({
  watchlistApi: {
    riskSummary: vi.fn(),
  },
}));

describe('WatchlistRiskRadar', () => {
  const mockSymbols = ['600519.SH', '000001.SZ', '510300'];

  const mockResponse: WatchlistRiskResponse = {
    status: 'ok',
    summary: {
      asset_count: 3,
      valid_count: 3,
      window_days: 120,
      start_date: '2026-04-01',
      end_date: '2026-09-30',
      portfolio_ann_vol: 16.85,
      portfolio_beta: 1.05,
      benchmark_corr: 0.88,
      max_drawdown: 11.42,
      var_95: 2.15,
      cvar_95: 3.25,
      diversification_ratio: 1.42,
      top_industry: '食品饮料',
      top_industry_ratio: 33.33,
      effective_sectors: 3.0,
    },
    industry_breakdown: [
      { industry: '食品饮料', weight: 33.33, count: 1, symbols: ['600519.SH'] },
      { industry: '银行', weight: 33.33, count: 1, symbols: ['000001.SZ'] },
      { industry: 'ETF指数基金', weight: 33.34, count: 1, symbols: ['510300'] },
    ],
    assets: [
      {
        symbol: '600519.SH',
        code: '600519',
        name: '贵州茅台',
        type: 'stock',
        industry: '食品饮料',
        weight: 33.33,
        ann_vol: 22.1,
        beta: 0.95,
        mdd: 14.5,
        period_return: 5.6,
      },
      {
        symbol: '000001.SZ',
        code: '000001',
        name: '平安银行',
        type: 'stock',
        industry: '银行',
        weight: 33.33,
        ann_vol: 18.2,
        beta: 0.88,
        mdd: 9.8,
        period_return: -2.1,
      },
      {
        symbol: '510300',
        code: '510300',
        name: '300ETF',
        type: 'etf',
        industry: 'ETF指数基金',
        weight: 33.34,
        ann_vol: 15.6,
        beta: 1.0,
        mdd: 8.2,
        period_return: 3.4,
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('正确渲染自选池组合风险度量与行业集中度', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValueOnce(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    expect(screen.getByText('自选池组合即时风险透视')).toBeInTheDocument();

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120);
    });

    await waitFor(() => {
      expect(screen.getByText('16.85%')).toBeInTheDocument();
      expect(screen.getByText('1.05')).toBeInTheDocument();
      expect(screen.getByText('1.42x')).toBeInTheDocument();
      expect(screen.getByText('-11.42%')).toBeInTheDocument();
      expect(screen.getByText('33.3%')).toBeInTheDocument();
    });

    // 行业暴露与标签
    expect(screen.getByText('自选池行业暴露构成')).toBeInTheDocument();
    expect(screen.getByText('食品饮料')).toBeInTheDocument();
    expect(screen.getByText('ETF指数基金')).toBeInTheDocument();
  });

  it('切换回看窗口会触发重新拉取风险度量', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValue(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120);
    });

    // 点击 60日
    const btn60 = screen.getByText('60日');
    fireEvent.click(btn60);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 60);
    });
  });

  it('展开各标的明细表格能显示成分股的波动率与Beta贡献', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValueOnce(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    await waitFor(() => {
      expect(screen.getByText('16.85%')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByText(/展开各标的风险贡献明细/);
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByText('贵州茅台')).toBeInTheDocument();
      expect(screen.getByText('平安银行')).toBeInTheDocument();
      expect(screen.getByText('300ETF')).toBeInTheDocument();
      expect(screen.getByText('22.10%')).toBeInTheDocument();
      expect(screen.getByText('18.20%')).toBeInTheDocument();
    });
  });

  it('数据降级时诚实披露原因，杜绝假数据', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValueOnce({
      status: 'degraded',
      reason: '所选标的历史日线样本不足',
      summary: {
        asset_count: 2,
        valid_count: 0,
        window_days: 120,
        start_date: null,
        end_date: null,
        portfolio_ann_vol: null,
        portfolio_beta: null,
        benchmark_corr: null,
        max_drawdown: null,
        diversification_ratio: null,
        top_industry: null,
        top_industry_ratio: null,
        effective_sectors: null,
      },
      industry_breakdown: [],
      assets: [],
    });

    render(<WatchlistRiskRadar symbols={['600519.SH', '000001.SZ']} totalSymbolsCount={2} />);

    await waitFor(() => {
      expect(screen.getByText(/数据降级：所选标的历史日线样本不足/)).toBeInTheDocument();
    });
  });
});
