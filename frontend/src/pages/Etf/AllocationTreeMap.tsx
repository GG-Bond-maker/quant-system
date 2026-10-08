/**
 * 大类资产配置与行业轮动矩阵（TreeMap 层级树图，P1 进阶组件）。
 *
 * 量化原理：
 * - ETF 是自上而下宏观配置与行业轮动执行的核心工具。
 * - 层级树图将全市场 ETF 组织为「大类板块 -> 代表标的」双层架构：
 *   1. 矩形面积大小映射资产规模（AUM，亿元）；
 *   2. 填充色相映射区间涨跌幅（红涨绿跌）；
 *   3. 直观展示资金偏好分布与宏观风格轮动。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { etfApi } from '@/api/etf';
import type { EtfAllocationData } from '@/types/etf';
import { chartPalette } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { PanelEmpty } from '@/components/ui';

export default function AllocationTreeMap({
  height = 280,
  onSelectBoard,
}: {
  height?: number;
  onSelectBoard?: (board: string) => void;
}) {
  const theme = useTheme();
  const [data, setData] = useState<EtfAllocationData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    etfApi.allocation()
      .then((res) => {
        if (active) setData(res);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.boards?.length) return null;
    const p = chartPalette();

    const treeData = data.boards.map((b) => {
      const children = (b.children || []).map((c) => ({
        name: `${c.name}\n${c.pct != null ? (c.pct >= 0 ? '+' : '') + c.pct.toFixed(2) + '%' : '—'}`,
        value: c.size_yi > 0 ? c.size_yi : 1.0,
        rawPct: c.pct ?? 0,
        code: c.code,
        price: c.price,
        size_yi: c.size_yi,
        board: b.name,
      }));

      return {
        name: `${b.name} (${b.size_yi.toFixed(0)}亿)`,
        value: b.size_yi > 0 ? b.size_yi : 1.0,
        board: b.name,
        avgPct: b.avg_pct,
        children: children.length ? children : undefined,
      };
    });

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const d = params.data;
          if (!d) return '';
          if (d.code) {
            const pctStr = d.rawPct != null ? `${d.rawPct >= 0 ? '+' : ''}${d.rawPct.toFixed(2)}%` : '—';
            return `
              <div class="font-sans text-xs">
                <div class="font-semibold text-ink">${d.code} ${d.name.split('\n')[0]}</div>
                <div class="mt-1 text-ink-muted">所属板块: <span class="text-ink">${d.board}</span></div>
                <div>最新规模: <span class="num text-ink font-semibold">${d.size_yi?.toFixed(2)} 亿</span></div>
                <div>当日涨跌: <span class="num font-semibold ${d.rawPct >= 0 ? 'text-up' : 'text-down'}">${pctStr}</span></div>
                ${d.price != null ? `<div>最新市价: <span class="num text-ink">${d.price}</span></div>` : ''}
              </div>
            `;
          }
          return `
            <div class="font-sans text-xs">
              <div class="font-semibold text-ink">${d.board} 板块总览</div>
              <div>总规模: <span class="num text-ink font-semibold">${d.value?.toFixed(1)} 亿</span></div>
              <div>平均涨跌: <span class="num ${d.avgPct >= 0 ? 'text-up' : 'text-down'}">${d.avgPct >= 0 ? '+' : ''}${d.avgPct}%</span></div>
            </div>
          `;
        },
      },
      series: [
        {
          type: 'treemap',
          width: '98%',
          height: '92%',
          top: '4%',
          left: '1%',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            formatter: '{b}',
            fontSize: 10,
            color: p.ON_SOLID,
          },
          levels: [
            {
              itemStyle: {
                borderColor: p.HAIR,
                borderWidth: 1,
                gapWidth: 2,
              },
            },
            {
              colorSaturation: [0.35, 0.65],
              itemStyle: {
                borderColor: p.SUNKEN,
                borderWidth: 1,
                gapWidth: 1,
              },
            },
          ],
          visualMin: -3.0,
          visualMax: 3.0,
          visualDimension: 'rawPct',
          levels_visual: true,
          color: [p.UP, p.WARN, p.BRAND, p.SUCCESS, p.DOWN],
          data: treeData,
        },
      ],
    };
  }, [data, theme]);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const chartCallbackRef = useChart(option);
  const setBox = useCallback(
    (el: HTMLDivElement | null) => {
      boxRef.current = el;
      chartCallbackRef(el);
    },
    [chartCallbackRef]
  );

  useEffect(() => {
    if (!boxRef.current) return;
    const inst = echarts.getInstanceByDom(boxRef.current);
    if (!inst) return;
    const onClick = (params: any) => {
      const board = params.data?.board;
      if (board && onSelectBoard) {
        onSelectBoard(board);
      }
    };
    inst.on('click', onClick);
    return () => {
      inst.off('click', onClick);
    };
  }, [option, onSelectBoard]);

  if (loading && !data) {
    return (
      <div style={{ height }} className="flex w-full items-center justify-center">
        <span className="text-xs text-ink-muted">正在构建全市场大类资产与行业轮动矩阵...</span>
      </div>
    );
  }

  if (!option) {
    return (
      <div style={{ height }} className="w-full">
        <PanelEmpty text="暂无大类资产数据" minH="h-full" />
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col">
      <div ref={setBox} style={{ height }} className="min-h-0 w-full flex-1" />
      <div className="flex items-center justify-between px-2 py-0.5 text-2xs text-ink-muted">
        <span>面积 = 资产规模(AUM) · 节点颜色 = 涨跌幅 · 点击板块可联动筛选</span>
        {data?.total_size_yi ? <span>统计规模: {data.total_size_yi.toFixed(0)} 亿元</span> : null}
      </div>
    </div>
  );
}
