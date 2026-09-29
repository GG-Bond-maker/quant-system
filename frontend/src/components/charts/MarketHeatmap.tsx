/**
 * 市场涨跌分布 + 资金流向看板（ECharts）。
 *
 * - 左：涨/平/跌 玫瑰图（红涨绿跌，含涨停/跌停计数标注）；
 * - 右：北向净流入柱（数据块不可用时显示占位文案，与后端降级语义一致）。
 */
import { useEffect, useRef } from 'react';
import * as echarts from '@/lib/echarts';
import type { HeatBlock, MarketMoneyFlowBlock } from '@/types/stock';

interface Props {
  heat: HeatBlock | null;
  moneyFlow: MarketMoneyFlowBlock | null;
  height?: number;
}

function heatOption(heat: HeatBlock): echarts.EChartsOption {
  const ok = heat.status === 'ok' && heat.up != null;
  const data = ok
    ? [
        { name: `上涨 ${heat.up}`, value: heat.up ?? 0, itemStyle: { color: '#ef4444' } },
        { name: `平盘 ${heat.flat}`, value: heat.flat ?? 0, itemStyle: { color: '#9ca3af' } },
        { name: `下跌 ${heat.down}`, value: heat.down ?? 0, itemStyle: { color: '#22c55e' } },
      ]
    : [];
  return {
    tooltip: { trigger: 'item', formatter: '{b}（{d}%）' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    title: ok
      ? {
          text: `涨停 ${heat.limit_up} · 跌停 ${heat.limit_down}`,
          subtext: '涨停为 ±9.8% 近似口径',
          left: 'center',
          top: 0,
          textStyle: { fontSize: 12, fontWeight: 'normal' },
          subtextStyle: { fontSize: 10, color: '#94a3b8' },
        }
      : {
          text: '涨跌分布暂不可用',
          subtext: heat.reason ?? '数据源失败',
          left: 'center',
          top: 'middle',
          textStyle: { fontSize: 13, fontWeight: 'normal', color: '#94a3b8' },
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
  const ok = money?.status === 'ok' && money.north_net_today != null;
  const value = ok ? Number(money?.north_net_today ?? 0) : 0;
  return {
    title: ok
      ? undefined
      : {
          text: '资金数据暂不可用',
          subtext: money?.reason ?? '',
          left: 'center',
          top: 'middle',
          textStyle: { fontSize: 13, fontWeight: 'normal', color: '#94a3b8' },
        },
    grid: { left: 60, right: 24, top: 24, bottom: 24 },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: (v: number) => `${v.toFixed(0)} 亿` },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    yAxis: {
      type: 'category',
      data: ok ? ['北向净流入'] : [],
      axisLabel: { fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        data: ok ? [{ value, itemStyle: { color: value >= 0 ? '#ef4444' : '#22c55e' } }] : [],
        barWidth: 22,
        label: {
          show: ok,
          position: 'right',
          formatter: () => `${value >= 0 ? '+' : ''}${value.toFixed(2)} 亿`,
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

  // 数据到达时更新 option
  useEffect(() => {
    heatInst.current?.setOption(heatOption(heat ?? { status: 'unavailable' }), true);
  }, [heat]);
  useEffect(() => {
    moneyInst.current?.setOption(moneyOption(moneyFlow), true);
  }, [moneyFlow]);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <div
        ref={heatRef}
        style={{ height }}
        className="w-full rounded-xl border border-slate-200 bg-white"
      />
      <div
        ref={moneyRef}
        style={{ height }}
        className="w-full rounded-xl border border-slate-200 bg-white"
      />
    </div>
  );
}
