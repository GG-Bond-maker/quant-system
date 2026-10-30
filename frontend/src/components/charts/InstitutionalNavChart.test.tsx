import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import InstitutionalNavChart from './InstitutionalNavChart';

vi.mock('@/utils/useChart', () => ({
  useChart: () => ({ current: null }),
}));

describe('InstitutionalNavChart Component', () => {
  const sampleNav = [
    { date: '2019-01-02', strategy: 1.0, benchmark: 1.0 },
    { date: '2022-06-15', strategy: 1.8, benchmark: 1.5 },
    { date: '2025-12-31', strategy: 2.15, benchmark: 1.75 },
  ];

  it('renders default institutional title with sample period and cost bps', () => {
    render(
      <InstitutionalNavChart
        nav={sampleNav}
        sampleLabel="样本外"
        costBps={35}
        annualStrategy={0.1216}
        annualBenchmark={0.0903}
        benchmarkName="中证500"
      />
    );

    // Title should contain: 累计净值（样本外 2019–2025，含双边成本 35bp）
    expect(screen.getByText(/累计净值/)).toBeDefined();
    expect(screen.getByText(/样本外/)).toBeDefined();
    expect(screen.getByText(/含双边成本 35bp/)).toBeDefined();
    expect(screen.getByText(/2019–2025/)).toBeDefined();
  });

  it('renders loading placeholder when loading is true', () => {
    const { container } = render(
      <InstitutionalNavChart nav={[]} loading={true} />
    );
    expect(container.querySelector('.animate-pulse')).toBeDefined();
  });

  it('renders empty fallback when nav array is empty', () => {
    render(
      <InstitutionalNavChart nav={[]} loading={false} />
    );
    expect(screen.getByText('暂无净值数据')).toBeDefined();
  });

  it('handles custom title override', () => {
    render(
      <InstitutionalNavChart
        nav={sampleNav}
        title="自定义投资组合回测净值"
      />
    );
    expect(screen.getByText('自定义投资组合回测净值')).toBeDefined();
  });
});
