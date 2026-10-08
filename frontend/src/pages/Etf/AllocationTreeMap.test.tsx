import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AllocationTreeMap from './AllocationTreeMap';
import type { EtfAllocationData } from '@/types/etf';

const h = vi.hoisted(() => ({
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: () => ({
    setOption: h.setOption,
    dispose: h.dispose,
    resize: h.resize,
  }),
  getInstanceByDom: () => ({
    on: h.on,
    off: h.off,
  }),
}));

const api = vi.hoisted(() => ({
  allocation: vi.fn(),
}));

vi.mock('@/api/etf', () => ({
  etfApi: api,
}));

const mockAllocationData: EtfAllocationData = {
  total_size_yi: 18500,
  board_count: 2,
  boards: [
    {
      name: '宽基ETF',
      count: 2,
      size_yi: 12000,
      amount_yi: 150,
      avg_pct: 0.85,
      children: [
        { code: '510300', name: '沪深300ETF', size_yi: 3500, pct: 1.1, price: 3.98, country: 'cn' },
        { code: '510500', name: '中证500ETF', size_yi: 1800, pct: 0.6, price: 5.6, country: 'cn' },
      ],
    },
    {
      name: '行业ETF',
      count: 1,
      size_yi: 4500,
      amount_yi: 80,
      avg_pct: -0.42,
      children: [
        { code: '512880', name: '证券ETF', size_yi: 900, pct: -0.8, price: 1.05, country: 'cn' },
      ],
    },
  ],
};

describe('AllocationTreeMap 大类资产配置树图组件', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.allocation.mockResolvedValue(mockAllocationData);
  });

  it('挂载时请求 /api/v1/etf/allocation 并初始化 ECharts Treemap 渲染', async () => {
    render(<AllocationTreeMap height={280} />);

    await waitFor(() => {
      expect(api.allocation).toHaveBeenCalledTimes(1);
      expect(h.setOption).toHaveBeenCalled();
    });

    expect(screen.getByText(/面积 = 资产规模\(AUM\)/)).toBeInTheDocument();
    expect(screen.getByText('统计规模: 18500 亿元')).toBeInTheDocument();

    const lastCall = h.setOption.mock.calls[h.setOption.mock.calls.length - 1][0];
    expect(lastCall.series[0].type).toBe('treemap');
    expect(lastCall.series[0].data).toHaveLength(2);
    expect(lastCall.series[0].data[0].name).toContain('宽基ETF');
  });

  it('数据为空时优雅呈现空状态', async () => {
    api.allocation.mockResolvedValue({ total_size_yi: 0, board_count: 0, boards: [] });
    render(<AllocationTreeMap height={280} />);

    expect(await screen.findByText('暂无大类资产数据')).toBeInTheDocument();
  });
});
