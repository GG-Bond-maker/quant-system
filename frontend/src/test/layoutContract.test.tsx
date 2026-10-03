/**
 * 布局不变量契约测试（C-3 首批，2026-10-02）—— 守 P0「高度链断裂」不再复发。
 *
 * ## 为什么这里断言的是 className 而不是几何量
 *
 * jsdom **没有布局引擎**：`getBoundingClientRect()` 恒为 0、`offsetHeight` 恒为 0、
 * 百分比高度不会被解析。所以「高度链是否真的撑开」**无法**由渲染断言证明 ——
 * 任何"渲染后断言高度 > 0"的测试都会**恒真**，属于自欺。
 *
 * 因此本文件守的是**结构不变量**：高度链的每一环都带上了它该带的类。
 * 这正是 2026-10-02 P0 事故的**唯一可静态判定的那一环**（缺失 `h-full`），
 * 也是唯一能在 CI 里稳定复现的那一环。真实几何表现仍**待人工目视验收**。
 *
 * ## 被守的不变量（与 App.tsx:69-83 的纪律注释一一对应）
 *
 * 1. `MainContent` 的**内层包装 div 必须带 `h-full`** —— 它是页面根百分比高度的
 *    解析基准。缺了它，页面根的 `min-h-full` 按 CSS 2.1 §10.7 退化为 0，
 *    `flex-1` 栅格拿不到自由空间 ⇒ 主轴塌陷、卡片按自然高度溢出压盖兄弟节点。
 * 2. **页面根用 `min-h-full`（至少撑满），不得改用 `h-full`（定高）** ——
 *    定高会把「内容高于视口」的页面裁掉/顶出，而滚动应由 `main` 的
 *    `overflow-y-auto` 承接。两者不可互换，App.tsx 注释已明确此分工。
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { MainContent } from '@/App';

const SRC = path.resolve(__dirname, '..');

function read(rel: string): string {
  return readFileSync(path.join(SRC, rel), 'utf8');
}

describe('高度链结构不变量（P0 回归）', () => {
  it('MainContent 内层包装 div 必须带 h-full（否则页面根百分比高度退化为 0）', () => {
    const { container } = render(
      <MemoryRouter>
        <MainContent>
          <div data-testid="page" />
        </MainContent>
      </MemoryRouter>,
    );

    const main = container.querySelector('main');
    expect(main, '未渲染出 <main>').not.toBeNull();
    // main 自身必须定高（flex-1 在定高父链下才成立）
    expect(main!.className).toContain('flex-1');

    const wrapper = main!.firstElementChild as HTMLElement | null;
    expect(wrapper, 'main 下没有包装 div').not.toBeNull();
    expect(
      wrapper!.className.split(/\s+/),
      '🔴 包装 div 缺 h-full ⇒ 高度链断裂（P0 事故根因）',
    ).toContain('h-full');
  });

  it('包装 div 仍负责居中限宽（修复不得顺手删掉 max-w-content）', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/market']}>
        <MainContent>
          <div />
        </MainContent>
      </MemoryRouter>,
    );
    const wrapper = container.querySelector('main')!.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain('max-w-content');
    expect(wrapper.className).toContain('mx-auto');
  });

  it('行情型页面（/stock、/desk）用更宽的 max-w-chart', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/desk']}>
        <MainContent>
          <div />
        </MainContent>
      </MemoryRouter>,
    );
    const wrapper = container.querySelector('main')!.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain('max-w-chart');
    expect(wrapper.className).not.toContain('max-w-content');
  });
});

describe('页面根高度类不变量', () => {
  // 审计 D-1 列出的 6 个页面 + ETF 中心，全部是「页面根」层级
  const PAGE_ROOTS = [
    'pages/Etf/index.tsx',
    'pages/Backtest/index.tsx',
    'pages/DataCenter/index.tsx',
    'pages/Report/index.tsx',
    'pages/Screener/index.tsx',
    'pages/Settings/index.tsx',
    'pages/Watchlist/index.tsx',
  ];

  it.each(PAGE_ROOTS)('%s 的页面根用 min-h-full（撑满），不用 h-full（定高）', (rel) => {
    const src = read(rel);
    expect(src, `${rel} 未找到页面根 min-h-full`).toContain('flex min-h-full flex-col');
    // 反向断言：页面根不得写成定高（`flex h-full flex-col gap-N` 是该页根形状），
    // 否则内容高于视口时会被裁掉 —— 滚动应由 main 的 overflow-y-auto 承接。
    expect(src, `${rel} 的页面根被改成了定高 h-full`).not.toMatch(
      /className="flex h-full flex-col gap-\d/,
    );
  });
});
