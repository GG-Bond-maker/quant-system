/**
 * ETF 表现图 —— 纵轴契约（刻度格式化 + 离群「超限呈现」开关）。
 *
 * ## 被守的缺陷
 *
 * ### 一、纵轴刻度浮点尾巴（2026-10-03）
 * 本图纵轴的 `min/max` 是**显式浮点**（按序列剔除离群后算出的 `yMin/yMax`），
 * 因此 ECharts 的刻度步长不是整数。原实现用字符串模板 `'{value}%'` 拼轴标签，
 * 会把 `70.70239999999999` 原样显示成 `70.70239999999999%`。
 *
 * ### 二、离群序列「超限呈现」（2026-10-03）
 * 热门榜按涨跌幅取前 N 时会混入杠杆标的（如 SOXL 半导体3x，1 年 max ≈ +670%，
 * 其余宽基仅 ±30%）。默认算法按振幅剔除离群后定轴 ⇒ 离群序列被画到轴外
 * （`yAxis.max` **小于**其真实峰值）。此时必须：
 *   ① 图内给出**不被裁切**的标注（`graphic`），点明其峰值「超出纵轴」；
 *   ② 提供「显示全部」开关，切换后纵轴回到全量（`yAxis.max ≥ 离群真实峰值`）。
 *
 * ## 为什么能在 jsdom 里测
 *
 * 断言的是 **`setOption` 收到的 option** 里的纯数据字段
 * （`yAxis.min/max`、`graphic`、`series[].lineStyle`），与布局引擎无关；
 * 交互断言走 DOM（`getByRole('button')` + `fireEvent.click`）。
 * 若有人把修复改回缺陷形态（formatter 退回模板串 / 开关失效），
 * 对应用例即变红。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtfPerformance } from '@/types/etf';

import PerformanceChart from './PerformanceChart';

// vi.mock 提升到 import 之上 ⇒ 工厂内只能引用 vi.hoisted 出来的东西
const h = vi.hoisted(() => ({ setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() }));

vi.mock('@/lib/echarts', () => ({
  init: () => ({ setOption: h.setOption, dispose: h.dispose, resize: h.resize }),
}));

interface CapturedOption {
  yAxis?: { min?: number; max?: number; axisLabel?: { formatter?: unknown } };
  graphic?: { elements?: Array<{ style?: { text?: string } }> };
  series?: Array<{ lineStyle?: { type?: string; opacity?: number } }>;
}

/** 取最近一次 setOption 的 option（本图 init 一次、复用实例，最后一次即当前数据） */
function lastOption(): CapturedOption {
  const calls = h.setOption.mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as CapturedOption;
}

/** 图内标注文本（graphic.elements[].style.text 拼接） */
function graphicText(): string {
  return (lastOption().graphic?.elements ?? []).map((e) => e.style?.text ?? '').join(' ');
}

/** 两段真实形态的行情（含一个会算出浮点刻度上界的序列；**振幅相近 ⇒ 无离群**） */
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

/** 离群序列的真实峰值（= 2026-10-03 实测 SOXL 的 1 年 max） */
const OUTLIER_PEAK = 669.63;

/**
 * 3 条序列：两条宽基（振幅 5 / 6）+ 一条杠杆（振幅 669.63）。
 * 中位振幅 = 6 ⇒ 阈值 6*3 = 18 ⇒ 仅杠杆那条被判离群、在默认模式下被剔除。
 */
const OUTLIER_DATA: EtfPerformance = {
  metric: 'pct',
  period: '1y',
  series: [
    {
      code: '510300', name: '沪深300ETF', status: 'ok',
      points: [{ date: '2026-01-02', value: 0 }, { date: '2026-12-31', value: 5 }],
    },
    {
      code: '510500', name: '中证500ETF', status: 'ok',
      points: [{ date: '2026-01-02', value: 0 }, { date: '2026-12-31', value: 6 }],
    },
    {
      code: 'SOXL', name: '半导体3x杠杆ETF', status: 'ok',
      points: [{ date: '2026-01-02', value: 0 }, { date: '2026-12-31', value: OUTLIER_PEAK }],
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

describe('ETF 表现图：离群「超限呈现 + 显示全部」开关', () => {
  it('有离群：默认纵轴上限 < 离群真实峰值，并渲染开关与图内标注', () => {
    render(<PerformanceChart data={OUTLIER_DATA} />);
    const opt = lastOption();
    // 默认被剔除 ⇒ 纵轴覆盖不到离群峰值（超限呈现）
    expect(
      opt?.yAxis?.max as number,
      '🔴 默认纵轴竟覆盖了离群峰值 ⇒ 说明离群未按预期剔除',
    ).toBeLessThan(OUTLIER_PEAK);
    // 图内标注：不被裁切，且含真实峰值标的名与「超出纵轴」字样
    expect(graphicText()).toContain('SOXL');
    expect(graphicText()).toContain('超出纵轴');
    // 开关存在
    expect(screen.getByRole('button', { name: '显示全部标的' })).toBeInTheDocument();
    // 离群序列被画成虚线（视觉上标记"画在轴外"）
    expect(opt?.series?.[2]?.lineStyle?.type).toBe('dashed');
  });

  it('点「显示全部」后：纵轴上限 ≥ 离群真实峰值（完整呈现）', () => {
    render(<PerformanceChart data={OUTLIER_DATA} />);
    fireEvent.click(screen.getByRole('button', { name: '显示全部标的' }));
    const opt = lastOption();
    expect(
      opt?.yAxis?.max as number,
      '🔴 切到「显示全部」后纵轴仍 < 离群峰值 ⇒ 开关未把量程切回全量',
    ).toBeGreaterThanOrEqual(OUTLIER_PEAK);
    // 按钮文案翻转
    expect(screen.getByRole('button', { name: '仅显示常规区间' })).toBeInTheDocument();
  });

  it('无离群：不渲染开关，纵轴覆盖全量极值', () => {
    render(<PerformanceChart data={DATA} />);
    expect(screen.queryByRole('button')).toBeNull();
    const opt = lastOption();
    const vals = DATA.series.flatMap((s) => s.points.map((p) => p.value));
    expect(opt?.yAxis?.max as number).toBeGreaterThanOrEqual(Math.max(...vals));
    expect(opt?.yAxis?.min as number).toBeLessThanOrEqual(Math.min(...vals));
  });
});
