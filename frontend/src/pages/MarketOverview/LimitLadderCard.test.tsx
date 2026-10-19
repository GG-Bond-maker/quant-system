import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LimitLadderCard from './LimitLadderCard';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/api/market', async () => {
  const actual = await vi.importActual('@/api/market');
  return {
    ...actual,
    marketApi: {
      limitLadder: vi.fn(),
    },
  };
});

import { marketApi } from '@/api/market';

const MOCK_LADDER_DATA = {
  as_of: '2026-10-19',
  total_limit_up: 32,
  total_broken: 8,
  max_boards: 6,
  broken_rate: 20.0,
  tiers: [
    {
      boards: 5,
      title: '5板及以上',
      count: 2,
      stocks: [
        {
          symbol: '000001.SZ',
          name: '高标先锋',
          price: 25.5,
          pct: 10.01,
          boards: 6,
          turnover: 15.2,
          amount: 850000000,
          status: 'limit_up',
        },
      ],
    },
    {
      boards: 4,
      title: '4连板',
      count: 1,
      stocks: [
        {
          symbol: '000002.SZ',
          name: '次席龙头',
          price: 18.2,
          pct: 9.98,
          boards: 4,
          turnover: 12.0,
          amount: 620000000,
          status: 'limit_up',
        },
      ],
    },
  ],
  broken_stocks: [
    {
      symbol: '000003.SZ',
      name: '炸板标的',
      price: 15.0,
      high: 15.6,
      pct: 5.5,
      boards: 0,
      turnover: 22.1,
      amount: 1100000000,
      status: 'broken',
    },
  ],
};

describe('LimitLadderCard 连板天梯与炸板池组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (marketApi.limitLadder as any).mockResolvedValue(MOCK_LADDER_DATA);
  });

  it('正确加载并渲染连板统计与高标梯队', async () => {
    render(
      <MemoryRouter>
        <LimitLadderCard />
      </MemoryRouter>,
    );

    expect(await screen.findByText('A 股连板天梯全景阵列')).toBeInTheDocument();
    expect(screen.getByText('高标先锋')).toBeInTheDocument();
    expect(screen.getByText('次席龙头')).toBeInTheDocument();
    expect(screen.getByText('5板及以上')).toBeInTheDocument();
    expect(screen.getByText('4连板')).toBeInTheDocument();
    expect(screen.getByText('6板')).toBeInTheDocument();
  });

  it('点击炸板池 Tab 切换至炸板预警标的', async () => {
    render(
      <MemoryRouter>
        <LimitLadderCard />
      </MemoryRouter>,
    );

    const brokenTab = await screen.findByRole('button', { name: /炸板池/i });
    fireEvent.click(brokenTab);

    expect(await screen.findByText('炸板标的')).toBeInTheDocument();
    expect(screen.getByText('￥15.00')).toBeInTheDocument();
  });

  it('点击股票卡片触发路由跳转至个股详情', async () => {
    render(
      <MemoryRouter>
        <LimitLadderCard />
      </MemoryRouter>,
    );

    const stockCard = await screen.findByText('高标先锋');
    fireEvent.click(stockCard.closest('button')!);

    expect(mockNavigate).toHaveBeenCalledWith('/stock/000001.SZ');
  });
});
