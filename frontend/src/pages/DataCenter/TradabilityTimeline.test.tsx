import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TradabilityTimelinePanel from './TradabilityTimelinePanel';
import type { TradabilityTimelineResult } from '@/api/datacenter';

const h = vi.hoisted(() => ({
  tradabilityTimeline: vi.fn(),
}));

vi.mock('@/api/datacenter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/datacenter')>();
  return {
    ...actual,
    datacenterApi: {
      ...actual.datacenterApi,
      tradabilityTimeline: h.tradabilityTimeline,
    },
  };
});

describe('TradabilityTimelinePanel 标的可交易性时序组件', () => {
  const mockResult: TradabilityTimelineResult = {
    symbol: '000001.SZ',
    name: '平安银行',
    total_days: 250,
    normal_days: 245,
    suspended_days: 2,
    st_days: 0,
    limit_up_days: 2,
    limit_down_days: 1,
    tradable_ratio: 0.992,
    timeline: [
      {
        date: '2026-09-29',
        status: 'NORMAL',
        status_label: '正常交易',
        close: 11.35,
        limit_up: 12.49,
        limit_down: 10.22,
        is_halted: false,
        is_st: false,
      },
      {
        date: '2026-09-30',
        status: 'LIMIT_UP',
        status_label: '涨停封板',
        close: 11.57,
        limit_up: 11.57,
        limit_down: 10.22,
        is_halted: false,
        is_st: false,
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('成功渲染标的名称、可交易性比率与状态机统计', async () => {
    h.tradabilityTimeline.mockResolvedValueOnce(mockResult);

    render(<TradabilityTimelinePanel />);

    await waitFor(() => {
      expect(screen.getByText('标的全生命周期可交易性状态机（停牌 · ST · 涨跌停封板时序）')).toBeInTheDocument();
      expect(screen.getByText('99.2%')).toBeInTheDocument(); // tradable ratio
      expect(screen.getByText('245 天')).toBeInTheDocument(); // normal days
      expect(screen.getByText('2 天')).toBeInTheDocument(); // suspended days
      expect(screen.getByText('2 涨停')).toBeInTheDocument(); // limit up
      expect(screen.getByText('1 跌停')).toBeInTheDocument(); // limit down
    });
  });
});
