/**
 * 榜单分布图：行业分布（饼图） / Score 分布（直方图） / 涨跌幅分布（直方图）。
 *
 * 数据由当前榜单 items 客户端聚合，与 StatsCards 同一份输入。
 * 每个图独立判空：某项数据不足时显示占位文案，不做「假数据」填充。
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import { PanelEmpty } from '@/components/ui';

interface ChartItem {
  score: number | null;
  pct: number | null;
  industry: string | null;
}

/**
 * Score 分布固定分箱边界（模型原始小数口径，跨日期可比，勿改）。
 * 抽成常量是因为 pct 判色需要按同一份边界推算桶的上界，硬编码在两处容易漂移。
 */
const SCORE_EDGES = [0, 0.002, 0.004, 0.006, 0.008, 0.01, 0.012];
/** 涨跌幅分布固定分箱边界（单位 %，跨日期可比，勿改） */
const PCT_EDGES = [-5, -2, 0, 2, 5, 10];

/**
 * 固定区间直方图（区间与设计稿一致，不随数据动态伸缩）：
 * 固定区间的好处是跨日期可比 —— 今天的 8~10 和昨天的 8~10 是同一个桶
 * （Score 图标签为千分位整数，8~10 即原始值的 0.008~0.010）。
 * edges 为升序边界；返回 [标签, 计数]，桶外值计入首/尾开区间。
 */
function fixedHistogram(values: number[], edges: number[], fmt: (v: number) => string): Array<[string, number]> {
  if (values.length === 0) return [];
  const labels: string[] = [];
  for (let i = 0; i <= edges.length; i++) {
    const lo = i === 0 ? null : edges[i - 1];
    const hi = i === edges.length ? null : edges[i];
    if (lo === null) labels.push(`<${fmt(hi as number)}`);
    else if (hi === null) labels.push(`>${fmt(lo)}`);
    else labels.push(`${fmt(lo)}~${fmt(hi)}`);
  }
  const counts = new Array(labels.length).fill(0);
  for (const v of values) {
    // 低于首边界落入第 0 桶（<edges[0]），必须先判，否则 -6 会被
    // findIndex 判成 -1 而错分到「>10」桶（桶标签由调用方的 fmt 决定，不含单位）
    let idx: number;
    if (v < edges[0]) idx = 0;
    else {
      const found = edges.findIndex((e) => v < e);
      idx = found === -1 ? labels.length - 1 : found;
    }
    counts[idx] += 1;
  }
  return labels.map((l, i) => [l, counts[i]]);
}

/** 极简 ECharts 容器：卸载时销毁实例，避免内存泄漏 */
function Chart({ option, height = 95, empty }: {
  option: echarts.EChartsOption | null; height?: number; empty?: string;
}) {
  // ⚠️ 占位高度必须是【静态 class】：Tailwind 按文本扫描生成 CSS，
  //    `min-h-[${height}px]` 这类动态模板串不会被编译进产物。
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    inst.current = chart;
    // setOption 默认 merge 语义：新 series 按**下标**合并，数组变短时多余的旧 series 不删除。
    // ⚠️ 诚实说明：本组件的 effect 依赖 [option]，cleanup 里 chart.dispose()，
    // option 一变化就 dispose + init 重建全新实例 ⇒ **merge 残留在当前实现下不可能发生**。
    // 因此这里传 notMerge 是**防御性加固 + 与 utils/useChart.ts 的语义对齐**（该 hook 是
    // 全项目唯一「init 一次、复用实例」的实现，早已用 setOption(option, true)），
    // 并非修复某个已观察到的 bug —— 将来若把容器改成 init 一次以省掉实例抖动，此处的
    // notMerge 才会真正开始承担作用。
    chart.setOption(option, true);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
      inst.current = null;
    };
  }, [option]);

  if (!option) {
    return <div style={{ height }}><PanelEmpty text={empty ?? '暂无数据'} minH="h-full" /></div>;
  }
  return <div ref={ref} style={{ height }} className="w-full" />;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-hair bg-white">
      <div className="border-b border-hair px-3 py-1.5">
        <h3 className="text-xs font-semibold text-ink">{title}</h3>
      </div>
      <div className="p-1.5">{children}</div>
    </div>
  );
}

