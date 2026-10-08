import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import QuintileSpreadsCard from './QuintileSpreadsCard';
import { screenerApi } from '@/api/screener';
import type { QuintileSpreadsResult } from '@/types/p1';

vi.mock('@/api/screener', () => ({
  screenerApi: {
    quintileSpreads: vi.fn(),
  },
}));

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

const MOCK_DATA: QuintileSpreadsResult = {
  status: 'ok',
  days: 30,
  board: 'all',
  dates: ['2026-09-01', '2026-09-02', '2026-09-03'],
  q1: [1.0, 1.02, 1.05],
  q2: [1.0, 1.01, 1.03],
  q3: [1.0, 1.0, 1.01],
  q4: [1.0, 0.99, 0.98],
  q5: [1.0, 0.98, 0.95],
  long_short: [1.0, 1.04, 1.10],
  metrics: {
    q1_total_ret: 5.0,
    q5_total_ret: -5.0,
    spread_total_ret: 10.0,
    spread_sharpe: 2.15,
    spread_max_dd: 1.5,
    monotonicity_score: 80.0,
    sample_count: 3,
  },
};

describe('QuintileSpreadsCard 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('成功渲染分层单调性核心指标与标签', async () => {
    vi.mocked(screenerApi.quintileSpreads).mockResolvedValue(MOCK_DATA);

    render(<QuintileSpreadsCard board="all" />);

    await waitFor(() => {
      expect(screen.getByText('模型分层单调性与多空走势')).toBeDefined();
      expect(screen.getByText(/单调得分 80分 · 强单调性/)).toBeDefined();
      expect(screen.getByText('+10.00%')).toBeDefined();
      expect(screen.getByText('2.15')).toBeDefined();
    });
  });

  it('展示空态或错误提示', async () => {
    vi.mocked(screenerApi.quintileSpreads).mockRejectedValue(new Error('网络超时'));

    render(<QuintileSpreadsCard board="all" />);

    await waitFor(() => {
      expect(screen.getByText('网络超时')).toBeDefined();
    });
  });
});
