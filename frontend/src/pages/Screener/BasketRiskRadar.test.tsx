import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BasketRiskRadar from './BasketRiskRadar';
import type { ScreenerItem } from '@/types/p1';

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

const MOCK_ITEMS: ScreenerItem[] = [
  {
    symbol: '600519.SH',
    name: '贵州茅台',
    industry: '食品饮料',
    close: 1600,
    pct: 1.5,
    amount: 5e8,
    turnover: 1.2,
    limit_pct: 10,
    score: 0.015,
    signal_strength: 'strong',
  },
  {
    symbol: '000858.SZ',
    name: '五粮液',
    industry: '食品饮料',
    close: 140,
    pct: 2.1,
    amount: 3e8,
    turnover: 2.0,
    limit_pct: 10,
    score: 0.012,
    signal_strength: 'strong',
  },
  {
    symbol: '300750.SZ',
    name: '宁德时代',
    industry: '电力设备',
    close: 220,
    pct: -0.5,
    amount: 4e8,
    turnover: 2.5,
    limit_pct: 20,
    score: 0.009,
    signal_strength: 'neutral',
  },
];

describe('BasketRiskRadar 组件', () => {
  it('正确统计行业 HHI 指数与前三大重仓行业', () => {
    render(<BasketRiskRadar items={MOCK_ITEMS} />);

    expect(screen.getByText('选股组合风格与行业风险透视')).toBeDefined();
    expect(screen.getByText(/篮子标的数: 3 只/)).toBeDefined();
    expect(screen.getByText(/食品饮料/)).toBeDefined();
    expect(screen.getByText(/电力设备/)).toBeDefined();
  });

  it('空候选池时安全渲染（返回 null）', () => {
    const { container } = render(<BasketRiskRadar items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
