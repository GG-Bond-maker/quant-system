import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import Watchlist from './index';
import { watchlistApi } from '@/api/watchlist';
import type { WatchDashboard } from '@/types/watchlist';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/api/watchlist', () => ({
  watchlistApi: {
    dashboard: vi.fn(),
    correlation: vi.fn(),
    riskSummary: vi.fn(),
  },
}));

// Mock echarts to prevent canvas error in jsdom
vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}));

describe('Watchlist 自选池多资产与多视图矩阵', () => {
  const mockDashboard: WatchDashboard = {
    status: 'ok',
    summary: {
      count: 4,
      stock_count: 1,
      etf_count: 1,
      futures_count: 1,
      hk_count: 1,
      avg_pct: 1.25,
      flow_total_yi: 12.5,
      alert_count: 1,
      alert_kinds: ['突破MA20'],
      quote_date: '2026-10-09',
    },
    items: [
      {
        symbol: '600519.SH',
        code: '600519',
        name: '贵州茅台',
        type: 'stock',
        close: 1680.0,
        pct: 2.15,
        amount_yi: 45.2,
        kline_state: '均线多头',
        alert: '突破MA20',
        date: '2026-10-09',
        closes: [1650, 1660, 1680],
        pe: 28.5,
        pb: 8.2,
        dividend_yield: 2.8,
        roe: 30.5,
        turnover_rate: 0.85,
        alpha_score: 88,
        momentum_20: 6.5,
        volatility_20: 18.2,
        rsi_14: 62.5,
        mdd_30: 4.8,
        source: 'local',
      },
      {
        symbol: '510300',
        code: '510300',
        name: '300ETF',
        type: 'etf',
        close: 3.98,
        pct: 0.85,
        amount_yi: 25.0,
        kline_state: '平稳',
        alert: null,
        date: '2026-10-09',
        closes: [3.95, 3.96, 3.98],
        pe: null,
        pb: null,
        alpha_score: 65,
        momentum_20: 3.2,
        volatility_20: 15.0,
        rsi_14: 55.0,
        mdd_30: 3.2,
        source: 'local',
      },
      {
        symbol: 'IF',
        code: 'IF',
        name: '沪深300股指期货主力',
        type: 'futures',
        close: 3950.0,
        pct: 1.15,
        amount_yi: 85.0,
        kline_state: '放量突破',
        alert: '放量异动',
        date: '2026-10-09',
        closes: [3900, 3920, 3950],
        pe: null,
        pb: null,
        alpha_score: 75,
        momentum_20: 5.8,
        volatility_20: 20.5,
        rsi_14: 58.0,
        mdd_30: 5.1,
        source: 'local',
      },
      {
        symbol: '00700.HK',
        code: '00700',
        name: '腾讯控股',
        type: 'hk',
        close: 380.0,
        pct: 1.5,
        amount_yi: 60.0,
        kline_state: '均线多头',
        alert: null,
        date: '2026-10-09',
        closes: [370, 375, 380],
        pe: 22.0,
        pb: 3.5,
        dividend_yield: 1.2,
        roe: 18.0,
        turnover_rate: 1.1,
        alpha_score: 82,
        momentum_20: 7.2,
        volatility_20: 24.0,
        rsi_14: 64.0,
        mdd_30: 6.0,
        source: 'local',
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(watchlistApi.dashboard).mockResolvedValue(mockDashboard);
    vi.mocked(watchlistApi.riskSummary).mockResolvedValue({
      status: 'ok',
      summary: {
        asset_count: 4,
        valid_count: 4,
        window_days: 120,
        start_date: '2026-04-01',
        end_date: '2026-10-09',
        portfolio_ann_vol: 17.5,
        portfolio_beta: 1.02,
        benchmark_corr: 0.9,
        max_drawdown: 10.5,
        diversification_ratio: 1.35,
        top_industry: '科技与金融',
        top_industry_ratio: 40.0,
        effective_sectors: 3.2,
        weights_mode: 'equal',
      },
      industry_breakdown: [],
      assets: [],
    });
  });

  it('展示多资产分类 Tab 并正确过滤', async () => {
    render(<Watchlist />);

    await waitFor(() => {
      expect(screen.getByText(/全部 \(4\)/)).toBeInTheDocument();
      expect(screen.getByText(/A股 \(1\)/)).toBeInTheDocument();
      expect(screen.getByText(/ETF \(1\)/)).toBeInTheDocument();
      expect(screen.getByText(/期货主力 \(1\)/)).toBeInTheDocument();
      expect(screen.getByText(/港股通 \(1\)/)).toBeInTheDocument();
    });

    // 默认展示全部 4 只标的
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('300ETF')).toBeInTheDocument();
    expect(screen.getByText('沪深300股指期货主力')).toBeInTheDocument();
    expect(screen.getByText('腾讯控股')).toBeInTheDocument();

    // 点击期货主力 Tab
    fireEvent.click(screen.getByText(/期货主力 \(1\)/));
    expect(screen.getByText('沪深300股指期货主力')).toBeInTheDocument();
    expect(screen.queryByText('贵州茅台')).not.toBeInTheDocument();
    expect(screen.queryByText('腾讯控股')).not.toBeInTheDocument();
  });

  it('支持在 4 大专业视图矩阵之间自由切换', async () => {
    render(<Watchlist />);

    await waitFor(() => {
      expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    });

    // 1. 默认交易行情视图
    expect(screen.getByText('K线多空形态')).toBeInTheDocument();

    // 2. 切换到量化因子视图
    const factorBtn = screen.getByRole('button', { name: /量化因子/ });
    fireEvent.click(factorBtn);

    expect(screen.getByText(/多头评分/)).toBeInTheDocument();
    expect(screen.getByText(/20日动量/)).toBeInTheDocument();
    expect(screen.getByText(/年化波动\(20D\)/)).toBeInTheDocument();
    expect(screen.getByText('88')).toBeInTheDocument(); // Alpha score for 茅台

    // 3. 切换到估值基本面视图
    const fundamentalBtn = screen.getByRole('button', { name: /估值基本面/ });
    fireEvent.click(fundamentalBtn);

    expect(screen.getByText(/PE\(TTM\)/)).toBeInTheDocument();
    expect(screen.getByText(/PB\(市净率\)/)).toBeInTheDocument();
    expect(screen.getByText(/股息率/)).toBeInTheDocument();
    expect(screen.getByText('28.50')).toBeInTheDocument(); // 茅台 PE

    // 4. 切换到风险与衍生视图
    const riskBtn = screen.getByRole('button', { name: /风险与衍生/ });
    fireEvent.click(riskBtn);

    expect(screen.getByText(/20日年化波动率/)).toBeInTheDocument();
    expect(screen.getByText(/30日区间最大回撤/)).toBeInTheDocument();
  });

  it('期货标的点击深度分析正确路由到期货中心', async () => {
    render(<Watchlist />);

    await waitFor(() => {
      expect(screen.getByText('沪深300股指期货主力')).toBeInTheDocument();
    });

    // 找到期货的深度分析按钮
    const rows = screen.getAllByRole('row');
    const futuresRow = rows.find((r) => r.textContent?.includes('沪深300股指期货主力'));
    expect(futuresRow).toBeDefined();

    const analysisBtn = futuresRow?.querySelector('button:first-of-type');
    expect(analysisBtn).toBeDefined();
    if (analysisBtn) {
      fireEvent.click(analysisBtn);
      expect(mockNavigate).toHaveBeenCalledWith('/futures/analysis?symbol=IF');
    }
  });
});
