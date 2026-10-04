/**
 * 参数寻优「进度与取消」面板回归（P8-c，2026-10-04 上线前全检）。
 *
 * ## 被守的三件事
 *
 * ### 一、进度**不编造**
 * `total` 来自后端 `opt-status`。它为 `null`（未开寻优 / 无网格 / 查不到）时，
 * 面板**不得**显示百分比或进度条 —— 只报"已算 N 组"。初版曾用 `total || 0`
 * 兜底，于是 `done/total` 变成 `NaN` 或 `Infinity`，进度条宽度写成
 * `width: NaN%`（CSS 静默忽略 ⇒ 看起来"永远是 0%"）。这里把该分支钉死。
 *
 * ### 二、取消的**粒度**必须写在界面上
 * 后端取消接在网格循环的"每个组合之前"，所以**当前参数组合会跑完才停**。
 * 若界面只显示一个按钮而不说明，用户会以为点了没反应（甚至连点多次）。
 * 故断言"当前参数组合会跑完"这类文案存在。
 *
 * ### 三、非 grid 寻优**不显示**取消按钮
 * 后端取消钩子只接在网格循环上（ga / optuna 是各自独立的循环，未接入）。
 * 给它们渲染一个按不动的按钮是比"不显示"更糟的欺骗，故断言按钮不存在。
 *
 * ## 为什么这些断言在 jsdom 里有效
 * 断言的全是 **DOM 文本 / 元素存在性 / style.width 字符串**，与布局引擎无关。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { OptimizationProgress } from '@/api/strategyBacktest';

import OptimizeProgressPanel from './OptimizeProgressPanel';

function progress(over: Partial<OptimizationProgress> = {}): OptimizationProgress {
  return {
    signature: 'abcdef12',
    n_done: 0,
    total: null,
    remaining: null,
    cancel_requested: false,
    has_checkpoint: false,
    ...over,
  };
}

const noop = () => {};

/**
 * 取进度条本体。
 *
 * ⚠️ 不能用 `div.h-full` 选：**外层容器也是 `h-full`**（`h-1.5 ... overflow-hidden`），
 * 选择器会拿到错的那个元素，断言变成"测了外框"。进度条是唯一带**内联 width** 的
 * 元素，故按 `style*="width"` 定位 —— 语义更准，也不受 class 调整影响。
 */
function barOf(container: HTMLElement): HTMLElement {
  const el = container.querySelector('div[style*="width"]') as HTMLElement | null;
  if (!el) throw new Error('未找到进度条（带内联 width 的 div）');
  return el;
}

describe('OptimizeProgressPanel', () => {
  it('两个数都有时显示 已算/共 与百分比', () => {
    render(
      <OptimizeProgressPanel
        progress={progress({ n_done: 30, total: 120, remaining: 90 })}
        cancelling={false}
        onCancel={noop}
        canCancel
      />,
    );
    expect(screen.getByText('30 / 120 组 · 25%')).toBeTruthy();
  });

  it('total 为 null 时**不编造**百分比，只报已完成组数', () => {
    render(
      <OptimizeProgressPanel
        progress={progress({ n_done: 7, total: null })}
        cancelling={false}
        onCancel={noop}
        canCancel
      />,
    );
    // 只报组数，不出现 "/" 与 "%"
    const label = screen.getByText(/组已完成/);
    expect(label.textContent).toBe('7 组已完成');
    expect(label.textContent).not.toContain('%');
    expect(label.textContent).not.toContain('/');
  });

  it('total 为 null 时进度条宽度是 0%（而不是 NaN%）', () => {
    const { container } = render(
      <OptimizeProgressPanel
        progress={progress({ n_done: 7, total: null })}
        cancelling={false}
        onCancel={noop}
        canCancel
      />,
    );
    const bar = barOf(container);
    expect(bar.style.width).toBe('0%');
    expect(bar.style.width).not.toContain('NaN');
  });

  it('还没拿到进度（progress=null）也不崩、不编造', () => {
    const { container } = render(
      <OptimizeProgressPanel progress={null} cancelling={false}
        onCancel={noop} canCancel />,
    );
    expect(screen.getByText('0 组已完成')).toBeTruthy();
    expect(barOf(container).style.width).toBe('0%');
  });

  it('进度超过 100% 时夹到 100%（防止条溢出）', () => {
    const { container } = render(
      <OptimizeProgressPanel
        progress={progress({ n_done: 130, total: 120 })}
        cancelling={false} onCancel={noop} canCancel />,
    );
    expect(barOf(container).style.width).toBe('100%');
  });

  it('canCancel 时按钮可点，并说明取消粒度是"一个参数组合"', () => {
    const onCancel = vi.fn();
    render(
      <OptimizeProgressPanel progress={progress()} cancelling={false}
        onCancel={onCancel} canCancel />,
    );
    const btn = screen.getByRole('button', { name: '取消寻优' });
    expect(btn).toBeTruthy();
    // 粒度披露：当前组合会跑完
    expect(screen.getByText(/当前组合会跑完/)).toBeTruthy();
  });

  it('取消已提交后按钮禁用并显示"正在停止…"（防连点）', () => {
    render(
      <OptimizeProgressPanel
        progress={progress({ cancel_requested: true })}
        cancelling={false} onCancel={noop} canCancel />,
    );
    const btn = screen.getByRole('button', { name: '正在停止…' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText(/当前参数组合会跑完才停止/)).toBeTruthy();
  });

  it('cancelling 期间按钮禁用并显示"提交取消中…"', () => {
    render(
      <OptimizeProgressPanel progress={progress()} cancelling
        onCancel={noop} canCancel />,
    );
    const btn = screen.getByRole('button', { name: '提交取消中…' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('canCancel=false（非 grid）时**不显示**取消按钮，并说明原因', () => {
    render(
      <OptimizeProgressPanel progress={progress()} cancelling={false}
        onCancel={noop} canCancel={false} />,
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText(/暂不支持中途取消/)).toBeTruthy();
  });
});
