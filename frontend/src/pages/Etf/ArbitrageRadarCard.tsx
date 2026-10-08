/**
 * ETF 折溢价与套利监控雷达（P1 进阶组件）。
 *
 * 量化原理：
 * - 实时监测二级市场现价 (Price) 与一级市场参考净值 (IOPV) 的偏离度：
 *   折溢价率(%) = (Price - IOPV) / IOPV * 100%
 * - 高溢价 (>2% 或 >5%)：提示二级市场过热风险、融券卖出或一二级市场套利平价回归；
 * - 高折价 (<-1%)：提示具备一揽子底层资产折价安全边际或申赎套利窗口。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { etfApi } from '@/api/etf';
import type { EtfArbitrageData, EtfItem } from '@/types/etf';
import { fmtNum, fmtPct, pctClass } from '@/utils/format';
import { PanelEmpty } from '@/components/ui';

export default function ArbitrageRadarCard({ onSelectCode }: { onSelectCode?: (code: string) => void }) {
  const [data, setData] = useState<EtfArbitrageData | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'premium' | 'discount'>('premium');

  useEffect(() => {
    let active = true;
    setLoading(true);
    etfApi.arbitrage(10)
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

  const items: EtfItem[] = activeTab === 'premium' ? (data?.high_premium ?? []) : (data?.high_discount ?? []);
  const summary = data?.summary;

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部标题与中位数 */}
      <div className="flex items-center justify-between border-b border-hair px-2.5 py-1.5">
        <div className="flex items-center gap-1.5">
          <h3 className="text-xs font-semibold text-ink">折溢价套利雷达</h3>
          {summary?.sample_count ? (
            <span className="rounded bg-surface-sunken px-1 py-0.2 text-2xs text-ink-muted">
              样本 {summary.sample_count}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1 text-2xs">
          <button
            type="button"
            onClick={() => setActiveTab('premium')}
            className={`rounded px-1.5 py-0.5 transition-colors ${
              activeTab === 'premium'
                ? 'bg-warn-bg font-medium text-warn shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            高溢价预警
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('discount')}
            className={`rounded px-1.5 py-0.5 transition-colors ${
              activeTab === 'discount'
                ? 'bg-up-bg font-medium text-up shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            折价安全垫
          </button>
        </div>
      </div>

      {/* 统计指标信息栏 */}
      {summary?.median_premium != null && (
        <div className="flex items-center justify-between border-b border-hair/50 bg-surface-sunken/40 px-2.5 py-1 text-2xs">
          <div className="flex items-center gap-2 text-ink-muted">
            <span>中位数: <b className={`num font-semibold ${pctClass(summary.median_premium)}`}>{fmtPct(summary.median_premium)}</b></span>
            <span>均值: <b className="num text-ink-secondary">{fmtPct(summary.avg_premium)}</b></span>
          </div>
          <div className="flex items-center gap-1.5 text-2xs text-ink-muted">
            <span className="text-warn">正溢价 {summary.positive_count}</span>
            <span className="text-hair">|</span>
            <span className="text-up">折价 {summary.negative_count}</span>
          </div>
        </div>
      )}

      {/* 内容表格 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-0">
        <table className="quant-table compact w-full">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th className="text-right">现价</th>
              <th className="text-right">IOPV</th>
              <th className="text-right">折溢价率</th>
            </tr>
          </thead>
          <tbody>
            {loading && !items.length ? (
              <tr>
                <td colSpan={5} className="py-6 text-center text-xs text-ink-muted">
                  正在扫描全市场实时 IOPV 与折溢价...
                </td>
              </tr>
            ) : items.length ? (
              items.map((it) => {
                const prem = it.premium_rate;
                const isExtreme = prem != null && (prem > 3.0 || prem < -3.0);
                return (
                  <tr
                    key={it.code}
                    className="hover:bg-surface-alt cursor-pointer"
                    onClick={() => onSelectCode?.(it.code)}
                  >
                    <td>
                      <Link
                        to={`/etf/${it.code}`}
                        state={{ kind: it.type, name: it.name }}
                        className="num text-brand-600 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {it.code}
                      </Link>
                    </td>
                    <td className="max-w-[6.5rem] truncate text-xs text-ink-secondary" title={it.name ?? ''}>
                      {it.name ?? '—'}
                    </td>
                    <td className="num text-right text-xs text-ink">{fmtNum(it.price)}</td>
                    <td className="num text-right text-xs text-ink-muted">{fmtNum(it.iopv)}</td>
                    <td className="text-right">
                      <span className={`num font-medium ${pctClass(prem)}`}>
                        {prem != null ? `${prem > 0 ? '+' : ''}${prem.toFixed(2)}%` : '—'}
                      </span>
                      {isExtreme && prem != null && prem > 3.0 && (
                        <span className="ml-0.5 text-2xs text-warn" title="高溢价警惕回落风险">⚠️</span>
                      )}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={5}>
                  <PanelEmpty minH="min-h-[100px]" text="暂无折溢价偏离标的" />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 底部微型量化提示 */}
      <div className="border-t border-hair bg-surface px-2 py-1 text-2xs text-ink-muted">
        <span>口径：交易所实时 IOPV · 刷新周期 30s · 杜绝估算假数</span>
      </div>
    </div>
  );
}
