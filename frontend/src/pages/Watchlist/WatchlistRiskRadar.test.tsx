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

// Mock echarts to avoid canvas error in jsdom
vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
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
      weights_mode: 'equal',
      weights_comparison: {
        equal: { ann_vol: 16.85, ann_return: 8.5, sharpe: 0.39, mdd: 11.42, dr: 1.42, beta: 1.05 },
        inv_vol: { ann_vol: 14.2, ann_return: 7.9, sharpe: 0.42, mdd: 9.8, dr: 1.55, beta: 0.96 },
        cap: { ann_vol: 18.1, ann_return: 9.2, sharpe: 0.40, mdd: 12.5, dr: 1.32, beta: 1.12 },
      },
      factor_radar: {
        size: 72.0,
        value: 65.0,
        momentum: 55.0,
        volatility: 60.0,
        quality: 68.0,
        liquidity: 65.0,
        beta: 52.0,
        diagnosis: '当前自选池具有显著的大盘价值与低波动风格，防守属性扎实，配置稳健。',
      },
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
        weights: { equal: 33.33, inv_vol: 28.5, cap: 50.0 },
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
        weights: { equal: 33.33, inv_vol: 33.5, cap: 25.0 },
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
        weights: { equal: 33.34, inv_vol: 38.0, cap: 25.0 },
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
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120, 'equal');
    });

    await waitFor(() => {
      expect(screen.getAllByText('16.85%').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('1.05').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('1.42x').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('-11.42%').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('33.3%')).toBeInTheDocument();
    });

    // 行业暴露与标签
    expect(screen.getByText('自选池行业暴露构成')).toBeInTheDocument();
    expect(screen.getByText('食品饮料')).toBeInTheDocument();
    expect(screen.getByText('ETF指数基金')).toBeInTheDocument();

    // Barra 风格雷达诊断
    expect(screen.getByText(/当前自选池具有显著的大盘价值与低波动风格/)).toBeInTheDocument();
    expect(screen.getByText('动态多权重模拟对比')).toBeInTheDocument();
  });

  it('切换回看窗口会触发重新拉取风险度量', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValue(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120, 'equal');
    });

    // 点击 60日
    const btn60 = screen.getByText('60日');
    fireEvent.click(btn60);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 60, 'equal');
    });
  });

  it('切换权重方案会触发重新拉取', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValue(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120, 'equal');
    });

    // 点击 逆波动率 (风险平价)
    const btnInvVol = screen.getByRole('button', { name: '逆波动率 (风险平价)' });
    fireEvent.click(btnInvVol);

    await waitFor(() => {
      expect(watchlistApi.riskSummary).toHaveBeenCalledWith(mockSymbols, 120, 'inv_vol');
    });
  });

  it('展开各标的明细表格能显示成分股的波动率与Beta贡献', async () => {
    vi.mocked(watchlistApi.riskSummary).mockResolvedValueOnce(mockResponse);

    render(<WatchlistRiskRadar symbols={mockSymbols} totalSymbolsCount={3} />);

    await waitFor(() => {
      expect(screen.getAllByText('16.85%').length).toBeGreaterThanOrEqual(1);
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
