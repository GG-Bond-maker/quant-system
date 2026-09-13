/**
 * ECharts 按需注册统一出口（L4，Sprint3）。
 *
 * 全仓约定：**禁止直接 `import * as echarts from 'echarts'`**（全量引入 ~1MB，
 * 是首屏 JS 的最大头）。一律 `import * as echarts from '@/lib/echarts'`：
 * - 运行时只注册项目实际用到的 charts/components/renderer（tree-shaking 生效）；
 * - 类型全量透传（EChartsOption/SeriesOption/... 为 type-only，零运行时成本），
 *   页面里 `echarts.EChartsOption` 等既有类型引用无需改动；
 * - 新增图表类型时在此登记，勿在页面单独 import 子包。
 */
import * as echartsCore from 'echarts/core';
import {
  BarChart, CandlestickChart, GaugeChart, GraphChart, HeatmapChart,
  LineChart, PieChart, ScatterChart,
} from 'echarts/charts';
import {
  AxisPointerComponent, DataZoomComponent, DatasetComponent, GraphicComponent,
  GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent,
  MarkPointComponent, TitleComponent, TooltipComponent, TransformComponent,
  VisualMapComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echartsCore.use([
  LineChart, BarChart, PieChart, ScatterChart, HeatmapChart, GaugeChart,
  CandlestickChart, GraphChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, VisualMapComponent, MarkLineComponent, MarkPointComponent,
  MarkAreaComponent, DatasetComponent, TransformComponent, GraphicComponent,
  AxisPointerComponent,
  CanvasRenderer,
]);

// 运行时命名导出：页面用 `echarts.init` / `echarts.graphic.LinearGradient`
export const { init, graphic } = echartsCore;
export default echartsCore;

// 类型透传（type-only）：与旧 'echarts' 命名空间用法兼容。
// ECharts 用 core 的 EChartsType 别名——必须与 init 返回类型同源，
// 否则页面的 `chartRef: ECharts` 与 init 赋值产生私有属性不兼容报错。
export type { EChartsType as ECharts, EChartsCoreOption } from 'echarts/core';
export type {
  DefaultLabelFormatterCallbackParams,
  EChartsOption,
  SeriesOption,
  YAXisComponentOption,
} from 'echarts';
