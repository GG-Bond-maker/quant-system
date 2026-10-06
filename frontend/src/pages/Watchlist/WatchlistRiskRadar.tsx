/**
 * 自选池组合即时风险透视看板（Watchlist Portfolio Risk Radar）。
 *
 * 核心指标：
 * 1. 等权组合年化波动率（Annualized Volatility, sigma_p）；
 * 2. 相对沪深 300 基准 Beta 与相关系数；
 * 3. 组合资产分散化倍数（Diversification Ratio = 加权均值资产波动率 / 组合波动率）；
 * 4. 区间历史最大回撤（MDD）；
 * 5. 申万/行业集中度分布与有效行业数（Herfindahl Inverse）。
 *
 * 全量消费后端 POST /api/v1/watchlist/risk-summary 真实计算，杜绝伪造数据。
 */
import { useEffect, useState } from 'react';
import { watchlistApi } from '@/api/watchlist';
import type { WatchlistRiskResponse, WatchlistRiskSummaryData } from '@/types/watchlist';
import { fmtPct, pctClass } from '@/utils/format';

import { CATEGORY_COLORS } from '@/lib/chartTheme';

const WINDOW_OPTIONS = [
  { label: '60日', value: 60 },
  { label: '120日', value: 120 },
  { label: '250日', value: 250 },
] as const;

// 行业调色板
const SECTOR_COLORS = CATEGORY_COLORS.PIE;

interface WatchlistRiskRadarProps {
  /** 当前选中的标的列表（若未选则使用当前分组全量标的） */
  symbols: string[];
  /** 标的显示总数 */
  totalSymbolsCount: number;
}

