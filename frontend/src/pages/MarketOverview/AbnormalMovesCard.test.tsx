import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AbnormalMovesCard from './AbnormalMovesCard';

// 模拟 SWR useApi hook
vi.mock('@/api/swr', () => ({
  useApi: vi.fn(),
}));

import { useApi } from '@/api/swr';

const MOCK_ABNORMAL_DATA = {
  auction: [
    {
      symbol: '600519.SH',
      name: '贵州茅台',
      open_price: 1800.0,
      pre_close: 1700.0,
      change_pct: 5.88,
      auction_volume: 5000,
      auction_turnover: 9000000,
      signal_type: 'high_open',
      description: '集合竞价大幅高开 +5.88%',
    },
  ],
  intraday: [
    {
      symbol: '300750.SZ',
      name: '宁德时代',
      price: 240.0,
      change_pct: 7.0,
      surge_pct: -4.0,
      signal_type: 'limit_break',
      timestamp: '10:15:00',
      description: '涨停开板（炸板），自涨停回落 -4.00%',
    },
  ],
  deviation: [
    {
      symbol: '688981.SH',
      name: '中芯国际',
      board: 'chinext_star',
      cum_3d_pct: 28.5,
      index_3d_pct: 1.5,
      deviation_3d_pct: 27.0,
      threshold_3d_pct: 30.0,
      ratio_3d: 0.9,
      severity: 'warning',
      detail: '接近严重异常波动门槛 70% 临界线 (偏离 +27.00% / 门槛 ±30%)',
    },
  ],
  total_signals: 3,
  as_of: '2026-10-19 15:00:00',
};

describe('AbnormalMovesCard 全时段异动与监管偏离雷达组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (useApi as any).mockReturnValue({
      data: MOCK_ABNORMAL_DATA,
      isValidating: false,
      mutate: vi.fn(),
    });
  });

  it('默认渲染竞价异动 Tab 及其指标列表', () => {
    render(
      <MemoryRouter>
        <AbnormalMovesCard />
      </MemoryRouter>
    );

    expect(screen.getByText('全时段异动与监管偏离雷达')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('+5.88%')).toBeInTheDocument();
    expect(screen.getByText('集合竞价大幅高开 +5.88%')).toBeInTheDocument();
  });

  it('支持切换至盘中脉冲 Tab 并展示分时拉升/炸板', () => {
    render(
      <MemoryRouter>
        <AbnormalMovesCard />
      </MemoryRouter>
    );

    const intradayTabBtn = screen.getByRole('button', { name: /盘中脉冲/i });
    fireEvent.click(intradayTabBtn);

    expect(screen.getByText('宁德时代')).toBeInTheDocument();
    expect(screen.getByText('涨停开板(炸板)')).toBeInTheDocument();
    expect(screen.getByText('10:15:00')).toBeInTheDocument();
  });

  it('支持切换至交易所偏离风险 Tab 并展示监管偏离值与严重等级', () => {
    render(
      <MemoryRouter>
        <AbnormalMovesCard />
      </MemoryRouter>
    );

    const deviationTabBtn = screen.getByRole('button', { name: /交易所偏离风险/i });
    fireEvent.click(deviationTabBtn);

    expect(screen.getByText('中芯国际')).toBeInTheDocument();
    expect(screen.getByText('+27.00%')).toBeInTheDocument();
    expect(screen.getByText('90.0%')).toBeInTheDocument();
    expect(screen.getByText('预警临界')).toBeInTheDocument();
  });
});
