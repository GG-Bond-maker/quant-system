/**
 * ETF 表现图 —— 纵轴刻度格式化契约（2026-10-03 浮点尾巴回归）。
 *
 * ## 被守的缺陷
 *
 * 本图纵轴的 `min/max` 是**显式浮点**（按序列剔除离群后算出的 `yMin/yMax`），
 * 因此 ECharts 的刻度步长不是整数。原实现用字符串模板 `'{value}%'` 拼轴标签，
 * 会把 `70.70239999999999` 原样显示成 `70.70239999999999%`。
 *
 * ## 为什么能在 jsdom 里测
 *
 * 断言的是 **`setOption` 收到的 option 里的 `yAxis.axisLabel.formatter`**：
 * 它是纯函数/字符串，与布局引擎无关。把 formatter 捕获出来**直接调用**，
 * 就能验证它是否函数化、以及输出是否已四舍五入（含"不产生负零"）。
 *
 * 若有人把修复改回缺陷形态（formatter 退回字符串模板），
 * 「是函数」这条断言与「调用它」这条断言都会失败 ⇒ 用例能变红。
 */
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfPerformance } from '@/types/etf';

import PerformanceChart from './PerformanceChart';

// vi.mock 提升到 import 之上 ⇒ 工厂内只能引用 vi.hoisted 出来的东西
const h = vi.hoisted(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() }));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
}));

/** 取最近一次 setOption 的 option（本图 init 一次、复用实例，最后一次即当前数据） */
function lastOption(): { yAxis?: { axisLabel?: { formatter?: unknown } } } {
  const calls = h.setOption.mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as never;
}

/** 两段真实形态的行情（含一个会算出浮点刻度上界的序列） */
const DATA: EtfPerformance = {
  metric: 'pct',
  period: '1y',
  series: [
    {
      code: '510300', name: '沪深300ETF', status: 'ok',
      points: [
        { date: '2026-01-02', value: 0 },
        { date: '2026-06-02', value: 70.7024 },
        { date: '2026-12-31', value: 12.34 },
      ],
    },
    {
      code: '510500', name: '中证500ETF', status: 'ok',
      points: [
        { date: '2026-01-02', value: 0 },
        { date: '2026-06-02', value: 5.1 },
        { date: '2026-12-31', value: -3.2 },
      ],
    },
  ],
};

beforeEach(() => {
  h.setOption.mockClear();
  h.dispose.mockClear();
  h.resize.mockClear();
});

describe('ETF 表现图：纵轴刻度格式化（浮点尾巴回归）', () => {
  it('formatter 是函数而不是字符串模板（字符串模板会原样显示浮点尾巴）', () => {
    render(<PerformanceChart data={DATA} />);
    const fmt = lastOption()?.yAxis?.axisLabel?.formatter;
    expect(
      typeof fmt,
      '🔴 formatter 不是函数 ⇒ 退回字符串模板就会把 70.70239999999999% 原样显示',
    ).toBe('function');
  });

  it('四舍五入去掉浮点尾巴，且不引入负零：70.70239999999999 -> "70.7%"', () => {
    render(<PerformanceChart data={DATA} />);
    const fmt = lastOption()?.yAxis?.axisLabel?.formatter as (v: number) => string;
    expect(fmt(70.70239999999999)).toBe('70.7%');
    expect(fmt(0)).toBe('0%');
    // -0.0000001 经四舍五入是 -0；模板串化必须归一为 "0"（不得出现 "-0%"）
    expect(fmt(-0.0000001)).toBe('0%');
    expect(fmt(-12.3456)).toBe('-12.35%');
    // 整数不补零（toFixed 会补成 "250.00%"）
    expect(fmt(250)).toBe('250%');
  });
});
