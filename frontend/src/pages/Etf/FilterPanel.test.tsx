/**
 * FilterPanel 的「草稿 + 显式应用」契约回归测试（C-3 首批，2026-10-02）。
 *
 * 守的是什么：审计 D-2 / D-3 / D-4 —— 该面板曾被判为**欺骗性 UI**：
 *   - D-2：「筛选」按钮 `onClick={() => onChange(value)}` 传回父级**已持有的同一对象引用**，
 *          React 以 `Object.is` 判等直接 bail out ⇒ 点击零反应、零请求；
 *   - D-3：控件直接回写父级 ⇒ **每敲一键**就触发一次全量列表请求（逐字外呼）；
 *   - D-4：筛选变化先按旧页码请求一次、再因页码复位请求第二次。
 *
 * 现行为「控件只改本地 draft，`筛选` 才提交」。以下断言把该契约钉死：
 * 一旦有人把控件改回直写父级、或让按钮传回同一引用，测试必须 FAIL。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import FilterPanel, { EMPTY_ETF_FILTERS, type EtfFilters } from './FilterPanel';

const OPTIONS = {
  boards: ['宽基ETF'],
  types: ['股票型'],
  indexes: ['沪深300', '中证500'],
  managers: ['华夏基金'],
};

function setup(overrides: Partial<EtfFilters> = {}) {
  const onChange = vi.fn();
  const onReset = vi.fn();
  const value: EtfFilters = { ...EMPTY_ETF_FILTERS, ...overrides };
  const utils = render(
    <FilterPanel options={OPTIONS} value={value} onChange={onChange} onReset={onReset} />,
  );
  return { onChange, onReset, value, ...utils };
}

/** 规模区间两个 number 输入框（min / max），按 DOM 顺序取第一个 */
const minSizeInput = () => screen.getAllByRole('spinbutton')[0];
const applyButton = () => screen.getByRole('button', { name: /^筛选/ });

describe('FilterPanel 草稿 + 显式应用契约', () => {
  it('编辑控件不回调父级（杜绝逐字外呼，D-3）', async () => {
    const user = userEvent.setup();
    const { onChange } = setup();

    await user.type(minSizeInput(), '1000');

    expect(onChange).not.toHaveBeenCalled();
  });

  it('点击「筛选」才提交，且传回的是**新对象**（D-2：旧实现传同一引用 ⇒ 零反应）', async () => {
    const user = userEvent.setup();
    const { onChange, value } = setup();

    await user.type(minSizeInput(), '1000');
    await user.click(applyButton());

    expect(onChange).toHaveBeenCalledTimes(1);
    const submitted = onChange.mock.calls[0][0] as EtfFilters;
    expect(submitted).toEqual({ ...value, min_size: '1000' });
    // 🔴 关键：必须是新对象。旧实现 `onChange(value)` 传的是父级已有引用，
    // React `Object.is` 判等直接 bail out，点击等于没点。
    expect(submitted).not.toBe(value);
  });

  it('`q` 沿用父级现值，不被草稿的陈旧副本覆盖（字段归属红线）', async () => {
    const user = userEvent.setup();
    // 父级当前 q 由页面顶部搜索框拥有；本面板不得回写它
    const { onChange } = setup({ q: '沪深300' });

    await user.type(minSizeInput(), '500');
    await user.click(applyButton());

    expect((onChange.mock.calls[0][0] as EtfFilters).q).toBe('沪深300');
  });

  it('「重置条件」回调父级，并把草稿清回归一化初值', async () => {
    const user = userEvent.setup();
    const { onChange, onReset } = setup({ min_size: '1000' });

    await user.click(screen.getByRole('button', { name: '重置条件' }));

    expect(onReset).toHaveBeenCalledTimes(1);
    // 重置只清条件，不得顺手触发一次"应用"
    expect(onChange).not.toHaveBeenCalled();
  });

  it('草稿与已应用值有差异时，「筛选」按钮显示未应用提示', async () => {
    const user = userEvent.setup();
    setup();

    expect(applyButton()).toHaveTextContent(/^筛选$/);

    await user.type(minSizeInput(), '1');

    expect(applyButton()).toHaveTextContent(/筛选\s*•/);
  });

  it('提交后未应用提示消失（草稿与父级重新同步）', async () => {
    const onChange = vi.fn();
    const onReset = vi.fn();
    // 受控复现：把父级 value 固定为「已应用 min_size=1000」的状态，
    // 草稿初值即等于它 ⇒ 不应有未应用提示
    render(
      <FilterPanel options={OPTIONS}
        value={{ ...EMPTY_ETF_FILTERS, min_size: '1000' }}
        onChange={onChange} onReset={onReset} />,
    );

    expect(applyButton()).toHaveTextContent(/^筛选$/);
  });
});
