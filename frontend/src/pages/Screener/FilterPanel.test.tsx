import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import FilterPanel, { DEFAULT_FILTERS, type ScreenerFilters } from './FilterPanel';

describe('FilterPanel 组件', () => {
  it('正确渲染筛选器与四大经典策略预设模板', () => {
    const onChange = vi.fn();
    const onApply = vi.fn();
    const onReset = vi.fn();

    render(
      <FilterPanel
        value={DEFAULT_FILTERS}
        onChange={onChange}
        onApply={onApply}
        onReset={onReset}
      />,
    );

    expect(screen.getByText('多维因子漏斗筛选')).toBeDefined();
    expect(screen.getByText('强信号精选')).toBeDefined();
    expect(screen.getByText('动量主升浪')).toBeDefined();
    expect(screen.getByText('核心流动性')).toBeDefined();
    expect(screen.getByText('均衡基准')).toBeDefined();

    // 点击预设模板
    fireEvent.click(screen.getByText('强信号精选'));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        minScore: '0.005',
        signal: 'strong',
        minTurnover: '1.5',
      }),
    );
  });

  it('支持展开多维量价与流动性漏斗并修改字段', () => {
    let currentVal: ScreenerFilters = { ...DEFAULT_FILTERS };
    const onChange = vi.fn((v) => { currentVal = v; });

    render(
      <FilterPanel
        value={currentVal}
        onChange={onChange}
        onApply={vi.fn()}
        onReset={vi.fn()}
      />,
    );

    // 展开高级漏斗
    fireEvent.click(screen.getByText('多维量价与流动性漏斗'));
    expect(screen.getByText('最低换手率 (%)')).toBeDefined();
    expect(screen.getByText('最低成交额 (亿元)')).toBeDefined();
  });
});
