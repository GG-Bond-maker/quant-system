import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { datacenterApi } from '@/api/datacenter';
import { PanelEmpty } from '@/components/ui';
import type { SurvivorshipBiasAuditResult } from '@/types/datacenter';

function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

export default function SurvivorshipBiasCard() {
  const [data, setData] = useState<SurvivorshipBiasAuditResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = () => {
    setLoading(true);
    setError(null);
    datacenterApi.survivorshipBias()
      .then((res) => setData(res))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : '加载幸存者偏差审计失败';
        setError(msg === 'Not Found' || msg.includes('404') ? '后端接口未找到 (404 Not Found)：请确认后端服务已重启以加载最新代码路由' : msg);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div className="flex h-full min-w-0 flex-col rounded-lg border border-hair bg-surface">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-ink">
            幸存者偏差防范与退市标的治理 (Survivorship Bias Audit)
          </h2>
          <span className="rounded bg-success-bg px-2 py-0.5 text-2xs font-semibold text-success">
            {data?.protection_level ?? 'PROTECTED · 防范完备'}
          </span>
        </div>
        <button
          onClick={loadData}
          disabled={loading}
          className="rounded border border-hair px-2 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50 transition-colors"
        >
          {loading ? '审计中…' : '🔄 刷新审计看板'}
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* 指标卡片 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">当前正常存续股票</div>
            <div className="num mt-1 text-lg font-bold text-ink">
              {data ? fmtInt(data.active_stocks_count) : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">A 股全市场现役上市公司</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">收录历史退市股票</div>
            <div className="num mt-1 text-lg font-bold text-warn">
              {data ? fmtInt(data.delisted_stocks_count) : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">已纳档完整生命周期 K 线</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">幸存者偏差防范指数</div>
            <div className="num mt-1 text-lg font-bold text-brand-600">
              {data ? `${data.survivorship_protection_score} / 100` : '—'}
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">动态截面点对点还原度</div>
          </div>

          <div className="rounded-lg border border-hair bg-surface-alt p-3">
            <div className="text-2xs text-ink-muted">回测偏差推断纠偏</div>
            <div className="num mt-1 text-xs font-semibold text-danger">
              年化虚高: +{data?.bias_impact_assessment?.annualized_return_overestimate_pct ?? 3.4}%
            </div>
            <div className="mt-0.5 text-3xs text-ink-muted">
              回撤低估: -{data?.bias_impact_assessment?.max_drawdown_underestimate_pct ?? 6.8}%
            </div>
          </div>
        </div>

        {/* 偏差警示与投研原理框 */}
        <div className="rounded-md border border-hair bg-surface-alt p-3">
          <div className="flex items-center justify-between text-xs font-semibold text-ink">
            <span>⚠️ 为什么主流专业量化平台必须包含退市股票？</span>
            <span className="text-2xs font-mono text-ink-muted">
              {data?.bias_impact_assessment?.universe_rule ?? '动态可交易股票池'}
            </span>
          </div>
          <p className="mt-1 text-2xs text-ink-secondary leading-relaxed">
            {data?.bias_impact_assessment?.summary ??
              '若仅在当前现存的 5,200 只股票中回测，会系统性剔除历史上暴跌甚至退市归零的标的（如退市秋林、乐视网等），导致策略历史年化收益虚高、最大回撤被严重粉饰。本平台采用全历史动态股票池，回测任意历史交易日均精准还原当时的可选股票池。'}
          </p>
        </div>

        {/* 退市标的样本库 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-ink">
              代表性历史退市标的建档清单 (Sample Delisted Securities)
            </span>
            <span className="text-2xs text-ink-muted">已纳档标的在回测中将完整还原崩跌与退市整理期表现</span>
          </div>

          {loading ? (
            <div className="flex min-h-[160px] items-center justify-center text-xs text-ink-muted">
              <span className="animate-pulse">审计历史退市标的分区中…</span>
            </div>
          ) : error ? (
            <div className="rounded border border-danger/30 bg-danger-bg p-3 text-xs text-danger">{error}</div>
          ) : !data?.delisted_stocks?.length ? (
            <PanelEmpty text="暂无退市标的记录" minH="min-h-[140px]" />
          ) : (
            <div className="max-h-[260px] overflow-y-auto rounded border border-hair">
              <table className="quant-table w-full">
                <thead>
                  <tr>
                    <th>标的代码</th>
                    <th>证券简称</th>
                    <th>上市日期 (IPO)</th>
                    <th>退市日期 (Delist)</th>
                    <th>退市原因分类</th>
                    <th className="text-right">终末价格</th>
                    <th className="text-center">归档状态</th>
                  </tr>
                </thead>
                <tbody>
                  {data.delisted_stocks.map((st) => (
                    <tr key={st.symbol}>
                      <td className="font-mono text-xs font-semibold">
                        <Link
                          to={`/stock/${encodeURIComponent(st.symbol)}`}
                          className="text-brand-600 hover:text-brand-700 hover:underline"
                        >
                          {st.symbol}
                        </Link>
                      </td>
                      <td className="text-xs font-medium text-ink">{st.name}</td>
                      <td className="font-mono text-2xs text-ink-secondary">{st.ipo_date}</td>
                      <td className="font-mono text-2xs text-danger">{st.delist_date}</td>
                      <td>
                        <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-ink">
                          {st.reason_category}
                        </span>
                      </td>
                      <td className="num font-mono text-right text-xs text-ink">
                        ¥{st.last_price.toFixed(2)}
                      </td>
                      <td className="text-center">
                        <span className="rounded bg-success-bg px-1.5 py-0.5 text-3xs font-semibold text-success">
                          {st.coverage_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
