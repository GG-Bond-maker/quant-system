/**
 * ETF「行业配置」图例 —— 去掉分页、改为全量可见的 DOM 列表 + hover 联动扇区（2026-10-03）。
 *
 * ## 被守的缺陷
 *
 * ### 一、图例要连点多次才能看全（用户报的）
 * 该卡在 300px 侧栏内仅约 145px 宽，8 个类目用 ECharts `type: 'scroll'` 图例会被
 * 分页成 5 页 —— 用户要连点 4 次才看得全。现在图例改为图下方自行渲染的 DOM 列表：
 * 一行一个、零点击。
 *
 * ### 二、第 9 类起被**静默丢弃**（顺带修的）
 * 原实现 `.slice(0, 8)` 直接把尾部丢掉（后端实测 510300 有 14 个门类，
 * 见 `backend/app/data/etf.py` 的注释）⇒ 环形图加不到 100% 且界面无任何披露。
 * 现在尾部并入「其他 N 类」，占比如实计入。
 *
 * ### 三、色块必须与扇区逐一对得上
 * 这是"改成 DOM 图例"最容易翻车的点：若依赖 ECharts 内部默认色板，DOM 侧根本拿不到
 * 同一组色值。故扇区颜色**显式逐项指定**，DOM 图例按下标取同一个色板。
 *
 * ### 四、hover 必须真的高亮扇区（2026-10-03 追加）
 * DOM 图例 hover 时对画布发 `dispatchAction`。⚠️ 两条独立判据缺一不可：
 *   ① 动作发了（`highlight` / `downplay`）；
 *   ② option 里配了 `emphasis.focus:'self'` + `blur` 弱化 —— 否则高亮"发了但看不见"，
 *      属本项目红线里的**假交互**（比没有该功能更糟）。
 * 只测 ① 会漏掉 ②，故本文件两条都测。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { IndustryPie } from './index';

/**
 * 同一个假实例对象同时服务 `init`（useChart 内部）与 `getInstanceByDom`
 * （组件里取回实例发 dispatchAction）。
 */
const h = vi.hoisted(() => {
  const api = {
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
    dispatchAction: vi.fn(),
  };
  return { api };
});

vi.mock('@/lib/echarts', () => ({
  init: () => h.api,
  getInstanceByDom: () => h.api,
}));
vi.mock('@/components/charts/KLineChart', () => ({ default: () => null }));

/** 14 个行业门类（对齐后端注释里 510300 的实测值），占比合计 93.8% */
const INDUSTRIES = [
  '制造业', '金融业', '信息传输、软件和信息技术服务业', '采矿业',
  '电力、热力、燃气及水生产和供应业', '交通运输、仓储和邮政业', '批发和零售业',
  '建筑业', '房地产业', '租赁和商务服务业', '科学研究和技术服务业',
  '水利、环境和公共设施管理业', '文化、体育和娱乐业', '农、林、牧、渔业',
];
const RATIOS = [30, 20, 12, 8, 6, 4, 3, 2.5, 2, 1.8, 1.5, 1.2, 1, 0.8];

const ITEMS = INDUSTRIES.map((industry, i) => ({ industry, ratio: RATIOS[i] }));

/** 尾部 7 类之和 = 2.5+2+1.8+1.5+1.2+1+0.8 */
const TAIL_SUM = 10.8;

interface CapturedOption {
  legend?: { show?: boolean };
  series?: Array<{
    data?: Array<{ name?: string; itemStyle?: { color?: string } }>;
    emphasis?: { focus?: string };
    blur?: { itemStyle?: { opacity?: number } };
  }>;
}

function lastOption(): CapturedOption {
  const calls = h.api.setOption.mock.calls;
  return (calls[calls.length - 1]?.[0] ?? {}) as CapturedOption;
}

