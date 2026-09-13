/** ECharts 挂载 Hook（页面组件共享）。
 * 用回调 ref：条件渲染（如 attr && <div ref=...>）下 div 后挂也能正确 init。 */
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
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(node);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, [node]);
  useEffect(() => { if (option && inst.current) inst.current.setOption(option, true); },
    [option]);
  return ref;
}