export function WatchlistRiskRadar({ symbols, totalSymbolsCount }: WatchlistRiskRadarProps) {
  const [windowDays, setWindowDays] = useState<number>(120);
  const [data, setData] = useState<WatchlistRiskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [showConstituents, setShowConstituents] = useState(false);

  useEffect(() => {
    if (!symbols.length) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    watchlistApi
      .riskSummary(symbols, windowDays)
      .then((res) => {
        if (!cancelled) {
          setData(res);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.message || '组合风险数据计算失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbols, windowDays]);

  if (!symbols.length) {
    return null;
  }

  const summary: WatchlistRiskSummaryData | null = data?.summary ?? null;
  const breakdown = data?.industry_breakdown ?? [];
  const assets = data?.assets ?? [];

  // Beta 风格分类
  const betaBadge = (b: number | null) => {
    if (b == null) return null;
    if (b < 0.85) return <span className="rounded bg-blue-50 px-1.5 py-0.5 text-2xs font-medium text-blue-600 border border-blue-200">防守型 (低Beta)</span>;
    if (b > 1.15) return <span className="rounded bg-rose-50 px-1.5 py-0.5 text-2xs font-medium text-rose-600 border border-rose-200">进取型 (高Beta)</span>;
    return <span className="rounded bg-slate-50 px-1.5 py-0.5 text-2xs font-medium text-ink-secondary border border-slate-200">均衡型</span>;
  };

  // 分散化倍数评级
  const drBadge = (dr: number | null) => {
    if (dr == null) return null;
    if (dr >= 1.35) return <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-2xs font-medium text-emerald-600 border border-emerald-200">高分散红利</span>;
    return <span className="rounded bg-amber-50 px-1.5 py-0.5 text-2xs font-medium text-amber-600 border border-amber-200">一般分散</span>;
  };

  return (
    <div className="rounded-lg border border-hair bg-white transition-all shadow-xs">
      {/* 标题栏与控制选项 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hair px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded bg-brand-50 text-brand-600">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          </div>
          <span className="text-xs font-semibold text-ink">自选池组合即时风险透视</span>
          <span className="text-2xs text-ink-muted">
            （基于当前 {symbols.length} 只标的等权组合 · 池容量 {totalSymbolsCount} 只 · 对齐沪深300基准）
          </span>
          {loading && <span className="text-2xs text-brand-500 animate-pulse">计算中...</span>}
        </div>

        <div className="flex items-center gap-2">
          {/* 回看窗口切换 */}
          <div className="inline-flex rounded-md border border-hair p-0.5 bg-slate-50">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setWindowDays(opt.value)}
                className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
                  windowDays === opt.value
                    ? 'bg-white text-brand-600 shadow-2xs font-semibold'
                    : 'text-ink-muted hover:text-ink'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 折叠切换 */}
          <button
            onClick={() => setExpanded(!expanded)}
            className="rounded p-1 text-ink-muted hover:bg-slate-100 hover:text-ink transition-colors"
            title={expanded ? '收起透视面板' : '展开透视面板'}
          >
            <svg
              viewBox="0 0 24 24"
              className={`h-4 w-4 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </div>
      </div>

      {expanded && (
        <div className="p-4 space-y-3.5">
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </div>
          )}

          {data?.status === 'degraded' && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              数据降级：{data.reason || '部分标的历史日线样本不足'}。仅对有效标的执行统计。
            </div>
          )}

          {/* 5 维核心量化指标卡片 */}
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
            {/* 1. 年化波动率 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>组合年化波动率</span>
                <span className="text-ink-muted">σ</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {summary?.portfolio_ann_vol != null ? `${summary.portfolio_ann_vol.toFixed(2)}%` : '—'}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                {summary?.window_days ? `最近 ${summary.window_days} 交易日` : '—'}
              </div>
            </div>

            {/* 2. 基准 Beta */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>基准 Beta (沪深300)</span>
                <span className="text-ink-muted">β</span>
              </div>
              <div className="num mt-1 flex items-baseline gap-1.5 text-base font-semibold text-ink">
                <span>{summary?.portfolio_beta != null ? summary.portfolio_beta.toFixed(2) : '—'}</span>
                {betaBadge(summary?.portfolio_beta ?? null)}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                相关系数: {summary?.benchmark_corr != null ? summary.benchmark_corr.toFixed(2) : '—'}
              </div>
            </div>

            {/* 3. 分散化倍数 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>资产分散倍数 (DR)</span>
                <span className="text-ink-muted">Σwσ / σp</span>
              </div>
              <div className="num mt-1 flex items-baseline gap-1.5 text-base font-semibold text-ink">
                <span>{summary?.diversification_ratio != null ? `${summary.diversification_ratio.toFixed(2)}x` : '—'}</span>
                {drBadge(summary?.diversification_ratio ?? null)}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                有效资产: {summary?.valid_count ?? 0} / {summary?.asset_count ?? symbols.length}
              </div>
            </div>

            {/* 4. 区间最大回撤 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>区间最大回撤 (MDD)</span>
                <span className="text-ink-muted">Peak-T</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-rose-600">
                {summary?.max_drawdown != null ? `-${summary.max_drawdown.toFixed(2)}%` : '—'}
              </div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                区间: {summary?.start_date ? `${summary.start_date.slice(5)} ~ ${summary.end_date?.slice(5)}` : '—'}
              </div>
            </div>

            {/* 5. 行业集中度与有效行业数 */}
            <div className="rounded-md border border-hair bg-slate-50/60 p-2.5">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span>行业集中度</span>
                <span className="text-ink-muted">HHI</span>
              </div>
              <div className="num mt-1 text-base font-semibold text-ink">
                {summary?.top_industry_ratio != null ? `${summary.top_industry_ratio.toFixed(1)}%` : '—'}
              </div>
              <div className="mt-0.5 truncate text-2xs text-ink-muted" title={summary?.top_industry ?? ''}>
                首要: {summary?.top_industry || '—'} · {summary?.effective_sectors ? `${summary.effective_sectors}有效行业` : ''}
              </div>
            </div>
          </div>

          {/* 行业分布堆叠进度条 */}
          {breakdown.length > 0 && (
            <div className="space-y-1.5 rounded-md border border-hair p-2.5 bg-slate-50/30">
              <div className="flex items-center justify-between text-2xs text-ink-secondary">
                <span className="font-medium">自选池行业暴露构成</span>
                <span>共覆盖 {breakdown.length} 个行业板块</span>
              </div>
              {/* 堆叠色彩长条 */}
              <div className="flex h-2.5 w-full overflow-hidden rounded bg-slate-100">
                {breakdown.map((item, idx) => (
                  <div
                    key={item.industry}
                    style={{
                      width: `${item.weight}%`,
                      backgroundColor: SECTOR_COLORS[idx % SECTOR_COLORS.length],
                    }}
                    title={`${item.industry}: ${item.weight}% (${item.count}只)`}
                    className="h-full transition-all duration-300 first:rounded-l last:rounded-r"
                  />
                ))}
              </div>
              {/* 行业标签流 */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 text-2xs">
                {breakdown.map((item, idx) => (
                  <div key={item.industry} className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 rounded-full shrink-0"
                      style={{ backgroundColor: SECTOR_COLORS[idx % SECTOR_COLORS.length] }}
                    />
                    <span className="text-ink">{item.industry}</span>
                    <span className="num font-semibold text-ink-secondary">{item.weight}%</span>
                    <span className="text-ink-muted">({item.count}只)</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 标的风险明细展开开关与表格 */}
          {assets.length > 0 && (
            <div className="pt-0.5">
              <button
                onClick={() => setShowConstituents(!showConstituents)}
                className="flex items-center gap-1 text-2xs font-medium text-brand-600 hover:text-brand-700"
              >
                <span>{showConstituents ? '收起各标的风险贡献明细' : '展开各标的风险贡献明细（波动率 / Beta / 回撤）'}</span>
                <svg
                  viewBox="0 0 24 24"
                  className={`h-3 w-3 transition-transform ${showConstituents ? 'rotate-180' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>

              {showConstituents && (
                <div className="mt-2 overflow-x-auto rounded border border-hair">
                  <table className="quant-table w-full text-2xs">
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>行业分类</th>
                        <th className="text-right">等权占比</th>
                        <th className="text-right">年化波动率</th>
                        <th className="text-right">基准Beta</th>
                        <th className="text-right">区间最大回撤</th>
                        <th className="text-right">区间收益率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {assets.map((ast) => (
                        <tr key={ast.symbol}>
                          <td className="num font-medium text-ink">{ast.code}</td>
                          <td className="text-ink-secondary">{ast.name}</td>
                          <td>
                            <span className="rounded bg-slate-100 px-1 py-0.5 text-ink-muted">
                              {ast.industry}
                            </span>
                          </td>
                          <td className="num text-right text-ink-muted">{ast.weight.toFixed(1)}%</td>
                          <td className="num text-right font-medium text-ink">
                            {ast.ann_vol != null ? `${ast.ann_vol.toFixed(2)}%` : '—'}
                          </td>
                          <td className="num text-right text-ink">
                            {ast.beta != null ? ast.beta.toFixed(2) : '—'}
                          </td>
                          <td className="num text-right text-rose-500">
                            {ast.mdd != null ? `-${ast.mdd.toFixed(2)}%` : '—'}
                          </td>
                          <td className={`num text-right font-medium ${pctClass(ast.period_return)}`}>
                            {ast.period_return != null ? fmtPct(ast.period_return) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
