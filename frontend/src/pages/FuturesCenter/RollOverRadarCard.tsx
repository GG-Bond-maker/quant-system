/**
 * 期货中心 · 品种移仓换月监控与理论无套利定价雷达卡片 (RollOverRadarCard)。
 *
 * 理论模型：
 * 1. 持仓量迁移与移仓时机模型 (Roll-over Transition Model)：
 *    - 计算各期限合约未平仓持仓量 (OI) 结构与占比分布；
 *    - 核算移仓进度 (次月OI / (当月OI + 次月OI))，识别常规持仓/移仓预警/换月高峰/主力切换阶段；
 * 2. 持有成本模型 (Cost of Carry Model) 与无套利定价：
 *    - 理论公允价格 F* = S * (1 + (r - q + u) * (DTE / 365))；
 *    - 测算摩擦成本带 [F* - Cost, F* + Cost]，诊断期现正套/反套机会；
 * 3. 沉淀资金规模统计 (Capital Commitment)：
 *    - 全期限合约保证金沉淀量估算，监控机构资产配置水位。
 */
import { useEffect, useMemo, useState } from 'react';
import type * as echarts from '@/lib/echarts';
import { chartPalette, withAlpha } from '@/lib/chartTheme';
import { useChart } from '@/utils/useChart';
import { useTheme } from '@/hooks/useTheme';
import { futuresApi } from '@/api/futures';
import type { FuturesContractItem, TermStructureResult } from '@/types/futures';
import { fmtNum } from '@/utils/format';

interface Props {
  symbol?: string;
  activeContract?: FuturesContractItem | null;
}

