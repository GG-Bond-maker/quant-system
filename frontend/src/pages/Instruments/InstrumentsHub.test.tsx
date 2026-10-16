/**
 * 标的中心（Multi-Asset Instruments Hub）聚合页面单元测试。
 *
 * 验证：
 * 1. 默认及 /screener 路由下，默认激活「选股中心」Tab，渲染 Screener；
 * 2. /etf 路由下，激活「ETF 中心」Tab，渲染 EtfCenter；
 * 3. /futures 路由下，激活「期货中心」Tab，渲染 FuturesCenter；
 * 4. 包含三大资产直达深度分析链接（个股分析、ETF分析、期货分析）；
 * 5. 点击 Tab 触发路由切换。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import InstrumentsHub from './index';

// 桩：Mock 子页面，隔离子模块自身的数据与生命周期
vi.mock('@/pages/Screener', () => ({
  default: () => <div data-testid="mock-screener-view">MOCK_SCREENER_PAGE</div>,
}));

vi.mock('@/pages/Etf', () => ({
  default: () => <div data-testid="mock-etf-view">MOCK_ETF_PAGE</div>,
}));

vi.mock('@/pages/FuturesCenter', () => ({
  default: () => <div data-testid="mock-futures-view">MOCK_FUTURES_PAGE</div>,
}));

describe('InstrumentsHub 标的中心聚合工作台', () => {
  it('在 /screener 下默认激活选股中心 Tab 并渲染 Screener', () => {
    render(
      <MemoryRouter initialEntries={['/screener']}>
        <Routes>
          <Route path="/screener" element={<InstrumentsHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('标的中心')).toBeInTheDocument();
    expect(screen.getByTestId('mock-screener-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-etf-view')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mock-futures-view')).not.toBeInTheDocument();

    const stockTab = screen.getByRole('tab', { name: /选股中心/i });
    expect(stockTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /etf 下自动激活 ETF 中心 Tab 并渲染 EtfCenter', () => {
    render(
      <MemoryRouter initialEntries={['/etf']}>
        <Routes>
          <Route path="/etf" element={<InstrumentsHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-etf-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-screener-view')).not.toBeInTheDocument();

    const etfTab = screen.getByRole('tab', { name: /ETF 中心/i });
    expect(etfTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /futures 下自动激活期货中心 Tab 并渲染 FuturesCenter', () => {
    render(
      <MemoryRouter initialEntries={['/futures']}>
        <Routes>
          <Route path="/futures" element={<InstrumentsHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-futures-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-screener-view')).not.toBeInTheDocument();

    const futuresTab = screen.getByRole('tab', { name: /期货中心/i });
    expect(futuresTab).toHaveAttribute('aria-selected', 'true');
  });

  it('提供股票、ETF、期货深度分析直达链接', () => {
    render(
      <MemoryRouter initialEntries={['/screener']}>
        <Routes>
          <Route path="/screener" element={<InstrumentsHub />} />
        </Routes>
      </MemoryRouter>
    );

    const stockLink = screen.getByRole('link', { name: /个股分析/i });
    const etfLink = screen.getByRole('link', { name: /ETF分析/i });
    const futuresLink = screen.getByRole('link', { name: /期货分析/i });

    expect(stockLink).toHaveAttribute('href', '/stock/600519.SH');
    expect(etfLink).toHaveAttribute('href', '/etf/510300');
    expect(futuresLink).toHaveAttribute('href', '/futures/analysis');
  });

  it('点击 Tab 切换按钮时正确导航至对应子路由', () => {
    render(
      <MemoryRouter initialEntries={['/screener']}>
        <Routes>
          <Route path="/screener" element={<InstrumentsHub />} />
          <Route path="/etf" element={<div data-testid="navigated-to-etf">ETF_ROUTE</div>} />
          <Route path="/futures" element={<div data-testid="navigated-to-futures">FUTURES_ROUTE</div>} />
        </Routes>
      </MemoryRouter>
    );

    const etfTab = screen.getByRole('tab', { name: /ETF 中心/i });
    fireEvent.click(etfTab);

    expect(screen.getByTestId('navigated-to-etf')).toBeInTheDocument();
  });
});
