import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FuturesKLineChart } from './FuturesKLineChart';
import type { FuturesKlineWithIndicator } from '@/types/futures';

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('FuturesKLineChart Component', () => {
  const mockBars: FuturesKlineWithIndicator[] = [
    {
      date: '2026-04-10',
      open: 3910.0,
      high: 3940.0,
      low: 3905.0,
      close: 3935.0,
      volume: 120000,
      open_interest: 215000,
      ma5: 3920.0,
      ma20: 3900.0,
      oi_delta: 2500,
    },
    {
      date: '2026-04-11',
      open: 3935.0,
      high: 3965.0,
      low: 3925.0,
      close: 3950.0,
      volume: 135000,
      open_interest: 218500,
      ma5: 3930.0,
      ma20: 3905.0,
      oi_delta: 3500,
    },
  ];

  it('renders K-line toolbar, floating info bar, and microstructure badge', () => {
    const onRangeChange = vi.fn();
    const onToggleContinuous = vi.fn();

    render(
      <FuturesKLineChart
        bars={mockBars}
        symbol="IF"
        name="沪深300股指"
        continuous={true}
        onRangeChange={onRangeChange}
        onToggleContinuous={onToggleContinuous}
      />
    );

    // 检查合约代码与名称
    expect(screen.getByText('IF')).toBeInTheDocument();
    expect(screen.getByText('沪深300股指')).toBeInTheDocument();

    // 检查快捷历史区间按钮
    expect(screen.getByText('60日')).toBeInTheDocument();
    expect(screen.getByText('120日(半年)')).toBeInTheDocument();
    expect(screen.getByText('250日(1年)')).toBeInTheDocument();

    // 检查展期后复权勾选
    const continuousLabel = screen.getByText('后复权连续合约');
    expect(continuousLabel).toBeInTheDocument();

    // 检查浮动数据栏
    expect(screen.getByText('2026-04-11')).toBeInTheDocument();
    expect(screen.getByText('3,950.00')).toBeInTheDocument();
    expect(screen.getByText('+15.00 (+0.38%)')).toBeInTheDocument();
    expect(screen.getByText('218,500')).toBeInTheDocument(); // 持仓量
    expect(screen.getByText('+3,500')).toBeInTheDocument(); // 增仓

    // 检查量价持仓研判徽章
    expect(screen.getByText(/增仓上涨/)).toBeInTheDocument();
  });

  it('supports changing indicators and clicking range buttons', () => {
    const onRangeChange = vi.fn();
    const onToggleContinuous = vi.fn();

    render(
      <FuturesKLineChart
        bars={mockBars}
        symbol="IF"
        name="沪深300股指"
        continuous={true}
        onRangeChange={onRangeChange}
        onToggleContinuous={onToggleContinuous}
      />
    );

    // 点击 60日
    const btn60 = screen.getByText('60日');
    fireEvent.click(btn60);
    expect(onRangeChange).toHaveBeenCalledWith(60);
    expect(btn60).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 点击 120日(半年)
    const btn120 = screen.getByText('120日(半年)');
    fireEvent.click(btn120);
    expect(onRangeChange).toHaveBeenCalledWith(120);
    expect(btn120).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 点击 500日(2年)
    const btn500 = screen.getByText('500日(2年)');
    fireEvent.click(btn500);
    expect(onRangeChange).toHaveBeenCalledWith(500);
    expect(btn500).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 点击 全历史 (2500)
    const btnAll = screen.getByText('全历史');
    fireEvent.click(btnAll);
    expect(onRangeChange).toHaveBeenCalledWith(2500);
    expect(btnAll).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    // 切换主图指标为 BOLL
    const selects = screen.getAllByRole('combobox');
    const mainSelect = selects[0];
    fireEvent.change(mainSelect, { target: { value: 'BOLL' } });
    expect(mainSelect).toHaveValue('BOLL');

    // 切换副图指标为 MACD
    const subSelect = selects[1];
    fireEvent.change(subSelect, { target: { value: 'MACD' } });
    expect(subSelect).toHaveValue('MACD');
  });

  it('respects controlled activeRange prop', () => {
    const { rerender } = render(
      <FuturesKLineChart
        bars={mockBars}
        symbol="IF"
        activeRange={120}
      />
    );

    const btn120 = screen.getByText('120日(半年)');
    expect(btn120).toHaveClass('bg-card text-primary shadow-sm font-semibold');

    rerender(
      <FuturesKLineChart
        bars={mockBars}
        symbol="IF"
        activeRange={500}
      />
    );

    const btn500 = screen.getByText('500日(2年)');
    expect(btn500).toHaveClass('bg-card text-primary shadow-sm font-semibold');
  });
});
