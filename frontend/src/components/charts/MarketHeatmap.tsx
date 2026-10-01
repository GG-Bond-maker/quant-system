/**
 * 市场涨跌分布 + 资金流向看板（ECharts）。
 *
 * - 左：涨/平/跌 玫瑰图（红涨绿跌，含涨停/跌停计数标注）；
 * - 右：北向净流入柱（数据块不可用时显示占位文案，与后端降级语义一致）。
 */
import { useEffect, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import type { HeatBlock, MarketMoneyFlowBlock } from '@/types/stock';

interface Props {
  heat: HeatBlock | null;
  moneyFlow: MarketMoneyFlowBlock | null;
  height?: number;
}

/*
 * ⚠️ 本文件原先把涨跌色硬编码为 `#ef4444` / `#22c55e`——这两个值既不是
 * `tailwind.config.js` 里的 token（`up #D92B2B` / `down #12995B`），
 * 又因 canvas 不参与 CSS 级联而在暗色主题下**完全不跟随**（继续画亮红亮绿）。
 * 现统一经 `chartPalette()` 读 CSS 变量。
 */

function heatOption(heat: HeatBlock): echarts.EChartsOption {
  const p = chartPalette();
  const ok = heat.status === 'ok' && heat.up != null;
  const data = ok
    ? [
        { name: `上涨 ${heat.up}`, value: heat.up ?? 0, itemStyle: { color: p.UP } },
        { name: `平盘 ${heat.flat}`, value: heat.flat ?? 0, itemStyle: { color: p.FLAT } },
        { name: `下跌 ${heat.down}`, value: heat.down ?? 0, itemStyle: { color: p.DOWN } },
      ]
    : [];
  return {
    tooltip: { trigger: 'item', formatter: '{b}（{d}%）' },
    legend: { bottom: 0, textStyle: { fontSize: 11, color: p.INK2 } },
    title: ok
      ? {
          text: `涨停 ${heat.limit_up} · 跌停 ${heat.limit_down}`,
          subtext: '涨停为 ±9.8% 近似口径',
          left: 'center',
          top: 0,
          textStyle: { fontSize: 12, fontWeight: 'normal', color: p.INK },
          subtextStyle: { fontSize: 10, color: p.INKM },
        }
      : {
          text: '涨跌分布暂不可用',
          subtext: heat.reason ?? '数据源失败',
          left: 'center',
          top: 'middle',
          textStyle: { fontSize: 13, fontWeight: 'normal', color: p.INKM },
        },
    series: [
      {
        type: 'pie',
        roseType: 'radius',
        radius: ['18%', '62%'],
        center: ['50%', '56%'],
        data,
        label: { show: false },
      },
    ],
  };
}

function moneyOption(money: MarketMoneyFlowBlock | null): echarts.EChartsOption {
  const p = chartPalette();
  const ok = money?.status === 'ok' && money.north_net_today != null;
  // ⚠️ `ok` 为假时**不得**保留 `value = 0`：`setOption(opt, true)` 的 notMerge
  // 只清掉被省略的组件，仍在 series 里的 0 会渲染成"±0.00 亿"的柱子——即用 0
  // 冒充"北向不可得"（同一缺陷的第三个面）。故用 `null`：ECharts 对 null 值
  // 按空数据处理，明确不画柱；坐标轴与 series 本就已按 `ok` 清空。
  const value = ok ? Number(money.north_net_today) : null;
  return {
    title: ok
      ? undefined
      : {
          text: '资金数据暂不可用',
          subtext: money?.reason ?? '',
          left: 'center',
          top: 'middle',
          textStyle: { fontSize: 13, fontWeight: 'normal', color: p.INKM },
        },
    grid: { left: 60, right: 24, top: 24, bottom: 24 },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: (v: number) => `${v.toFixed(0)} 亿`, color: p.INKM },
      splitLine: { lineStyle: { color: p.HAIR } },
    },
    yAxis: {
      type: 'category',
      data: ok ? ['北向净流入'] : [],
      axisLabel: { fontSize: 11, color: p.INK2 },
    },
    series: [
      {
        type: 'bar',
        data: ok ? [{ value, itemStyle: { color: Number(value) >= 0 ? p.UP : p.DOWN } }] : [],
        barWidth: 22,
        label: {
          show: ok,
          position: 'right',
          color: p.INK2,
          // `value` 为 `number | null`（ok=false ⇒ null，见上方注释）⇒ 必须判空，
          // 否则 `null.toFixed` 会抛 TypeError 且 `null >= 0` 恒为 true（假正值）。
          formatter: () => (value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)} 亿`),
        },
      },
    ],
  };
}

export default function MarketHeatmap({ heat, moneyFlow, height = 220 }: Props) {
  const heatRef = useRef<HTMLDivElement | null>(null);
  const moneyRef = useRef<HTMLDivElement | null>(null);
  const heatInst = useRef<echarts.ECharts | null>(null);
  const moneyInst = useRef<echarts.ECharts | null>(null);
  // 主题变化时驱动 option 重设（见下方 effect 注释）
  const theme = useTheme();

  // 初始化（一次），ResizeObserver 自适应
  useEffect(() => {
    const els = [heatRef.current, moneyRef.current];
    const insts: echarts.ECharts[] = [];
    els.forEach((el) => {
      if (el) {
        const inst = echarts.init(el);
        insts.push(inst);
      }
    });
    heatInst.current = insts[0] ?? null;
    moneyInst.current = insts[1] ?? null;
    const ro = new ResizeObserver(() => insts.forEach((i) => i.resize()));
    els.forEach((el) => el && ro.observe(el));
    return () => {
      ro.disconnect();
      insts.forEach((i) => i.dispose());
      heatInst.current = null;
      moneyInst.current = null;
    };
  }, []);

  // 数据到达时更新 option。
  // ⚠️ 主题也必须进依赖：`chartPalette()` 现读 `<html>` 上的 CSS 变量，
  // 而 ECharts canvas 不参与 CSS 级联——不重设 option 则切主题后图表保持旧色。
  useEffect(() => {
    heatInst.current?.setOption(heatOption(heat ?? { status: 'unavailable' }), true);
  }, [heat, theme]);
  useEffect(() => {
    moneyInst.current?.setOption(moneyOption(moneyFlow), true);
  }, [moneyFlow, theme]);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <div
        ref={heatRef}
        style={{ height }}
        className="w-full rounded-xl border border-hair bg-surface"
      />
      <div
        ref={moneyRef}
        style={{ height }}
        className="w-full rounded-xl border border-hair bg-surface"
      />
    </div>
  );
}