export default function RollOverRadarCard({ symbol = 'IF', activeContract }: Props) {
  const [data, setData] = useState<TermStructureResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<'oi_distribution' | 'arbitrage_bands'>('oi_distribution');
  const theme = useTheme();

  useEffect(() => {
    let unmounted = false;
    setLoading(true);

    futuresApi.termStructure(symbol)
      .then((res) => {
        if (!unmounted) setData(res);
      })
      .catch((err) => {
        console.error('Failed to load term structure for rollover radar', err);
      })
      .finally(() => {
        if (!unmounted) setLoading(false);
      });

    return () => {
      unmounted = true;
    };
  }, [symbol]);

  const rollover = data?.rollover_stats;
  const carry = data?.cost_of_carry;
  const contracts = data?.contracts ?? [];

  // 图表配置
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (!contracts.length) return null;
    const p = chartPalette();

    if (mode === 'oi_distribution') {
      const labels = contracts.map((c) => `${c.label}\n(${c.contract_code})`);
      const ois = contracts.map((c) => Math.round((c.open_interest ?? 0) / 1000) / 10); // 万手
      const shares = contracts.map((c) => c.oi_share_pct ?? 0);

      return {
        grid: { left: 48, right: 48, top: 28, bottom: 36 },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' },
          formatter: (params: unknown) => {
            const items = params as Array<{ name: string; value: number; seriesName: string }>;
            if (!items?.length) return '';
            const idx = contracts.findIndex((c) => `${c.label}\n(${c.contract_code})` === items[0].name);
            const c = contracts[idx] ?? contracts[0];
            return `
              <div style="font-size:11px;font-family:monospace">
                <b>${c.label} (${c.contract_code})</b><br/>
                持仓量(OI): <b class="num">${(c.open_interest ?? 0).toLocaleString()} 手</b><br/>
                持仓占比: <b class="num">${c.oi_share_pct ?? 0}%</b><br/>
                成交量: <b class="num">${(c.volume ?? 0).toLocaleString()} 手</b><br/>
                到期天数: <b class="num">${c.days_to_expiry} 天</b>
              </div>
            `;
          },
        },
        legend: {
          data: ['持仓量 (万手)', '持仓占比 (%)'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        xAxis: {
          type: 'category',
          data: labels,
          axisLabel: { fontSize: 9, color: p.INKM },
          axisLine: { lineStyle: { color: p.HAIR } },
        },
        yAxis: [
          {
            type: 'value',
            name: '万手',
            axisLabel: { fontSize: 9, color: p.INKM },
            splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
          },
          {
            type: 'value',
            name: '占比 (%)',
            min: 0,
            max: 100,
            axisLabel: { fontSize: 9, color: p.INKM, formatter: '{value}%' },
            splitLine: { show: false },
          },
        ],
        series: [
          {
            name: '持仓量 (万手)',
            type: 'bar',
            yAxisIndex: 0,
            data: ois,
            barMaxWidth: 24,
            itemStyle: {
              color: (params: any) =>
                params.dataIndex === 0
                  ? p.BRAND
                  : params.dataIndex === 1
                  ? p.INFO
                  : withAlpha(p.INKM, 0.4),
              borderRadius: [4, 4, 0, 0],
            },
          },
          {
            name: '持仓占比 (%)',
            type: 'line',
            yAxisIndex: 1,
            data: shares,
            smooth: true,
            symbolSize: 6,
            itemStyle: { color: p.WARN },
            lineStyle: { width: 2, color: p.WARN },
          },
        ],
      };
    } else {
      // 无套利定价通道视图
      const labels = contracts.map((c) => `${c.label}\n(${c.contract_code})`);
      const prices = contracts.map((c) => c.price);
      const spot = data?.spot_price ?? 3960;
      const fairP = carry?.fair_price ?? spot;
      const upBand = carry?.upper_band ?? fairP * 1.002;
      const lowBand = carry?.lower_band ?? fairP * 0.998;

      return {
        grid: { left: 56, right: 28, top: 28, bottom: 36 },
        tooltip: {
          trigger: 'axis',
        },
        legend: {
          data: ['实际合约价格', '理论公允期价', '无套利上轨', '无套利下轨'],
          top: 0,
          textStyle: { fontSize: 10, color: p.INK2 },
        },
        xAxis: {
          type: 'category',
          data: labels,
          axisLabel: { fontSize: 9, color: p.INKM },
          axisLine: { lineStyle: { color: p.HAIR } },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: p.INKM },
          splitLine: { lineStyle: { color: p.HAIR, type: 'dashed' } },
        },
        series: [
          {
            name: '实际合约价格',
            type: 'line',
            data: prices,
            symbolSize: 8,
            itemStyle: { color: p.BRAND },
            lineStyle: { width: 2.5 },
          },
          {
            name: '理论公允期价',
            type: 'line',
            data: contracts.map(() => fairP),
            lineStyle: { type: 'dashed', color: p.INFO, width: 1.5 },
            symbol: 'none',
          },
          {
            name: '无套利上轨',
            type: 'line',
            data: contracts.map(() => upBand),
            lineStyle: { type: 'dotted', color: p.DANGER, width: 1.2 },
            symbol: 'none',
          },
          {
            name: '无套利下轨',
            type: 'line',
            data: contracts.map(() => lowBand),
            lineStyle: { type: 'dotted', color: p.DOWN, width: 1.2 },
            symbol: 'none',
          },
        ],
      };
    }
  }, [contracts, mode, carry, data?.spot_price, theme]);

  const chartRef = useChart(option);

  // 移仓阶段徽章色彩映射
  const stageBadgeCls = (() => {
    switch (rollover?.status_tag) {
      case 'danger':
        return 'bg-danger/10 text-danger border-danger/30';
      case 'warn':
        return 'bg-warn-bg text-warn border-warn/30';
      case 'brand':
        return 'bg-brand-50 text-brand-700 border-brand-300';
      default:
        return 'bg-down-bg text-down border-down/30';
    }
  })();

  return (
    <div className="flex flex-col rounded-lg border border-hair bg-surface p-3.5 shadow-sm space-y-3">
      {/* 头部导航与模式切换 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">移仓换月时机与理论无套利定价</span>
          {rollover?.stage && (
            <span className={`rounded px-1.5 py-0.2 text-3xs font-semibold border ${stageBadgeCls}`}>
              {rollover.stage} ({rollover.progress_pct}%)
            </span>
          )}
          {carry?.arbitrage_label && (
            <span
              className={`rounded px-1.5 py-0.2 text-3xs font-semibold border ${
                carry.arbitrage_status === 'fair'
                  ? 'bg-surface-sunken text-ink-secondary border-hair'
                  : 'bg-brand-50 text-brand-700 border-brand-300'
              }`}
            >
              {carry.arbitrage_label}
            </span>
          )}
        </div>

        {/* 模式切换 pills */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMode('oi_distribution')}
            className={`rounded px-2 py-0.5 text-2xs transition-colors ${
              mode === 'oi_distribution'
                ? 'bg-brand-600 text-white font-medium'
                : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
            }`}
          >
            📊 持仓迁移分布
          </button>
          <button
            type="button"
            onClick={() => setMode('arbitrage_bands')}
            className={`rounded px-2 py-0.5 text-2xs transition-colors ${
              mode === 'arbitrage_bands'
                ? 'bg-brand-600 text-white font-medium'
                : 'border border-hair bg-surface text-ink-secondary hover:border-brand-200'
            }`}
          >
            🎯 无套利通道
          </button>
        </div>
      </div>

      {/* 核心指标统计区 (4 格紧凑卡片) */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {/* 1. 移仓进度 */}
        <div className="rounded-md border border-hair/80 bg-surface-alt p-2">
          <div className="text-3xs text-ink-muted">换月进度 (次月/近月)</div>
          <div className="flex items-baseline gap-1 mt-0.5">
            <span className="num text-sm font-bold text-ink">
              {rollover ? `${rollover.progress_pct}%` : '—'}
            </span>
            <span className="text-3xs text-ink-muted">
              {rollover ? `DTE ${rollover.days_to_expiry}d` : ''}
            </span>
          </div>
          <div className="text-3xs text-ink-secondary truncate mt-0.5">
            {rollover ? `${rollover.near_code} → ${rollover.next_code}` : '计算中...'}
          </div>
        </div>

        {/* 2. 沉淀资金 */}
        <div className="rounded-md border border-hair/80 bg-surface-alt p-2">
          <div className="text-3xs text-ink-muted">沉淀资金规模 (保证金)</div>
          <div className="flex items-baseline gap-1 mt-0.5">
            <span className="num text-sm font-bold text-brand-700">
              {rollover ? `${rollover.capital_commitment_yi}` : '—'}
            </span>
            <span className="text-3xs text-ink-muted">亿元</span>
          </div>
          <div className="text-3xs text-ink-secondary truncate mt-0.5">
            {activeContract?.margin_per_lot
              ? `一手保证金 ¥${fmtNum(activeContract.margin_per_lot)}`
              : rollover
              ? `全合约 ${(rollover.total_oi / 10000).toFixed(1)}万手`
              : '—'}
          </div>
        </div>

        {/* 3. 理论公允期价 */}
        <div className="rounded-md border border-hair/80 bg-surface-alt p-2">
          <div className="text-3xs text-ink-muted">持有成本公允期价 (F*)</div>
          <div className="flex items-baseline gap-1 mt-0.5">
            <span className="num text-sm font-bold text-ink">
              {carry ? fmtNum(carry.fair_price) : '—'}
            </span>
          </div>
          <div className="text-3xs text-ink-secondary truncate mt-0.5">
            {carry ? `理论基差 ${fmtNum(carry.fair_basis)}点` : '—'}
          </div>
        </div>

        {/* 4. 套利偏离度 */}
        <div className="rounded-md border border-hair/80 bg-surface-alt p-2">
          <div className="text-3xs text-ink-muted">基差偏离度 / 摩擦带</div>
          <div className="flex items-baseline gap-1 mt-0.5">
            <span
              className={`num text-sm font-bold ${
                (carry?.mispricing ?? 0) >= 0 ? 'text-up' : 'text-down'
              }`}
            >
              {carry ? `${carry.mispricing > 0 ? '+' : ''}${carry.mispricing}` : '—'}
            </span>
            <span className="text-3xs text-ink-muted">点</span>
          </div>
          <div className="text-3xs text-ink-secondary truncate mt-0.5">
            {carry ? `摩擦宽 ±${carry.friction_val}` : '—'}
          </div>
        </div>
      </div>

      {/* 图表展示区 */}
      {loading && (
        <div className="flex h-44 items-center justify-center text-xs text-ink-muted animate-pulse">
          正在核算移仓换月时序分布与无套利定价...
        </div>
      )}

      {!loading && (
        <div ref={chartRef} className="h-44 w-full" data-testid="rollover-radar-chart" />
      )}

      {/* 底部策略指引与状态说明条 */}
      <div className="rounded bg-surface-sunken px-2.5 py-1.5 text-2xs text-ink-secondary flex items-start gap-1.5">
        <span className="text-brand-600 font-semibold shrink-0">💡 量化策略指引:</span>
        <span className="leading-snug">
          {mode === 'oi_distribution'
            ? rollover?.stage_desc ?? '监控近远月持仓转移比率，辅助 CTA 展期与中性套保滑点控制。'
            : carry?.arbitrage_desc ?? '基于持有成本定价模型监控现货指数与各合约理论基差定价。'}
        </span>
      </div>
    </div>
  );
}
