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
 * 固定区间直方图（区间与设计稿一致，不随数据动态伸缩）：
 * 固定区间的好处是跨日期可比 —— 今天 0.08~0.10 和昨天 0.08~0.10 是同一个桶。
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
    // 低于首边界落入第 0 桶（<edges[0]），必须先判，否则 -6% 会被
    // findIndex 判成 -1 而错分到「>10%」桶
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
    chart.setOption(option);
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
    const bins = fixedHistogram(vals, [0, 0.002, 0.004, 0.006, 0.008, 0.01, 0.012],
      (v) => v.toFixed(3));
    return {
      tooltip: { trigger: 'axis', formatter: '{b}：{c} 只' },
      grid: { left: 8, right: 12, top: 14, bottom: 8, containLabel: true },
      xAxis: { type: 'category', data: bins.map((b) => b[0]), axisLabel: { fontSize: 9, interval: 0 } },
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
    const bins = fixedHistogram(vals, [-5, -2, 0, 2, 5, 10], (v) => `${v.toFixed(0)}%`);
    return {
      tooltip: { trigger: 'axis', formatter: '{b}：{c} 只' },
      grid: { left: 8, right: 12, top: 14, bottom: 8, containLabel: true },
      xAxis: { type: 'category', data: bins.map((b) => b[0]), axisLabel: { fontSize: 9, interval: 0 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
      series: [{
        type: 'bar', barMaxWidth: 22,
        data: bins.map(([label, c]) => ({
          value: c,
          // 负区间桶（< -5%、-5~-2%）为绿，其余为红
          itemStyle: { color: label.startsWith('<') || label.startsWith('-') ? '#16A34A' : '#DC2626', borderRadius: [2, 2, 0, 0] },
        })),
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
