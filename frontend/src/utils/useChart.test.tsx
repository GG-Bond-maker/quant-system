/**
 * `useChart` 契约测试（2026-10-02）—— 守 G-5 与 D-6 两条**性能/布局**不变量。
 *
 * ## 为什么值得单独测
 *
 * `utils/useChart.ts` 是**全项目唯一的「init 一次、复用实例」图表实现**，
 * 也是 G-5 与 D-6 的共同修法。它的两条语义一旦被改回旧写法，就会同时复发两个缺陷：
 *
 * - **G-5（性能）**：若改成 `useEffect(..., [option])` + `dispose()`，
 *   option 每变一次就重建实例 ⇒ 实例抖动 + canvas 重建开销。
 * - **D-6（布局）**：若只 `window.addEventListener('resize')`，
 *   「窗口没变、容器却变高/变矮」（数据到达 / 右栏内容变化 / 栅格切列）**不会**触发
 *   `resize()`，而 zrender 的 canvas 是 `position:absolute`、像素尺寸在 `init` 时写死
 *   ⇒ 图表停留在旧尺寸被拉伸/压扁。
 *
 * 这两条都能在 jsdom 里**用 spy 精确观测**（不需要布局引擎）：
 * `init` 的调用次数、`setOption` 的 notMerge 参数、`ResizeObserver.observe` 的实参。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useChart } from './useChart';

// vi.mock 会被提升到所有 import 之上 ⇒ 工厂内只能引用 vi.hoisted 出来的东西
const h = vi.hoisted(() => ({
  init: vi.fn(),
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  observe: vi.fn(),
  disconnect: vi.fn(),
}));

vi.mock('@/lib/echarts', () => ({
  init: (el: HTMLElement) => {
    h.init(el);
    // 模拟真实 ECharts：`init` 会往容器里插入一个 zrender 根 div（`position: relative`，
    // 宽高随后由 `resize()` 写成像素值）。它是"文档流内"的子节点，正是高度正反馈环的一环。
    const root = el.ownerDocument.createElement('div');
    root.style.position = 'relative';
    el.appendChild(root);
    return { setOption: h.setOption, dispose: h.dispose, resize: h.resize };
  },
}));

class SpyResizeObserver {
  observe = h.observe;
  unobserve = vi.fn();
  disconnect = h.disconnect;
}
vi.stubGlobal('ResizeObserver', SpyResizeObserver);

/** 每次渲染都产生**新的** option 对象（真实页面的 useMemo 也会在数据变化时这样做） */
function Harness() {
  const [n, setN] = useState(0);
  const ref = useChart({ series: [{ type: 'bar', data: [n] }] } as never);
  return (
    <div>
      <button onClick={() => setN((v) => v + 1)}>bump</button>
      <div ref={ref} data-testid="host" />
    </div>
  );
}

beforeEach(() => {
  for (const spy of Object.values(h)) spy.mockClear();
});

describe('useChart：实例复用（G-5 回归）', () => {
  it('option 变化时复用同一实例 —— init 只调用一次，绝不重建', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(h.init).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button'));
    await user.click(screen.getByRole('button'));

    expect(h.init, '🔴 option 变化触发了实例重建（G-5 复发）').toHaveBeenCalledTimes(1);
    expect(h.dispose).not.toHaveBeenCalled();
    // 初次挂载 1 次 + 两次 option 更新 2 次
    expect(h.setOption).toHaveBeenCalledTimes(3);
  });

  it('每次 setOption 都传 notMerge=true（series 变短时不留上一次的残影）', () => {
    render(<Harness />);
    expect(h.setOption.mock.calls.length).toBeGreaterThan(0);
    for (const call of h.setOption.mock.calls) {
      expect(call[1]).toBe(true);
    }
  });

  it('卸载时释放：disconnect + dispose', () => {
    const { unmount } = render(<Harness />);
    unmount();
    expect(h.disconnect).toHaveBeenCalled();
    expect(h.dispose).toHaveBeenCalledTimes(1);
  });
});

describe('useChart：容器尺寸跟随（D-6 回归）', () => {
  it('用 ResizeObserver 观察**容器本身**，而不是只监听 window.resize', () => {
    render(<Harness />);
    const host = screen.getByTestId('host');
    expect(h.observe, '🔴 未 observe 容器 ⇒ 容器尺寸变化不会触发 resize（D-6 复发）')
      .toHaveBeenCalledWith(host);
  });

  it('未注册 window.resize 监听（避免与 ResizeObserver 重复触发）', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    render(<Harness />);
    const resizeRegs = addSpy.mock.calls.filter(([t]) => t === 'resize');
    expect(resizeRegs).toHaveLength(0);
    addSpy.mockRestore();
  });
});

/**
 * 高度正反馈环（2026-10-03）回归。
 *
 * 这是**结构不变量**测试，不依赖布局引擎：jsdom 没有布局引擎，但**会解析内联样式**，
 * 所以"容器是不是定位容器 / zrender 根有没有被摘出文档流"是可以在 jsdom 里稳定断言的
 * （与 `test/layoutContract.test.tsx` 同样的取向：只守能静态判定的那一环）。
 *
 * 为什么这一环是关键：`echarts.init` 插入的 zrender 根在**文档流内**且宽高被写死像素值，
 * 于是「容器高 = 内容高 = canvas 高」→ RO 观察容器 → `resize()` 改内容高 → 容器再变高
 * 的自增强环成立。把输出摘出文档流（容器 `position:relative` + 首子层 `position:absolute`）
 * 后，容器高度**在结构上不可能**由 canvas 决定。若有人删掉这几行，本用例必须变红。
 */
describe('useChart：ECharts 输出被摘出文档流（高度正反馈环回归）', () => {
  it('容器成为定位容器，且 zrender 根被绝对定位铺满', () => {
    render(<Harness />);
    const host = screen.getByTestId('host');

    expect(
      host.style.position,
      '🔴 容器未设 position:relative ⇒ 无法作为绝对定位子层的定位容器',
    ).toBe('relative');

    const root = host.firstElementChild as HTMLElement | null;
    expect(root, '容器内没有 zrender 根节点（模拟 init 未插入子节点）').not.toBeNull();
    // 这几个内联样式的组合 = 「摘出文档流 + 恒铺满容器」。
    expect(
      root!.style.getPropertyValue('position'),
      '🔴 zrender 根未绝对定位 ⇒ 它仍在文档流内，会反向撑高容器（正反馈环复发）',
    ).toBe('absolute');
    expect(root!.style.getPropertyValue('width')).toBe('100%');
    expect(root!.style.getPropertyValue('height')).toBe('100%');
  });
});
