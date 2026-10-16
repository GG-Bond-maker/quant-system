/**
 * 数据工程中枢（DataHub）聚合工作台单元测试。
 *
 * 验证：
 * 1. 在 /data 下默认激活「数据资产」Tab，渲染 DataCenter；
 * 2. 在 /dataquality 下激活「质量巡检」Tab，渲染 DataQuality；
 * 3. 在 /pipeline 下激活「调度流水线」Tab，渲染 Pipeline；
 * 4. 点击 Tab 触发切换。
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import DataHub from './index';

vi.mock('@/pages/DataCenter', () => ({
  default: () => <div data-testid="mock-datacenter-view">MOCK_DATACENTER_PAGE</div>,
}));

vi.mock('@/pages/DataQuality', () => ({
  default: () => <div data-testid="mock-dataquality-view">MOCK_DATAQUALITY_PAGE</div>,
}));

vi.mock('@/pages/Pipeline', () => ({
  default: () => <div data-testid="mock-pipeline-view">MOCK_PIPELINE_PAGE</div>,
}));

describe('DataHub 数据工程聚合工作台', () => {
  it('在 /data 下默认激活数据资产 Tab 并渲染 DataCenter', () => {
    render(
      <MemoryRouter initialEntries={['/data']}>
        <Routes>
          <Route path="/data" element={<DataHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('数据工程')).toBeInTheDocument();
    expect(screen.getByTestId('mock-datacenter-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-dataquality-view')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mock-pipeline-view')).not.toBeInTheDocument();

    const dataTab = screen.getByRole('tab', { name: /数据资产/i });
    expect(dataTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /dataquality 下自动激活质量巡检 Tab 并渲染 DataQuality', () => {
    render(
      <MemoryRouter initialEntries={['/dataquality']}>
        <Routes>
          <Route path="/dataquality" element={<DataHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-dataquality-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-datacenter-view')).not.toBeInTheDocument();

    const qualityTab = screen.getByRole('tab', { name: /质量巡检/i });
    expect(qualityTab).toHaveAttribute('aria-selected', 'true');
  });

  it('在 /pipeline 下自动激活调度流水线 Tab 并渲染 Pipeline', () => {
    render(
      <MemoryRouter initialEntries={['/pipeline']}>
        <Routes>
          <Route path="/pipeline" element={<DataHub />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId('mock-pipeline-view')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-datacenter-view')).not.toBeInTheDocument();

    const pipelineTab = screen.getByRole('tab', { name: /调度流水线/i });
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true');
  });

  it('点击 Tab 触发路由导航', () => {
    render(
      <MemoryRouter initialEntries={['/data']}>
        <Routes>
          <Route path="/data" element={<DataHub />} />
          <Route path="/dataquality" element={<DataHub />} />
          <Route path="/pipeline" element={<DataHub />} />
        </Routes>
      </MemoryRouter>
    );

    const qualityTab = screen.getByRole('tab', { name: /质量巡检/i });
    fireEvent.click(qualityTab);
    expect(screen.getByTestId('mock-dataquality-view')).toBeInTheDocument();
  });
});
