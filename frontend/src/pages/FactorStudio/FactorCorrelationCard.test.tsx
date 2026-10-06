import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import FactorCorrelationCard from './FactorCorrelationCard';
import { researchApi } from '@/api/research';

// Mock researchApi
vi.mock('@/api/research', () => ({
  researchApi: {
    factorCorr: vi.fn(),
  },
}));

// Mock echarts & useChart
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('FactorCorrelationCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('初始挂载并发起真实截面 Spearman 计算，成功展示结果', async () => {
    vi.mocked(researchApi.factorCorr).mockResolvedValueOnce({
      factors: ['ret_5', 'vol_20', 'rsi_14'],
      matrix: [
        [1.0, 0.12, 0.45],
        [0.12, 1.0, -0.08],
        [0.45, -0.08, 1.0],
      ],
      high_corr_pairs: [],
      window_days: 60,
    });

    render(<FactorCorrelationCard />);

    expect(screen.getByText('因子正交与相关性矩阵看板')).toBeInTheDocument();
    expect(screen.getByText('真实截面 Spearman')).toBeInTheDocument();

    await waitFor(() => {
      expect(researchApi.factorCorr).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByText('低共线性')).toBeInTheDocument();
      expect(screen.getByText('✓ 候选池因子间无 |ρ| ≥ 0.7 显著共线性')).toBeInTheDocument();
    });
  });

  it('存在 |ρ| >= 0.7 因子对时，显示共线预警及详细因子对并触发纯化回调', async () => {
    const onSelectOrthogonalize = vi.fn();
    vi.mocked(researchApi.factorCorr).mockResolvedValueOnce({
      factors: ['ret_5', 'rsi_14'],
      matrix: [
        [1.0, 0.82],
        [0.82, 1.0],
      ],
      high_corr_pairs: [{ a: 'ret_5', b: 'rsi_14', r: 0.82 }],
      window_days: 60,
    });

    render(
      <FactorCorrelationCard onSelectForOrthogonalize={onSelectOrthogonalize} />,
    );

    await waitFor(() => {
      expect(screen.getByText('共线预警')).toBeInTheDocument();
    });

    expect(screen.getByText(/检测到 1 对高相关因子/)).toBeInTheDocument();
    expect(screen.getByText('0.820')).toBeInTheDocument();

    // 点击“纯化”按钮
    const purifyBtn = screen.getByText('纯化');
    fireEvent.click(purifyBtn);
    expect(onSelectOrthogonalize).toHaveBeenCalledWith('ret_5');
  });

  it('切换回看窗口时重新调用接口计算', async () => {
    vi.mocked(researchApi.factorCorr).mockResolvedValue({
      factors: ['ret_5', 'vol_20'],
      matrix: [
        [1.0, 0.05],
        [0.05, 1.0],
      ],
      high_corr_pairs: [],
      window_days: 120,
    });

    render(<FactorCorrelationCard />);

    await waitFor(() => {
      expect(researchApi.factorCorr).toHaveBeenCalled();
    });

    // 切换到 120 日
    const select = screen.getByDisplayValue('60日 (近1季)');
    fireEvent.change(select, { target: { value: '120' } });

    await waitFor(() => {
      expect(researchApi.factorCorr).toHaveBeenCalledWith(
        expect.objectContaining({ window_days: 120 }),
        expect.anything(),
      );
    });
  });
});
