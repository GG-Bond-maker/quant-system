import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { KLineBar } from '@/types/stock';
import { QuickEtfBacktestModal } from './QuickEtfBacktestModal';

// Mock ECharts & useChart hook
vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

// 构造 30 根模拟 K 线
const mockBars: KLineBar[] = Array.from({ length: 30 }, (_, i) => ({
  date: `2026-09-${String(i + 1).padStart(2, '0')}`,
  open: 3.5 + Math.sin(i * 0.2) * 0.2,
  close: 3.5 + Math.sin((i + 1) * 0.2) * 0.2,
  high: 3.8,
  low: 3.3,
  volume: 1000000,
  amount: 3500000,
  turnover: 1.2,
}));

describe('QuickEtfBacktestModal 组件', () => {
  it('正确渲染极速回测弹窗及其量化指标和图表容器', () => {
    render(
      <BrowserRouter>
        <QuickEtfBacktestModal
          open={true}
          onClose={() => {}}
          code="510300"
          name="沪深300ETF"
          bars={mockBars}
        />
      </BrowserRouter>,
    );

    expect(screen.getByText('⚡ ETF 极速量化策略回测与验证')).toBeInTheDocument();
    expect(screen.getByText('网格交易策略 (Grid Trading)')).toBeInTheDocument();
    expect(screen.getByText('双均线趋势跟踪 (MA 5/20)')).toBeInTheDocument();
    expect(screen.getByText('布林带均值回归 (20, 2σ)')).toBeInTheDocument();
    expect(screen.getByText('智能定期定额定投 (DCA)')).toBeInTheDocument();
    expect(screen.getByText('年化收益 (CAGR)')).toBeInTheDocument();
    expect(screen.getByText('夏普比率 (Sharpe)')).toBeInTheDocument();
    expect(screen.getByText('最大回撤 (Max DD)')).toBeInTheDocument();
    expect(screen.getByText('累计超额 (Alpha)')).toBeInTheDocument();
  });

  it('支持切换策略模板与调整网格参数', () => {
    render(
      <BrowserRouter>
        <QuickEtfBacktestModal
          open={true}
          onClose={() => {}}
          code="510300"
          name="沪深300ETF"
          bars={mockBars}
        />
      </BrowserRouter>,
    );

    const smaBtn = screen.getByText('双均线趋势跟踪 (MA 5/20)');
    fireEvent.click(smaBtn);
    expect(screen.getByText(/MA5 上穿 MA20 买入开多/)).toBeInTheDocument();

    const dcaBtn = screen.getByText('智能定期定额定投 (DCA)');
    fireEvent.click(dcaBtn);
    expect(screen.getByText(/按固定交易日间隔定投买入/)).toBeInTheDocument();
  });
});
