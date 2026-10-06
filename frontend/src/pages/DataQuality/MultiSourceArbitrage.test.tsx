import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MultiSourceArbitragePanel from './MultiSourceArbitragePanel';
import type { MultiSourceArbitrageResult } from '@/api/production';

const h = vi.hoisted(() => ({
  multiSourceArbitrage: vi.fn(),
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: h.setOption,
    dispose: h.dispose,
    resize: h.resize,
  }),
}));

vi.mock('@/api/production', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/production')>();
  return {
    ...actual,
    opsApi: {
      ...actual.opsApi,
      multiSourceArbitrage: h.multiSourceArbitrage,
    },
  };
});

describe('MultiSourceArbitragePanel 多源行情对账组件', () => {
  const mockResult: MultiSourceArbitrageResult = {
    trade_date: '2026-09-30',
    total_checked: 50,
    consistent_count: 48,
    tolerable_count: 2,
    discrepancy_count: 0,
    tolerance_corridor_pct: 0.3,
    mean_deviation_pct: 0.0012,
    max_deviation_pct: 0.045,
    items: [
      {
        symbol: '000001.SZ',
        name: '平安银行',
        trade_date: '2026-09-30',
        primary_source: 'eastmoney_universe',
        primary_price: 11.57,
        secondary_source: 'akshare',
        secondary_price: 11.57,
        deviation_pct: 0.0,
        status: 'CONSISTENT',
        status_label: '完全一致',
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('成功渲染对账总览指标、容差走廊与散点列表', async () => {
    h.multiSourceArbitrage.mockResolvedValueOnce(mockResult);

    render(<MultiSourceArbitragePanel />);

    await waitFor(() => {
      expect(screen.getByText('多源行情高频交叉对账与偏离度仲裁（Eastmoney vs AKShare 双源稽核）')).toBeInTheDocument();
      expect(screen.getByText('50 只')).toBeInTheDocument(); // total checked
      expect(screen.getByText('48 只')).toBeInTheDocument(); // consistent count
      expect(screen.getByText('2 只')).toBeInTheDocument(); // tolerable count
      expect(screen.getByText('0.0012%')).toBeInTheDocument(); // mean dev
      expect(screen.getByText('000001.SZ')).toBeInTheDocument();
      expect(screen.getByText('完全一致')).toBeInTheDocument();
    });
  });
});
