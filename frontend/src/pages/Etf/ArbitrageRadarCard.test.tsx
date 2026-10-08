import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ArbitrageRadarCard from './ArbitrageRadarCard';
import type { EtfArbitrageData, EtfItem } from '@/types/etf';

const api = vi.hoisted(() => ({
  arbitrage: vi.fn(),
}));

vi.mock('@/api/etf', () => ({
  etfApi: api,
}));

const sampleHighPremium: EtfItem = {
  code: '513500',
  name: '标普500ETF',
  country: 'cn',
  exchange: 'SH',
  type: '股票型',
  board: '跨境ETF',
  tracking_index: 'SPX',
  manager: '博时基金',
  inception: '2014-04-01',
  price: 2.688,
  pct: 1.25,
  amount: 25e8,
  size_yi: 120,
  quote_status: 'ok',
  overseas: null,
  iopv: 2.447,
  premium_rate: 9.85,
  turnover: 6.2,
};

const sampleDiscount: EtfItem = {
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
  pct: -0.5,
  amount: 35e8,
  size_yi: 3000,
  quote_status: 'ok',
  overseas: null,
  iopv: 4.04,
  premium_rate: -1.49,
  turnover: 1.1,
};

const mockArbitrageData: EtfArbitrageData = {
  summary: {
    sample_count: 850,
    median_premium: 0.12,
    avg_premium: 0.28,
    positive_count: 510,
    negative_count: 340,
  },
  high_premium: [sampleHighPremium],
  high_discount: [sampleDiscount],
};

function renderCard(onSelectCode?: (code: string) => void) {
  return render(
    <MemoryRouter initialEntries={['/etf']}>
      <Routes>
        <Route path="/etf" element={<ArbitrageRadarCard onSelectCode={onSelectCode} />} />
        <Route path="/etf/:code" element={<div>ETF_PAGE_STUB</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ArbitrageRadarCard 折溢价套利雷达组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.arbitrage.mockResolvedValue(mockArbitrageData);
  });

  it('挂载时请求 /api/v1/etf/arbitrage 并渲染统计指标及高溢价标的', async () => {
    renderCard();

    expect(await screen.findByText('折溢价套利雷达')).toBeInTheDocument();
    expect(screen.getByText('样本 850')).toBeInTheDocument();
    expect(screen.getByText('正溢价 510')).toBeInTheDocument();
    expect(screen.getByText('折价 340')).toBeInTheDocument();

    // 默认展示高溢价预警标的
    expect(screen.getByText('标普500ETF')).toBeInTheDocument();
    expect(screen.getByText('+9.85%')).toBeInTheDocument();
  });

  it('支持切换至「折价安全垫」Tab 并渲染高折价标的', async () => {
    renderCard();
    await screen.findByText('标普500ETF');

    const discountBtn = screen.getByRole('button', { name: '折价安全垫' });
    fireEvent.click(discountBtn);

    expect(await screen.findByText('沪深300ETF')).toBeInTheDocument();
    expect(screen.getByText('-1.49%')).toBeInTheDocument();
  });

  it('点击表格行触发 onSelectCode 回调，代码点击具有链接跳转能力', async () => {
    const onSelect = vi.fn();
    renderCard(onSelect);

    await screen.findByText('标普500ETF');
    const row = screen.getByText('标普500ETF').closest('tr');
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    expect(onSelect).toHaveBeenCalledWith('513500');

    // 代码为链接，跳转至详情
    const link = screen.getByRole('link', { name: '513500' });
    expect(link).toHaveAttribute('href', '/etf/513500');
    fireEvent.click(link);
    expect(await screen.findByText('ETF_PAGE_STUB')).toBeInTheDocument();
  });
});