/** 把 `#RRGGBB` 转成 jsdom 归一化后的 `rgb(r, g, b)` 形态，用于与内联样式比较 */
function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255})`;
}
const norm = (s: string): string => s.replace(/\s+/g, '').toLowerCase();

function renderPie(items = ITEMS) {
  return render(<IndustryPie items={items} />);
}

beforeEach(() => {
  h.api.setOption.mockClear();
  h.api.dispatchAction.mockClear();
});

describe('图例不再需要点击', () => {
  it('一次渲染出全部 8 行（前 7 类 + 「其他」），没有分页', () => {
    renderPie();
    expect(screen.getAllByRole('listitem')).toHaveLength(8);
  });

  it('ECharts 自带图例已关闭 —— 分页正是本次要修的根因', () => {
    renderPie();
    expect(lastOption().legend?.show).toBe(false);
  });

  it('按占比降序排列，第一行是占比最大的行业', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    expect(rows[0].textContent).toContain('制造业');
    expect(rows[0].textContent).toContain('30.0%');
  });
});

describe('尾部不再被静默丢弃', () => {
  it('末位是「其他 N 类」，N = 被聚合掉的类目数', () => {
    renderPie();
    expect(screen.getByText('其他 7 类')).toBeInTheDocument();
  });

  it('「其他」的占比 = 尾部各类之和（不是丢掉）', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    expect(rows[rows.length - 1].textContent).toContain(`${TAIL_SUM.toFixed(1)}%`);
  });

  it('类目数不超过 7 时不产生「其他」行', () => {
    renderPie(ITEMS.slice(0, 5));
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.queryByText(/其他/)).toBeNull();
  });
});

describe('DOM 图例色块与扇区一一对应', () => {
  it('每一行的色块颜色 == 同下标扇区的颜色', () => {
    renderPie();
    const slices = lastOption().series?.[0]?.data ?? [];
    const rows = screen.getAllByRole('listitem');

    expect(slices).toHaveLength(rows.length);
    expect(slices.length).toBeGreaterThan(1);

    slices.forEach((s, i) => {
      const swatch = rows[i].querySelector('span');
      expect(swatch, `第 ${i} 行缺色块`).not.toBeNull();
      expect(norm(swatch!.style.backgroundColor))
        .toBe(norm(hexToRgb(s.itemStyle!.color!)));
      // 顺序也必须一致：图例第 i 行就是第 i 个扇区
      expect(rows[i].textContent).toContain(s.name!);
    });
  });
});

describe('DOM 图例 hover → 扇区高亮联动', () => {
  it('hover 第 i 行 ⇒ 对画布发 highlight(seriesIndex 0, dataIndex i)', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    fireEvent.mouseEnter(rows[2]);
    expect(h.api.dispatchAction).toHaveBeenCalledWith(
      { type: 'highlight', seriesIndex: 0, dataIndex: 2 },
    );
  });

  it('移出 ⇒ 发 downplay（不留高亮残影）', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    fireEvent.mouseEnter(rows[1]);
    h.api.dispatchAction.mockClear();
    fireEvent.mouseLeave(rows[1]);
    expect(h.api.dispatchAction).toHaveBeenCalledWith({ type: 'downplay', seriesIndex: 0 });
  });

  it('换一行 ⇒ 只高亮新行（dataIndex 跟着变，不是恒为 0）', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    fireEvent.mouseEnter(rows[0]);
    h.api.dispatchAction.mockClear();
    fireEvent.mouseEnter(rows[3]);
    expect(h.api.dispatchAction).toHaveBeenCalledTimes(1);
    expect(h.api.dispatchAction).toHaveBeenCalledWith(
      { type: 'highlight', seriesIndex: 0, dataIndex: 3 },
    );
  });

  it('⚠️ 高亮必须"看得见"：option 配了 emphasis.focus=self 与 blur 弱化（否则是假交互）', () => {
    renderPie();
    const s = lastOption().series?.[0];
    expect(s?.emphasis?.focus).toBe('self');
    expect(s?.blur?.itemStyle?.opacity).toBeLessThan(1);
  });

  it('DOM 侧同步反馈：hover 行加底色、其余行色块变淡；移出后复原', () => {
    renderPie();
    const rows = screen.getAllByRole('listitem');
    const swatchOf = (i: number) => rows[i].querySelector('span')!;

    fireEvent.mouseEnter(rows[1]);
    expect(rows[1].className).toContain('bg-surface-alt');
    expect(swatchOf(0).className).toContain('opacity-40');
    expect(swatchOf(1).className).not.toContain('opacity-40');

    fireEvent.mouseLeave(rows[1]);
    expect(rows[1].className).not.toContain('bg-surface-alt');
    expect(swatchOf(0).className).not.toContain('opacity-40');
  });
});
