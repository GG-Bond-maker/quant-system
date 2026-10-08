/**
 * 组合持仓流动性瓶颈与变现天数（Days-to-Liquidate）锁喉诊断卡片。
 * 对标 Bloomberg PORT / WorldQuant BRAIN 资金容量短板风险控制体系。
 */
import { fmtNum } from '@/utils/format';
import type { PortfolioBottleneckResult } from '@/api/production';

export interface PortfolioBottleneckCardProps {
  data: PortfolioBottleneckResult | null | undefined;
  targetAum: number;
  participationCap: number;
  liquidationDaysTarget: number;
  onAumChange: (val: number) => void;
  onCapChange: (val: number) => void;
  onDaysChange: (val: number) => void;
  loading?: boolean;
}

export default function PortfolioBottleneckCard({
  data,
  targetAum,
  participationCap,
  liquidationDaysTarget,
  onAumChange,
  onCapChange,
  onDaysChange,
  loading = false,
}: PortfolioBottleneckCardProps) {
  const highStressCount = data?.holdings?.filter((h) => h.stress_level === 'high').length ?? 0;

  return (
    <div className="space-y-3">
      {/* 警戒与诊断横幅 */}
      {data?.bottleneck_symbol && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-2xs">
          <div className="flex items-center gap-2 text-ink">
            <span className="font-semibold text-warn">⚠️ 流动性锁喉短板诊断：</span>
            <span>
              标的 <b className="font-mono text-brand-700">{data.bottleneck_symbol}</b>
              {data.bottleneck_name && ` (${data.bottleneck_name})`} 是当前组合的流动性木桶最短板。
            </span>
            <span className="text-ink-secondary">
              在 {liquidationDaysTarget} 日变现、{(participationCap * 100).toFixed(0)}% ADV 参与率约束下，
              组合最大承载规模上限为{' '}
              <b className="num text-danger">{data.bottleneck_capacity_yi != null ? `${data.bottleneck_capacity_yi} 亿元` : '—'}</b>
            </span>
          </div>
          {highStressCount > 0 && (
            <span className="rounded bg-danger-bg px-1.5 py-0.5 text-2xs font-medium text-danger">
              {highStressCount} 只标的处于高变现压力 (DtL &gt; {liquidationDaysTarget} 日)
            </span>
          )}
        </div>
      )}

      {/* 交互调节参数条 */}
      <div className="grid grid-cols-1 gap-3 rounded-lg border border-hair bg-surface-alt p-3 sm:grid-cols-3">
        <label className="space-y-1 text-2xs text-ink-secondary">
          <div className="flex justify-between">
            <span>组合目标规模 (AUM)</span>
            <span className="num font-semibold text-ink">{(targetAum / 1e4).toFixed(0)} 万元 ({ (targetAum / 1e8).toFixed(2) } 亿元)</span>
          </div>
          <input
            type="range"
            min={1e6}
            max={5e8}
            step={5e6}
            value={targetAum}
            onChange={(e) => onAumChange(Number(e.target.value))}
            className="w-full accent-brand-500"
          />
          <span className="block text-ink-muted">100 万 ~ 5 亿元</span>
        </label>

        <label className="space-y-1 text-2xs text-ink-secondary">
          <div className="flex justify-between">
            <span>变现参与率上限 (ADV)</span>
            <span className="num font-semibold text-ink">{(participationCap * 100).toFixed(1)}%</span>
          </div>
          <input
            type="range"
            min={0.01}
            max={0.30}
            step={0.01}
            value={participationCap}
            onChange={(e) => onCapChange(Number(e.target.value))}
            className="w-full accent-brand-500"
          />
          <span className="block text-ink-muted">1% ~ 30%（机构风控基准 10%）</span>
        </label>

        <label className="space-y-1 text-2xs text-ink-secondary">
          <div className="flex justify-between">
            <span>允许变现天数约束</span>
            <span className="num font-semibold text-ink">{liquidationDaysTarget} 交易日</span>
          </div>
          <input
            type="range"
            min={1}
            max={10}
            step={1}
            value={liquidationDaysTarget}
            onChange={(e) => onDaysChange(Number(e.target.value))}
            className="w-full accent-brand-500"
          />
          <span className="block text-ink-muted">1 ~ 10 日平仓窗口</span>
        </label>
      </div>

      {/* 核心指标统计卡 */}
      <div className="grid grid-cols-2 gap-2 text-2xs sm:grid-cols-4">
        <div className="rounded-md border border-hair bg-surface p-2.5">
          <div className="text-ink-muted">短板锁喉容量上限</div>
          <div className="num mt-1 text-lg font-semibold text-danger">
            {data?.bottleneck_capacity_yi != null ? `${data.bottleneck_capacity_yi} 亿元` : '—'}
          </div>
          <div className="text-2xs text-ink-muted">{data?.bottleneck_symbol || '无短板约束'}</div>
        </div>

        <div className="rounded-md border border-hair bg-surface p-2.5">
          <div className="text-ink-muted">加权平均变现天数</div>
          <div className="num mt-1 text-lg font-semibold text-ink">
            {data?.weighted_dtl != null ? `${data.weighted_dtl} 天` : '—'}
          </div>
          <div className="text-2xs text-ink-muted">Σ(权重 × DtL) 综合流动性</div>
        </div>

        <div className="rounded-md border border-hair bg-surface p-2.5">
          <div className="text-ink-muted">高流动性压力标的</div>
          <div className={`num mt-1 text-lg font-semibold ${highStressCount > 0 ? 'text-danger' : 'text-success'}`}>
            {highStressCount} 只
          </div>
          <div className="text-2xs text-ink-muted">变现天数 &gt; {liquidationDaysTarget} 日警戒</div>
        </div>

        <div className="rounded-md border border-hair bg-surface p-2.5">
          <div className="text-ink-muted">持仓覆盖标的数</div>
          <div className="num mt-1 text-lg font-semibold text-ink">
            {data?.holdings_count ?? 0} 只
          </div>
          <div className="text-2xs text-ink-muted">真实持仓逐只穿透计算</div>
        </div>
      </div>

      {/* 各持仓标的变现天数与流动性短板穿透表格 */}
      {data?.holdings && data.holdings.length > 0 && (
        <div className="overflow-x-auto">
          <div className="mb-1 text-2xs font-medium text-ink-secondary">
            组合持仓微观流动性与变现天数穿透明细表（按变现压力降序排列）
          </div>
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-hair text-ink-secondary">
                <th className="py-1 text-left font-medium">标的代码 / 名称</th>
                <th className="py-1 text-left font-medium">所属行业</th>
                <th className="py-1 text-right font-medium">持仓权重</th>
                <th className="py-1 text-right font-medium">持仓市值 (万元)</th>
                <th className="py-1 text-right font-medium">20日 ADV (万元)</th>
                <th className="py-1 text-right font-medium">变现天数 (DtL)</th>
                <th className="py-1 text-right font-medium">单标的容量上限</th>
                <th className="py-1 text-center font-medium">流动性压力</th>
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => {
                const isBottleneck = h.symbol === data.bottleneck_symbol;
                return (
                  <tr
                    key={h.symbol}
                    className={`border-b border-hair ${
                      isBottleneck
                        ? 'bg-warn/10 font-medium text-warn'
                        : 'hover:bg-surface-alt'
                    }`}
                  >
                    <td className="py-1 font-mono">
                      {h.symbol} <span className="font-sans text-ink-muted">({h.name})</span>
                      {isBottleneck && <span className="ml-1 text-2xs text-danger font-semibold">⚠️ 锁喉</span>}
                    </td>
                    <td className="py-1">{h.industry}</td>
                    <td className="num py-1 text-right">{(h.weight * 100).toFixed(1)}%</td>
                    <td className="num py-1 text-right font-mono">{fmtNum(h.position_val_wan)}</td>
                    <td className="num py-1 text-right font-mono">{fmtNum(h.adv_wan)}</td>
                    <td
                      className={`num py-1 text-right font-semibold ${
                        h.stress_level === 'high'
                          ? 'text-danger'
                          : h.stress_level === 'medium'
                          ? 'text-warn'
                          : 'text-success'
                      }`}
                    >
                      {h.days_to_liquidate.toFixed(1)} 日
                    </td>
                    <td className="num py-1 text-right font-mono">{h.capacity_limit_yi} 亿元</td>
                    <td className="py-1 text-center">
                      <span
                        className={`inline-block rounded px-1.5 py-0.2 text-2xs ${
                          h.stress_level === 'high'
                            ? 'bg-danger-bg text-danger font-semibold'
                            : h.stress_level === 'medium'
                            ? 'bg-warn/20 text-warn font-medium'
                            : 'bg-success/15 text-success'
                        }`}
                      >
                        {h.stress_level === 'high' ? '高压力' : h.stress_level === 'medium' ? '中等' : '充沛'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {loading && <div className="text-center text-2xs text-brand-600">正在重算持仓流动性短板…</div>}
    </div>
  );
}
