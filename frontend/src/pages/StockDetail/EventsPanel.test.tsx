/**
 * 个股「近期事件」摘要卡 —— 去掉重复入口 + 撑满卡片（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * ### 一、两个入口是同一个动作（重复）
 * 卡片头部有「查看全部 15 条 →」，卡片底部还有一个「还有 12 条 · 展开完整列表」——
 * 两者都只是 `setDrawerOpen(true)`，属重复入口。已删除底部那个。
 *
 * ### 二、只放 3 条 ⇒ 卡片被同行兄弟卡撑高后底部留一大块空白
 * 本卡在 3×2 网格里（`grid lg:grid-cols-3`），网格项默认 `align-items: stretch`，
 * 所以卡片高度由**同行最高的那张卡**决定。只渲染 3 条时，多出来的高度全变成空白。
 * 现在改为 6 条，并把列表区做成 `flex-1` + `overflow-y-auto`：
 * 撑满可用高度，且万一被压短时由滚动承接溢出而不是把内容顶出去。
 *
 * ⚠️ jsdom 没有布局引擎 —— 这里断言的是**结构与文案**（条数、按钮、class 里的
 * flex/overflow 不变量），"真的填满了多少像素"仍待人工目视验收。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { EventItem, EventsBlock } from '@/types/stock';

import { EventsPanel } from './index';

// 只测摘要卡本身：避免把整页（含 lightweight-charts 的 K 线）拖进 jsdom
vi.mock('@/components/charts/KLineChart', () => ({ default: () => null }));

const mkItems = (n: number): EventItem[] =>
  Array.from({ length: n }, (_, i) => ({
    title: `事件标题 ${i + 1}`,
    date: `2026-06-${String((i % 28) + 1).padStart(2, '0')}`,
    url: null,
  }));

const okBlock = (n: number): EventsBlock => ({ status: 'ok', items: mkItems(n) });

function renderPanel(n = 15, requestedLimit = 15) {
  return render(
    <EventsPanel symbol="600519" block={okBlock(n)} requestedLimit={requestedLimit}
      onOpen={() => {}} onNeedMore={() => {}} />,
  );
}

describe('摘要卡：重复入口已删除', () => {
  it('不再渲染「展开完整列表」，也不再有「还有 N 条」文案', () => {
    renderPanel();
    expect(screen.queryByText(/展开完整列表/)).toBeNull();
    expect(screen.queryByText(/还有 \d+ 条/)).toBeNull();
  });

  it('头部「查看全部 N 条 →」仍在，是唯一入口', () => {
    renderPanel();
    expect(screen.getByRole('button', { name: /查看全部 15 条/ })).toBeInTheDocument();
  });
});

describe('摘要卡：展示 6 条并撑满', () => {
  it('展示 6 条，第 7 条不出现', () => {
    renderPanel();
    expect(screen.getByText('事件标题 6')).toBeInTheDocument();
    expect(screen.queryByText('事件标题 7')).toBeNull();
  });

  it('条数不足 6 时全部展示（不补空行、不报错）', () => {
    renderPanel(4);
    expect(screen.getByText('事件标题 4')).toBeInTheDocument();
    expect(screen.queryByText('事件标题 5')).toBeNull();
  });

  it('列表区是 min-h-0 + flex-1 + overflow-y-auto（撑满，溢出交给滚动）', () => {
    renderPanel();
    const ul = screen.getByText('事件标题 1').closest('ul');
    expect(ul).not.toBeNull();
    expect(ul!.className.split(/\s+/)).toEqual(
      expect.arrayContaining(['min-h-0', 'flex-1', 'overflow-y-auto']),
    );
  });

  it('头部「查看全部」打开抽屉，能看到第 7 条之后的事件', () => {
    renderPanel();
    expect(screen.queryByText('事件标题 7')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /查看全部 15 条/ }));

    expect(screen.getByTestId('events-scroll')).toBeInTheDocument();
    expect(screen.getByText('事件标题 7')).toBeInTheDocument();
  });
});
