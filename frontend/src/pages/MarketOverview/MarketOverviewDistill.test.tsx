import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import MarketOverview from './index';

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}));

vi.mock('@/hooks/useMarketSession', () => ({
  isMarketOpen: () => false,
}));

vi.mock('@/api/market', () => ({
  marketApi: {
    overviewRt: vi.fn(),
    limitLadder: vi.fn().mockResolvedValue({
      total_limit_up: 55,
      total_broken: 12,
      max_boards: 7,
      broken_rate: 17.9,
      tiers: [],
      broken_stocks: [],
    }),
  },
  OVERVIEW_TIMEOUT: 30000,
}));

vi.mock('@/api/swr', () => ({
  useApi: vi.fn((url: string | null) => {
    if (!url || typeof url !== 'string') {
      return { data: null, isLoading: false, error: null, mutate: vi.fn() };
    }
    if (url.includes('/rt')) {
      return {
        data: {
          trade_date: '20261023',
          as_of: '15:00:00',
          indices: { status: 'ok', items: [] },
          heat: { status: 'ok', total_amount_yi: 18000, up_count: 3200, down_count: 1500 },
          sentiment: { status: 'ok', score: 68 },
          speculation: { status: 'ok', limit_up_count: 55, limit_down_count: 2 },
          sectors: { status: 'ok', items: [] },
          from_cache: false,
          stale: false,
        },
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      };
    }
    if (url.includes('/daily')) {
      return {
        data: {
          trade_date: '20261023',
          pred_dates: ['20261022', '20261023'],
          style_factors: { status: 'ok', items: [] },
          valuation: { status: 'ok', csi300: { pe_ttm: 12.5 } },
          recommend: { status: 'ok', items: [] },
        },
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      };
    }
    if (url.includes('/global')) {
      return {
        data: {
          status: 'ok',
          items: [
            { code: '.DJI', name: '道琼斯', close: 42000, pct: 0.5, data_date: '2026-10-23' },
          ],
        },
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      };
    }
    if (url.includes('/ah-spread')) {
      return {
        data: { total: 0, items: [], summary: {} },
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      };
    }
    return { data: null, isLoading: false, error: null, mutate: vi.fn() };
  }),
  REFRESH: { realtime: 15 },
}));

describe('MarketOverview Distill 早盘四宫格与全景按需折叠', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('默认激活「早盘四宫格」视图，避免 5000px 全景认知过载', () => {
    render(
      <MemoryRouter>
        <MarketOverview />
      </MemoryRouter>
    );

    // 默认早盘四宫格按钮处于激活态
    const morningTab = screen.getByRole('button', { name: /早盘四宫格/ });
    expect(morningTab).toBeInTheDocument();
    expect(morningTab.className).toContain('bg-brand-500');

    // 隔夜外盘基准概览默认处于收起状态以节省纵深空间
    expect(screen.getByText('展开外盘 ▼')).toBeInTheDocument();

    // 存在主力风向标切换栏（板块轮动 / 资金流向）
    expect(screen.getByText(/主力风向标/)).toBeInTheDocument();
  });

  it('点击展开外盘按钮能够切换外盘基准的展示与收起', () => {
    render(
      <MemoryRouter>
        <MarketOverview />
      </MemoryRouter>
    );

    const toggleGlobalBtn = screen.getByText('展开外盘 ▼');
    fireEvent.click(toggleGlobalBtn);

    // 变为收起外盘
    expect(screen.getByText('收起外盘 ▲')).toBeInTheDocument();
    expect(screen.getByText(/道琼斯/)).toBeInTheDocument();

    // 再次点击收起
    fireEvent.click(screen.getByText('收起外盘 ▲'));
    expect(screen.getByText('展开外盘 ▼')).toBeInTheDocument();
  });

  it('支持切换至全景总览，并在全景模式下提供一键全部收起/展开', () => {
    render(
      <MemoryRouter>
        <MarketOverview />
      </MemoryRouter>
    );

    // 切换至全景总览
    const allTab = screen.getByRole('button', { name: /全景总览/ });
    fireEvent.click(allTab);

    // 出现全景长卷控制条
    expect(screen.getByText('全景长卷模式')).toBeInTheDocument();
    expect(screen.getByText('全部收起')).toBeInTheDocument();
    expect(screen.getByText('全部展开')).toBeInTheDocument();

    // 切换后持久化至 localStorage
    expect(localStorage.getItem('aqp:market:tab')).toBe('all');
  });

  it('在全景模式下能够对单个 Section 进行按需折叠', () => {
    render(
      <MemoryRouter>
        <MarketOverview />
      </MemoryRouter>
    );

    const allTab = screen.getByRole('button', { name: /全景总览/ });
    fireEvent.click(allTab);

    // 初始状态下各 SectionTitle 有收起按钮
    const collapseBtns = screen.getAllByRole('button', { name: '收起 ▲' });
    expect(collapseBtns.length).toBeGreaterThan(0);

    // 点击第一个收起
    fireEvent.click(collapseBtns[0]);
    expect(screen.getAllByRole('button', { name: '展开 ▼' }).length).toBeGreaterThan(0);
  });
});
