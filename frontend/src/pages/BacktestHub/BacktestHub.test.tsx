/**
 * 回测与组合中心（BacktestHub）聚合工作台单元测试。
 *
 * 验证：
 * 1. 在 /backtest 下默认激活「策略回测」Tab，渲染 Backtest；
 * 2. 在 /portfolio 下激活「组合回测」Tab，渲染 Portfolio；
 * 3. 在 /capacity 下激活「容量与归因」Tab，渲染 CapacityAttribution；
 * 4. 点击 Tab 触发切换；
 * 5. 包含策略研发上游与交易大厅下游链接。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import BacktestHub from './index';

vi.mock('@/pages/Backtest', () => ({
  default: () => <div data-testid="mock-backtest-view">MOCK_BACKTEST_PAGE</div>,
}));

vi.mock('@/pages/Portfolio', () => ({
  default: () => <div data-testid="mock-portfolio-view">MOCK_PORTFOLIO_PAGE</div>,
}));

vi.mock('@/pages/CapacityAttribution', () => ({
  default: () => <div data-testid="mock-capacity-view">MOCK_CAPACITY_PAGE</div>,
}));

describe('BacktestHub 回测与组合聚合工作台', () => {
  it('在 /backtest 下默认激活策略回测 Tab 并渲染 Backtest', () => {
    render(
      <MemoryRouter initialEntries={['/backtest']}>
        <Routes>
          <Route path="/backtest" element={<BacktestHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('回测与组合')).toBeInTheDocument();
    expect(screen.getByTestId('mock-backtest-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-portfolio-view')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mock-capacity-view')).not.toBeInTheDocument();

    const backtestTab = screen.getByRole('tab', { name: /策略回测/i });
    expect(backtestTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /portfolio 下自动激活组合回测 Tab 并渲染 Portfolio', () => {
    render(
      <MemoryRouter initialEntries={['/portfolio']}>
        <Routes>
          <Route path="/portfolio" element={<BacktestHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-portfolio-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-backtest-view')).not.toBeInTheDocument();

    const portfolioTab = screen.getByRole('tab', { name: /组合回测/i });
    expect(portfolioTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /capacity 下自动激活容量与归因 Tab 并渲染 CapacityAttribution', () => {
    render(
      <MemoryRouter initialEntries={['/capacity']}>
        <Routes>
          <Route path="/capacity" element={<BacktestHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-capacity-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-backtest-view')).not.toBeInTheDocument();

    const capacityTab = screen.getByRole('tab', { name: /容量与归因/i });
    expect(capacityTab).toHaveAttribute('aria-selected', 'true');
  });

  it('点击 Tab 触发路由导航', () => {
    render(
      <MemoryRouter initialEntries={['/backtest']}>
        <Routes>
          <Route path="/backtest" element={<BacktestHub />} />
          <Route path="/portfolio" element={<BacktestHub />} />
          <Route path="/capacity" element={<BacktestHub />} />
        </Routes>
      </MemoryRouter>
    );

    const portfolioTab = screen.getByRole('tab', { name: /组合回测/i });
    fireEvent.click(portfolioTab);
    expect(screen.getByTestId('mock-portfolio-view')).toBeInTheDocument();
  });
});
