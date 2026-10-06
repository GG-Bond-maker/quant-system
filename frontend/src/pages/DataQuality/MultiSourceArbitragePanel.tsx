import { useCallback, useEffect, useMemo, useState } from 'react';
import * as echarts from '@/lib/echarts';
import { chartPalette } from '@/lib/chartTheme';
import { useTheme } from '@/hooks/useTheme';
import { useChart } from '@/utils/useChart';
import { opsApi, type MultiSourceArbitrageResult } from '@/api/production';
import { ApiError } from '@/api/client';
import { SectionCard } from '@/components/ui';

export default function MultiSourceArbitragePanel() {
  const [limit, setLimit] = useState(50);
  const [data, setData] = useState<MultiSourceArbitrageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const theme = useTheme();
  const p = useMemo(() => chartPalette(), [theme]);

  const loadData = useCallback(async (sampleLimit: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await opsApi.multiSourceArbitrage(sampleLimit);
      setData(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '获取多源行情对账数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData(limit);
  }, [limit, loadData]);

  // ECharts 残差偏离度走廊散点图
  const chartOption = useMemo<echarts.EChartsOption | null>(() => {
    if (!data?.items || data.items.length === 0) return null;

    const scatterData = data.items.map((item) => [
      item.primary_price,
      item.deviation_pct,
      item.symbol,
      item.name,
      item.secondary_price,
      item.status,
    ]);

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const item = params as { value: [number, number, string, string, number, string] };
          if (!item?.value) return '';
          const [, dev, sym, name, secP, st] = item.value;
          return `<b>${name} (${sym})</b><br/>` +
                 `主源价格 (Universe): ¥${item.value[0]}<br/>` +
                 `备源价格 (AKShare): ¥${secP}<br/>` +
                 `残差偏离度: ${dev.toFixed(4)}%<br/>` +
                 `状态: ${st === 'CONSISTENT' ? '完全一致' : st === 'TOLERABLE' ? '容差走廊内' : '异常偏离待仲裁'}`;
        },
      },
      grid: {
        top: 25,
        bottom: 25,
        left: 45,
        right: 25,
      },
      xAxis: {
        type: 'value',
        name: '基准价格 (元)',
        nameTextStyle: { color: p.INKM, fontSize: 10 },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '偏离度 (%)',
        nameTextStyle: { color: p.INKM, fontSize: 10 },
        splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        axisLabel: { color: p.INKM, fontSize: 10 },
      },
      series: [
        {
          name: '行情偏离散点',
          type: 'scatter',
          symbolSize: 8,
          data: scatterData,
          itemStyle: {
            color: (params: unknown) => {
              const val = (params as { value: [number, number, string, string, number, string] }).value;
              const st = val[5];
              if (st === 'DISCREPANCY') return p.DANGER;
              if (st === 'TOLERABLE') return p.WARN;
              return p.SUCCESS;
            },
          },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              {
                yAxis: 0.3,
                lineStyle: { color: p.DANGER, type: 'dashed', width: 1.5 },
                label: { formatter: '容差走廊上限 (0.30%)', color: p.DANGER, fontSize: 10, position: 'insideEndTop' },
              },
              {
                yAxis: 0.05,
                lineStyle: { color: p.WARN, type: 'dotted', width: 1 },
                label: { formatter: '一致性阈值 (0.05%)', color: p.WARN, fontSize: 10, position: 'insideEndTop' },
              },
            ],
          },
        },
      ],
    };
  }, [data, p]);

  const chartRef = useChart(chartOption);

  return (
    <SectionCard
      title="多源行情高频交叉对账与偏离度仲裁（Eastmoney vs AKShare 双源稽核）"
      action={
        <div className="flex items-center gap-2">
          <span className="text-2xs text-ink-muted">样本量：</span>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded border border-hair bg-surface px-2 py-1 text-xs outline-none focus:border-brand-300"
          >
            <option value={30}>30 标的</option>
            <option value={50}>50 标的</option>
            <option value={100}>100 标的</option>
          </select>
          <button
            onClick={() => void loadData(limit)}
            className="rounded bg-brand-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-brand-600"
          >
            重新对账
          </button>
        </div>
      }
    >
      {error && (
        <div className="rounded-md border border-danger/30 bg-danger-bg p-2 text-2xs text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-10 text-center text-xs text-ink-muted">正在进行主备多源行情高频交叉比对与残差计算…</div>
      ) : data ? (
        <div className="space-y-3">
          {/* 指标总览 */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">稽核标的总数</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.total_checked} 只
              </div>
              <div className="text-3xs text-ink-muted">基准日: {data.trade_date}</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">完全一致标的 (&lt;0.05%)</div>
              <div className="num mt-1 text-base font-semibold text-success">
                {data.consistent_count} 只
              </div>
              <div className="text-3xs text-ink-muted">零偏离高置信标的</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">容差走廊内 (0.05%~0.3%)</div>
              <div className="num mt-1 text-base font-semibold text-warn">
                {data.tolerable_count} 只
              </div>
              <div className="text-3xs text-ink-muted">微小跳步容差内</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">异常偏离标的 (≥0.3%)</div>
              <div className={`num mt-1 text-base font-semibold ${data.discrepancy_count > 0 ? 'text-danger' : 'text-ink-secondary'}`}>
                {data.discrepancy_count} 只
              </div>
              <div className="text-3xs text-ink-muted">触发仲裁报警隔离</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">平均残差偏离度</div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {data.mean_deviation_pct.toFixed(4)}%
              </div>
              <div className="text-3xs text-ink-muted">主备相对误差均值</div>
            </div>
            <div className="rounded-md border border-hair bg-surface-alt p-2.5">
              <div className="text-2xs text-ink-muted">最大截面残差</div>
              <div className={`num mt-1 text-base font-semibold ${data.max_deviation_pct >= 0.3 ? 'text-danger' : 'text-ink'}`}>
                {data.max_deviation_pct.toFixed(4)}%
              </div>
              <div className="text-3xs text-ink-muted">极限单点偏离读数</div>
            </div>
          </div>

          {/* ECharts 散点走廊图 */}
          <div className="rounded-md border border-hair bg-surface p-2">
            <div ref={chartRef} style={{ height: 220 }} className="w-full" />
          </div>

          {/* 对账明细表 */}
          <div className="overflow-x-auto rounded-lg border border-hair">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt text-2xs text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">标的代码</th>
                  <th className="px-3 py-2 font-medium">名称</th>
                  <th className="px-3 py-2 text-right font-medium">主源价格 (Universe)</th>
                  <th className="px-3 py-2 text-right font-medium">备源价格 (AKShare)</th>
                  <th className="px-3 py-2 text-right font-medium">相对偏离度</th>
                  <th className="px-3 py-2 text-center font-medium">仲裁结论</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {data.items.slice(0, 15).map((it) => (
                  <tr key={it.symbol} className="hover:bg-surface-alt/50">
                    <td className="px-3 py-1.5 font-mono font-medium text-ink">{it.symbol}</td>
                    <td className="px-3 py-1.5 text-ink-secondary">{it.name}</td>
                    <td className="num px-3 py-1.5 text-right font-medium text-ink">¥{it.primary_price.toFixed(2)}</td>
                    <td className="num px-3 py-1.5 text-right text-ink-secondary">¥{it.secondary_price.toFixed(2)}</td>
                    <td className={`num px-3 py-1.5 text-right font-semibold ${
                      it.status === 'DISCREPANCY' ? 'text-danger' : it.status === 'TOLERABLE' ? 'text-warn' : 'text-success'
                    }`}>
                      {it.deviation_pct.toFixed(4)}%
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      <span className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                        it.status === 'DISCREPANCY'
                          ? 'bg-danger-bg text-danger'
                          : it.status === 'TOLERABLE'
                          ? 'bg-warn-bg text-warn'
                          : 'bg-success-bg text-success'
                      }`}>
                        {it.status_label}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="text-2xs text-ink-muted">
            {'ⓘ 多源交叉对账准则：当双源日线价格偏离度超过 0.30% 容差走廊时，系统自动判定该标的处于异常状态，禁止参与实盘模型推理与模拟撮合。'}
          </div>
        </div>
      ) : null}
    </SectionCard>
  );
}
