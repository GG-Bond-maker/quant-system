import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { KLineBar } from '@/types/stock';
import { QuickStockBacktestModal } from './QuickStockBacktestModal';

// Mock ECharts & useChart hook
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

const mockBars: KLineBar[] = Array.from({ length: 60 }, (_, i) => {
  const p = 100 + Math.sin(i / 5) * 10 + i * 0.5;
  return {
    date: `2026-01-${String((i % 28) + 1).padStart(2, '0')}`,
    open: p - 1,
    high: p + 2,
    low: p - 2,
    close: p,
    volume: 100000,
    amount: 10000000,
    ma_5: p,
    ma_20: p - 1,
    boll_mid: p,
    boll_up: p + 3,
    boll_low: p - 3,
    rsi_14: 50 + (i % 20),
    macd_dif: 0.5,
    macd_dea: 0.2,
  };
});

describe('QuickStockBacktestModal 组件', () => {
  it('正常打开时计算并渲染策略绩效指标与净值曲线容器', () => {
    render(
      <BrowserRouter>
        <QuickStockBacktestModal
          open={true}
          onClose={() => {}}
          symbol="600519.SH"
          name="贵州茅台"
          bars={mockBars}
        />
      </BrowserRouter>,
    );

    expect(screen.getByText(/单标的极速策略回测 · 贵州茅台/)).toBeInTheDocument();
    expect(screen.getByText('双均线趋势 (MA 5/20)')).toBeInTheDocument();
    expect(screen.getByText('布林带通道突破 (20, 2σ)')).toBeInTheDocument();
    expect(screen.getByText('RSI 均值回归 (14)')).toBeInTheDocument();
    expect(screen.getByText('MACD 动量金叉')).toBeInTheDocument();

    expect(screen.getByText('策略总收益')).toBeInTheDocument();
    expect(screen.getByText('年化收益 CAGR')).toBeInTheDocument();
    expect(screen.getByText('夏普比率')).toBeInTheDocument();
    expect(screen.getByText('最大回撤')).toBeInTheDocument();
    expect(screen.getByText('交易胜率')).toBeInTheDocument();
    expect(screen.getByText('超额 Alpha')).toBeInTheDocument();
  });

  it('支持切换策略模板与调整参数', () => {
    render(
      <BrowserRouter>
        <QuickStockBacktestModal
          open={true}
          onClose={() => {}}
          symbol="600519.SH"
          name="贵州茅台"
          bars={mockBars}
        />
      </BrowserRouter>,
    );

    const bollBtn = screen.getByText('布林带通道突破 (20, 2σ)');
    fireEvent.click(bollBtn);
    expect(bollBtn).toHaveClass('bg-brand-500');

    const rsiBtn = screen.getByText('RSI 均值回归 (14)');
    fireEvent.click(rsiBtn);
    expect(rsiBtn).toHaveClass('bg-brand-500');
  });
});
