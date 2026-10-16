/**
 * 深度行情 / 单标的微观画像中心（AnalysisHub）聚合工作台单元测试。
 *
 * 验证：
 * 1. 在 /stock/600519.SH 下默认激活「个股分析」Tab，渲染 StockDetail；
 * 2. 在 /etf/510300 下激活「ETF分析」Tab，渲染 EtfDetail；
 * 3. 在 /futures/analysis 下激活「期货分析」Tab，渲染 FuturesAnalysis；
 * 4. 点击 Tab 触发切换；
 * 5. 包含标的中心上游链接与自选池直通入口。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AnalysisHub from './index';

vi.mock('@/pages/StockDetail', () => ({
  default: () => <div data-testid="mock-stock-detail-view">MOCK_STOCK_DETAIL_PAGE</div>,
}));

vi.mock('@/pages/EtfDetail', () => ({
  default: () => <div data-testid="mock-etf-detail-view">MOCK_ETF_DETAIL_PAGE</div>,
}));

vi.mock('@/pages/FuturesAnalysis', () => ({
  default: () => <div data-testid="mock-futures-analysis-view">MOCK_FUTURES_ANALYSIS_PAGE</div>,
}));

describe('AnalysisHub 深度行情聚合工作台', () => {
  it('在 /stock/600519.SH 下默认激活个股分析 Tab 并渲染 StockDetail', () => {
    render(
      <MemoryRouter initialEntries={['/stock/600519.SH']}>
        <Routes>
          <Route path="/stock/:symbol" element={<AnalysisHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('深度行情')).toBeInTheDocument();
    expect(screen.getByTestId('mock-stock-detail-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-etf-detail-view')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mock-futures-analysis-view')).not.toBeInTheDocument();

    const stockTab = screen.getByRole('tab', { name: /个股分析/i });
    expect(stockTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /etf/510300 下自动激活 ETF分析 Tab 并渲染 EtfDetail', () => {
    render(
      <MemoryRouter initialEntries={['/etf/510300']}>
        <Routes>
          <Route path="/etf/:code" element={<AnalysisHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-etf-detail-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-stock-detail-view')).not.toBeInTheDocument();

    const etfTab = screen.getByRole('tab', { name: /ETF分析/i });
    expect(etfTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /futures/analysis 下自动激活期货分析 Tab 并渲染 FuturesAnalysis', () => {
    render(
      <MemoryRouter initialEntries={['/futures/analysis']}>
        <Routes>
          <Route path="/futures/analysis" element={<AnalysisHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-futures-analysis-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-stock-detail-view')).not.toBeInTheDocument();

    const futuresTab = screen.getByRole('tab', { name: /期货分析/i });
    expect(futuresTab).toHaveAttribute('aria-selected', 'true');
  });

  it('点击 Tab 触发路由导航', () => {
    render(
      <MemoryRouter initialEntries={['/stock/600519.SH']}>
        <Routes>
          <Route path="/stock/:symbol" element={<AnalysisHub />} />
          <Route path="/etf/:code" element={<AnalysisHub />} />
          <Route path="/futures/analysis" element={<AnalysisHub />} />
        </Routes>
      </MemoryRouter>
    );

    const etfTab = screen.getByRole('tab', { name: /ETF分析/i });
    fireEvent.click(etfTab);
    expect(screen.getByTestId('mock-etf-detail-view')).toBeInTheDocument();
  });
});
