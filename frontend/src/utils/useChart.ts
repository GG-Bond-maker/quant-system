/**
 * ECharts 挂载 Hook（页面组件共享）。
 * 用回调 ref：条件渲染（如 attr && <div ref=...>）下 div 后挂也能正确 init。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';

export function useChart(option: echarts.EChartsCoreOption | null) {
  // 参数用宽松的 CoreOption：严格 EChartsOption 可赋值于它；
  // 图例触发 tooltip 等组合在严格 ComposeOption 联合下过窄，宽松类型统一承接
  const inst = useRef<echarts.ECharts | null>(null);
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => setNode(el), []);
  useEffect(() => {
    if (!node) return;
    inst.current = echarts.init(node);
    /**
     * 🔴 把 ECharts 的输出**摘出文档流**（高度正反馈环的结构性修复，2026-10-03）。
     *
     * `echarts.init(node)` 会往 `node` 里插入一个 zrender 根 div（`position: relative`
     * 且宽高被写死为像素值），**它在文档流内**。于是：
     *   容器高 = 内容高 = zrender 根高 = canvas 高
     * ⇒ `ResizeObserver` 观察到容器变高 → `resize()` 把 zrender 根写得更高
     * ⇒ 容器再变高 …… **自增强正反馈**（浏览器会以
     * `ResizeObserver loop completed with undelivered notifications` 中断回调）。
     *
     * ⚠️ 症状分两层，务必分清（否则会把"没复现出失控"误判成"没有这个缺陷"）：
     * - **恒定必现（结构性）**：`canvas 高 ≠ 容器高` —— canvas 始终落后于容器。
     * - **间歇（受时序/竞态影响）**：失控幅度。QA 复现实测：同一代码、同一操作步骤下，
     *   连续 9 次 fresh load 有 8 次落在"小态"（容器 565px / canvas 539px，二者仍然不等），
     *   约 **1/9** 命中失控态（容器 4142px / canvas 落后到 2924px）；一旦命中便稳定保持。
     *   ⇒ 判据是 `canvas ≠ 容器`，而非"有没有涨到几千像素"。
     *
     * 修法：把容器设为**定位容器**（`position: relative`），并把 zrender 根（容器的首个子元素）
     * 改为**绝对定位铺满**。这样容器高度**在结构上不可能**由 canvas 决定，环不成立。
     * 本修复覆盖共享 hook 的**全部 17 个渲染点 / 9 个文件**（SignalAnalysisPanel、
     * CapacityAttribution、DataQuality、Etf/index、Etf/PerformanceChart、FactorLab、
     * FactorStudio ×3、OrderDesk、Research/parts ×7），而非只修 ETF 表现图。
     *
     * 关键细节：
     * - `setProperty(..., 'important')`：`resize()` 之后仍会往 zrender 根写 `width/height`
     *   内联样式（非 important）。用 important 覆写后，该根节点**恒为容器尺寸**，
     *   不会出现"图表只铺满容器一部分"或反向撑高容器。
     * - **不加** `overflow: hidden`：ECharts tooltip 默认 append 在容器内，会被裁掉；
     *   绝对定位的 tooltip 本身不需要 overflow 裁剪。
     * - 只在 `init` 之后设置一次即可：`resize()` 只改 width/height 内联样式，不重置 position。
     */
    node.style.position = 'relative';
    const root = node.firstElementChild as HTMLElement | null;
    if (root) {
      root.style.setProperty('position', 'absolute', 'important');
      root.style.setProperty('top', '0px', 'important');
      root.style.setProperty('left', '0px', 'important');
      root.style.setProperty('width', '100%', 'important');
      root.style.setProperty('height', '100%', 'important');
    }
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(node);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, [node]);
  useEffect(() => { if (option && inst.current) inst.current.setOption(option, true); },
    [node, option]);
  return ref;
}