export default function DistributionCharts({ items }: { items: ChartItem[] }) {
  // 行业分布：Top 6 + 其他，环形 + 右侧图例（对齐设计稿）
  const industryOption = useMemo<echarts.EChartsOption | null>(() => {
    const counts = new Map<string, number>();
    for (const it of items) {
      const k = it.industry?.trim() || '未分类';
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    if (counts.size === 0) return null;
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    const top = sorted.slice(0, 6);
    const rest = sorted.slice(6).reduce((s, [, c]) => s + c, 0);
    const data = top.map(([name, value]) => ({ name, value }));
    if (rest > 0) data.push({ name: '其他', value: rest });
    const total = items.length || 1;
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} 只（{d}%）' },
      legend: {
        orient: 'vertical', right: 4, top: 'middle',
        textStyle: { fontSize: 10 }, itemWidth: 8, itemHeight: 8,
        formatter: (name: string) => {
          const d = data.find((x) => x.name === name);
          return d ? `${name}  ${((d.value / total) * 100).toFixed(0)}%` : name;
        },
      },
      series: [{
        type: 'pie', radius: ['40%', '68%'], center: ['34%', '50%'],
        data, label: { show: false },
        color: ['#2563EB', '#DC2626', '#16A34A', '#F59E0B', '#06B6D4', '#A855F7', '#CBD5E1'],
      }],
    };
  }, [items]);

  // Score 分布：固定区间，跨日期可比
  const scoreOption = useMemo<echarts.EChartsOption | null>(() => {
    const vals = items.map((i) => i.score).filter((v): v is number => v != null);
    if (vals.length === 0) return null;
    // 标签千分位整数化：0.002 → "2"（10 字符的 `0.000~0.002` 压到 5 字符内，解决
    // interval:0 强制全显时的挤压重叠）。**必须用 Math.round**：0.008*1000 在 IEEE754
    // 下是 8.000000000000002，取整才能保证边界值精确落在 0/2/4/6/8/10/12 上。
    const bins = fixedHistogram(vals, SCORE_EDGES, (v) => String(Math.round(v * 1000)));
    return {
      tooltip: { trigger: 'axis', formatter: '{b}：{c} 只' },
      grid: { left: 8, right: 20, top: 14, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category', data: bins.map((b) => b[0]),
        // 标签已整数化为千分位，补轴名披露量纲（0~2 即 0‰~2‰），避免被读成 Score=2。
        // ⚠️ 这里**必须是 ‰ 不能用 %**：Score 是小数口径，0~2 折成百分比只有 0.2%，
        //    标 % 会整整差一个量级。grid.right 从 12 放宽到 20 是为 nameLocation:'end'
        //    的轴名让位，否则会和最右侧标签（`>12`）挤在一起。
        name: '‰', nameLocation: 'end', nameGap: 2,
        nameTextStyle: { fontSize: 9, color: '#94A3B8' },
        axisLabel: { fontSize: 9, interval: 0 },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [{
        type: 'bar', data: bins.map((b) => b[1]), barMaxWidth: 22,
        itemStyle: { color: '#2563EB', borderRadius: [2, 2, 0, 0] },
      }],
    };
  }, [items]);

  // 涨跌幅分布（红涨绿跌），区间对齐 A 股习惯
  const pctOption = useMemo<echarts.EChartsOption | null>(() => {
    const vals = items.map((i) => i.pct).filter((v): v is number => v != null);
    if (vals.length === 0) return null;
    // 标签去掉逐个重复的 %（`-5%~-2%` 7 字符 × 7 桶过挤），单位改由 xAxis.name 统一承载
    const bins = fixedHistogram(vals, PCT_EDGES, (v) => v.toFixed(0));
    return {
      tooltip: { trigger: 'axis', formatter: '{b}：{c} 只' },
      grid: { left: 8, right: 20, top: 14, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category', data: bins.map((b) => b[0]),
        // 标签已去掉逐个 %，用轴名统一披露单位。grid.right 从 12 放宽到 20 是为
        // nameLocation:'end' 的轴名让位，否则会和最右侧标签（`>10`）挤在一起。
        name: '%', nameLocation: 'end', nameGap: 2,
        nameTextStyle: { fontSize: 9, color: '#94A3B8' },
        axisLabel: { fontSize: 9, interval: 0 },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [{
        type: 'bar', barMaxWidth: 22,
        data: bins.map(([, c], i) => {
          // 红绿判定必须基于**桶的上界**，不能基于标签首字符 —— 否则标签文案一改
          // （如本次去掉了 %）颜色会静默判错且不报错。桶 i 的上界即 PCT_EDGES[i]，
          // 末桶（>10%）上界为 +∞。上界 <= 0 的桶整体落在非正区间 ⇒ 绿，其余红。
          const hi = i < PCT_EDGES.length ? PCT_EDGES[i] : Infinity;
          return {
            value: c,
            itemStyle: { color: hi <= 0 ? '#16A34A' : '#DC2626', borderRadius: [2, 2, 0, 0] },
          };
        }),
      }],
    };
  }, [items]);

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <Panel title="行业分布">
        <Chart option={industryOption} empty="暂无行业数据" />
      </Panel>
      <Panel title="Score 分布">
        <Chart option={scoreOption} empty="有效样本不足" />
      </Panel>
      <Panel title="涨跌幅分布">
        <Chart option={pctOption} empty="有效样本不足" />
      </Panel>
    </div>
  );
}
