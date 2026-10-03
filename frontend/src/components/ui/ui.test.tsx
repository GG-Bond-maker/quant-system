/**
 * 浮层（`Modal` / `Drawer`）行为测试 —— 补齐"注释承诺过、实现没做"的两件事。
 *
 * 历史：`Modal` 的 JSDoc 曾写"Esc / 点击遮罩关闭，body 滚动锁定"，实现里只有遮罩点击。
 * 本文件把该承诺变成**可执行**的断言。
 *
 * 覆盖的是**结构不变量**，不是像素：
 *  - Esc 关闭 / 关闭态不响应 / 修饰键放行 / 非 Esc 不关闭 / 卸载后摘除监听
 *  - 层叠时只有**最上层**响应（一次 Esc 不得关掉整叠）
 *  - 背景滚动锁：目标必须是 `[data-scroll-root]`（**不是** `document.body`），
 *    且解锁要**恢复原值**、要**引用计数**
 *  - ARIA：`role=dialog` / `aria-modal` / `aria-labelledby` 指向标题
 *  - 回归：遮罩点击仍可关闭；关闭态不渲染 DOM
 *
 * ⚠️ jsdom **没有布局引擎** —— 这里断言的是 `style.overflow` 这个 **DOM 状态**，
 * 而**不是**"背景真的滚不动了"。后者只能人工目视验收（见 `.workbuddy-ai/memory`）。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Drawer, Modal } from './index';

/** 造一个 `[data-scroll-root]`，模拟 `App.tsx::MainContent` 里的 `<main>` */
function mountScrollRoot(): HTMLElement {
  const el = document.createElement('main');
  el.setAttribute('data-scroll-root', '');
  document.body.appendChild(el);
  return el;
}

let root: HTMLElement;

beforeEach(() => {
  root = mountScrollRoot();
});

afterEach(() => {
  cleanup();
  root.remove();
  vi.restoreAllMocks();
});

describe('Modal · Esc 关闭', () => {
  it('打开时按 Esc 会调用 onClose', () => {
    const onClose = vi.fn();
    render(<Modal title="确认" open onClose={onClose}>内容</Modal>);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭态按 Esc 不会调用 onClose', () => {
    const onClose = vi.fn();
    render(<Modal title="确认" open={false} onClose={onClose}>内容</Modal>);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('非 Esc 键不关闭', () => {
    const onClose = vi.fn();
    render(<Modal title="确认" open onClose={onClose}>内容</Modal>);
    fireEvent.keyDown(document, { key: 'Enter' });
    fireEvent.keyDown(document, { key: 'a' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('带修饰键的 Esc 一律放行（不关闭）', () => {
    const onClose = vi.fn();
    render(<Modal title="确认" open onClose={onClose}>内容</Modal>);
    fireEvent.keyDown(document, { key: 'Escape', ctrlKey: true });
    fireEvent.keyDown(document, { key: 'Escape', altKey: true });
    fireEvent.keyDown(document, { key: 'Escape', metaKey: true });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('卸载后不再响应 Esc（监听器已摘除）', () => {
    const onClose = vi.fn();
    const { unmount } = render(<Modal title="确认" open onClose={onClose}>内容</Modal>);
    unmount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('浮层层叠：只有最上层响应 Esc', () => {
  it('Modal 先挂载、Drawer 后挂载 ⇒ 只关 Drawer', () => {
    const onModalClose = vi.fn();
    const onDrawerClose = vi.fn();
    render(
      <>
        <Modal title="弹窗" open onClose={onModalClose}>A</Modal>
        <Drawer title="抽屉" open onClose={onDrawerClose}>B</Drawer>
      </>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(onModalClose).not.toHaveBeenCalled();
  });

  it('渲染顺序相反时，最上层随之改变', () => {
    const onModalClose = vi.fn();
    const onDrawerClose = vi.fn();
    render(
      <>
        <Drawer title="抽屉" open onClose={onDrawerClose}>B</Drawer>
        <Modal title="弹窗" open onClose={onModalClose}>A</Modal>
      </>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onModalClose).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).not.toHaveBeenCalled();
  });
});

describe('背景滚动锁定', () => {
  it('锁的是 [data-scroll-root]，不是 document.body', () => {
    render(<Modal title="确认" open onClose={() => {}}>内容</Modal>);
    expect(root.style.overflow).toBe('hidden');
    // 这是本次修复的核心：外壳 overflow-hidden ⇒ body 从不滚动，锁 body 是死代码
    expect(document.body.style.overflow).toBe('');
  });

  it('Drawer 同样锁定 [data-scroll-root]', () => {
    render(<Drawer title="近期事件" open onClose={() => {}}>内容</Drawer>);
    expect(root.style.overflow).toBe('hidden');
  });

  it('解锁时恢复打开前的原值（不是粗暴置空串）', () => {
    root.style.overflow = 'auto';
    const { rerender } = render(<Modal title="确认" open onClose={() => {}}>内容</Modal>);
    expect(root.style.overflow).toBe('hidden');
    rerender(<Modal title="确认" open={false} onClose={() => {}}>内容</Modal>);
    expect(root.style.overflow).toBe('auto');
  });

  it('叠开时用引用计数：关掉一个仍锁定，全关才解锁', () => {
    const { rerender } = render(
      <>
        <Modal title="弹窗" open onClose={() => {}}>A</Modal>
        <Drawer title="抽屉" open onClose={() => {}}>B</Drawer>
      </>,
    );
    expect(root.style.overflow).toBe('hidden');

    rerender(
      <>
        <Modal title="弹窗" open onClose={() => {}}>A</Modal>
        <Drawer title="抽屉" open={false} onClose={() => {}}>B</Drawer>
      </>,
    );
    expect(root.style.overflow).toBe('hidden');

    rerender(
      <>
        <Modal title="弹窗" open={false} onClose={() => {}}>A</Modal>
        <Drawer title="抽屉" open={false} onClose={() => {}}>B</Drawer>
      </>,
    );
    expect(root.style.overflow).toBe('');
  });

  it('缺少 [data-scroll-root] 时不崩，且显式告警（不静默）', () => {
    root.remove();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<Modal title="确认" open onClose={() => {}}>内容</Modal>);
    expect(warn).toHaveBeenCalled();
  });
});

describe('无障碍语义', () => {
  it('Modal 暴露 role=dialog / aria-modal，且 aria-labelledby 指向标题', () => {
    render(<Modal title="同步任务详情" open onClose={() => {}}>内容</Modal>);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const id = dialog.getAttribute('aria-labelledby');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toHaveTextContent('同步任务详情');
  });

  it('Drawer 的 aria-labelledby 同样指向标题', () => {
    render(<Drawer title="近期事件" open onClose={() => {}}>内容</Drawer>);
    const dialog = screen.getByRole('dialog');
    const id = dialog.getAttribute('aria-labelledby');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toHaveTextContent('近期事件');
  });
});

describe('回归：原有交互不变', () => {
  it('点击遮罩仍关闭 Modal', () => {
    const onClose = vi.fn();
    const { container } = render(<Modal title="确认" open onClose={onClose}>内容</Modal>);
    const backdrop = container.querySelector('.absolute.inset-0');
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('点击遮罩仍关闭 Drawer', () => {
    const onClose = vi.fn();
    const { container } = render(<Drawer title="近期事件" open onClose={onClose}>内容</Drawer>);
    fireEvent.click(container.querySelector('.absolute.inset-0') as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭态不渲染任何 DOM', () => {
    const { container } = render(<Modal title="确认" open={false} onClose={() => {}}>内容</Modal>);
    expect(container).toBeEmptyDOMElement();
  });
});
