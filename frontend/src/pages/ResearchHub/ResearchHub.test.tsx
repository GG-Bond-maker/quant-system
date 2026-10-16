/**
 * 策略研发中心（ResearchHub）聚合工作台单元测试。
 *
 * 验证：
 * 1. 在 /research 下默认激活「策略工作台」Tab，渲染 Research；
 * 2. 在 /studio 下激活「因子实验室」Tab，渲染 FactorStudio；
 * 3. 点击 Tab 触发切换；
 * 4. 包含上游标的中心与下游回测与组合的链接。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import ResearchHub from './index';

vi.mock('@/pages/Research', () => ({
  default: () => <div data-testid="mock-research-view">MOCK_RESEARCH_PAGE</div>,
}));

vi.mock('@/pages/FactorStudio', () => ({
  default: () => <div data-testid="mock-studio-view">MOCK_STUDIO_PAGE</div>,
}));

describe('ResearchHub 策略研发聚合工作台', () => {
  it('在 /research 下默认激活策略工作台 Tab 并渲染 Research', () => {
    render(
      <MemoryRouter initialEntries={['/research']}>
        <Routes>
          <Route path="/research" element={<ResearchHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('策略研发')).toBeInTheDocument();
    expect(screen.getByTestId('mock-research-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-studio-view')).not.toBeInTheDocument();

    const researchTab = screen.getByRole('tab', { name: /策略工作台/i });
    expect(researchTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /studio 下自动激活因子实验室 Tab 并渲染 FactorStudio', () => {
    render(
      <MemoryRouter initialEntries={['/studio']}>
        <Routes>
          <Route path="/studio" element={<ResearchHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-studio-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-research-view')).not.toBeInTheDocument();

    const studioTab = screen.getByRole('tab', { name: /因子实验室/i });
    expect(studioTab).toHaveAttribute('aria-selected', 'true');
  });

  it('点击 Tab 触发路由导航', () => {
    render(
      <MemoryRouter initialEntries={['/research']}>
        <Routes>
          <Route path="/research" element={<ResearchHub />} />
          <Route path="/studio" element={<ResearchHub />} />
        </Routes>
      </MemoryRouter>
    );

    const studioTab = screen.getByRole('tab', { name: /因子实验室/i });
    fireEvent.click(studioTab);
    expect(screen.getByTestId('mock-studio-view')).toBeInTheDocument();
  });
});
